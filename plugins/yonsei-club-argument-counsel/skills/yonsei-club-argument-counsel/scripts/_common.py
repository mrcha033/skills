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


def resolve_domain(body: str, requested: str = "auto") -> str:
    if requested in DOMAINS:
        return requested
    if requested != "auto":
        raise ValueError(f"unsupported domain: {requested}")
    club = any(marker in body for marker in CLUB_BODY_MARKERS)
    student = any(marker in body for marker in STUDENT_COUNCIL_BODY_MARKERS)
    if club and not student:
        return "club_union"
    if student and not club:
        return "student_council"
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
