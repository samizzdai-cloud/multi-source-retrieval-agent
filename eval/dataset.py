"""Eval dataset loader — load, validate, filter, group eval queries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEFAULT_PATH = Path(__file__).parent / "eval_queries.example.json"


def load_queries(
    path: Path | str | None = None,
    ids: Optional[List[int]] = None,
    sample: Optional[int] = None,
    difficulty: Optional[str] = None,
    query_type: Optional[str] = None,
    project: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Load and filter eval queries.

    Args:
        path: JSON file path (defaults to eval/eval_queries.json)
        ids: only include these query IDs
        sample: random sample of N queries (after other filters)
        difficulty: filter by "easy" / "medium" / "hard"
        query_type: filter by "data" / "concept" / "mixed"
        project: filter by project name
    """
    p = Path(path) if path else _DEFAULT_PATH
    raw = json.loads(p.read_text())
    queries: List[Dict[str, Any]] = raw.get("queries", raw) if isinstance(raw, dict) else raw

    if ids:
        id_set = set(ids)
        queries = [q for q in queries if q.get("id") in id_set]

    if difficulty:
        queries = [q for q in queries if q.get("difficulty") == difficulty]

    if query_type:
        queries = [q for q in queries if q.get("query_type_expected") == query_type]

    if project:
        queries = [q for q in queries if q.get("project") == project]

    if sample and sample < len(queries):
        import random
        queries = random.sample(queries, sample)

    return queries


def validate_queries(queries: List[Dict[str, Any]]) -> List[str]:
    """Return list of validation warnings (empty = all good)."""
    warnings = []
    seen_ids = set()
    for q in queries:
        qid = q.get("id")
        if qid is None:
            warnings.append(f"Query missing 'id': {q.get('query', '')[:50]}")
        elif qid in seen_ids:
            warnings.append(f"Duplicate id: {qid}")
        else:
            seen_ids.add(qid)

        if not q.get("query"):
            warnings.append(f"Query id={qid} has empty 'query' field")

        if not q.get("query_type_expected"):
            warnings.append(f"Query id={qid} missing 'query_type_expected'")

        if not q.get("difficulty"):
            warnings.append(f"Query id={qid} missing 'difficulty'")

    return warnings


def group_by(queries: List[Dict[str, Any]], key: str) -> Dict[str, List[Dict[str, Any]]]:
    """Group queries by a field value (e.g. 'difficulty', 'project')."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for q in queries:
        val = str(q.get(key, "unknown"))
        groups.setdefault(val, []).append(q)
    return groups
