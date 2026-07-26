#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from _common import REFERENCES_DIR, SKILL_DIR, load_json, load_jsonl, write_jsonl
from adaptive_source_discovery import (
    AllowlistedRedirectHandler,
    DisallowedRedirectError,
    auth_is_terminal,
    encoded_public_url,
    evaluate_result,
    extract_candidates,
    failure_complete,
    filter_candidates,
    marker_in_url,
    url_host_allowed,
)
from check_official_paths import archive_entry_ids, drive_item_ids
from prepare_case import excluded_source_reviews


def run(script: str, *arguments: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / script), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"{script} returned {result.returncode}, expected {expected}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def main() -> int:
    checks: list[str] = []
    corpus = json.loads(run("validate_corpus.py").stdout)
    assert corpus["passed"] and corpus["articles"] >= 800
    assert corpus["source_lineage"]["passed"]
    assert corpus["domains"]["club_union"] > 300
    assert corpus["domains"]["student_council"] > 400
    checks.append("corpus")
    lineage = json.loads(run("validate_source_lineage.py").stdout)
    assert lineage["passed"] and lineage["covered_documents"] == lineage["documents"] == 15
    lineage_registry = load_json(REFERENCES_DIR / "source-lineages.json")
    student_lineage = next(
        row for row in lineage_registry["lineages"] if row["domain"] == "student_council"
    )
    excluded = student_lineage["excluded_artifacts"]
    assert len(excluded) == 1
    assert excluded[0]["file_id"] in student_lineage["catalog_expected_file_ids"]
    assert excluded[0]["reason"] and excluded[0]["review_when"]
    checks.append("source lineage")
    synthetic = """
    <html><head>
      <meta property="og:description"
            content="연세대학교 총학생회 회·세칙 공개 https://bit.ly/연세총학법제위_회세칙공개_2025">
    </head><body>
      <div data-id="1_wj2klJ67Erq2w1XnZXBOHxVJswElJrn"
           data-tooltip="총학생회 Shared folder"></div>
    </body></html>
    """
    candidates = extract_candidates(
        synthetic,
        "https://www.instagram.com/yonsei_legislation/p/example/",
        "총학생회 회세칙",
    )
    candidate_urls = {candidate["url"] for candidate in candidates}
    assert any("bit.ly/연세총학법제위_회세칙공개_2025" in url for url in candidate_urls)
    assert any("1_wj2klJ67Erq2w1XnZXBOHxVJswElJrn" in url for url in candidate_urls)
    assert marker_in_url(
        "bit.ly/연세총학법제위_회세칙공개_2025",
        "https://bit.ly/%EC%97%B0%EC%84%B8%EC%B4%9D%ED%95%99%EB%B2%95%EC%A0%9C%EC%9C%84_%ED%9A%8C%EC%84%B8%EC%B9%99%EA%B3%B5%EA%B0%9C_2025",
    )
    assert marker_in_url(
        "dongari.yonsei.ac.kr",
        "https://dongari.yonsei.ac.kr/kr/notice/data.php",
    )
    assert not marker_in_url(
        "dongari.yonsei.ac.kr",
        "https://evil.example/?next=dongari.yonsei.ac.kr",
    )
    assert marker_in_url("idx=97", "https://example.test/data.php?idx=97")
    assert not marker_in_url("idx=97", "https://example.test/data.php?idx=970")
    assert url_host_allowed(
        "https://drive.google.com/drive/folders/example",
        ["drive.google.com"],
    )
    assert not url_host_allowed(
        "https://evil.example/?next=drive.google.com",
        ["drive.google.com"],
    )
    assert not url_host_allowed("http://drive.google.com/", ["drive.google.com"])
    assert not url_host_allowed("https://drive.google.com/", [])
    filtered_candidates, rejected_hosts = filter_candidates(
        [
            {
                "url": "http://linktr.ee/yonseidongari",
                "label": "official link",
                "kind": "anchor",
                "score": 1,
            },
            {
                "url": "https://evil.example/?next=linktr.ee",
                "label": "bundled script lead",
                "kind": "raw_url",
                "score": 100,
            },
        ],
        ["linktr.ee"],
    )
    assert [row["url"] for row in filtered_candidates] == [
        "https://linktr.ee/yonseidongari"
    ]
    assert rejected_hosts == {"evil.example": 1}
    redirect_handler = AllowlistedRedirectHandler(["drive.google.com"])
    try:
        redirect_handler.redirect_request(
            type("RequestStub", (), {"full_url": "https://drive.google.com/start"})(),
            None,
            302,
            "Found",
            {},
            "https://evil.example/private",
        )
    except DisallowedRedirectError:
        pass
    else:
        raise AssertionError("off-allowlist redirect was not rejected")
    catalog_candidates = [
        {
            "url": "https://drive.google.com/open?id=1Gy0HFKKb-RfK-GpGE1hklhbGsXdiyU6b",
            "label": "의결기구에 관한 세칙",
            "kind": "drive_item",
        },
        {
            "url": "https://dongari.yonsei.ac.kr/kr/notice/data.php?bgu=view&idx=97",
            "label": "연세대학교 총동아리연합회칙(25.09.23.)",
            "kind": "anchor",
        },
        {
            "url": "https://dongari.yonsei.ac.kr/kr/notice/data.php?bgu=view&idx=83",
            "label": "총동아리연합회 선거 및 동아리 총투표 시행에 관한 세칙",
            "kind": "anchor",
        },
    ]
    assert drive_item_ids(catalog_candidates) == {
        "1Gy0HFKKb-RfK-GpGE1hklhbGsXdiyU6b"
    }
    assert archive_entry_ids(
        catalog_candidates,
        "(회칙|세칙|규정집)",
        ["dongari.yonsei.ac.kr"],
    ) == {"idx=83", "idx=97"}
    encoded = encoded_public_url("https://bit.ly/연세총학법제위_회세칙공개_2025")
    assert encoded.isascii()
    assert "%EC%97%B0%EC%84%B8" in encoded
    accepted = evaluate_result(
        {"ok": True, "verdict": "strong_ok", "final_url": "https://example.test/"},
        synthetic + "<script>window.recaptcha='incidental library string';</script>",
        ["회·세칙 공개"],
        False,
        [],
    )
    assert accepted["accepted"]
    assert accepted["challenge_markers_observed"] == ["recaptcha"]
    assert accepted["blocking_challenge_markers"] == []
    weak = evaluate_result(
        {"ok": True, "verdict": "weak_ok", "final_url": "https://example.test/"},
        synthetic,
        ["회·세칙 공개"],
        False,
        [],
    )
    assert not weak["accepted"]
    assert not failure_complete({"ok": True}, weak)
    pending = {
        "ok": False,
        "grid_exhausted": False,
        "untried_routes": ["playwright_mcp"],
        "must_invoke_playwright_mcp": True,
    }
    assert not failure_complete(pending, {"accepted": False})
    terminal = {
        "ok": False,
        "grid_exhausted": True,
        "untried_routes": [],
        "must_invoke_playwright_mcp": False,
    }
    assert failure_complete(terminal, {"accepted": False})
    auth_metadata = {"trace": [{"status": 401}]}
    assert auth_is_terminal(auth_metadata, "sign in to continue", {"required_text_matches": []})
    with tempfile.TemporaryDirectory(prefix="yonsei-no-insane-") as temporary:
        located = json.loads(
            run(
                "adaptive_source_discovery.py",
                "--locate-only",
                "--engine-dir",
                str(Path(temporary) / "missing"),
            ).stdout
        )
        assert located["passed"] and located["degraded"]
        assert located["runtime"]["stdlib_ready"]
    checks.append("adaptive discovery failure gate")
    local_refresh = json.loads(run("refresh_sources.py", "--check", "--local-only").stdout)
    assert local_refresh["passed"]
    assert local_refresh["integrity_passed"]
    assert local_refresh["catalog_current"] is None
    assert local_refresh["lineage_current"] is None
    assert not local_refresh["substantive_use_allowed"]
    checks.append("freshness abstention gate")
    rejected_scope_cases = (
        (
            "서울대학교 총학생회 중앙운영위원회",
            "outside this Yonsei-specific corpus",
        ),
        (
            "서울대 총학생회 중앙운영위원회",
            "outside this Yonsei-specific corpus",
        ),
        (
            "고려 대학교 총학생회 중앙운영위원회",
            "outside this Yonsei-specific corpus",
        ),
        (
            "Korea University General Student Council 중앙운영위원회",
            "outside this Yonsei-specific corpus",
        ),
        (
            "연세대학교 미래캠퍼스 총학생회 중앙운영위원회",
            "outside this skill's Sinchon/International Campus scope",
        ),
        (
            "연세대학교 미래 캠퍼스 총학생회 중앙운영위원회",
            "outside this skill's Sinchon/International Campus scope",
        ),
        (
            "연세대 미래캠 총학생회 중앙운영위원회",
            "outside this skill's Sinchon/International Campus scope",
        ),
        (
            "연세대 원주캠 총학생회 중앙운영위원회",
            "outside this skill's Sinchon/International Campus scope",
        ),
        (
            "연세대학교 대학원총학생회 중앙운영위원회",
            "outside this skill's undergraduate scope",
        ),
        (
            "연세대학교 대학원생 총학생회 중앙운영위원회",
            "outside this skill's undergraduate scope",
        ),
        (
            "연세대학교 교환학생 총학생회 중앙운영위원회",
            "outside this skill's supported central governance entities",
        ),
    )
    for body, expected_error in rejected_scope_cases:
        rejected = run(
            "prepare_case.py",
            "--agenda",
            "회의 안건 상정 절차",
            "--position",
            "상정할 수 있다",
            "--body",
            body,
            "--domain",
            "student_council",
            "--meeting-date",
            "2026-07-28",
            expected=2,
        )
        assert expected_error in rejected.stderr
    context_rejected = run(
        "prepare_case.py",
        "--agenda",
        "서울대학교 총학생회 회의 안건 상정 절차",
        "--position",
        "상정할 수 있다",
        "--body",
        "중앙운영위원회",
        "--domain",
        "student_council",
        "--meeting-date",
        "2026-07-28",
        expected=2,
    )
    assert "outside this Yonsei-specific corpus" in context_rejected.stderr
    for external_abbreviation_body in (
        "서울대 법제위원회",
        "서울대 감사위원회",
        "서울대 동아리운영위원회",
    ):
        external_abbreviation = run(
            "prepare_case.py",
            "--agenda",
            "회의 안건 상정 절차",
            "--position",
            "상정할 수 있다",
            "--body",
            external_abbreviation_body,
            "--meeting-date",
            "2026-07-28",
            expected=2,
        )
        assert "outside this Yonsei-specific corpus" in external_abbreviation.stderr
    mixed_english_context_rejected = run(
        "prepare_case.py",
        "--agenda",
        "Korea University and Yonsei University governance comparison",
        "--position",
        "상정할 수 있다",
        "--body",
        "중앙운영위원회",
        "--domain",
        "student_council",
        "--meeting-date",
        "2026-07-28",
        expected=2,
    )
    assert "outside this Yonsei-specific corpus" in mixed_english_context_rejected.stderr
    with tempfile.TemporaryDirectory(prefix="yonsei-direct-create-scope-test-") as temporary:
        direct_create_rejected = run(
            "create_case.py",
            "--agenda",
            "회의 안건 상정 절차",
            "--position",
            "상정할 수 있다",
            "--body",
            "서울대학교 총학생회 중앙운영위원회",
            "--domain",
            "student_council",
            "--meeting-date",
            "2026-07-28",
            "--output",
            str(Path(temporary) / "case"),
            expected=2,
        )
        assert "outside this Yonsei-specific corpus" in direct_create_rejected.stderr
        assert not (Path(temporary) / "case").exists()
    with tempfile.TemporaryDirectory(prefix="yonsei-explicit-scope-test-") as temporary:
        explicit_yonsei = json.loads(
            run(
                "prepare_case.py",
                "--agenda",
                "서울대학교 사례를 참고한 회의 안건 상정 절차",
                "--position",
                "연세대학교 회칙에 따라 상정할 수 있다",
                "--body",
                "연세대학교 중앙운영위원회",
                "--domain",
                "student_council",
                "--meeting-date",
                "2026-07-28",
                "--output",
                str(Path(temporary) / "case"),
            ).stdout
        )
        assert explicit_yonsei["governance_domain"] == "student_council"
    for body, requested_domain in (
        ("동아리운영위원회", "student_council"),
        ("중앙운영위원회", "club_union"),
    ):
        conflicting = run(
            "prepare_case.py",
            "--agenda",
            "회의 안건 상정 절차",
            "--position",
            "상정할 수 있다",
            "--body",
            body,
            "--domain",
            requested_domain,
            "--meeting-date",
            "2026-07-28",
            expected=2,
        )
        assert "conflicts with requested domain" in conflicting.stderr
    checks.append("institution campus and member scope guard")
    with tempfile.TemporaryDirectory(prefix="yonsei-branch-body-gap-test-") as temporary:
        branch_case = Path(temporary) / "case"
        branch = json.loads(
            run(
                "prepare_case.py",
                "--agenda",
                "정기회의 안건 상정 절차",
                "--position",
                "상정할 수 있다",
                "--body",
                "연세대학교 체육분과위원회 운영위원회",
                "--meeting-date",
                "2026-07-28",
                "--output",
                str(branch_case),
                expected=2,
            ).stdout
        )
        assert branch["blocked"]
        assert any(
            row["source_id"] == "idx=80"
            for row in branch["required_source_reviews"]
        )
    checks.append("branch body source-gap trigger")
    with tempfile.TemporaryDirectory(prefix="yonsei-subordinate-body-gap-test-") as temporary:
        subordinate_case = Path(temporary) / "case"
        subordinate = json.loads(
            run(
                "prepare_case.py",
                "--agenda",
                "운영위원회 안건 상정 절차",
                "--position",
                "상정할 수 있다",
                "--body",
                "연세대학교 공과대학 학생회 운영위원회",
                "--domain",
                "student_council",
                "--meeting-date",
                "2026-07-28",
                "--output",
                str(subordinate_case),
                expected=2,
            ).stdout
        )
        assert subordinate["blocked"]
        assert any(
            row["source_kind"] == "body_specific_rule_gap"
            for row in subordinate["required_source_reviews"]
        )
    checks.append("subordinate body own-rule gate")
    with tempfile.TemporaryDirectory(prefix="yonsei-club-subordinate-gap-test-") as temporary:
        club_subordinate_case = Path(temporary) / "case"
        club_subordinate = json.loads(
            run(
                "prepare_case.py",
                "--agenda",
                "정기회의 안건 상정 절차",
                "--position",
                "상정할 수 있다",
                "--body",
                "연세대학교 총동아리연합회 소속 중앙동아리 ABC",
                "--meeting-date",
                "2026-07-28",
                "--output",
                str(club_subordinate_case),
                expected=2,
            ).stdout
        )
        assert any(
            row["source_kind"] == "body_specific_rule_gap"
            for row in club_subordinate["required_source_reviews"]
        )
    checks.append("club subordinate own-rule gate")
    for body, domain in (
        ("연세대학교 총학생회 산하 학생인권위원회", "student_council"),
        ("연세대학교 총동아리연합회 산하 미디어위원회", "club_union"),
    ):
        with tempfile.TemporaryDirectory(prefix="yonsei-generic-subordinate-gap-test-") as temporary:
            generic_subordinate = json.loads(
                run(
                    "prepare_case.py",
                    "--agenda",
                    "정기회의 안건 상정 절차",
                    "--position",
                    "상정할 수 있다",
                    "--body",
                    body,
                    "--domain",
                    domain,
                    "--meeting-date",
                    "2026-07-28",
                    "--output",
                    str(Path(temporary) / "case"),
                    expected=2,
                ).stdout
            )
            assert any(
                row["source_kind"] == "body_specific_rule_gap"
                for row in generic_subordinate["required_source_reviews"]
            )
    checks.append("generic subordinate own-rule gate")
    with tempfile.TemporaryDirectory(prefix="yonsei-club-central-body-test-") as temporary:
        club_central_case = Path(temporary) / "case"
        club_central = json.loads(
            run(
                "prepare_case.py",
                "--agenda",
                "법제위원회 안건 상정 절차",
                "--position",
                "상정할 수 있다",
                "--body",
                "연세대학교 총동아리연합회 법제위원회",
                "--meeting-date",
                "2026-07-28",
                "--output",
                str(club_central_case),
            ).stdout
        )
        assert club_central["governance_domain"] == "club_union"
    checks.append("umbrella-qualified central body routing")
    with tempfile.TemporaryDirectory(prefix="yonsei-source-gap-test-") as temporary:
        blocked_case = Path(temporary) / "case"
        blocked = json.loads(
            run(
                "prepare_case.py",
                "--agenda",
                "중앙운영위원회 폭력 사건 징계 및 피해자 보호",
                "--position",
                "반폭력 자치규약을 적용해 제재해야 한다",
                "--body",
                "중앙운영위원회",
                "--domain",
                "student_council",
                "--meeting-date",
                "2026-07-28",
                "--output",
                str(blocked_case),
                expected=2,
            ).stdout
        )
        assert blocked["blocked"]
        assert any(
            row["source_id"] == "1Ctjw1h8M_AN9gIAo1ycB64olARczPOfS"
            for row in blocked["required_source_reviews"]
        )
        blocked_state = load_json(blocked_case / "state.json")
        assert blocked_state["status"] == "SOURCE_REVIEW_REQUIRED"
        assert blocked_state["source_gap_review"]["unresolved_count"] == 1
        blocked_validation = json.loads(
            run(
                "validate_argument_ledger.py",
                "--case",
                str(blocked_case),
                expected=2,
            ).stdout
        )
        assert any(
            "unresolved triggered source review" in error
            for error in blocked_validation["hard_errors"]
        )
    checks.append("excluded-source agenda trigger")
    with tempfile.TemporaryDirectory(prefix="yonsei-historical-gap-test-") as temporary:
        historical_case = Path(temporary) / "case"
        historical = json.loads(
            run(
                "prepare_case.py",
                "--agenda",
                "동아리대표자회의 의결 절차",
                "--position",
                "당시 유효한 회칙을 적용해야 한다",
                "--body",
                "동아리대표자회의",
                "--domain",
                "club_union",
                "--meeting-date",
                "2025-06-01",
                "--output",
                str(historical_case),
                expected=2,
            ).stdout
        )
        assert historical["blocked"]
        assert any(
            row["source_id"] == "idx=81" and row["matched_date_range"]
            for row in historical["required_source_reviews"]
        )
    checks.append("historical-version agenda trigger")
    for historical_day in ("2025-09-22", "2025-09-23", "2025-09-24"):
        boundary_reviews = excluded_source_reviews(
            "club_union",
            "동아리대표자회의 의결 절차",
            "당시 유효한 회칙을 적용해야 한다",
            date.fromisoformat(historical_day),
        )
        assert any(row["source_id"] == "idx=81" for row in boundary_reviews)
    current_boundary_reviews = excluded_source_reviews(
        "club_union",
        "동아리대표자회의 의결 절차",
        "현행 회칙을 적용해야 한다",
        date.fromisoformat("2025-09-25"),
    )
    assert not any(row["source_id"] == "idx=81" for row in current_boundary_reviews)
    checks.append("historical-version date boundaries")
    with tempfile.TemporaryDirectory(prefix="yonsei-root-rule-gap-test-") as temporary:
        root_gap_case = Path(temporary) / "case"
        root_gap = json.loads(
            run(
                "prepare_case.py",
                "--agenda",
                "중앙운영위원회 의결 절차",
                "--position",
                "당시 유효한 총학생회칙을 적용해야 한다",
                "--body",
                "중앙운영위원회",
                "--domain",
                "student_council",
                "--meeting-date",
                "2025-01-01",
                "--output",
                str(root_gap_case),
                expected=2,
            ).stdout
        )
        assert any(
            row["source_kind"] == "historical_corpus_gap"
            for row in root_gap["required_source_reviews"]
        )
    checks.append("historical root-rule gap")
    search = json.loads(
        run(
            "search_authorities.py",
            "--query",
            "수정안 재청 동아리운영위원회",
            "--as-of",
            "2026-07-23",
            "--limit",
            "5",
        ).stdout
    )
    assert any(row["id"] == "BYLAW-PROCEDURE-2023-03-07:9-2" for row in search["results"])
    checks.append("search")
    student_search = json.loads(
        run(
            "search_authorities.py",
            "--query",
            "중앙운영위원회 안건 정족수 수정안",
            "--domain",
            "student_council",
            "--as-of",
            "2026-07-23",
            "--limit",
            "10",
        ).stdout
    )
    assert any(row["id"] == "SC-RULE-2025-09-11:57" for row in student_search["results"])
    assert all(row["domain"] == "student_council" for row in student_search["results"])
    checks.append("student-council domain search")
    detail = json.loads(
        run("detail_authority.py", "--id", "BYLAW-PROCEDURE-2023-03-07:9-2").stdout
    )
    assert detail["found"] and detail["reliance_allowed"]
    checks.append("detail")
    empty = json.loads(run("search_authorities.py", "--query", "zzqxvvnnmm").stdout)
    assert empty["result_count"] == 0
    assert "warning" in empty
    checks.append("zero-result guard")
    threshold = json.loads(
        run("calculate_threshold.py", "--basis", "present", "--count", "17", "--rule", "majority").stdout
    )
    assert threshold["required"] == 9
    checks.append("threshold")
    with tempfile.TemporaryDirectory(prefix="yonsei-counsel-test-") as temporary:
        case = Path(temporary) / "case"
        prepared = json.loads(
            run(
            "prepare_case.py",
            "--agenda",
            "동아리운영위원회 수정안 재청 요건",
            "--position",
            "출석 단위 3분의 1의 재청이 필요하다",
            "--body",
            "동아리운영위원회",
            "--meeting-date",
            "2026-07-28",
            "--output",
            str(case),
            ).stdout
        )
        assert prepared["candidate_source_count"] > 0
        registered_ids = {
            row["id"] for row in load_jsonl(case / "sources" / "sources.jsonl")
        }
        assert "BYLAW-PROCEDURE-2023-03-07:9-2" in registered_ids
        assert "RULE-2025-09-23:17" in registered_ids
        checks.append("automatic case preparation")
        proposition = {
            "proposition_id": "p_001",
            "text": "동아리운영위원회 수정안에는 출석 단위 3분의 1 이상의 재청이 필요하다.",
            "proposition_type": "direct_rule",
            "supports_conclusion": True,
            "supporting_source_ids": [
                "BYLAW-PROCEDURE-2023-03-07:9-2",
                "RULE-2025-09-23:17",
            ],
            "adverse_source_ids": [],
            "inference": "회칙의 위임에 따른 절차 세칙을 적용한다.",
            "applicability": "2026-07-28 동아리운영위원회",
            "counter_search": "수정안, 재청, 예외, 완화 규정을 역방향 검색했으나 반대 규정을 찾지 못했다.",
            "counter_refuted": False,
            "conflicting": False,
            "disputed": False,
        }
        ledger_path = case / "artifacts" / "argument_ledger.jsonl"
        write_jsonl(ledger_path, [proposition])
        valid = json.loads(run("validate_argument_ledger.py", "--case", str(case)).stdout)
        assert valid["passed"] and valid["counts"]["verified"] == 1
        brief = case / "outputs" / "argument_brief.md"
        brief.write_text(
            "수정안에는 출석 단위 3분의 1 이상의 재청이 필요합니다. "
            "[P:p_001] [S:BYLAW-PROCEDURE-2023-03-07:9-2] "
            "[S:RULE-2025-09-23:17]\n",
            encoding="utf-8",
        )
        evaluated = json.loads(
            run("eval_argument_brief.py", "--case", str(case), "--brief", str(brief)).stdout
        )
        assert evaluated["verdict"] == "PASS"
        proposition["counter_search"] = ""
        write_jsonl(ledger_path, [proposition])
        invalid = json.loads(
            run("validate_argument_ledger.py", "--case", str(case), expected=1).stdout
        )
        assert not invalid["passed"] and invalid["process_errors"]
        checks.extend(["positive ledger gate", "brief leak gate", "negative counter-search gate"])
    with tempfile.TemporaryDirectory(prefix="yonsei-student-council-test-") as temporary:
        case = Path(temporary) / "case"
        prepared = json.loads(
            run(
                "prepare_case.py",
                "--agenda",
                "중앙운영위원회 수정안 의결 요건",
                "--position",
                "적법한 상정과 표결 절차가 필요하다",
                "--body",
                "중앙운영위원회",
                "--meeting-date",
                "2026-07-28",
                "--output",
                str(case),
            ).stdout
        )
        assert prepared["governance_domain"] == "student_council"
        registered = load_jsonl(case / "sources" / "sources.jsonl")
        assert registered and all(row["domain"] == "student_council" for row in registered)
        assert any(row["id"] == "SC-RULE-2025-09-11:12" for row in registered)
        checks.append("jurisdiction-routed case preparation")
    print(json.dumps({"passed": True, "checks": checks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
