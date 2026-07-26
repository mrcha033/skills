#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

from _common import (
    GOVERNANCE_SCOPE,
    explicitly_identifies_yonsei,
    resolve_domain,
    utc_now,
    write_json,
    write_jsonl,
)


def slugify(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z가-힣]+", "-", value).strip("-").lower()
    return value[:48] or "agenda"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a resumable governance argument case.")
    parser.add_argument("--agenda", required=True)
    parser.add_argument("--position", required=True)
    parser.add_argument("--body", default="unknown")
    parser.add_argument("--domain", required=True, choices=["club_union", "student_council"])
    parser.add_argument("--meeting-date", default=date.today().isoformat())
    parser.add_argument("--known-fact", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    date.fromisoformat(args.meeting_date)
    try:
        scope_context = (
            args.body
            if explicitly_identifies_yonsei(args.body)
            else " ".join([args.body, args.agenda, args.position, *args.known_fact])
        )
        domain = resolve_domain(args.body, args.domain, scope_context)
    except ValueError as exc:
        parser.error(str(exc))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    case_dir = args.output or (Path.cwd() / "CASES" / f"{slugify(args.agenda)}_{timestamp}")
    for child in ("sources", "artifacts", "outputs"):
        (case_dir / child).mkdir(parents=True, exist_ok=True)
    case_input = {
        "agenda": args.agenda,
        "desired_position": args.position,
        "meeting_body": args.body,
        "governance_scope": GOVERNANCE_SCOPE,
        "governance_domain": domain,
        "meeting_date": args.meeting_date,
        "known_facts": [{"text": fact, "status": "user_asserted"} for fact in args.known_fact],
        "created_at": utc_now(),
    }
    state = {
        "case_id": case_dir.name,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "status": "RESEARCHING",
        "current_phase": "authority_search",
        "progress": {
            "authority_search": "in_progress",
            "source_detail": "pending",
            "counter_search": "pending",
            "ledger_validation": "pending",
            "synthesis": "pending",
            "evaluation": "pending",
        },
        "verification": {"passed": False, "signature": None},
        "errors": [],
    }
    write_json(case_dir / "input.json", case_input)
    write_json(case_dir / "state.json", state)
    write_jsonl(case_dir / "sources" / "sources.jsonl", [])
    write_jsonl(case_dir / "artifacts" / "argument_ledger.jsonl", [])
    print(case_dir.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
