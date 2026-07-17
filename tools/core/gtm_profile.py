"""Bounded, shared GTM retrieval profile.

Profile data is a query-rewrite candidate only.  It never supplies evidence or
causes a fallback unless the primary API search produced zero candidates.
"""
from __future__ import annotations

import json
import re
import sqlite3
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence

DEFAULT_SCOPE = "department:GTM"
_TRACE_EVENT_FIELDS = frozenset({
    "event", "source", "chunk_id", "focus", "focuses", "sub_query_id", "query_id", "query_type",
    "api_rank", "document", "document_id", "heading", "range", "batch_id",
    "content_sha256", "content_chars", "timeout_s", "outcome", "tier", "fallback", "fallback_origin", "fallback_kind",
    "candidate_count", "primary_candidate_count", "fallback_candidate_count", "replan",
})
_SKU = re.compile(r"\bsku\b|\b[a-z]{1,8}[-_]?(?:\d{3,}|\d{2}[a-z]\d*)\b", re.I)
_GENERIC = {"data", "report", "marketing", "campaign", "sales", "region", "market", "analysis"}


def normalize(value: str) -> str:
    """Case-insensitive lexical form; case-only variants never become data."""
    return " ".join(re.findall(r"[\w.-]+", (value or "").casefold(), flags=re.UNICODE))


@dataclass(frozen=True)
class MemoryPack:
    entities: List[dict]
    expressions: List[dict]

    def prompt_block(self) -> str:
        if not self.entities and not self.expressions:
            return ""
        return "GTM profile query candidates only; not evidence or facts: " + json.dumps(
            {"entities": self.entities, "expression_sets": self.expressions}, ensure_ascii=False,
        )

    def fallback_variants(self, original_query: str) -> tuple[List[str], List[str]]:
        """Return at most one alias and one expression replacement.

        Never append a loosely related term: Feishu token matching treats that
        as an additional mandatory keyword.  A fallback may replace only a
        matched entity/expression surface form, leaving the rest of the user
        query (its retrieval anchors) unchanged.
        """
        aliases, expressions = [], []
        for entity in self.entities:
            for alias in entity.get("aliases", []):
                entity_forms = [entity.get("name", "")] + list(entity.get("aliases", []))
                matched = next((form for form in entity_forms if normalize(form) in normalize(original_query)), "")
                if matched and normalize(alias) and normalize(alias) != normalize(matched):
                    aliases.append(re.sub(re.escape(str(matched)), str(alias), original_query, count=1, flags=re.I))
                    break
        for expression in self.expressions:
            for variant in expression.get("variants", []):
                forms = [expression.get("name", "")] + list(expression.get("variants", []))
                matched = next((form for form in forms if normalize(form) in normalize(original_query)), "")
                if matched and normalize(variant) and normalize(variant) != normalize(matched):
                    expressions.append(re.sub(re.escape(str(matched)), str(variant), original_query, count=1, flags=re.I))
                    break
        return aliases[:1], expressions[:1]


