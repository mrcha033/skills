#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from _common import (
    DOMAINS,
    GOVERNANCE_SCOPE,
    INDEX_PATH,
    REFERENCES_DIR,
    body_matches_domain,
    effective_on,
    explicitly_identifies_yonsei,
    load_json,
    load_jsonl,
    resolve_domain,
    utc_now,
    write_json,
    write_jsonl,
)
from search_authorities import score_rows


BODY_SPECIFIC_RULE_PATTERNS = {
    "student_council": (
        (
            re.compile(r"(?:산하|소속)"),
            "총학생회 산하·소속 기구",
        ),
        (
            re.compile(r"(?:단과대학|[0-9A-Za-z가-힣·]+대학)\s*학생회"),
            "단과대학 학생회",
        ),
        (
            re.compile(
                r"(?:[0-9A-Za-z가-힣·]+학과|[0-9A-Za-z가-힣·]+과|"
                r"[0-9A-Za-z가-힣·]+반|독립학부)\s*학생회"
            ),
            "과·반·독립학부 학생회",
        ),
        (
            re.compile(
                r"(?:생협학생위원회|장애인권위원회|국제캠퍼스 학생대표위원회|"
                r"특별자치단체|특별기구)"
            ),
            "자치·특별기구",
        ),
    ),
    "club_union": (
        (
            re.compile(r"(?:산하|소속)"),
            "총동아리연합회 산하·소속 기구",
        ),
        (
            re.compile(r"(?:단과대학|[0-9A-Za-z가-힣·]+대학)\s*동아리연합회"),
            "단과대학 동아리연합회",
        ),
        (
            re.compile(r"국제캠퍼스\s*동아리연합회"),
            "국제캠퍼스 동아리연합회",
        ),
        (
            re.compile(
                r"(?:중앙|일반|국제캠퍼스)\s*"
                r"동아리(?!운영위원회|대표자회의|총회|연합회)"
            ),
            "개별 동아리",
        ),
        (
            re.compile(
                r"(?:단과대학|[0-9A-Za-z가-힣·]+대학)\s*"
                r"동아리(?!운영위원회|대표자회의|총회|연합회)"
            ),
            "단과대학 소속 동아리",
        ),
        (
            re.compile(
                r"(?:소속|산하).{1,40}"
                r"동아리(?!운영위원회|대표자회의|총회|연합회)"
            ),
            "총동아리연합회 소속 개별 동아리",
        ),
    ),
}


def excluded_source_reviews(
    domain: str,
    agenda: str,
    position: str,
    meeting_date: date,
    body: str = "",
) -> list[dict]:
    registry = load_json(REFERENCES_DIR / "source-lineages.json")
    haystack = f"{agenda} {position} {body}".lower()
    reviews: list[dict] = []
    for lineage in registry.get("lineages", []):
        if lineage.get("domain") != domain:
            continue
        sources = [
            ("drive_artifact", row, row.get("file_id"))
            for row in lineage.get("excluded_artifacts", [])
        ]
        sources.extend(
            ("archive_entry", row, row.get("entry_id"))
            for row in lineage.get("excluded_catalog_entries", [])
        )
        for source_kind, row, source_id in sources:
            terms = [
                term for term in row.get("trigger_terms", []) if term.lower() in haystack
            ]
            effective_from = (
                date.fromisoformat(row["effective_from"])
                if row.get("effective_from")
                else None
            )
            effective_to = (
                date.fromisoformat(row["effective_to"])
                if row.get("effective_to")
                else None
            )
            date_triggered = bool(
                (effective_from or effective_to)
                and (effective_from is None or effective_from <= meeting_date)
                and (effective_to is None or meeting_date <= effective_to)
            )
            if not terms and not date_triggered:
                continue
            reviews.append(
                {
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "title": row.get("title"),
                    "matched_terms": terms,
                    "matched_date_range": date_triggered,
                    "meeting_date": meeting_date.isoformat(),
                    "reason": row.get("reason"),
                    "required_action": row.get("review_when"),
                    "status": "unresolved",
                    "resolution": None,
                    "source_ids": [],
                }
            )
    return reviews


