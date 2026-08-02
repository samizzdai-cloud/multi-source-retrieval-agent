"""Token-aware batching for source evaluation contexts.

The estimator is deliberately conservative and provider-neutral: it budgets
serialized prompt text, not only chunk bodies.  It is a guardrail, not a
replacement for API usage accounting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from tools.core.models import ChunkEntry


# These limits define the actual source-evaluation prompt surface. The batcher
# must budget this rendered preview, not the full caller payload.
DOCUMENT_PROMPT_CHARS = 300
CONCEPT_PROMPT_CHARS = 1_200  # diagnosis/analysis chunks need more context
TABLE_PROMPT_CHARS = 2_000
_TABLE_MARKERS = ("| --- |", "| --- ", "Bitable:", "Sheet:", "Excel:")


def prompt_preview_text(chunk: "ChunkEntry") -> str:
    """Return the body window exposed to source-evaluation prompts.

    Concept/diagnostic query chunks receive a larger window (1200 chars)
    because their key conclusions often sit beyond the 300-char document
    default.  Table chunks already get 2000 chars via markdown detection.
    """
    text = (chunk.preview_content or chunk.content or "").replace("\n", " ").strip()
    if any(marker in (chunk.content or "") for marker in _TABLE_MARKERS):
        limit = TABLE_PROMPT_CHARS
    else:
        query_types = set((chunk.metadata or {}).get("selected_query_types") or [])
        limit = CONCEPT_PROMPT_CHARS if "concept" in query_types else DOCUMENT_PROMPT_CHARS
    return text if len(text) <= limit else text[:limit] + "..."


@dataclass(frozen=True)
class ContextBudget:
    context_limit: int = 1_000_000
    response_reserve: int = 20_000
    prompt_reserve: int = 30_000
    flush_threshold: int = 600_000
    hard_ceiling: int = 650_000
    # Partial eval emits only a compact evidence shortlist.  Omitted chunks are
    # observable as not_shortlisted (not mislabeled as semantic noise).
    max_chunks_per_batch: int = 80

    @property
    def usable_tokens(self) -> int:
        return min(self.hard_ceiling, self.context_limit - self.response_reserve - self.prompt_reserve)


def estimate_tokens(text: str) -> int:
    """Conservative mixed Chinese/English approximation (never an exact API count)."""
    # CJK generally costs close to one token/character; Latin prose costs less.
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + max(1, (len(text) - cjk + 2) // 3)


def estimate_chunk_tokens(chunk: ChunkEntry) -> int:
    meta = chunk.metadata or {}
    wrapper = "\n".join((chunk.id, str(meta.get("source_name", "")), str(meta.get("heading_path", ""))))
    return estimate_tokens(wrapper + "\n" + prompt_preview_text(chunk)) + 24


def split_chunks(chunks: Sequence[ChunkEntry], budget: ContextBudget) -> List[List[ChunkEntry]]:
    """Stable batches; no semantic filtering and no chunk loss."""
    batches: List[List[ChunkEntry]] = []
    current: List[ChunkEntry] = []
    current_tokens = budget.prompt_reserve
    for chunk in chunks:
        cost = estimate_chunk_tokens(chunk)
        if current and (
            current_tokens + cost > budget.flush_threshold
            or len(current) >= budget.max_chunks_per_batch
        ):
            batches.append(current)
            current, current_tokens = [], budget.prompt_reserve
        # A pathological single chunk is kept intact; readers must not silently
        # truncate source evidence.  It gets its own request and is observable.
        current.append(chunk)
        current_tokens += cost
    if current:
        batches.append(current)
    return batches
