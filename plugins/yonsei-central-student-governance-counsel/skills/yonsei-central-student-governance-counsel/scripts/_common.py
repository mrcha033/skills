#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SKILL_DIR = Path(__file__).resolve().parent.parent
REFERENCES_DIR = SKILL_DIR / "references"
MANIFEST_PATH = REFERENCES_DIR / "source-manifest.json"
INDEX_PATH = REFERENCES_DIR / "article-index.jsonl"

TARGETS = {
    "rule",
    "bylaw",
    "regulation",
    "interpretation",
    "minutes",
    "decision",
    "notice",
    "agenda",
    "external_law",
    "user_evidence",
}

DOMAINS = {"club_union", "student_council"}

GOVERNANCE_SCOPE = {
    "institution_id": "yonsei",
    "institution_name": "연세대학교",
    "campuses": ["sinchon", "international"],
    "member_scope": "undergraduate",
    "governance_domains": ["club_union", "student_council"],
}

CLUB_BODY_MARKERS = (
    "총동아리연합회",
    "동아리운영위원회",
    "동아리대표자회의",
    "동아리총회",
    "분과위원회",
)

STUDENT_COUNCIL_BODY_MARKERS = (
    "총학생회",
    "중앙운영위원회",
    "확대운영위원회",
    "학생총회",
    "학생총투표",
    "법제위원회",
    "감사위원회",
)

