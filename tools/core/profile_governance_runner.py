"""External, scheduler-safe entrypoint for offline Profile Delta extraction.

Examples:
  python -m tools.core.profile_governance_runner status
  python -m tools.core.profile_governance_runner extract --limit 100
  python -m tools.core.profile_governance_runner run-once

`run-once` advances only an audit cursor.  It never changes gtm_profile.sqlite.
Use it as a Windows Task Scheduler action or invoke it manually for testing.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .gtm_profile import RetrievalGovernanceStore, extract_profile_deltas


DEFAULT_STORE = "data/retrieval_governance.sqlite"
DEFAULT_CURSOR = "data/profile_delta_cursor.json"


def _read_cursor(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return max(0, int(payload.get("last_report_id", 0)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _summary(records: list[dict], deltas: list[object], *, after_id: int) -> dict:
    last_report_id = max((int(record["report_id"]) for record in records), default=after_id)
    return {
        "mode": "offline_delta_extraction",
        "reports_scanned": len(records),
        "after_id": after_id,
        "last_report_id": last_report_id,
        "candidate_delta_count": len(deltas),
        "qualified_delta_count": sum(1 for delta in deltas if delta.qualifies()),
        "profile_write": "disabled",
        "deltas": [asdict(delta) | {"qualifies": delta.qualifies()} for delta in deltas],
    }


def _load_records(args: argparse.Namespace, store: RetrievalGovernanceStore, after_id: int) -> list[dict]:
    if not args.report_json:
        return store.iter_reports(after_id=after_id, limit=args.limit)
    payload = json.loads(Path(args.report_json).read_text(encoding="utf-8"))
    reports = payload if isinstance(payload, list) else [payload]
    return [
        {"report_id": index + 1, "created_at": "external", "report": report}
        for index, report in enumerate(reports) if isinstance(report, dict)
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract body-free Profile candidate Deltas from retrieval audit reports.")
    parser.add_argument("command", choices=("status", "extract", "run-once"))
    parser.add_argument("--store", default=DEFAULT_STORE, help="body-free governance SQLite path")
    parser.add_argument("--after-id", type=int, default=0, help="exclusive report id for stateless extract")
    parser.add_argument("--limit", type=int, default=1000, help="maximum audit reports to scan")
    parser.add_argument("--cursor", default=DEFAULT_CURSOR, help="audit cursor used by run-once")
    parser.add_argument("--json-out", help="optional JSON result file")
    parser.add_argument("--report-json", help="body-free report JSON fixture for manual extraction tests")
    args = parser.parse_args(argv)

    store = RetrievalGovernanceStore(args.store)
    if args.command == "status":
        output = {"store": str(store.path), "shadow_report_count": store.count(), "profile_write": "disabled"}
    else:
        cursor_path = Path(args.cursor)
        after_id = _read_cursor(cursor_path) if args.command == "run-once" else max(0, args.after_id)
        records = _load_records(args, store, after_id)
        deltas = extract_profile_deltas(records)
        output = _summary(records, deltas, after_id=after_id)
        if args.command == "run-once" and not args.report_json:
            _write_json(cursor_path, {"last_report_id": output["last_report_id"]})

    if args.json_out:
        _write_json(Path(args.json_out), output)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
