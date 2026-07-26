#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from _common import SKILL_DIR, load_jsonl, write_jsonl


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
    assert corpus["domains"]["club_union"] > 300
    assert corpus["domains"]["student_council"] > 400
    checks.append("corpus")
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
