"""Native-order bounded bucket helpers.

These helpers are intentionally lexical and deterministic: they do not call a
model and never change the order returned by a source API.  Evidence quality is
decided only after preview chunks have been built.
"""
from __future__ import annotations

from typing import Any, List, Mapping, Sequence


def native_candidate_budget(query_type: str) -> int:
    """Source page/read budget; it never represents a relevance threshold."""
    return 6 if query_type == "structured" else 5


def bounded_native_items(items: Sequence[Any], limit: int) -> List[Any]:
    """Deduplicate while retaining the first source-provided occurrence."""
    seen: set[str] = set()
    result: List[Any] = []
    for item in items:
        if isinstance(item, Mapping):
            key = str(item.get("id") or item.get("token") or item.get("url") or repr(item))
        else:
            key = str(getattr(item, "id", "") or repr(item))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= max(0, limit):
            break
    return result
