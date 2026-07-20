"""Per-parent metadata rerank for Sheet/Bitable child selection.

This is a pre-read I/O boundary, not the post-I/O evidence reranker.  Scores
are comparable only among children of the same parent and are never treated as
evidence confidence.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Optional, Sequence

from tools.core.rerank import RerankSettings, rerank_documents


def select_table_children(
    query: str,
    children: Sequence[Mapping[str, Any]],
    *,
    top_n: int = 3,
    enabled: bool = True,
    model: str = "qwen3-rerank",
    timeout_s: float = 2.0,
    instruct: str,
    transport: Optional[Callable] = None,
) -> tuple[list[dict], dict]:
    """Return all children when small, otherwise rerank one parent to top N.

    Provider/config failures fail open to the first N children in stable API
    order.  The metadata result is suitable for operational trace.
    """
    items = [dict(item) for item in children]
    cap = max(1, int(top_n))
    if len(items) <= cap:
        return items, {
            "mode": "all_children", "input_count": len(items),
            "selected_count": len(items), "provider_called": False,
        }
    if not enabled:
        return items[:cap], {
            "mode": "disabled_api_order_fallback", "input_count": len(items),
            "selected_count": cap, "provider_called": False,
        }

    documents = []
    for item in items:
        documents.append(json.dumps({
            "child_title": str(
                item.get("_child_title") or item.get("title") or item.get("name") or ""
            )[:300],
            "summary": str(item.get("summary") or "")[:500],
            "row_count": item.get("row_count"),
            "column_count": item.get("column_count"),
        }, ensure_ascii=False, separators=(",", ":")))

    try:
        base = RerankSettings.from_env()
        settings = RerankSettings(
            api_key=base.api_key,
            workspace_id=base.workspace_id,
            model=model or base.model,
            timeout_s=max(0.2, float(timeout_s)),
            instruct=instruct,
            endpoint=base.endpoint,
        )
        results = rerank_documents(
            query, documents, settings=settings, top_n=cap, transport=transport,
        )
        ordered = sorted(results, key=lambda result: result.relevance_score, reverse=True)
        selected_indexes = []
        scores = []
        for result in ordered:
            if 0 <= result.index < len(items) and result.index not in selected_indexes:
                selected_indexes.append(result.index)
                scores.append(float(result.relevance_score))
            if len(selected_indexes) >= cap:
                break
        for index in range(len(items)):
            if len(selected_indexes) >= cap:
                break
            if index not in selected_indexes:
                selected_indexes.append(index)
        return [items[index] for index in selected_indexes], {
            "mode": "qwen_parent_rerank",
            "input_count": len(items),
            "selected_count": len(selected_indexes),
            "provider_called": True,
            "selected_indexes": selected_indexes,
            "scores": scores,
        }
    except Exception as exc:
        return items[:cap], {
            "mode": "rerank_error_api_order_fallback",
            "input_count": len(items),
            "selected_count": cap,
            "provider_called": True,
            "error_type": type(exc).__name__,
            "error": str(exc)[:160],
        }
