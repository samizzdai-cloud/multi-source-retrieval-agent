"""
Test script for the Feishu RAG pipeline.

Usage:
    python -m rag.test_pipeline
or:
    cd /path/to/project && python rag/test_pipeline.py
"""
import os
import sys
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Callable
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(

    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Tracing data structures ───────────────────────────────────────────────────

@dataclass
class LLMCallTrace:
    """Records a single LLM call's full input prompt and output response."""
    caller_tag: str
    prompt: str
    response: str
    call_index: int  # sequential index across all LLM calls in this run


@dataclass
class StageTrace:
    stage_num: int
    stage_name: str
    func_name: str
    input_summary: dict
    output_summary: dict
    elapsed_seconds: float
    success: bool
    error: Optional[str] = None


@dataclass
class TracePayload:
    stages: List[StageTrace]
    llm_calls: List[LLMCallTrace]
    wall_time_seconds: float
    all_success: bool


class Tracer:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled
        self._stages: List[StageTrace] = []

    def record(self, trace: StageTrace) -> None:
        if self._enabled:
            self._stages.append(trace)

    def make_patch(
        self,
        stage_num: int,
        stage_name: str,
        func: Callable,
        summarize_in: Callable,
        summarize_out: Callable,
    ) -> Callable:
        import asyncio
        if not self._enabled:
            return func

        if asyncio.iscoroutinefunction(func):
            async def async_wrapper(*args, **kwargs):
                try:
                    input_summary = summarize_in(*args, **kwargs)
                except Exception as e:
                    input_summary = {"error": str(e)}

                t0 = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                except Exception as e:
                    elapsed = time.perf_counter() - t0
                    self.record(StageTrace(
                        stage_num=stage_num,
                        stage_name=stage_name,
                        func_name=func.__name__,
                        input_summary=input_summary,
                        output_summary={},
                        elapsed_seconds=elapsed,
                        success=False,
                        error=str(e),
                    ))
                    raise

                elapsed = time.perf_counter() - t0
                try:
                    output_summary = summarize_out(result)
                except Exception as e:
                    output_summary = {"error": str(e)}

                self.record(StageTrace(
                    stage_num=stage_num,
                    stage_name=stage_name,
                    func_name=func.__name__,
                    input_summary=input_summary,
                    output_summary=output_summary,
                    elapsed_seconds=elapsed,
                    success=True,
                ))
                return result

            return async_wrapper

        def wrapper(*args, **kwargs):
            try:
                input_summary = summarize_in(*args, **kwargs)
            except Exception as e:
                input_summary = {"error": str(e)}

            t0 = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            except Exception as e:
                elapsed = time.perf_counter() - t0
                self.record(StageTrace(
                    stage_num=stage_num,
                    stage_name=stage_name,
                    func_name=func.__name__,
                    input_summary=input_summary,
                    output_summary={},
                    elapsed_seconds=elapsed,
                    success=False,
                    error=str(e),
                ))
                raise

            elapsed = time.perf_counter() - t0
            try:
                output_summary = summarize_out(result)
            except Exception as e:
                output_summary = {"error": str(e)}

            self.record(StageTrace(
                stage_num=stage_num,
                stage_name=stage_name,
                func_name=func.__name__,
                input_summary=input_summary,
                output_summary=output_summary,
                elapsed_seconds=elapsed,
                success=True,
            ))
            return result

        return wrapper

    def finalize(self, wall_time: float) -> TracePayload:
        all_success = all(t.success for t in self._stages) if self._stages else True
        return TracePayload(
            stages=self._stages,
            llm_calls=[],  # populated separately by LLMCallCollector
            wall_time_seconds=wall_time,
            all_success=all_success,
        )


class LLMCallCollector:
    """Collects every LLM call's prompt and response via set_llm_trace_hook."""

    def __init__(self) -> None:
        self._calls: List[LLMCallTrace] = []

    def _on_call(self, prompt: str, response: str, caller_tag: str) -> None:
        self._calls.append(LLMCallTrace(
            caller_tag=caller_tag,
            prompt=prompt,
            response=response,
            call_index=len(self._calls),
        ))

    def install(self) -> None:
        from tools.llm_client import set_llm_trace_hook
        set_llm_trace_hook(self._on_call)

    def uninstall(self) -> None:
        from tools.llm_client import clear_llm_trace_hook
        clear_llm_trace_hook()

    @property
    def calls(self) -> List[LLMCallTrace]:
        return self._calls


# ── Serialization helpers ─────────────────────────────────────────────────────

def _truncate_str(s: str, max_len: int = 500) -> str:
    return s[:max_len] if len(s) > max_len else s


def _to_serializable_trace(obj):
    """Recursively convert dataclass/Enum/list/dict to JSON-serializable types."""
    import dataclasses
    import enum
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_serializable_trace(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, list):
        return [_to_serializable_trace(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_serializable_trace(v) for k, v in obj.items()}
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return type(obj).__name__


def _truncate_summary_strings(d: dict) -> dict:
    """Recursively traverse dict and truncate all string values to 500 chars."""
    result = {}
    for k, v in d.items():
        if isinstance(v, str):
            result[k] = _truncate_str(v)
        elif isinstance(v, dict):
            result[k] = _truncate_summary_strings(v)
        elif isinstance(v, list):
            new_list = []
            for item in v:
                if isinstance(item, dict):
                    new_list.append(_truncate_summary_strings(item))
                elif isinstance(item, str):
                    new_list.append(_truncate_str(item))
                else:
                    new_list.append(item)
            result[k] = new_list
        else:
            result[k] = v
    return result


# ── Stage summary functions ───────────────────────────────────────────────────

def _summarize_in_s1(query, page_size, max_pages):
    return {"query": query, "page_size": page_size, "max_pages": max_pages}

def _summarize_out_s1(result):
    candidates = result.get("items", [])
    return {
        "status": result.get("status"),
        "candidates_count": len(candidates),
        "candidates": [{"doc_id": c.get("id", c.get("document_id", c.get("doc_id", ""))), "title": c.get("title", "")} for c in candidates],
    }


def _summarize_in_s2(query, candidates, max_candidates):
    return {"query": query, "candidates_count": len(candidates), "max_candidates": max_candidates}

def _summarize_out_s2(filtered_ids):
    return {"filtered_ids": list(filtered_ids), "retained_count": len(filtered_ids)}


def _summarize_in_s3(filtered_docs, max_concurrency):
    return {"doc_ids": [d.get("id", d.get("document_id", d.get("doc_id", ""))) for d in filtered_docs], "max_concurrency": max_concurrency}

def _summarize_out_s3(doc_blocks):
    return {
        "fetched_count": len(doc_blocks),
        "docs": [{"doc_id": d["doc_id"], "blocks_count": len(d.get("blocks", []))} for d in doc_blocks],
    }


def _summarize_in_s4(doc_blocks):
    return {"docs_count": len(doc_blocks), "docs": [{"doc_id": d["doc_id"], "blocks_count": len(d.get("blocks", []))} for d in doc_blocks]}

def _summarize_out_s4(all_chunks):
    from feishu_read_tool.pipeline_models import ChunkContentType
    text_count = sum(1 for c in all_chunks if c.content_type == ChunkContentType.TEXT)
    table_count = len(all_chunks) - text_count
    per_doc = {}
    for c in all_chunks:
        per_doc[c.doc_id] = per_doc.get(c.doc_id, 0) + 1
    return {
        "total_chunks": len(all_chunks),
        "text_chunks": text_count,
        "table_chunks": table_count,
        "per_doc": [{"doc_id": k, "chunks": v} for k, v in per_doc.items()],
    }


def _summarize_in_s5(chunks, query):
    return {"chunks_count": len(chunks), "query": query}

def _summarize_out_s5(scored_chunks, threshold):
    if not scored_chunks:
        return {"scored_count": 0, "max_score": None, "min_score": None, "avg_score": None, "retained_after_threshold": 0}
    scores = [c.relevance_score for c in scored_chunks]
    retained = sum(1 for s in scores if s >= threshold)
    return {
        "scored_count": len(scored_chunks),
        "max_score": round(max(scores), 2),
        "min_score": round(min(scores), 2),
        "avg_score": round(sum(scores) / len(scores), 2),
        "retained_after_threshold": retained,
    }


def _summarize_in_s6(scored_chunks, cfg):
    return {
        "candidates_count": len(scored_chunks),
        "total_token_budget": cfg.total_token_budget,
        "per_doc_token_budget": cfg.per_doc_token_budget,
        "max_chunks_total": cfg.max_chunks_total,
        "max_chunks_per_doc": cfg.max_chunks_per_doc,
    }

def _summarize_out_s6(selected):
    truncated = sum(1 for c in selected if hasattr(c, "chunks_content") and "[内容已截断]" in c.chunks_content)
    return {
        "selected_count": len(selected),
        "total_tokens": sum(c.token_count for c in selected),
        "truncated_count": truncated,
        "selected": [{"chunk_id": c.chunk_id, "score": round(c.relevance_score, 2)} for c in selected],
    }


def _summarize_out_s7(result, wall_time):
    return {
        "total_docs_searched": result.total_docs_searched,
        "total_docs_read": result.total_docs_read,
        "total_chunks": result.total_chunks,
        "total_tokens": result.total_tokens,
        "wall_time_seconds": round(wall_time, 3),
    }


# ── Test configuration ────────────────────────────────────────────────────────

QUERY = "hypershell 竞品"

# Pipeline config overrides (uses PipelineConfig defaults for everything else)
CFG_OVERRIDES = dict(
    search_page_size=20,
    search_max_pages=1,
    max_candidates=5,
)

# Debug print toggles
PRINT_SEARCH_RESULT = True
PRINT_FILTERED_IDS = True
PRINT_CHUNKS_BEFORE = True
PRINT_CHUNKS_AFTER = True
PRINT_FINAL_RESULT = True

# Output saving
SAVE_OUTPUT_JSON = True
OUTPUT_DIR = "./test_output"
ENABLE_STAGE_TRACING = True


# ── Test runner ───────────────────────────────────────────────────────────────

def run_test() -> None:
    from feishu_read_tool.search import search_feishu_file
    from feishu_read_tool.pipeline_models import PipelineConfig
    from feishu_read_tool.pipeline import run_rag_pipeline
    import feishu_read_tool.pipeline as _pipeline_mod

    UAT = os.getenv("FEISHU_UAT", "")
    if not UAT:
        logger.warning("FEISHU_UAT not set — API calls may fail")

    cfg = PipelineConfig(**CFG_OVERRIDES)
    tracer = Tracer(enabled=ENABLE_STAGE_TRACING)
    llm_collector = LLMCallCollector()
    llm_collector.install()
    wall_t0 = time.perf_counter()

    # ── Stage 1: Feishu search ────────────────────────────────────────────────
    logger.info("Stage 1: searching Feishu for query: %s", QUERY)
    s1_t0 = time.perf_counter()
    try:
        search_result = search_feishu_file(
            QUERY,
            page_size=cfg.search_page_size,
            max_pages=cfg.search_max_pages,
        )
        s1_elapsed = time.perf_counter() - s1_t0
        tracer.record(StageTrace(
            stage_num=1,
            stage_name="Feishu Search",
            func_name="search_feishu_file",
            input_summary=_summarize_in_s1(QUERY, cfg.search_page_size, cfg.search_max_pages),
            output_summary=_truncate_summary_strings(_summarize_out_s1(search_result)),
            elapsed_seconds=round(s1_elapsed, 3),
            success=True,
        ))
    except Exception as e:
        s1_elapsed = time.perf_counter() - s1_t0
        tracer.record(StageTrace(
            stage_num=1,
            stage_name="Feishu Search",
            func_name="search_feishu_file",
            input_summary=_summarize_in_s1(QUERY, cfg.search_page_size, cfg.search_max_pages),
            output_summary={},
            elapsed_seconds=round(s1_elapsed, 3),
            success=False,
            error=str(e),
        ))
        raise

    if search_result.get("status") != "success":
        logger.error("Search failed: %s", search_result)
        return

    candidates = search_result.get("items", [])
    logger.info("Stage 1: found %d candidates", len(candidates))

    if PRINT_SEARCH_RESULT:
        print("\n=== Stage 1: Search Results ===")
        for i, c in enumerate(candidates):
            doc_id = c.get("id", c.get("document_id", c.get("doc_id", "")))
            title = c.get("title", "")
            print(f"  [{i}] {doc_id}  {title}")

    if not candidates:
        logger.warning("No candidates found — exiting test")
        return

    # ── Stage 2-6: Monkey-patch pipeline functions for tracing ────────────────
    # Stage 2: llm_filter_candidates
    _orig_filter = _pipeline_mod.llm_filter_candidates
    _pipeline_mod.llm_filter_candidates = tracer.make_patch(
        stage_num=2,
        stage_name="LLM Filter Candidates",
        func=_orig_filter,
        summarize_in=lambda q, cands, max_c: _summarize_in_s2(q, cands, max_c),
        summarize_out=lambda result: _truncate_summary_strings(_summarize_out_s2(result)),
    )

    # Stage 3: _fetch_all_docs_async (called inside run_rag_pipeline via loop.run_until_complete)
    _orig_fetch = _pipeline_mod._fetch_all_docs_async
    _pipeline_mod._fetch_all_docs_async = tracer.make_patch(
        stage_num=3,
        stage_name="Fetch Document Blocks",
        func=_orig_fetch,
        summarize_in=lambda client, filtered_docs, max_concurrency=5: _summarize_in_s3(filtered_docs, max_concurrency),
        summarize_out=lambda result: _truncate_summary_strings(_summarize_out_s3(result)),
    )

    # Stage 5: llm_score_and_summarize
    _orig_score = _pipeline_mod.llm_score_and_summarize
    _pipeline_mod.llm_score_and_summarize = tracer.make_patch(
        stage_num=5,
        stage_name="LLM Score and Summarize",
        func=_orig_score,
        summarize_in=lambda q, chunks, cfg_: _summarize_in_s5(chunks, q),
        summarize_out=lambda result: _truncate_summary_strings(_summarize_out_s5(result, cfg.relevance_threshold)),
    )

    # Stage 6: allocate_budget
    _orig_budget = _pipeline_mod.allocate_budget
    _pipeline_mod.allocate_budget = tracer.make_patch(
        stage_num=6,
        stage_name="Budget Allocation",
        func=_orig_budget,
        summarize_in=lambda scored_chunks, cfg_: _summarize_in_s6(scored_chunks, cfg_),
        summarize_out=lambda result: _truncate_summary_strings(_summarize_out_s6(result)),
    )

    # ── Stage 2-7: Run pipeline (patches active) ──────────────────────────────
    logger.info("Running pipeline stages 2-7 ...")
    try:
        result = run_rag_pipeline(
            query=QUERY,
            candidates=candidates,
            user_access_token=UAT,
            cfg=cfg,
        )
    finally:
        # Always restore original functions
        _pipeline_mod.llm_filter_candidates = _orig_filter
        _pipeline_mod._fetch_all_docs_async = _orig_fetch
        _pipeline_mod.llm_score_and_summarize = _orig_score
        _pipeline_mod.allocate_budget = _orig_budget
        llm_collector.uninstall()

    # ── Stage 4: infer from stage 3 output and stage 5 input ─────────────────
    # Stage 4 has no single function entry point; we infer from tracer records
    s3_trace = next((t for t in tracer._stages if t.stage_num == 3), None)
    s5_trace = next((t for t in tracer._stages if t.stage_num == 5), None)
    if ENABLE_STAGE_TRACING and s3_trace and s5_trace:
        doc_blocks_summary = s3_trace.output_summary.get("docs", [])
        chunks_count = s5_trace.input_summary.get("chunks_count", 0)
        tracer.record(StageTrace(
            stage_num=4,
            stage_name="Build Chunks",
            func_name="build_chunks",
            input_summary={"docs_count": len(doc_blocks_summary), "docs": doc_blocks_summary},
            output_summary={"total_chunks": chunks_count},
            elapsed_seconds=0.0,  # not directly measurable
            success=True,
        ))
        # Re-sort stages by stage_num
        tracer._stages.sort(key=lambda t: t.stage_num)

    # ── Stage 7: assemble result trace ────────────────────────────────────────
    wall_time = time.perf_counter() - wall_t0
    tracer.record(StageTrace(
        stage_num=7,
        stage_name="Assemble Result",
        func_name="RAGResult",
        input_summary={},
        output_summary=_summarize_out_s7(result, wall_time),
        elapsed_seconds=0.0,
        success=True,
    ))

    trace_payload = tracer.finalize(wall_time)
    trace_payload.llm_calls = llm_collector.calls

    # ── Print results ─────────────────────────────────────────────────────────
    if PRINT_FINAL_RESULT:
        print("\n=== Final RAGResult ===")
        print(f"  Query             : {result.query}")
        print(f"  Docs searched     : {result.total_docs_searched}")
        print(f"  Docs read         : {result.total_docs_read}")
        print(f"  Chunks selected   : {result.total_chunks}")
        print(f"  Total tokens      : {result.total_tokens}")
        print(f"  Number of chunks  : {len(result.chunks)}")
        print()
        for i, chunk in enumerate(result.chunks):
            print(f"  --- Chunk {i} ---")
            print(f"    file      : {chunk.file_name}")
            print(f"    heading   : {chunk.heading_path}")
            print(f"    type      : {chunk.content_type}")
            print(f"    score     : {chunk.relevance_score:.2f}")
            print(f"    tokens    : {chunk.token_count}")
            print(f"    summary   : {chunk.summary[:120]!r}")
            preview = chunk.chunks_content[:200].replace("\n", " ")
            print(f"    content   : {preview!r}")
            print()

    # ── Save JSON output ──────────────────────────────────────────────────────
    if SAVE_OUTPUT_JSON:
        output_dir = Path(OUTPUT_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)

        safe_query = QUERY[:40].replace("/", "_").replace(" ", "_")
        output_path = output_dir / f"rag_result_{safe_query}.json"

        def _to_serializable(obj):
            """Recursively convert dataclass / enum to JSON-serializable form."""
            import dataclasses, enum
            if dataclasses.is_dataclass(obj):
                return {k: _to_serializable(v) for k, v in dataclasses.asdict(obj).items()}
            if isinstance(obj, enum.Enum):
                return obj.value
            if isinstance(obj, list):
                return [_to_serializable(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _to_serializable(v) for k, v in obj.items()}
            return obj

        payload = _to_serializable(result)

        # ── Append tracing data if enabled ────────────────────────────────────
        if ENABLE_STAGE_TRACING:
            payload["stage_traces"] = _to_serializable_trace(trace_payload.stages)
            payload["llm_io"] = [
                {
                    "call_index": c.call_index,
                    "caller_tag": c.caller_tag,
                    "prompt": c.prompt,
                    "response": c.response,
                }
                for c in trace_payload.llm_calls
            ]
            payload["trace_summary"] = {
                "wall_time_seconds": round(trace_payload.wall_time_seconds, 3),
                "llm_calls_count": len(trace_payload.llm_calls),
                "llm_calls_by_tag": {
                    tag: sum(1 for c in trace_payload.llm_calls if c.caller_tag == tag)
                    for tag in dict.fromkeys(c.caller_tag for c in trace_payload.llm_calls)
                },
                "stage_elapsed": [
                    {"stage_num": t.stage_num, "stage_name": t.stage_name, "elapsed_seconds": round(t.elapsed_seconds, 3)}
                    for t in trace_payload.stages
                ],
                "all_success": trace_payload.all_success,
            }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        logger.info("Output saved to %s", output_path)
        print(f"\nOutput saved to: {output_path}")


if __name__ == "__main__":
    run_test()
