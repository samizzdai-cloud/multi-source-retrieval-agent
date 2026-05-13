"""阿里云 Qwen3-Rerank 工具封装。

提供三层能力：
1. ``rerank_documents``: 直接调用 DashScope rerank API。
2. ``rerank_search_candidates``: 对 Feishu 搜索候选（title + summary）做重排。
3. ``rerank_chunks``: 对 chunks 做重排并按阈值过滤。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

import requests
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_PROJECT_ROOT / ".env", override=True)

logger = logging.getLogger(__name__)


# ── 配置 ─────────────────────────────────────────────────────────────────────

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")

QWEN3_RERANK_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
STANDARD_RERANK_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"

INSTRUCT_TEMPLATES = {
    "qa": "Given a web search query, retrieve relevant passages that answer the query.",
    "semantic": "Retrieve semantically similar text.",
    "code": "Given a code search query, retrieve relevant code snippets.",
    "classification": "Given a classification query, retrieve relevant documents.",
    "fact_verification": "Given a claim, retrieve documents that support or refute the claim.",
}

MODEL_CONFIGS = {
    "qwen3-rerank": {
        "max_documents": 500,
        "endpoint": QWEN3_RERANK_ENDPOINT,
        "use_simplified_api": True,
    },
    "gte-rerank-v2": {
        "max_documents": 30000,
        "endpoint": STANDARD_RERANK_ENDPOINT,
        "use_simplified_api": False,
    },
    "qwen3-vl-rerank": {
        "max_documents": 100,
        "endpoint": STANDARD_RERANK_ENDPOINT,
        "use_simplified_api": False,
    },
}

DOC_CANDIDATE_SELECTION_CONFIGS: Dict[str, Dict[str, float]] = {
    "structured": {"top_k": 4, "score_threshold": 0.33, "min_keep": 3},
    "concept":    {"top_k": 5, "score_threshold": 0.25, "min_keep": 3},
}

CHUNK_SELECTION_CONFIGS: Dict[str, Dict[str, float]] = {
    "structured": {"score_threshold": 0.30, "min_keep": 4},
    "concept": {"score_threshold": 0.25, "min_keep": 4},
}


def configure_rerank_api_key(api_key: str) -> None:
    """显式注入 DashScope API key，避免依赖 .env。"""
    global DASHSCOPE_API_KEY
    DASHSCOPE_API_KEY = api_key


# ── 基础工具 ─────────────────────────────────────────────────────────────────

DocumentInput = Union[str, Mapping[str, Any]]


def _pick_instruct(
    instruct: Optional[str],
    instruct_template: Optional[str],
) -> str:
    if instruct:
        return instruct
    if instruct_template:
        if instruct_template not in INSTRUCT_TEMPLATES:
            raise ValueError(
                f"未知的 instruct_template: {instruct_template}，"
                f"支持的模板: {list(INSTRUCT_TEMPLATES.keys())}"
            )
        return INSTRUCT_TEMPLATES[instruct_template]
    return INSTRUCT_TEMPLATES["qa"]


def _coerce_document_text(doc: DocumentInput) -> str:
    if isinstance(doc, str):
        return doc
    if isinstance(doc, Mapping):
        text = doc.get("text") or doc.get("content")
        if text:
            return str(text)
        return json.dumps(dict(doc), ensure_ascii=False, sort_keys=True)
    return str(doc)


def _map_results(
    originals: Sequence[Mapping[str, Any]],
    prepared_documents: Sequence[str],
    raw_results: Sequence[Mapping[str, Any]],
    item_key: str,
) -> List[Dict[str, Any]]:
    mapped: List[Dict[str, Any]] = []
    for rank, item in enumerate(raw_results, start=1):
        raw_index = item.get("index")
        if not isinstance(raw_index, int) or raw_index < 0 or raw_index >= len(originals):
            logger.warning("rerank: 跳过非法 index=%r (len=%d)", raw_index, len(originals))
            continue

        mapped_item: Dict[str, Any] = {
            "rank": rank,
            "index": raw_index,
            "original_index": raw_index,
            "relevance_score": float(item.get("relevance_score", 0.0) or 0.0),
            "prepared_document": prepared_documents[raw_index],
            item_key: originals[raw_index],
        }
        if "document" in item:
            mapped_item["document"] = item["document"]
        mapped.append(mapped_item)
    return mapped


def build_focus_sub_query_question(focus_text: str, sub_query: str) -> str:
    """构造 Feishu doc 候选 QA rerank 的问题文本。"""
    return f"Focus: {focus_text.strip()}\nSubQuery: {sub_query.strip()}"


def get_doc_candidate_selection_config(query_type: str) -> Dict[str, float]:
    """按 query_type 返回固定的 doc 候选筛选参数。"""
    normalized = "concept" if query_type == "concept" else "structured"
    return dict(DOC_CANDIDATE_SELECTION_CONFIGS[normalized])


def get_chunk_selection_config(query_type: str) -> Dict[str, float]:
    """按 query_type 返回固定的 chunk 筛选参数。"""
    normalized = "concept" if query_type == "concept" else "structured"
    return dict(CHUNK_SELECTION_CONFIGS[normalized])


# ── 底层 API 调用 ───────────────────────────────────────────────────────────

def rerank_documents(
    query: str,
    documents: Sequence[DocumentInput],
    model: str = "qwen3-rerank",
    top_n: Optional[int] = None,
    instruct: Optional[str] = None,
    instruct_template: Optional[str] = None,
    return_documents: bool = False,
    api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """对文档列表进行重排序。"""
    token = api_key or DASHSCOPE_API_KEY
    if not token:
        return {
            "status": "error",
            "message": "未配置 DASHSCOPE_API_KEY",
            "code": "MissingAPIKey",
        }

    if not documents:
        return {
            "status": "error",
            "message": "documents 列表为空",
            "code": "EmptyDocuments",
        }

    if model not in MODEL_CONFIGS:
        return {
            "status": "error",
            "message": f"不支持的模型: {model}，支持的模型: {list(MODEL_CONFIGS.keys())}",
            "code": "UnsupportedModel",
        }

    cfg = MODEL_CONFIGS[model]
    effective_documents = list(documents[: cfg["max_documents"]])
    if len(effective_documents) < len(documents):
        logger.warning(
            "rerank: 文档数量 %d 超过模型上限 %d，已截断",
            len(documents),
            cfg["max_documents"],
        )

    try:
        final_instruct = _pick_instruct(instruct, instruct_template)
    except ValueError as exc:
        return {
            "status": "error",
            "message": str(exc),
            "code": "InvalidInstructTemplate",
        }

    doc_texts = [_coerce_document_text(doc) for doc in effective_documents]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    if cfg["use_simplified_api"]:
        payload: Dict[str, Any] = {
            "model": model,
            "query": query,
            "documents": doc_texts,
        }
        if top_n is not None:
            payload["top_n"] = top_n
        if final_instruct:
            payload["instruct"] = final_instruct
        if return_documents:
            payload["return_documents"] = True
    else:
        payload = {
            "model": model,
            "input": {
                "query": query,
                "documents": doc_texts,
            },
            "parameters": {},
        }
        if top_n is not None:
            payload["parameters"]["top_n"] = top_n
        if return_documents:
            payload["parameters"]["return_documents"] = True
        if final_instruct:
            payload["parameters"]["instruct"] = final_instruct

    try:
        logger.debug(
            "rerank request: model=%s endpoint=%s docs=%d top_n=%s",
            model, cfg["endpoint"], len(doc_texts), top_n,
        )
        response = requests.post(
            cfg["endpoint"],
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.Timeout:
        return {
            "status": "error",
            "message": f"请求超时（{timeout}s）",
            "code": "RequestTimeout",
        }
    except requests.exceptions.RequestException as exc:
        body = ""
        if getattr(exc, "response", None) is not None:
            body = exc.response.text[:500]
        message = f"请求失败: {exc}"
        if body:
            message = f"{message} | body={body}"
        return {
            "status": "error",
            "message": message,
            "code": "RequestFailed",
        }
    except Exception as exc:
        logger.exception("rerank_documents 未预期异常")
        return {
            "status": "error",
            "message": f"内部错误: {exc}",
            "code": "InternalError",
        }

    logger.debug("rerank raw response: %s", data)

    if data.get("code"):
        return {
            "status": "error",
            "message": data.get("message", "未知错误"),
            "code": data.get("code"),
            "request_id": data.get("request_id") or data.get("id"),
        }

    output = data.get("output") or {}
    results = output.get("results")
    if results is None:
        results = data.get("results") or []
    return {
        "status": "success",
        "results": results,
        "usage": data.get("usage", {}),
        "request_id": data.get("request_id") or data.get("id"),
        "_debug": {
            "model": model,
            "query": query,
            "instruct": final_instruct,
            "endpoint": cfg["endpoint"],
            "input_count": len(doc_texts),
            "output_count": len(results),
            "response_shape": "compat" if "results" in data and "output" not in data else "standard",
        },
    }


# ── Feishu 搜索候选专用 ─────────────────────────────────────────────────────

def build_search_candidate_document(
    candidate: Mapping[str, Any],
    *,
    include_doc_type: bool = True,
    include_source: bool = True,
    max_summary_chars: Optional[int] = 800,
) -> str:
    """把 Feishu 搜索结果组合成适合 rerank 的 doc 文本。"""
    title = str(candidate.get("title", "") or "").strip()
    summary = str(candidate.get("summary", "") or "").strip()
    doc_type = str(candidate.get("doc_type", "") or "").strip()
    source = str(candidate.get("_source") or candidate.get("source") or "").strip()

    if max_summary_chars and len(summary) > max_summary_chars:
        summary = summary[:max_summary_chars].rstrip() + "..."

    parts: List[str] = []
    if title:
        parts.append(f"Title: {title}")
    if summary:
        parts.append(f"Summary: {summary}")
    if include_doc_type and doc_type:
        parts.append(f"Type: {doc_type}")
    if include_source and source:
        parts.append(f"Source: {source}")

    if parts:
        return "\n".join(parts)
    return json.dumps(dict(candidate), ensure_ascii=False, sort_keys=True)


def rerank_search_candidates(
    query: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    model: str = "qwen3-rerank",
    top_n: Optional[int] = None,
    score_threshold: Optional[float] = None,
    instruct: Optional[str] = None,
    instruct_template: str = "qa",
    return_documents: bool = False,
    timeout: float = 30.0,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """重排搜索候选，并保留 ``index -> 原候选`` 映射。"""
    prepared_documents = [
        build_search_candidate_document(candidate)
        for candidate in candidates
    ]
    raw_result = rerank_documents(
        query=query,
        documents=prepared_documents,
        model=model,
        top_n=top_n,
        instruct=instruct,
        instruct_template=instruct_template,
        return_documents=return_documents,
        timeout=timeout,
        api_key=api_key,
    )
    if raw_result["status"] != "success":
        return raw_result

    mapped_results = _map_results(candidates, prepared_documents, raw_result["results"], "candidate")
    if score_threshold is not None:
        mapped_results = [item for item in mapped_results if item["relevance_score"] >= score_threshold]

    return {
        **raw_result,
        "results": mapped_results,
        "selected_candidates": [item["candidate"] for item in mapped_results],
        "_debug": {
            **raw_result.get("_debug", {}),
            "score_threshold": score_threshold,
            "prepared_documents": prepared_documents,
        },
    }


def select_search_candidates(
    query: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    model: str = "qwen3-rerank",
    top_n: int,
    score_threshold: float,
    min_keep: int,
    instruct: Optional[str] = None,
    instruct_template: str = "qa",
    timeout: float = 30.0,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """搜索候选筛选：先 rerank，再按阈值过滤并保底补齐。"""
    result = rerank_search_candidates(
        query=query,
        candidates=candidates,
        model=model,
        top_n=top_n,
        instruct=instruct,
        instruct_template=instruct_template,
        return_documents=False,
        timeout=timeout,
        api_key=api_key,
    )
    if result["status"] != "success":
        return result

    reranked_results = list(result["results"])
    selected = [item for item in reranked_results if item["relevance_score"] >= score_threshold]
    if len(selected) < min_keep:
        chosen_indexes = {item["index"] for item in selected}
        for item in reranked_results:
            if item["index"] in chosen_indexes:
                continue
            selected.append(item)
            chosen_indexes.add(item["index"])
            if len(selected) >= min_keep:
                break

    selected = selected[:top_n]
    return {
        **result,
        "results": reranked_results,
        "selected_results": selected,
        "selected_candidates": [item["candidate"] for item in selected],
        "_debug": {
            **result.get("_debug", {}),
            "score_threshold": score_threshold,
            "min_keep": min_keep,
            "selected_count": len(selected),
        },
    }


# ── Chunk 专用 ──────────────────────────────────────────────────────────────

def build_chunk_document(
    chunk: Mapping[str, Any],
    *,
    text_field: str = "text",
    include_source_name: bool = True,
    include_heading_path: bool = True,
    max_text_chars: Optional[int] = None,
) -> str:
    """把 chunk 组合成适合 rerank 的 doc 文本。"""
    source_name = str(chunk.get("source_name", "") or chunk.get("file_name", "") or "").strip()
    heading_path = str(chunk.get("heading_path", "") or "").strip()
    content = str(chunk.get(text_field, "") or chunk.get("content", "") or "").strip()

    if max_text_chars and len(content) > max_text_chars:
        content = content[:max_text_chars].rstrip() + "..."

    parts: List[str] = []
    if include_source_name and source_name:
        parts.append(f"Source: {source_name}")
    if include_heading_path and heading_path:
        parts.append(f"Heading: {heading_path}")
    if content:
        parts.append(f"Content: {content}")

    if parts:
        return "\n".join(parts)
    return json.dumps(dict(chunk), ensure_ascii=False, sort_keys=True)


def rerank_chunks(
    query: str,
    chunks: Sequence[Mapping[str, Any]],
    *,
    model: str = "qwen3-rerank",
    top_n: Optional[int] = None,
    score_threshold: Optional[float] = 0.3,
    text_field: str = "text",
    max_text_chars: Optional[int] = None,
    instruct: Optional[str] = None,
    instruct_template: str = "qa",
    return_documents: bool = False,
    timeout: float = 30.0,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """重排 chunks，并输出过滤后的 chunk 列表。"""
    prepared_documents = [
        build_chunk_document(
            chunk,
            text_field=text_field,
            max_text_chars=max_text_chars,
        )
        for chunk in chunks
    ]
    raw_result = rerank_documents(
        query=query,
        documents=prepared_documents,
        model=model,
        top_n=top_n,
        instruct=instruct,
        instruct_template=instruct_template,
        return_documents=return_documents,
        timeout=timeout,
        api_key=api_key,
    )
    if raw_result["status"] != "success":
        return raw_result

    mapped_results = _map_results(chunks, prepared_documents, raw_result["results"], "chunk")
    filtered_chunks: List[Dict[str, Any]] = []
    dropped_count = 0

    for item in mapped_results:
        score = item["relevance_score"]
        if score_threshold is not None and score < score_threshold:
            dropped_count += 1
            continue

        chunk_copy = dict(item["chunk"])
        chunk_copy["rerank_score"] = score
        chunk_copy["rerank_index"] = item["index"]
        filtered_chunks.append(chunk_copy)

    return {
        **raw_result,
        "results": mapped_results,
        "filtered_chunks": filtered_chunks,
        "selected_chunks": filtered_chunks,
        "_debug": {
            **raw_result.get("_debug", {}),
            "score_threshold": score_threshold,
            "dropped_count": dropped_count,
            "prepared_documents": prepared_documents,
        },
    }


def select_reranked_chunks(
    query: str,
    chunks: Sequence[Mapping[str, Any]],
    *,
    model: str = "qwen3-rerank",
    top_n: int,
    score_threshold: float,
    min_keep: int,
    text_field: str = "text",
    max_text_chars: Optional[int] = 1200,
    instruct: Optional[str] = None,
    instruct_template: str = "qa",
    timeout: float = 30.0,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """chunk 筛选：先 rerank，再按阈值过滤并保底补齐。"""
    result = rerank_chunks(
        query=query,
        chunks=chunks,
        model=model,
        top_n=top_n,
        score_threshold=None,
        text_field=text_field,
        max_text_chars=max_text_chars,
        instruct=instruct,
        instruct_template=instruct_template,
        return_documents=False,
        timeout=timeout,
        api_key=api_key,
    )
    if result["status"] != "success":
        return result

    reranked_results = list(result["results"])
    selected = [item for item in reranked_results if item["relevance_score"] >= score_threshold]
    if len(selected) < min_keep:
        chosen_indexes = {item["index"] for item in selected}
        for item in reranked_results:
            if item["index"] in chosen_indexes:
                continue
            selected.append(item)
            chosen_indexes.add(item["index"])
            if len(selected) >= min_keep:
                break

    selected = selected[:top_n]
    return {
        **result,
        "results": reranked_results,
        "selected_results": selected,
        "selected_chunks": [item["chunk"] for item in selected],
        "_debug": {
            **result.get("_debug", {}),
            "score_threshold": score_threshold,
            "min_keep": min_keep,
            "selected_count": len(selected),
            "max_text_chars": max_text_chars,
        },
    }


# ── 兼容便捷函数 ────────────────────────────────────────────────────────────

def rerank_for_rag(
    query: str,
    chunks: List[Dict[str, Any]],
    top_n: int = 5,
    score_threshold: float = 0.3,
    text_field: str = "text",
) -> List[Dict[str, Any]]:
    """RAG 场景便捷函数。"""
    if not chunks:
        return []

    result = rerank_chunks(
        query=query,
        chunks=chunks,
        top_n=top_n,
        score_threshold=score_threshold,
        text_field=text_field,
        instruct_template="qa",
        return_documents=False,
    )
    if result["status"] != "success":
        logger.warning("rerank_for_rag 失败: %s", result.get("message"))
        return chunks[:top_n]
    return result["filtered_chunks"]


def rerank_for_faq(
    user_question: str,
    faq_questions: List[str],
    top_n: int = 3,
) -> List[Dict[str, Any]]:
    """FAQ 场景便捷函数。"""
    result = rerank_documents(
        query=user_question,
        documents=faq_questions,
        model="qwen3-rerank",
        top_n=top_n,
        instruct_template="semantic",
        return_documents=True,
    )
    if result["status"] != "success":
        logger.warning("rerank_for_faq 失败: %s", result.get("message"))
        return []

    matches = []
    for item in result["results"]:
        matches.append({
            "question": item.get("document", {}).get("text", faq_questions[item["index"]]),
            "score": item.get("relevance_score", 0.0),
            "index": item.get("index"),
        })
    return matches
