import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.core.chunk_preview import build_chunk_preview
from tools.core.context_budget import ContextBudget, estimate_chunk_tokens, split_chunks
from tools.core.models import ChunkEntry
from tools.core.eval_layer import LlmEvalLayer, _normalize_eval_payload, _render_chunk_for_eval


def test_document_preview_preserves_original_chunk() -> None:
    text = "正文" * 1000
    assert text in build_chunk_preview(text, {"source_name": "doc"})


def test_table_preview_is_compact() -> None:
    text = "表格内容" * 1000
    preview = build_chunk_preview(text, {"table_preview": text}, limit=80)
    assert len(preview) < len(text)


def test_batcher_keeps_all_ids_in_stable_order() -> None:
    chunks = [ChunkEntry(id=str(i), source="feishu", content="中" * 300_000) for i in range(3)]
    batches = split_chunks(chunks, ContextBudget())
    assert [c.id for batch in batches for c in batch] == ["0", "1", "2"]
    # The evaluator receives a bounded prompt preview, not full payload text.
    assert len(batches) == 1


def test_batcher_estimates_the_same_window_as_source_eval() -> None:
    document = ChunkEntry(id="doc", source="feishu", content="中" * 300_000)
    table = ChunkEntry(id="table", source="feishu", content="Bitable:\n" + "中" * 300_000)
    assert estimate_chunk_tokens(document) < 500
    assert estimate_chunk_tokens(table) < 2_500


def test_batcher_uses_token_budget_without_chunk_cap() -> None:
    chunks = [ChunkEntry(id=str(i), source="feishu", content="short") for i in range(241)]
    batches = split_chunks(chunks, ContextBudget())
    assert [len(batch) for batch in batches] == [241]
    assert [c.id for batch in batches for c in batch] == [str(i) for i in range(241)]


def test_cross_source_eval_uses_preview_not_full_content() -> None:
    chunk = ChunkEntry(id="x", source="feishu", content="FULL_SECRET", preview_content="PREVIEW")
    rendered = _render_chunk_for_eval(chunk)
    assert "PREVIEW" in rendered
    assert "FULL_SECRET" not in rendered


class ContextBudgetTests(unittest.TestCase):
    def test_trigger_is_forty_percent_of_usable_input(self):
        budget = ContextBudget()
        self.assertEqual(budget.usable_tokens, 576784)
        self.assertEqual(budget.flush_threshold, int(budget.usable_tokens * 0.4))

    def test_serialized_prompt_budget_can_split_few_large_chunks(self):
        chunks = [ChunkEntry(id=str(i), source="feishu", content="body") for i in range(5)]
        batches = split_chunks(chunks, ContextBudget(hard_ceiling=1000), prompt_tokens=lambda batch: len(batch) * 200)
        self.assertEqual([len(batch) for batch in batches], [2, 2, 1])

    def test_document_preview(self):
        test_document_preview_preserves_original_chunk()

    def test_table_preview(self):
        test_table_preview_is_compact()

    def test_stable_batches(self):
        test_batcher_keeps_all_ids_in_stable_order()

    def test_preview_for_eval(self):
        test_cross_source_eval_uses_preview_not_full_content()

    def test_output_shape_batch_limit(self):
        test_batcher_uses_token_budget_without_chunk_cap()

    def test_global_llm_eval_uses_deepseek_default(self):
        self.assertEqual(LlmEvalLayer().model, "deepseek/deepseek-flash")

    def test_global_llm_eval_recovers_top_level_tier_list(self):
        payload = _normalize_eval_payload([
            {"chunk_id": "low", "tier": "important", "relevance": 0.7},
            {"chunk_id": "high", "tier": "critical", "relevance": 0.6},
        ], top_k=1)
        self.assertEqual(payload["selected_ids"], ["high"])
        self.assertEqual(len(payload["tiers"]), 2)

    def test_global_llm_eval_records_body_free_attempt_and_fallback(self):
        chunk = ChunkEntry(id="c1", source="feishu", content="full", preview_content="preview", tier="critical")
        layer = LlmEvalLayer()
        observed = {}

        def empty_model_call(*_args, **kwargs):
            observed.update(kwargs)
            kwargs["trace_callback"]({
                "event": "llm_call_attempt", "caller_tag": "eval_layer.llm_tool",
                "attempt": 1, "outcome": "failed", "error_type": "empty_response",
            })
            raise RuntimeError("empty response from model")

        with patch("tools.core.llm_client.call_llm", side_effect=empty_model_call):
            selected = layer.rank([chunk], [SimpleNamespace(focus_id="F1", text="focus")], 1)

        self.assertEqual([item.id for item in selected], ["c1"])
        self.assertEqual(observed["max_retries"], 2)
        self.assertTrue(any(event["event"] == "model_call_attempt" for event in layer.trace_events))
        fallback = next(event for event in layer.trace_events if event["event"] == "eval_layer_fallback")
        self.assertEqual(fallback["fallback"], "rule")
