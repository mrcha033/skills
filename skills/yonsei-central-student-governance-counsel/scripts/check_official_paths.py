#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from _common import REFERENCES_DIR, SKILL_DIR, load_json, utc_now
from adaptive_source_discovery import marker_in_url


LINEAGES_PATH = REFERENCES_DIR / "source-lineages.json"
ADAPTER_PATH = SKILL_DIR / "scripts" / "adaptive_source_discovery.py"


def drive_item_ids(candidates: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for candidate in candidates:
        if candidate.get("kind") != "drive_item":
            continue
        parsed = urlsplit(str(candidate.get("url") or ""))
        if parsed.hostname != "drive.google.com":
            continue
        for value in parse_qs(parsed.query).get("id", []):
            if re.fullmatch(r"[A-Za-z0-9_-]{15,}", value):
                found.add(value)
        for value in re.findall(r"[A-Za-z0-9_-]{15,}", parsed.path):
            found.add(value)
    return found


def archive_entry_ids(
    candidates: list[dict[str, Any]],
    title_pattern: str,
    allowed_hosts: list[str],
) -> set[str]:
    pattern = re.compile(title_pattern)
    found: set[str] = set()
    for candidate in candidates:
        if candidate.get("kind") != "anchor":
            continue
        parsed = urlsplit(str(candidate.get("url") or ""))
        if parsed.hostname not in allowed_hosts:
            continue
        if not pattern.search(str(candidate.get("label") or "")):
            continue
        for value in parse_qs(parsed.query).get("idx", []):
            if value.isdigit():
                found.add(f"idx={value}")
    return found


def run_check(
    check: dict[str, Any],
    allowed_hosts: list[str],
    adapter_timeout: int,
    per_check_timeout: int,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ADAPTER_PATH),
        "--url",
        check["url"],
        "--query",
        " ".join(check.get("require_any_text") or []),
        "--timeout",
        str(adapter_timeout),
    ]
    for selector in check.get("selectors") or []:
        command.extend(["--selector", selector])
    for marker in check.get("require_any_text") or []:
        command.extend(["--require-any-text", marker])
    for marker in check.get("expect_final_url_contains") or []:
        command.extend(["--expected-final-url-contains", marker])
    for host in allowed_hosts:
        command.extend(["--allowed-host", host])
    if check.get("allow_weak"):
        command.append("--allow-weak")
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=per_check_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        timeout_receipt = {
            "accepted": False,
            "access_route": "bounded-timeout",
            "runtime": {},
            "access": {
                "ok": False,
                "verdict": "timeout",
                "stop_reason": "bounded_timeout",
                "grid_exhausted": False,
                "untried_routes": ["retry the same public check once"],
                "must_invoke_playwright_mcp": False,
            },
            "validation": {
                "accepted": False,
                "reasons": [
                    f"live check exceeded the {per_check_timeout}-second bound"
                ],
            },
            "candidates": [],
            "failure_gate": {
                "complete": False,
                "untried_routes": ["retry the same public check once"],
            },
        }
        result = subprocess.CompletedProcess(
            command,
            20,
            stdout=json.dumps(timeout_receipt, ensure_ascii=False),
            stderr="bounded live-check timeout",
        )
    try:
        receipt = json.loads(result.stdout)
    except json.JSONDecodeError:
        receipt = {
            "accepted": False,
            "error": "adaptive discovery returned invalid JSON",
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:],
        }
    candidate_urls = [
        str(candidate.get("url") or "") for candidate in receipt.get("candidates", [])
    ]
    expected_candidates = check.get("expect_candidate_contains") or []
    candidate_matches = {
        expected: any(marker_in_url(expected, candidate_url) for candidate_url in candidate_urls)
        for expected in expected_candidates
    }
    catalog_checks: list[dict[str, Any]] = []
    exact_drive_ids = check.get("catalog_exact_drive_item_ids")
    if exact_drive_ids is not None:
        expected = set(exact_drive_ids)
        actual = drive_item_ids(receipt.get("candidates", []))
        catalog_checks.append(
            {
                "kind": "drive_items",
                "passed": actual == expected,
                "expected": sorted(expected),
                "actual": sorted(actual),
                "missing": sorted(expected - actual),
                "unexpected": sorted(actual - expected),
            }
        )
    exact_archive_ids = check.get("catalog_exact_archive_entry_ids")
    if exact_archive_ids is not None:
        expected = set(exact_archive_ids)
        try:
            actual = archive_entry_ids(
                receipt.get("candidates", []),
                check["catalog_title_pattern"],
                allowed_hosts,
            )
            catalog_error = None
        except re.error as exc:
            actual = set()
            catalog_error = f"invalid catalog_title_pattern: {exc}"
        catalog_checks.append(
            {
                "kind": "archive_entries",
                "passed": catalog_error is None and actual == expected,
                "expected": sorted(expected),
                "actual": sorted(actual),
                "missing": sorted(expected - actual),
                "unexpected": sorted(actual - expected),
                "error": catalog_error,
            }
        )
    catalog_passed = all(row["passed"] for row in catalog_checks)
    passed = (
        bool(receipt.get("accepted"))
        and all(candidate_matches.values())
        and catalog_passed
    )
    errors: list[str] = []
    if not receipt.get("accepted"):
        errors.append("adaptive fetch was not accepted")
    missing = [marker for marker, matched in candidate_matches.items() if not matched]
    if missing:
        errors.append(f"expected discovered links were absent: {', '.join(missing)}")
    for catalog_check in catalog_checks:
        if catalog_check["passed"]:
            continue
        if catalog_check.get("error"):
            errors.append(str(catalog_check["error"]))
        if catalog_check["missing"]:
            errors.append(
                "catalog entries disappeared: "
                + ", ".join(catalog_check["missing"])
            )
        if catalog_check["unexpected"]:
            errors.append(
                "unclassified catalog entries appeared: "
                + ", ".join(catalog_check["unexpected"])
            )
    relevant_candidates = [
        candidate
        for candidate in receipt.get("candidates", [])
        if not expected_candidates
        or any(
            marker_in_url(marker, str(candidate.get("url") or ""))
            for marker in expected_candidates
        )
    ][:25]
    access = receipt.get("access") or receipt.get("engine") or {}
    receipt_summary = {
        "accepted": receipt.get("accepted"),
        "access_route": receipt.get("access_route"),
        "runtime": {
            "engine_dir": (receipt.get("runtime") or {}).get("engine_dir"),
            "interpreter": (receipt.get("runtime") or {}).get("interpreter"),
            "insane_search_ready": (receipt.get("runtime") or {}).get(
                "insane_search_ready"
            ),
        },
        "access": {
            "ok": access.get("ok"),
            "final_url": access.get("final_url"),
            "verdict": access.get("verdict"),
            "profile_used": access.get("profile_used"),
            "summary": access.get("summary"),
            "planned_attempts": access.get("planned_attempts"),
            "executed_attempts": access.get("executed_attempts"),
            "grid_exhausted": access.get("grid_exhausted"),
            "stop_reason": access.get("stop_reason"),
            "untried_routes": access.get("untried_routes"),
            "must_invoke_playwright_mcp": access.get("must_invoke_playwright_mcp"),
        },
        "validation": receipt.get("validation"),
        "content": receipt.get("content"),
        "candidate_count": len(receipt.get("candidates", [])),
        "candidate_filter": receipt.get("candidate_filter"),
        "relevant_candidates": relevant_candidates,
        "catalog_checks": catalog_checks,
        "failure_gate": receipt.get("failure_gate"),
    }
    return {
        "check_id": check["check_id"],
        "url": check["url"],
        "passed": passed,
        "adapter_exit_code": result.returncode,
        "candidate_matches": candidate_matches,
        "errors": errors,
        "receipt": receipt_summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trace and validate the current public paths to Yonsei governance sources."
    )
    parser.add_argument("--domain", choices=("student_council", "club_union"))
    parser.add_argument("--check-id", action="append", default=[])
    parser.add_argument("--output")
    parser.add_argument("--adapter-timeout", type=int, default=12)
    parser.add_argument("--per-check-timeout", type=int, default=60)
    parser.add_argument("--max-workers", type=int, default=12)
    args = parser.parse_args()
    if args.adapter_timeout <= 0 or args.per_check_timeout <= 0:
        parser.error("timeouts must be positive")
    if args.max_workers <= 0:
        parser.error("--max-workers must be positive")

    registry = load_json(LINEAGES_PATH)
    jobs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for lineage in registry.get("lineages", []):
        if args.domain and lineage.get("domain") != args.domain:
            continue
        for check in lineage.get("live_checks", []):
            if args.check_id and check.get("check_id") not in args.check_id:
                continue
            jobs.append((lineage, check))
    if not jobs:
        parser.error("no live checks matched the requested filters")
    ordered_results: list[dict[str, Any] | None] = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=min(args.max_workers, len(jobs))) as executor:
        futures = {
            executor.submit(
                run_check,
                check,
                lineage.get("allowed_hosts") or [],
                args.adapter_timeout,
                args.per_check_timeout,
            ): (index, lineage, check)
            for index, (lineage, check) in enumerate(jobs)
        }
        for future in as_completed(futures):
            index, lineage, check = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {
                    "check_id": check.get("check_id"),
                    "url": check.get("url"),
                    "passed": False,
                    "adapter_exit_code": 20,
                    "candidate_matches": {},
                    "errors": [f"live-check worker failed: {type(exc).__name__}: {exc}"],
                    "receipt": {
                        "accepted": False,
                        "failure_gate": {
                            "complete": False,
                            "untried_routes": ["inspect and retry the failed worker"],
                        },
                    },
                }
            row["lineage_id"] = lineage["lineage_id"]
            row["domain"] = lineage["domain"]
            ordered_results[index] = row
    results = [row for row in ordered_results if row is not None]
    payload = {
        "schema_version": 1,
        "checked_at": utc_now(),
        "passed": all(row["passed"] for row in results),
        "check_count": len(results),
        "execution_bounds": {
            "adapter_timeout_seconds": args.adapter_timeout,
            "per_check_timeout_seconds": args.per_check_timeout,
            "max_workers": min(args.max_workers, len(jobs)),
        },
        "results": results,
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target = Path(args.output).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    print(body, end="")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