class GTMProfileRepository:
    def __init__(self, path: str | Path = "data/gtm_profile.sqlite", scope: str = DEFAULT_SCOPE):
        self.path, self.scope = Path(path), scope
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS entity(id INTEGER PRIMARY KEY, scope TEXT NOT NULL, name TEXT NOT NULL,
                kind TEXT NOT NULL, normalized TEXT NOT NULL, UNIQUE(scope, normalized));
              CREATE TABLE IF NOT EXISTS entity_alias(id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL,
                alias TEXT NOT NULL, normalized TEXT NOT NULL, UNIQUE(entity_id, normalized));
              CREATE TABLE IF NOT EXISTS expression_set(id INTEGER PRIMARY KEY, scope TEXT NOT NULL, name TEXT NOT NULL,
                normalized TEXT NOT NULL, variants TEXT NOT NULL DEFAULT '', UNIQUE(scope, normalized));
              CREATE VIRTUAL TABLE IF NOT EXISTS expression_fts USING fts5(scope UNINDEXED, name, variants);
              CREATE TABLE IF NOT EXISTS profile_stats(item_type TEXT NOT NULL, item_id INTEGER NOT NULL,
                attempt_count INTEGER DEFAULT 0, candidate_hit_count INTEGER DEFAULT 0,
                evidence_hit_count INTEGER DEFAULT 0, failed_attempt_count INTEGER DEFAULT 0,
                PRIMARY KEY(item_type,item_id));
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def build_pack(self, query: str) -> MemoryPack:
        q = normalize(query)
        if not q:
            return MemoryPack([], [])
        with self._connect() as db:
            entities = []
            for eid, name, kind in db.execute("SELECT id,name,kind FROM entity WHERE scope=?", (self.scope,)):
                aliases = [row[0] for row in db.execute("SELECT alias FROM entity_alias WHERE entity_id=?", (eid,))]
                if normalize(name) in q or any(normalize(alias) in q for alias in aliases):
                    entities.append({"name": name, "kind": kind, "aliases": aliases[:2]})
            expressions = []
            # FTS narrows candidates, then exact lexical checks prevent broad semantic expansion.
            # FTS5's query grammar treats punctuation (for example ``5.20``)
            # as syntax. It is an optional narrowing optimization only: a
            # malformed user query must never prevent retrieval planning.
            fts_terms = [token for token in re.findall(r"[^\W_]+", q, flags=re.UNICODE) if len(token) > 1]
            fts_query = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in fts_terms)
            if fts_query:
                try:
                    rows = db.execute(
                        "SELECT name,variants FROM expression_fts WHERE expression_fts MATCH ? AND scope=?",
                        (fts_query, self.scope),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = db.execute("SELECT name,variants FROM expression_set WHERE scope=?", (self.scope,)).fetchall()
            else:
                rows = db.execute("SELECT name,variants FROM expression_set WHERE scope=?", (self.scope,)).fetchall()
            for name, raw_variants in rows:
                variants = [v for v in raw_variants.split("|") if v]
                if normalize(name) in q or any(normalize(v) in q for v in variants):
                    expressions.append({"name": name, "variants": variants[:2]})
        return MemoryPack(entities[:3], expressions[:3])

    def validate_patch(self, patch: Mapping[str, object], *, trace_supported: bool = False) -> bool:
        action, typ = str(patch.get("action", "")), str(patch.get("type", ""))
        value = str(patch.get("name") or patch.get("alias") or "").strip()
        norm = normalize(value)
        if not trace_supported or action not in {"add_entity", "add_alias", "add_expression", "update_expression", "delete"}:
            return False
        if action == "delete":
            return bool(patch.get("id"))
        if typ not in {"entity", "alias", "expression"} or len(value) < 2 or len(value) > 80:
            return False
        if _SKU.search(value) or norm in _GENERIC or not norm:
            return False
        return True

    def apply_patch(self, patch: Mapping[str, object], *, trace_supported: bool = False) -> bool:
        if not self.validate_patch(patch, trace_supported=trace_supported):
            return False
        action, typ = str(patch["action"]), str(patch["type"])
        with self._connect() as db:
            if action == "add_entity" and typ == "entity":
                db.execute("INSERT OR IGNORE INTO entity(scope,name,kind,normalized) VALUES(?,?,?,?)",
                           (self.scope, str(patch["name"]), str(patch.get("kind", "other")), normalize(str(patch["name"]))))
            elif action == "add_alias" and typ == "alias":
                entity_id = int(patch.get("entity_id", 0) or 0)
                if not entity_id or db.execute("SELECT 1 FROM entity_alias WHERE normalized=?", (normalize(str(patch["alias"])),)).fetchone():
                    return False
                db.execute("INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized) VALUES(?,?,?)",
                           (entity_id, str(patch["alias"]), normalize(str(patch["alias"]))))
            elif action in {"add_expression", "update_expression"} and typ == "expression":
                name, variants = str(patch["name"]), str(patch.get("variants", ""))
                db.execute("INSERT OR REPLACE INTO expression_set(scope,name,normalized,variants) VALUES(?,?,?,?)",
                           (self.scope, name, normalize(name), variants))
                db.execute("DELETE FROM expression_fts WHERE scope=? AND name=?", (self.scope, name))
                db.execute("INSERT INTO expression_fts(scope,name,variants) VALUES(?,?,?)", (self.scope, name, variants))
            elif action == "delete":
                table = {"entity": "entity", "expression": "expression_set"}.get(typ)
                if not table:
                    return False
                db.execute(f"DELETE FROM {table} WHERE id=? AND scope=?", (int(patch["id"]), self.scope))
            else:
                return False
        return True


@dataclass(frozen=True)
class RetrievalDelta:
    focus: str
    source: str
    query_type: str
    changed_kind: str
    first_candidate_hits: int
    final_evidence_hits: int
    fallback_candidate_hits: int = 0
    rank_before: int | None = None
    rank_after: int | None = None

    def qualifies(self) -> bool:
        return (
            self.changed_kind in {"alias", "expression"}
            and self.first_candidate_hits == 0
            and self.final_evidence_hits > 0
        ) or (
            self.changed_kind in {"alias", "expression"}
            and self.final_evidence_hits > 0
            and self.rank_before is not None and self.rank_after is not None
            and self.rank_before - self.rank_after >= 2
        )


@dataclass(frozen=True)
class RetrievalTrace:
    """Compact asynchronous-governance input; intentionally excludes body text."""
    user_query: str
    focus: str
    source: str
    query_type: str
    api_query: str
    candidates: tuple[dict, ...] = ()
    reads: tuple[dict, ...] = ()
    judgments: tuple[dict, ...] = ()
    fallback: str = ""
    replan: str = ""

    def compact(self) -> dict:
        return {
            "user_query": self.user_query,
            "focus": self.focus,
            "source": self.source,
            "query_type": self.query_type,
            "api_query": self.api_query,
            "candidates": list(self.candidates),
            "reads": list(self.reads),
            "judgments": list(self.judgments),
            "fallback": self.fallback,
            "replan": self.replan,
        }


def auto_write_allowed(deltas: Sequence[RetrievalDelta], *, holdout_candidate_delta: float,
                       holdout_evidence_delta: float, false_expansions: int) -> bool:
    successes = [d for d in deltas if d.qualifies()]
    distinct = {(d.focus, d.source, d.query_type) for d in successes}
    hit_rate = len(successes) / len(deltas) if deltas else 0.0
    return (len(distinct) >= 3 and hit_rate >= 0.60 and holdout_candidate_delta >= 0
            and holdout_evidence_delta >= 0 and false_expansions == 0)


def extract_profile_deltas(report_records: Iterable[Mapping[str, object]]) -> list[RetrievalDelta]:
    """Convert persisted Profile-fallback observations into candidate Deltas.

    This is deliberately an offline, body-free extraction boundary.  A fallback
    candidate is *not* evidence: `final_evidence_hits` remains zero until a
    separate holdout/evaluation join supplies verified retained-evidence data.
    Therefore this function cannot, by itself, make a Profile write eligible.
    """
    deltas: list[RetrievalDelta] = []
    for record in report_records:
        report = record.get("report", record)
        if not isinstance(report, Mapping):
            continue
        events = report.get("events", [])
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, Mapping) or event.get("event") != "profile_fallback":
                continue
            kind = str(event.get("fallback_kind") or "")
            candidate_hits = int(event.get("fallback_candidate_count") or 0)
            if kind not in {"alias", "expression"} or candidate_hits <= 0:
                continue
            deltas.append(RetrievalDelta(
                focus=str(event.get("focus") or ""),
                source=str(event.get("source") or ""),
                query_type=str(event.get("query_type") or "unknown"),
                changed_kind=kind,
                first_candidate_hits=int(event.get("primary_candidate_count") or 0),
                fallback_candidate_hits=candidate_hits,
                final_evidence_hits=0,
            ))
    return deltas