def body_specific_source_reviews(
    domain: str,
    body: str,
    meeting_date: date,
) -> list[dict]:
    normalized = re.sub(r"\s+", " ", body).strip()
    matched_scope: str | None = None
    for pattern, label in BODY_SPECIFIC_RULE_PATTERNS[domain]:
        if pattern.search(normalized):
            matched_scope = label
            break
    if matched_scope is None and body_matches_domain(normalized, domain):
        return []
    if matched_scope is None:
        matched_scope = "식별되지 않은 산하·자치기구"
    return [
        {
            "source_kind": "body_specific_rule_gap",
            "source_id": f"{domain}:body-specific-rules",
            "title": f"{normalized or '미상 회의체'} 자체 회칙·세칙",
            "matched_terms": [matched_scope],
            "matched_date_range": False,
            "meeting_date": meeting_date.isoformat(),
            "reason": (
                "The packaged corpus contains central umbrella rules but does not "
                "contain or verify this body's own governing rules."
            ),
            "required_action": (
                "Retrieve the body's current official rules and publication lineage, "
                "register the applicable provisions, and resolve any conflict with "
                "the central umbrella rules before substantive reliance."
            ),
            "status": "unresolved",
            "resolution": None,
            "source_ids": [],
        }
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a case and automatically retrieve supporting, adverse, and procedural candidates."
    )
    parser.add_argument("--agenda", required=True)
    parser.add_argument("--position", required=True)
    parser.add_argument("--body", default="unknown")
    parser.add_argument("--domain", default="auto", choices=["auto", *sorted(DOMAINS)])
    parser.add_argument("--meeting-date", default=date.today().isoformat())
    parser.add_argument("--known-fact", action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit-per-route", type=int, default=8)
    parser.add_argument("--index", type=Path, default=INDEX_PATH)
    args = parser.parse_args()
    date_value = date.fromisoformat(args.meeting_date)
    try:
        scope_context = (
            args.body
            if explicitly_identifies_yonsei(args.body)
            else " ".join([args.body, args.agenda, args.position, *args.known_fact])
        )
        domain = resolve_domain(args.body, args.domain, scope_context)
    except ValueError as exc:
        parser.error(str(exc))
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "create_case.py"),
        "--agenda",
        args.agenda,
        "--position",
        args.position,
        "--body",
        args.body,
        "--domain",
        domain,
        "--meeting-date",
        args.meeting_date,
    ]
    for fact in args.known_fact:
        command.extend(["--known-fact", fact])
    if args.output:
        command.extend(["--output", str(args.output)])
    created = subprocess.run(command, text=True, capture_output=True, check=False)
    if created.returncode != 0:
        sys.stderr.write(created.stderr)
        return created.returncode
    case_dir = Path(created.stdout.strip())
    source_reviews = excluded_source_reviews(
        domain,
        args.agenda,
        args.position,
        date_value,
        args.body,
    )
    source_reviews.extend(body_specific_source_reviews(domain, args.body, date_value))
    rows = [
        row
        for row in load_jsonl(args.index)
        if row.get("domain") == domain and effective_on(row, date_value)
    ]
    if not any(
        row.get("target") == "rule"
        and str(row.get("status") or "").startswith("current")
        for row in rows
    ):
        source_reviews.append(
            {
                "source_kind": "historical_corpus_gap",
                "source_id": f"{domain}:root-rule:{args.meeting_date}",
                "title": "Applicable domain root rule is absent from the corpus",
                "matched_terms": [],
                "matched_date_range": True,
                "meeting_date": args.meeting_date,
                "reason": (
                    "No current-status root rule in the packaged corpus is effective "
                    "on the requested meeting date."
                ),
                "required_action": (
                    "Retrieve and index the domain's root rule effective on this date "
                    "before relying on subordinate bylaws."
                ),
                "status": "unresolved",
                "resolution": None,
                "source_ids": [],
            }
        )
    routes = {
        "supporting": f"{args.agenda} {args.position}",
        "adverse": f"{args.agenda} 반대 예외 제한 무효 부결 철회",
        "procedure": f"{args.body} 정족수 의결 상정 재청 수정안 권한",
    }
    discoveries: dict[str, list[dict]] = {}
    registered: dict[str, dict] = {}
    for route, query in routes.items():
        candidates = score_rows(rows, query)[: max(args.limit_per_route, 0)]
        discoveries[route] = []
        for score, row in candidates:
            discoveries[route].append(
                {
                    "id": row["id"],
                    "score": round(score, 4),
                    "document_title": row["document_title"],
                    "article": row["article"],
                    "title": row["title"],
                    "page": row["page"],
                    "snippet": row["full_text"][:500],
                    "detail_retrieved": True,
                }
            )
            detailed = dict(row)
            detailed["registered_from_detail"] = True
            detailed["auto_candidate_routes"] = sorted(
                set(detailed.get("auto_candidate_routes", [])) | {route}
            )
            registered[row["id"]] = detailed
    foundational_by_domain = {
        "club_union": {
        "RULE-2025-09-23:4",
        "RULE-2025-09-23:9",
        "RULE-2025-09-23:10",
        "RULE-2025-09-23:13",
        "RULE-2025-09-23:14",
        "RULE-2025-09-23:17",
        "RULE-2025-09-23:149",
        "RULE-2025-09-23:150",
        "RULE-2025-09-23:151",
        "RULE-2025-09-23:152",
        "BYLAW-PROCEDURE-2023-03-07:2",
        "BYLAW-PROCEDURE-2023-03-07:3",
        "BYLAW-PROCEDURE-2023-03-07:7",
        "BYLAW-PROCEDURE-2023-03-07:9",
        "BYLAW-PROCEDURE-2023-03-07:9-2",
        "BYLAW-PROCEDURE-2023-03-07:12",
        },
        "student_council": {
            "SC-RULE-2025-09-11:5",
            "SC-RULE-2025-09-11:12",
            "SC-RULE-2025-09-11:13",
            "SC-RULE-2025-09-11:14",
            "SC-RULE-2025-09-11:15",
            "SC-RULE-2025-09-11:16",
            "SC-RULE-2025-09-11:17",
            "SC-RULE-2025-09-11:18",
            "SC-RULE-2025-09-11:19",
            "SC-RULE-2025-09-11:20",
            "SC-BYLAW-DELIBERATION-2025-03-31:1",
            "SC-BYLAW-DELIBERATION-2025-03-31:3",
            "SC-BYLAW-DELIBERATION-2025-03-31:7",
            "SC-BYLAW-DELIBERATION-2025-03-31:9",
            "SC-BYLAW-DELIBERATION-2025-03-31:15",
            "SC-BYLAW-DELIBERATION-2025-03-31:16",
        },
    }
    foundational_ids = foundational_by_domain[domain]
    row_by_id = {row["id"]: row for row in rows}
    discoveries["foundation"] = []
    for identifier in sorted(foundational_ids):
        if identifier not in row_by_id:
            continue
        row = row_by_id[identifier]
        discoveries["foundation"].append(
            {
                "id": row["id"],
                "document_title": row["document_title"],
                "article": row["article"],
                "title": row["title"],
                "page": row["page"],
                "detail_retrieved": True,
            }
        )
        detailed = dict(row)
        detailed["registered_from_detail"] = True
        detailed["auto_candidate_routes"] = sorted(
            set(detailed.get("auto_candidate_routes", [])) | {"foundation"}
        )
        registered[row["id"]] = detailed
    write_json(
        case_dir / "artifacts" / "candidate_search.json",
        {
            "prepared_at": utc_now(),
            "meeting_date": args.meeting_date,
            "governance_scope": GOVERNANCE_SCOPE,
            "governance_domain": domain,
            "routes": [{"name": name, "query": query} for name, query in routes.items()],
            "discoveries": discoveries,
            "warning": "Candidates are not conclusions. Build and validate the proposition ledger before drafting.",
        },
    )
    write_json(
        case_dir / "artifacts" / "source_gap_reviews.json",
        {
            "prepared_at": utc_now(),
            "governance_scope": GOVERNANCE_SCOPE,
            "governance_domain": domain,
            "reviews": source_reviews,
        },
    )
    write_jsonl(
        case_dir / "sources" / "sources.jsonl",
        sorted(registered.values(), key=lambda row: row["id"]),
    )
    state_path = case_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["updated_at"] = utc_now()
    state["status"] = (
        "SOURCE_REVIEW_REQUIRED" if source_reviews else "CANDIDATES_PREPARED"
    )
    state["current_phase"] = (
        "source_gap_review" if source_reviews else "source_detail"
    )
    state["progress"]["authority_search"] = "completed"
    state["progress"]["source_detail"] = "blocked" if source_reviews else "completed"
    state["progress"]["counter_search"] = "blocked" if source_reviews else "in_progress"
    state["candidate_source_count"] = len(registered)
    state["governance_scope"] = GOVERNANCE_SCOPE
    state["governance_domain"] = domain
    state["source_gap_review"] = {
        "passed": not source_reviews,
        "unresolved_count": len(source_reviews),
    }
    write_json(state_path, state)
    print(
        json.dumps(
            {
                "case_dir": str(case_dir.resolve()),
                "governance_scope": GOVERNANCE_SCOPE,
                "governance_domain": domain,
                "candidate_source_count": len(registered),
                "routes": {name: len(items) for name, items in discoveries.items()},
                "blocked": bool(source_reviews),
                "required_source_reviews": source_reviews,
                "next": (
                    "Retrieve, inspect, and register every triggered excluded source; "
                    "then mark source_gap_reviews.json resolved before argument validation."
                    if source_reviews
                    else "Review candidates, perform proposition-level counter-search, "
                    "and populate argument_ledger.jsonl."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2 if source_reviews else 0


if __name__ == "__main__":
    sys.exit(main())
