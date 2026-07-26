#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from _common import load_json, load_jsonl, utc_now, write_json

PROPOSITION_TOKEN = re.compile(r"\[P:([A-Za-z0-9_.:@-]+)\]")
SOURCE_TOKEN = re.compile(r"\[S:([A-Za-z0-9_.:@-]+)\]")


def ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {row["proposition_id"] for row in load_json(path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check proposition leakage and source resolution in a brief.")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--brief", type=Path, required=True)
    args = parser.parse_args()
    state = load_json(args.case / "state.json")
    brief = args.brief.read_text(encoding="utf-8")
    referenced_propositions = set(PROPOSITION_TOKEN.findall(brief))
    referenced_sources = set(SOURCE_TOKEN.findall(brief))
    verified = ids(args.case / "outputs" / "verified_propositions.json")
    arguable = ids(args.case / "outputs" / "arguable_propositions.json")
    blocked = ids(args.case / "outputs" / "blocked_propositions.json")
    refuted = ids(args.case / "outputs" / "refuted_propositions.json")
    registered_sources = {row["id"] for row in load_jsonl(args.case / "sources" / "sources.jsonl")}
    known_propositions = verified | arguable | blocked | refuted
    leaked = sorted(referenced_propositions & (blocked | refuted))
    unknown_propositions = sorted(referenced_propositions - known_propositions)
    unresolved_sources = sorted(referenced_sources - registered_sources)
    uncited_allowed = sorted((verified | arguable) - referenced_propositions)
    used_registered = referenced_sources & registered_sources
    orphan_sources = sorted(registered_sources - referenced_sources)
    issues: list[str] = []
    if not state.get("verification", {}).get("passed"):
        issues.append("ledger verification has not passed")
    if leaked:
        issues.append(f"blocked/refuted proposition leakage: {leaked}")
    if unknown_propositions:
        issues.append(f"unknown proposition tokens: {unknown_propositions}")
    if unresolved_sources:
        issues.append(f"unregistered source tokens: {unresolved_sources}")
    if not referenced_propositions:
        issues.append("brief contains no proposition tokens")
    if not referenced_sources:
        issues.append("brief contains no source tokens")
    report = {
        "evaluated_at": utc_now(),
        "verdict": "PASS" if not issues else "FAIL",
        "issues": issues,
        "metrics": {
            "proposition_leak_rate": len(leaked) / max(len(referenced_propositions), 1),
            "citation_resolution_rate": len(used_registered) / max(len(referenced_sources), 1),
            "orphan_source_rate": len(orphan_sources) / max(len(registered_sources), 1),
            "allowed_proposition_coverage_rate": (
                len(referenced_propositions & (verified | arguable)) / max(len(verified | arguable), 1)
            ),
        },
        "details": {
            "referenced_propositions": sorted(referenced_propositions),
            "referenced_sources": sorted(referenced_sources),
            "leaked_propositions": leaked,
            "unknown_propositions": unknown_propositions,
            "unresolved_sources": unresolved_sources,
            "uncited_allowed_propositions": uncited_allowed,
            "orphan_sources": orphan_sources,
        },
    }
    write_json(args.case / "outputs" / "eval_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
