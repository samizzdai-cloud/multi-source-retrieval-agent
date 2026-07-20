"""Immutable Profile snapshot publication and rollback boundary."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

from tools.core.gtm_profile import GTMProfileRepository, RetrievalDelta, auto_write_allowed
from tools.core.models import ProfileGovernanceConfig, ProfileRerankConfig


@dataclass(frozen=True)
class SnapshotValidation:
    independent_requests: int
    query_signatures: int
    successful_deltas: int
    total_deltas: int
    candidate_recall_delta: float
    evidence_recall_delta: float
    false_expansions: int
    median_rank_improvement: float = 0.0
    candidate_recall_improvement_pp: float = 0.0
    stable_runs: int = 2
    transport_inconclusive: int = 0

    def passes(self, config: ProfileGovernanceConfig) -> bool:
        success_rate = self.successful_deltas / self.total_deltas if self.total_deltas else 0.0
        quality_gain = (
            self.median_rank_improvement >= 2
            or self.candidate_recall_improvement_pp >= 5
        )
        return (
            self.independent_requests >= config.min_independent_requests
            and self.query_signatures >= config.min_query_signatures
            and success_rate >= config.min_success_rate
            and self.candidate_recall_delta >= 0
            and self.evidence_recall_delta >= 0
            and self.false_expansions == 0
            and self.stable_runs >= 2
            and quality_gain
        )


class ProfileSnapshotManager:
    """Build staging DBs, validate them, then atomically switch a pointer."""

    def __init__(
        self,
        *,
        live_path: str | Path = "data/gtm_profile.sqlite",
        root: str | Path = "data/profile_snapshots",
        rerank: ProfileRerankConfig | None = None,
        governance: ProfileGovernanceConfig | None = None,
    ) -> None:
        self.live_path = Path(live_path)
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.pointer_path = self.root / "active.json"
        self.approval_path = self.root / "commissioning.json"
        self.rerank = rerank or ProfileRerankConfig()
        self.governance = governance or ProfileGovernanceConfig()

    def policy_fingerprint(self) -> str:
        material = json.dumps(
            {"rerank": asdict(self.rerank), "governance": asdict(self.governance)},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def commission(self, *, approved: bool) -> bool:
        if not approved:
            return False
        self._atomic_json(self.approval_path, {
            "approved": True,
            "policy_fingerprint": self.policy_fingerprint(),
            "approved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        return True

    def is_commissioned(self) -> bool:
        try:
            approval = json.loads(self.approval_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(approval.get("approved")) and approval.get("policy_fingerprint") == self.policy_fingerprint()

    def publish_patch(
        self,
        patch: Mapping[str, object],
        *,
        deltas: Sequence[RetrievalDelta],
        validation: SnapshotValidation,
    ) -> str | None:
        if self.governance.mode != "autonomous" or not self.is_commissioned():
            return None
        if not validation.passes(self.governance):
            return None
        if not auto_write_allowed(
            deltas,
            holdout_candidate_delta=validation.candidate_recall_delta,
            holdout_evidence_delta=validation.evidence_recall_delta,
            false_expansions=validation.false_expansions,
        ):
            return None

        snapshot_id = time.strftime("%Y%m%dT%H%M%S") + "-" + hashlib.sha256(
            json.dumps(dict(patch), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:8]
        staging = self.root / f"{snapshot_id}.sqlite"
        source = self.active_database()
        if not source.exists():
            GTMProfileRepository(source)
        with sqlite3.connect(source) as src, sqlite3.connect(staging) as dst:
            src.backup(dst)
        repository = GTMProfileRepository(staging)
        if not repository.apply_patch(patch, trace_supported=True):
            staging.unlink(missing_ok=True)
            return None
        manifest = self._validate_and_manifest(staging, snapshot_id, validation)
        manifest_path = self.root / f"{snapshot_id}.manifest.json"
        self._atomic_json(manifest_path, manifest)
        self._atomic_json(self.pointer_path, {
            "snapshot_id": snapshot_id,
            "database_path": str(staging.resolve()),
            "manifest_path": str(manifest_path.resolve()),
            "policy_fingerprint": self.policy_fingerprint(),
            "previous_snapshot_id": self.active_snapshot_id(),
            "activated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        return snapshot_id

    def active_database(self) -> Path:
        try:
            path = Path(str(json.loads(self.pointer_path.read_text(encoding="utf-8"))["database_path"]))
            if path.exists():
                return path
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            pass
        return self.live_path

    def active_snapshot_id(self) -> str:
        try:
            return str(json.loads(self.pointer_path.read_text(encoding="utf-8")).get("snapshot_id") or "")
        except (OSError, json.JSONDecodeError):
            return ""

    def rollback(self, snapshot_id: str) -> bool:
        database = self.root / f"{snapshot_id}.sqlite"
        manifest = self.root / f"{snapshot_id}.manifest.json"
        if not database.exists() or not manifest.exists():
            return False
        self._atomic_json(self.pointer_path, {
            "snapshot_id": snapshot_id,
            "database_path": str(database.resolve()),
            "manifest_path": str(manifest.resolve()),
            "policy_fingerprint": self.policy_fingerprint(),
            "rollback": True,
            "activated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        return True

    def _validate_and_manifest(
        self, database: Path, snapshot_id: str, validation: SnapshotValidation,
    ) -> dict:
        with sqlite3.connect(database) as db:
            integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise RuntimeError(f"profile snapshot integrity failed:{integrity}")
            active_rows = db.execute(
                "SELECT id FROM expression_set WHERE status='active' ORDER BY id"
            ).fetchall()
        included = [int(row[0]) for row in active_rows[:self.rerank.package_limit]]
        excluded = [int(row[0]) for row in active_rows[self.rerank.package_limit:]]
        with sqlite3.connect(database) as db:
            if included:
                placeholders = ",".join("?" for _ in included)
                registry_rows = db.execute(
                    f"SELECT id,name,variants FROM expression_set WHERE id IN ({placeholders})",
                    included,
                ).fetchall()
                by_id = {int(row[0]): row for row in registry_rows}
                registry_rows = [by_id[item_id] for item_id in included]
            else:
                registry_rows = []
        content_fingerprint = hashlib.sha256(
            json.dumps(registry_rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "snapshot_id": snapshot_id,
            "database_path": str(database.resolve()),
            "policy_fingerprint": self.policy_fingerprint(),
            "registry": {"included_ids": included, "excluded_ids": excluded,
                         "package_limit": self.rerank.package_limit,
                         "content_fingerprint": content_fingerprint},
            "holdout": asdict(validation),
            "canary": [10, 50, 100],
            "integrity": integrity,
        }

    @staticmethod
    def _atomic_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
