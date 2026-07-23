"""HTTP client and coverage-safe selection for post-I/O evidence reranking.

This module ranks read chunks, never source-search candidates.  It cannot
change an upstream source API's recall or I/O decisions.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


DEFAULT_MODEL = "qwen3-rerank"
DEFAULT_INSTRUCT = (
    "Rank each passage by how useful it is as evidence for answering the user "
    "focus. Prefer direct support for requested entities, time periods, metrics, "
    "decisions, and necessary complementary facts. Do not use topical similarity "
    "alone; preserve complementary evidence needed for a multi-passage answer."
)


class RerankError(RuntimeError):
    """A recoverable provider or configuration failure."""


class RerankWallTimeout(RerankError):
    """The caller's wall deadline elapsed while sync HTTP may still drain."""


@dataclass(frozen=True)
class RerankSettings:
    api_key: str
    workspace_id: str
    model: str = DEFAULT_MODEL
    timeout_s: float = 20.0
    instruct: str = DEFAULT_INSTRUCT
    endpoint: str = ""

    @property
    def url(self) -> str:
        if self.endpoint:
            return self.endpoint.rstrip("/")
        return f"https://{self.workspace_id}.cn-beijing.maas.aliyuncs.com/compatible-api/v1/reranks"

    @classmethod
    def from_env(cls) -> "RerankSettings":
        if load_dotenv is not None:
            load_dotenv(override=False)
        api_key = _first_env("RERANK_API_KEY", "DASHSCOPE_API_KEY", "BAILIAN_API_KEY")
        workspace_id = _first_env("RERANK_WORKSPACE_ID", "DASHSCOPE_WORKSPACE_ID", "BAILIAN_WORKSPACE_ID")
        if not api_key or not workspace_id:
            raise RerankError("qwen rerank needs BAILIAN_API_KEY and BAILIAN_WORKSPACE_ID")
        return cls(
            api_key=api_key,
            workspace_id=workspace_id,
            model=_first_env("RERANK_MODEL") or DEFAULT_MODEL,
            timeout_s=_env_float("RERANK_TIMEOUT_S", 20.0),
            instruct=_first_env("RERANK_INSTRUCT") or DEFAULT_INSTRUCT,
            endpoint=_first_env("RERANK_ENDPOINT"),
        )


@dataclass(frozen=True)
class RerankResult:
    index: int
    relevance_score: float
    text: str = ""


@dataclass(frozen=True)
class RankedItem:
    """Scores are comparable only within ``batch_index``."""
    item_id: str
    relevance_score: float
    batch_index: int
    rank: int


Transport = Callable[[str, Mapping[str, str], Mapping[str, Any], float], Mapping[str, Any]]


def rerank_documents(query: str, documents: Sequence[str], *, settings: Optional[RerankSettings] = None,
                     top_n: Optional[int] = None, transport: Optional[Transport] = None) -> list[RerankResult]:
    """Call Qwen compatible HTTP and normalize both known response shapes."""
    if not query.strip() or not documents:
        return []
    if len(documents) > 500:
        raise RerankError("one qwen3-rerank request accepts at most 500 documents")
    settings = settings or RerankSettings.from_env()
    payload: dict[str, Any] = {"model": settings.model, "query": query, "documents": list(documents),
                               "instruct": settings.instruct, "return_documents": True}
    if top_n is not None:
        payload["top_n"] = max(1, min(int(top_n), len(documents)))
    sender = transport or _http_post_json
    try:
        response = sender(settings.url, {"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"},
                          payload, settings.timeout_s)
    except RerankError:
        raise
    except Exception as exc:
        raise RerankError(f"qwen rerank request failed: {type(exc).__name__}") from exc
    return _parse_results(response)


def rerank_items(query: str, items: Sequence[tuple[str, str]], *, settings: Optional[RerankSettings] = None,
                 batch_size: int = 500, transport: Optional[Transport] = None) -> list[RankedItem]:
    if batch_size < 1 or batch_size > 500:
        raise ValueError("batch_size must be in 1..500")
    ranked: list[RankedItem] = []
    for batch_index, start in enumerate(range(0, len(items), batch_size)):
        batch = list(items[start:start + batch_size])
        results = rerank_documents(query, [text for _, text in batch], settings=settings, transport=transport)
        for rank, result in enumerate(results):
            if 0 <= result.index < len(batch):
                ranked.append(RankedItem(batch[result.index][0], result.relevance_score, batch_index, rank))
    return ranked


def select_ranked_ids(ranked: Sequence[RankedItem], *, keep_ratio: float,
                      score_threshold: Optional[float] = None, minimum_keep: int = 1) -> set[str]:
    """Select per provider batch; threshold is optional because scores are relative."""
    if not 0 < keep_ratio <= 1:
        raise ValueError("keep_ratio must be in (0, 1]")
    by_batch: dict[int, list[RankedItem]] = {}
    for item in ranked:
        by_batch.setdefault(item.batch_index, []).append(item)
    selected: set[str] = set()
    for batch in by_batch.values():
        ordered = sorted(batch, key=lambda x: (x.rank, -x.relevance_score, x.item_id))
        eligible = [x for x in ordered if score_threshold is None or x.relevance_score >= score_threshold]
        selected.update(x.item_id for x in eligible[:max(minimum_keep, math.ceil(len(ordered) * keep_ratio))])
    return selected


def _parse_results(response: Mapping[str, Any]) -> list[RerankResult]:
    raw = response.get("results")
    if raw is None and isinstance(response.get("output"), Mapping):
        raw = response["output"].get("results")
    if not isinstance(raw, list):
        raise RerankError("qwen rerank response has no results list")
    results: list[RerankResult] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        try:
            index, score = int(item["index"]), float(item["relevance_score"])
        except (KeyError, TypeError, ValueError):
            continue
        document = item.get("document")
        results.append(RerankResult(index, score, str(document.get("text", "") if isinstance(document, Mapping) else "")))
    if not results:
        raise RerankError("qwen rerank response contained no valid result")
    return results


def _http_post_json(url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout_s: float) -> Mapping[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RerankError(f"qwen rerank HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RerankError(f"qwen rerank transport failure: {type(exc).__name__}") from exc
    if not isinstance(body, Mapping):
        raise RerankError("qwen rerank response is not an object")
    return body


def _first_env(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return ""


def _env_float(name: str, default: float) -> float:
    try:
        return float(_first_env(name) or default)
    except ValueError:
        return default
