"""Bounded, shared GTM retrieval profile.

Profile data is a query-rewrite candidate only.  It never supplies evidence or
causes a fallback unless the primary API search produced zero candidates.
"""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Sequence

DEFAULT_SCOPE = "department:GTM"
_TRACE_EVENT_FIELDS = frozenset({
    "event", "resolution_id", "source", "chunk_id", "focus", "focuses", "sub_query_id", "query_id", "query_ids", "query_type",
    "api_query", "api_rank", "document", "document_id", "heading", "range", "batch_id",
    "content_sha256", "content_chars", "timeout_s", "outcome", "tier", "fallback", "fallback_origin", "fallback_kind",
    "candidate_count", "primary_candidate_count", "fallback_candidate_count", "replan", "route", "phase",
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
        """Return at most one alias and one exact-expression replacement."""
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


@dataclass(frozen=True)
class ProfileResolution:
    """Immutable request-scoped profile decision shared by every consumer."""

    resolution_id: str
    snapshot_id: str
    exact_entity_matches: tuple[dict, ...]
    exact_expression_matches: tuple[dict, ...]
    semantic_expression_candidates: tuple[dict, ...]
    planner_pack: MemoryPack
    fallback_pack: MemoryPack
    warnings: tuple[str, ...] = ()
    registry_snapshot_id: str = ""
    registry_state: str = "not_configured"
    registry_included_count: int = 0
    registry_excluded_count: int = 0

    def planner_prompt_block(self) -> str:
        body = self.planner_pack.prompt_block()
        if not body:
            return ""
        return f"<profile_context>\n{body}\n</profile_context>"

class GTMProfileRepository:
    def __init__(self, path: str | Path = "data/gtm_profile.sqlite", scope: str = DEFAULT_SCOPE):
        requested = Path(path)
        self._active_pointer: dict[str, Any] = {}
        if str(requested).replace("\\", "/") == "data/gtm_profile.sqlite":
            pointer = Path("data/profile_snapshots/active.json")
            try:
                self._active_pointer = json.loads(pointer.read_text(encoding="utf-8"))
                active = Path(str(self._active_pointer["database_path"]))
                if active.exists():
                    requested = active
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                pass
        self.path, self.scope = requested, scope
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
            self._ensure_column(db, "entity", "status", "TEXT NOT NULL DEFAULT 'active'")
            self._ensure_column(db, "entity", "tombstoned_at", "TEXT")
            self._ensure_column(db, "expression_set", "status", "TEXT NOT NULL DEFAULT 'active'")
            self._ensure_column(db, "expression_set", "tombstoned_at", "TEXT")

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, name: str, declaration: str) -> None:
        existing = {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def build_pack(self, query: str) -> MemoryPack:
        q = normalize(query)
        if not q:
            return MemoryPack([], [])
        with self._connect() as db:
            entities = []
            for eid, name, kind in db.execute(
                "SELECT id,name,kind FROM entity WHERE scope=? AND status='active'", (self.scope,),
            ):
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
                        "SELECT f.name,f.variants FROM expression_fts f "
                        "JOIN expression_set e ON e.scope=f.scope AND e.name=f.name "
                        "WHERE expression_fts MATCH ? AND f.scope=? AND e.status='active'",
                        (fts_query, self.scope),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = db.execute(
                        "SELECT name,variants FROM expression_set WHERE scope=? AND status='active'", (self.scope,),
                    ).fetchall()
            else:
                rows = db.execute(
                    "SELECT name,variants FROM expression_set WHERE scope=? AND status='active'", (self.scope,),
                ).fetchall()
            for name, raw_variants in rows:
                variants = [v for v in raw_variants.split("|") if v]
                if normalize(name) in q or any(normalize(v) in q for v in variants):
                    expressions.append({"name": name, "variants": variants[:2]})
        return MemoryPack(entities[:3], expressions[:3])

    def resolve(self, query: str, config: Any, *, resolution_id: str = "") -> ProfileResolution:
        """Resolve exact and optional semantic vocabulary exactly once per root request."""
        exact = self.build_pack(query)
        warnings: list[str] = []
        semantic: list[dict] = []
        registry_rows, snapshot_id, registry_state, included_count, excluded_count, registry_warnings = (
            self._load_frozen_semantic_registry(config)
        )
        warnings.extend(registry_warnings)

        if (
            bool(getattr(config, "enabled", True))
            and not exact.expressions
            and registry_rows
            and registry_state == "active"
        ):
            packages: list[str] = []
            package_rows: list[tuple[int, str, str]] = []
            max_chars = max(50, int(getattr(config, "max_package_chars", 300)))
            for item_id, name, raw_variants in registry_rows:
                variants = [v.strip() for v in str(raw_variants or "").split("|") if v.strip()][:2]
                package = json.dumps(
                    {"canonical": str(name), "variants": variants},
                    ensure_ascii=False, separators=(",", ":"),
                )
                if len(package) > max_chars:
                    warnings.append(f"semantic_package_excluded:{item_id}:too_long")
                    continue
                packages.append(package)
                package_rows.append((int(item_id), str(name), str(raw_variants or "")))
            try:
                from tools.core.rerank import RerankSettings, rerank_documents

                base = RerankSettings.from_env()
                settings = RerankSettings(
                    api_key=base.api_key,
                    workspace_id=base.workspace_id,
                    model=str(getattr(config, "model", "qwen3-rerank")),
                    timeout_s=float(getattr(config, "timeout_s", 1.5)),
                    instruct=str(getattr(config, "instruct", base.instruct)),
                    endpoint=base.endpoint,
                )
                results = rerank_documents(
                    query, packages, settings=settings,
                    top_n=min(int(getattr(config, "top_n", 8)), len(packages)),
                )
                ordered = sorted(results, key=lambda item: item.relevance_score, reverse=True)
                for result in ordered:
                    if 0 <= result.index < len(package_rows):
                        item_id, name, raw_variants = package_rows[result.index]
                        semantic.append({
                            "id": item_id, "name": name,
                            "variants": [v for v in raw_variants.split("|") if v][:2],
                            "score": result.relevance_score,
                        })
            except Exception as exc:
                warnings.append(f"semantic_rerank_disabled:{type(exc).__name__}")

        semantic_planner: list[dict] = []
        if semantic and not bool(getattr(config, "shadow", True)):
            top_score = float(semantic[0]["score"])
            next_score = float(semantic[1]["score"]) if len(semantic) > 1 else 0.0
            if (
                top_score >= float(getattr(config, "min_score", 0.60))
                and top_score - next_score >= float(getattr(config, "min_margin", 0.05))
            ):
                semantic_planner = [{
                    "name": semantic[0]["name"],
                    "variants": list(semantic[0].get("variants") or [])[:2],
                }]

        planner_entities = list(exact.entities)[:int(getattr(config, "max_planner_entities", 2))]
        planner_expressions = (
            list(exact.expressions) + semantic_planner
        )[:int(getattr(config, "max_planner_expressions", 2))]
        planner_pack = MemoryPack(planner_entities, planner_expressions)
        max_pack_chars = int(getattr(config, "max_planner_pack_chars", 400))
        while len(planner_pack.prompt_block()) > max_pack_chars and planner_expressions:
            planner_expressions.pop()
            planner_pack = MemoryPack(planner_entities, planner_expressions)
        while len(planner_pack.prompt_block()) > max_pack_chars and planner_entities:
            planner_entities.pop()
            planner_pack = MemoryPack(planner_entities, planner_expressions)

        return ProfileResolution(
            resolution_id=resolution_id or f"res_{uuid.uuid4().hex}",
            snapshot_id=snapshot_id,
            exact_entity_matches=tuple(exact.entities),
            exact_expression_matches=tuple(exact.expressions),
            semantic_expression_candidates=tuple(semantic),
            planner_pack=planner_pack,
            fallback_pack=exact,
            warnings=tuple(warnings),
            registry_snapshot_id=snapshot_id,
            registry_state=registry_state,
            registry_included_count=included_count,
            registry_excluded_count=excluded_count,
        )

    def _load_frozen_semantic_registry(self, config: Any) -> tuple[list[tuple[int, str, str]], str, str, int, int, list[str]]:
        """Load only the IDs frozen in the active snapshot manifest.

        Exact/lexical matching intentionally remains available when this fails;
        semantic rerank must fail closed rather than silently slice live rows.
        """
        pointer = self._active_pointer
        manifest_path = Path(str(pointer.get("manifest_path") or "")) if pointer else None
        if not pointer or not manifest_path or not manifest_path.exists():
            return [], str(pointer.get("snapshot_id") or ""), "manifest_missing", 0, 0, ["semantic_registry_disabled:manifest_missing"]
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            registry = manifest["registry"]
            included = [int(value) for value in registry["included_ids"]]
            excluded = [int(value) for value in registry.get("excluded_ids") or []]
            package_limit = min(450, max(1, int(getattr(config, "package_limit", 450))))
            if (manifest.get("snapshot_id") != pointer.get("snapshot_id")
                    or int(registry.get("package_limit", 0)) != package_limit
                    or len(included) > package_limit
                    or len(set(included)) != len(included)):
                raise ValueError("manifest_contract")
            with self._connect() as db:
                placeholders = ",".join("?" for _ in included) or "NULL"
                rows = db.execute(
                    f"SELECT id,name,variants FROM expression_set WHERE scope=? AND status='active' AND id IN ({placeholders})",
                    [self.scope, *included],
                ).fetchall()
            by_id = {int(row[0]): row for row in rows}
            if set(by_id) != set(included):
                raise ValueError("manifest_ids_missing")
            ordered = [by_id[item_id] for item_id in included]
            fingerprint = hashlib.sha256(json.dumps(ordered, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
            if fingerprint != str(registry.get("content_fingerprint") or ""):
                raise ValueError("manifest_fingerprint")
            return ordered, str(pointer.get("snapshot_id") or ""), "active", len(included), len(excluded), []
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
            return [], str(pointer.get("snapshot_id") or ""), "invalid", 0, 0, [f"semantic_registry_disabled:{type(exc).__name__}"]

    def validate_patch(self, patch: Mapping[str, object], *, trace_supported: bool = False) -> bool:
        action, typ = str(patch.get("action", "")), str(patch.get("type", ""))
        value = str(patch.get("name") or patch.get("alias") or "").strip()
        norm = normalize(value)
        if not trace_supported or action not in {
            "add_entity", "add_alias", "add_expression", "update_expression",
            "delete", "quarantine", "retire", "reactivate",
        }:
            return False
        if action in {"delete", "quarantine", "retire", "reactivate"}:
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
                db.execute(
                    "INSERT INTO entity(scope,name,kind,normalized,status,tombstoned_at) VALUES(?,?,?,?, 'active', NULL) "
                    "ON CONFLICT(scope,normalized) DO UPDATE SET name=excluded.name,kind=excluded.kind",
                    (self.scope, str(patch["name"]), str(patch.get("kind", "other")), normalize(str(patch["name"]))),
                )
            elif action == "add_alias" and typ == "alias":
                entity_id = int(patch.get("entity_id", 0) or 0)
                alias_norm = normalize(str(patch["alias"]))
                entity_active = db.execute(
                    "SELECT 1 FROM entity WHERE id=? AND scope=? AND status='active'",
                    (entity_id, self.scope),
                ).fetchone()
                conflict = (
                    db.execute("SELECT 1 FROM entity_alias WHERE normalized=?", (alias_norm,)).fetchone()
                    or db.execute("SELECT 1 FROM entity WHERE normalized=? AND scope=?", (alias_norm, self.scope)).fetchone()
                )
                if not entity_active or conflict:
                    return False
                db.execute("INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized) VALUES(?,?,?)",
                           (entity_id, str(patch["alias"]), alias_norm))
            elif action in {"add_expression", "update_expression"} and typ == "expression":
                name, variants = str(patch["name"]), str(patch.get("variants", ""))
                normalized = normalize(name)
                old = db.execute(
                    "SELECT name FROM expression_set WHERE scope=? AND normalized=?",
                    (self.scope, normalized),
                ).fetchone()
                if old:
                    db.execute("DELETE FROM expression_fts WHERE scope=? AND name=?", (self.scope, old[0]))
                db.execute(
                    "INSERT INTO expression_set(scope,name,normalized,variants,status,tombstoned_at) "
                    "VALUES(?,?,?,?, 'active', NULL) "
                    "ON CONFLICT(scope,normalized) DO UPDATE SET "
                    "name=excluded.name,variants=excluded.variants",
                    (self.scope, name, normalized, variants),
                )
                db.execute("INSERT INTO expression_fts(scope,name,variants) VALUES(?,?,?)", (self.scope, name, variants))
            elif action in {"delete", "quarantine", "retire", "reactivate"}:
                table = {"entity": "entity", "expression": "expression_set"}.get(typ)
                if not table:
                    return False
                item_id = int(patch["id"])
                status = {
                    "delete": "retired", "quarantine": "quarantined",
                    "retire": "retired", "reactivate": "active",
                }[action]
                if table == "expression_set":
                    row = db.execute(
                        "SELECT name,variants FROM expression_set WHERE id=? AND scope=?",
                        (item_id, self.scope),
                    ).fetchone()
                    if not row:
                        return False
                    db.execute("DELETE FROM expression_fts WHERE scope=? AND name=?", (self.scope, row[0]))
                    if status == "active":
                        db.execute(
                            "INSERT INTO expression_fts(scope,name,variants) VALUES(?,?,?)",
                            (self.scope, row[0], row[1]),
                        )
                cursor = db.execute(
                    f"UPDATE {table} SET status=?, tombstoned_at="
                    "CASE WHEN ?='active' THEN NULL ELSE CURRENT_TIMESTAMP END "
                    "WHERE id=? AND scope=?",
                    (status, status, item_id, self.scope),
                )
                if cursor.rowcount != 1:
                    return False
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
    root_request_id: str = ""
    query_signature: str = ""

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
    distinct_requests = {d.root_request_id for d in successes if d.root_request_id}
    distinct_signatures = {d.query_signature for d in successes if d.query_signature}
    hit_rate = len(successes) / len(deltas) if deltas else 0.0
    return (len(distinct_requests) >= 3 and len(distinct_signatures) >= 2
            and hit_rate >= 0.60 and holdout_candidate_delta >= 0
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
        query_chunks: dict[str, set[str]] = {}
        final_ids = {
            str(event.get("chunk_id")) for event in events
            if isinstance(event, Mapping) and event.get("event") == "final_retained"
            and event.get("chunk_id")
        }
        for event in events:
            if not isinstance(event, Mapping) or event.get("event") != "read":
                continue
            chunk_id = str(event.get("chunk_id") or "")
            for query_id in event.get("query_ids") or []:
                if chunk_id:
                    query_chunks.setdefault(str(query_id), set()).add(chunk_id)
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
                final_evidence_hits=len(query_chunks.get(str(event.get("query_id") or ""), set()) & final_ids),
                root_request_id=str(event.get("resolution_id") or ""),
                query_signature=normalize(str(event.get("api_query") or event.get("query_id") or "")),
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
            db.execute(
                "CREATE TABLE IF NOT EXISTS governance_outbox("
                "id INTEGER PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, "
                "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, state TEXT NOT NULL DEFAULT 'pending', "
                "lease_until TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, "
                "report_json TEXT NOT NULL)"
            )

    def append(self, report: Mapping[str, object]) -> None:
        payload = json.dumps(dict(report), ensure_ascii=False, separators=(",", ":"))
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO shadow_report(report_json) VALUES(?)", (payload,))

    def enqueue(self, report: Mapping[str, object], *, idempotency_key: str = "") -> bool:
        payload = json.dumps(dict(report), ensure_ascii=False, separators=(",", ":"))
        key = idempotency_key or hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with sqlite3.connect(self.path, timeout=0.08) as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO governance_outbox(idempotency_key,report_json) VALUES(?,?)",
                (key, payload),
            )
        return cursor.rowcount == 1

    def drain_outbox(self, *, limit: int = 100) -> int:
        """Idempotently project pending request events into the audit ledger."""
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT id,report_json FROM governance_outbox WHERE state='pending' ORDER BY id LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            moved = 0
            for outbox_id, payload in rows:
                db.execute("INSERT INTO shadow_report(report_json) VALUES(?)", (payload,))
                db.execute(
                    "UPDATE governance_outbox SET state='done',attempts=attempts+1,last_error=NULL WHERE id=?",
                    (outbox_id,),
                )
                moved += 1
        return moved

    def outbox_counts(self) -> dict[str, int]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT state,COUNT(*) FROM governance_outbox GROUP BY state").fetchall()
        return {str(state): int(count) for state, count in rows}

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
        await asyncio.to_thread(self.store.enqueue, report)
        self.reports.append(report)
        del self.reports[:-self.max_reports]
        return report

    async def observe_events(self, events: Sequence[Mapping[str, object]]) -> dict:
        class _Result:
            retrieval_trace = list(events)
        return await self.observe([_Result()])

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