def build_shadow_report(results: Iterable[object]) -> dict:
    """Aggregate and redact compact source traces without profile writes.

    Source traces may evolve, so this boundary intentionally allowlists routing
    and outcome fields. Document/chunk body fields can never enter governance.
    """
    events: list[dict] = []
    for result in results:
        for event in getattr(result, "retrieval_trace", []) or []:
            if not isinstance(event, Mapping):
                continue
            compact = {key: value for key, value in event.items() if key in _TRACE_EVENT_FIELDS}
            if compact.get("event") in {"query", "candidate", "read", "judgment", "timeout", "profile_fallback"}:
                events.append(compact)
    counts = {kind: sum(1 for event in events if event.get("event") == kind)
              for kind in ("query", "candidate", "read", "judgment", "timeout", "profile_fallback")}
    return {"mode": "shadow", "event_counts": counts, "events": events}


class RetrievalGovernanceStore:
    """Durable, body-free audit store kept separate from approved Profile data."""

    def __init__(self, path: str | Path = "data/retrieval_governance.sqlite") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS shadow_report("
                "id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, report_json TEXT NOT NULL)"
            )

    def append(self, report: Mapping[str, object]) -> None:
        payload = json.dumps(dict(report), ensure_ascii=False, separators=(",", ":"))
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO shadow_report(report_json) VALUES(?)", (payload,))

    def count(self) -> int:
        with sqlite3.connect(self.path) as db:
            return int(db.execute("SELECT COUNT(*) FROM shadow_report").fetchone()[0])

    def iter_reports(self, *, after_id: int = 0, limit: int = 1000) -> list[dict]:
        """Read bounded audit records for offline Delta extraction only."""
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT id, created_at, report_json FROM shadow_report WHERE id>? ORDER BY id LIMIT ?",
                (max(0, int(after_id)), max(1, int(limit))),
            ).fetchall()
        reports: list[dict] = []
        for report_id, created_at, payload in rows:
            try:
                report = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(report, dict):
                reports.append({"report_id": int(report_id), "created_at": created_at, "report": report})
        return reports