SUPPORTED_INSTITUTION_ALIASES = {"연세대학교", "연세대", "연대"}
UNSUPPORTED_CAMPUS_MARKERS = ("미래캠퍼스", "원주캠퍼스", "미래캠", "원주캠")
UNSUPPORTED_ENTITY_MARKERS = (
    "교환학생총학생회",
    "유학생총학생회",
    "외국인학생총학생회",
)
EXPLICIT_UNIVERSITY_RE = re.compile(
    r"(?<![0-9A-Za-z가-힣·])([0-9A-Za-z가-힣·]+대학교)"
)
EXPLICIT_ENGLISH_UNIVERSITY_RE = re.compile(
    r"(?<![A-Za-z])([A-Za-z][A-Za-z&.'-]*\s+University)(?![A-Za-z])",
    re.IGNORECASE,
)
ABBREVIATED_UNIVERSITY_RE = re.compile(
    r"(?<![0-9A-Za-z가-힣·])([0-9A-Za-z가-힣·]{2,20}대)\s*"
    r"(?=(?:총학생회|총동아리연합회|동아리운영위원회|동아리대표자회의|"
    r"동아리총회|분과위원회|중앙운영위원회|확대운영위원회|학생총회|"
    r"학생총투표|법제위원회|감사위원회))"
)
NAMED_EXTERNAL_INSTITUTION_RE = re.compile(
    r"(?<![0-9A-Za-z])(KAIST|POSTECH|UNIST|GIST|DGIST)(?![0-9A-Za-z])",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: invalid JSON: {exc}") from exc
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(body, encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def effective_on(record: dict[str, Any], as_of: date) -> bool:
    start = parse_date(record.get("effective_from"))
    end = parse_date(record.get("effective_to"))
    return (start is None or start <= as_of) and (end is None or as_of <= end)


def validate_governance_scope(body: str) -> None:
    normalized = re.sub(r"\s+", " ", body).strip()
    compact = re.sub(r"\s+", "", normalized)
    institution_normalized = re.sub(
        r"(?<![0-9A-Za-z가-힣·])([0-9A-Za-z가-힣·]+)\s+대학교",
        r"\1대학교",
        normalized,
    )
    for marker in UNSUPPORTED_CAMPUS_MARKERS:
        if marker in compact:
            raise ValueError(
                f"{marker} is outside this skill's Sinchon/International Campus scope"
            )
    if "대학원" in compact:
        raise ValueError(
            "graduate student governance is outside this skill's undergraduate scope"
        )
    for marker in UNSUPPORTED_ENTITY_MARKERS:
        if marker in compact:
            raise ValueError(
                f"{marker} is outside this skill's supported central governance entities"
            )
    named_external = NAMED_EXTERNAL_INSTITUTION_RE.search(normalized)
    if named_external:
        raise ValueError(
            f"{named_external.group(1)} is outside this Yonsei-specific governance corpus"
        )
    institutions = set(EXPLICIT_UNIVERSITY_RE.findall(institution_normalized))
    english_institutions = {
        re.sub(r"\s+", " ", match).strip()
        for match in EXPLICIT_ENGLISH_UNIVERSITY_RE.findall(normalized)
    }
    abbreviations = {
        match
        for match in ABBREVIATED_UNIVERSITY_RE.findall(normalized)
        if not re.fullmatch(r"제\d+대", match)
    }
    explicit_names = institutions | abbreviations
    unsupported = sorted(explicit_names - SUPPORTED_INSTITUTION_ALIASES)
    unsupported_english = sorted(
        name
        for name in english_institutions
        if not name.lower().endswith("yonsei university")
    )
    if unsupported or unsupported_english:
        raise ValueError(
            "meeting body identifies an institution outside this Yonsei-specific corpus: "
            + ", ".join([*unsupported, *unsupported_english])
        )


def explicitly_identifies_yonsei(value: str) -> bool:
    compact = re.sub(r"\s+", "", value).lower()
    return (
        "연세대학교" in compact
        or "연세대" in compact
        or re.search(
            r"(?<![0-9A-Za-z가-힣·])연대"
            r"(?=(?:총학생회|총동아리연합회|중앙운영위원회|확대운영위원회|학생총회|학생총투표))",
            compact,
        )
        is not None
        or "yonseiuniversity" in compact
    )


def matching_domains(body: str) -> set[str]:
    club_umbrella = "총동아리연합회" in body
    student_umbrella = "총학생회" in body
    if club_umbrella and student_umbrella:
        return {"club_union", "student_council"}
    if club_umbrella:
        return {"club_union"}
    if student_umbrella:
        return {"student_council"}
    matches: set[str] = set()
    if any(marker in body for marker in CLUB_BODY_MARKERS):
        matches.add("club_union")
    if any(marker in body for marker in STUDENT_COUNCIL_BODY_MARKERS):
        matches.add("student_council")
    return matches


def body_matches_domain(body: str, domain: str) -> bool:
    if domain not in DOMAINS:
        raise ValueError(f"unsupported domain: {domain}")
    return domain in matching_domains(body)


def resolve_domain(
    body: str,
    requested: str = "auto",
    scope_context: str | None = None,
) -> str:
    validate_governance_scope(scope_context or body)
    matches = matching_domains(body)
    if requested in DOMAINS:
        if len(matches) > 1:
            raise ValueError(
                "meeting body identifies both governance domains; provide a single "
                "competent body rather than forcing --domain"
            )
        if matches and requested not in matches:
            raise ValueError(
                f"meeting body conflicts with requested domain {requested}: "
                + ", ".join(sorted(matches))
            )
        return requested
    if requested != "auto":
        raise ValueError(f"unsupported domain: {requested}")
    if len(matches) == 1:
        return next(iter(matches))
    raise ValueError(
        "meeting body does not identify one controlling domain; "
        "specify --domain club_union or --domain student_council"
    )


def normalize_text(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\ufeff", "")
    value = re.sub(r"-\s*\d+\s*-", " ", value)
    value = re.sub(r"[\t\r\n ]+", " ", value)
    return value.strip()


def tokenize(value: str) -> list[str]:
    compact = re.sub(r"[^0-9A-Za-z가-힣]+", " ", value.lower())
    tokens = [token for token in compact.split() if len(token) > 1]
    joined = "".join(tokens)
    if len(joined) >= 2:
        tokens.extend(joined[i : i + 2] for i in range(len(joined) - 1))
    return tokens


def validate_pdf_bytes(data: bytes) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not data.startswith(b"%PDF"):
        errors.append("missing PDF signature")
    if len(data) < 10_000:
        errors.append(f"implausibly small payload: {len(data)} bytes")
    head = data[:8192].lower()
    for marker in (b"<html", b"access denied", b"just a moment", b"captcha"):
        if marker in head:
            errors.append(f"challenge/HTML marker found: {marker.decode(errors='ignore')}")
    return not errors, errors


def resolve_skill_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return SKILL_DIR / path