class ProfileGovernanceAgent:
    """Non-blocking, trace-only profile governance boundary.

    The agent never reads a source, never sees document bodies, and defaults to
    shadow mode.  A caller must explicitly supply a holdout decision before a
    callback is allowed to persist a validated patch.
    """

    def __init__(self, repository: GTMProfileRepository | None = None,
                 store: RetrievalGovernanceStore | None = None, *, max_reports: int = 100) -> None:
        self.repository = repository or GTMProfileRepository()
        self.store = store or RetrievalGovernanceStore()
        self.reports: list[dict] = []
        self.max_reports = max(1, max_reports)

    async def observe(self, results: Iterable[object]) -> dict:
        report = await asyncio.to_thread(build_shadow_report, list(results))
        await asyncio.to_thread(self.store.append, report)
        self.reports.append(report)
        del self.reports[:-self.max_reports]
        return report

    def recent_reports(self) -> tuple[dict, ...]:
        """Return an immutable snapshot for operators; no profile mutation."""
        return tuple(self.reports)

    def callback(self, patch: Mapping[str, object], *, deltas: Sequence[RetrievalDelta],
                 holdout_candidate_delta: float, holdout_evidence_delta: float,
                 false_expansions: int) -> bool:
        """Persist only after trace and isolated-holdout gates both pass."""
        if not auto_write_allowed(
            deltas,
            holdout_candidate_delta=holdout_candidate_delta,
            holdout_evidence_delta=holdout_evidence_delta,
            false_expansions=false_expansions,
        ):
            return False
        return self.repository.apply_patch(patch, trace_supported=True)
