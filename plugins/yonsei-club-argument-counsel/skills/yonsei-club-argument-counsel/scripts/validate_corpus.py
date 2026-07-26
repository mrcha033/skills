#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

from _common import (
    INDEX_PATH,
    MANIFEST_PATH,
    load_json,
    load_jsonl,
    resolve_skill_path,
    sha256_file,
    validate_pdf_bytes,
)


def main() -> int:
    manifest = load_json(MANIFEST_PATH)
    rows = load_jsonl(INDEX_PATH)
    errors: list[str] = []
    warnings: list[str] = []
    documents = manifest.get("documents", [])
    if manifest.get("schema_version") != 2:
        errors.append("unsupported manifest schema_version")
    identifiers = [row.get("id") for row in rows]
    if len(identifiers) != len(set(identifiers)):
        errors.append("article index contains duplicate IDs")
    document_ids = {document["document_id"] for document in documents}
    indexed_ids = {row.get("document_id") for row in rows}
    missing_documents = sorted(document_ids - indexed_ids)
    if missing_documents:
        errors.append(f"documents missing from index: {missing_documents}")
    for document in documents:
        if document.get("domain") not in {"club_union", "student_council"}:
            errors.append(f"{document.get('document_id')}: missing or invalid domain")
        pdf = resolve_skill_path(document["local_pdf"])
        text = resolve_skill_path(document["local_text"])
        if not pdf.exists():
            errors.append(f"missing PDF: {pdf}")
            continue
        data = pdf.read_bytes()
        ok, pdf_errors = validate_pdf_bytes(data)
        if not ok:
            errors.extend(f"{document['document_id']}: {error}" for error in pdf_errors)
        digest = sha256_file(pdf)
        if digest != document["expected_sha256"]:
            errors.append(f"{document['document_id']}: SHA-256 mismatch")
        if not text.exists() or len(text.read_text(encoding="utf-8")) < 200:
            errors.append(f"{document['document_id']}: missing or implausibly short extracted text")
        if str(document.get("status", "")).endswith("unconfirmed_after_compendium"):
            warnings.append(
                f"{document['document_id']}: official current listing exists, but no later standalone attachment "
                "was found; recheck the archive before substantive use"
            )
        if document.get("status") == "current_unversioned":
            warnings.append(
                f"{document['document_id']}: official current file has no version date; "
                "do not make date-specific claims from it without additional evidence"
            )
    required = {
        "RULE-2025-09-23:4",
        "RULE-2025-09-23:9",
        "RULE-2025-09-23:10",
        "RULE-2025-09-23:13",
        "RULE-2025-09-23:14",
        "RULE-2025-09-23:17",
        "RULE-2025-09-23:33",
        "RULE-2025-09-23:46",
        "RULE-2025-09-23:149",
        "RULE-2025-09-23:150",
        "RULE-2025-09-23:152",
        "BYLAW-PROCEDURE-2023-03-07:3",
        "BYLAW-PROCEDURE-2023-03-07:7",
        "BYLAW-PROCEDURE-2023-03-07:9-2",
        "BYLAW-PROCEDURE-2023-03-07:12",
        "BYLAW-PROCEDURE-2023-03-07:15",
        "SC-RULE-2025-09-11:5",
        "SC-RULE-2025-09-11:12",
        "SC-RULE-2025-09-11:13",
        "SC-RULE-2025-09-11:20",
        "SC-RULE-2025-09-11:49",
        "SC-RULE-2025-09-11:61",
        "SC-RULE-2025-09-11:100",
        "SC-RULE-2025-09-11:105",
        "SC-RULE-2025-09-11:176",
        "SC-RULE-2025-09-11:183",
        "SC-BYLAW-DELIBERATION-2025-03-31:1",
        "SC-BYLAW-DELIBERATION-2025-03-31:9",
        "SC-BYLAW-DELIBERATION-2025-03-31:15",
        "SC-BYLAW-LEGISLATION-2025-03-31:9",
        "SC-BYLAW-AUDIT-2025-09-08:9",
    }
    missing_required = sorted(required - set(identifiers))
    if missing_required:
        errors.append(f"required governance provisions missing: {missing_required}")
    for row in rows:
        if row.get("domain") not in {"club_union", "student_council"}:
            errors.append(f"{row.get('id')}: missing or invalid domain")
        if not row.get("content_verified") or not row.get("content_sha256"):
            errors.append(f"{row.get('id')}: unverified or unhashed article")
    report = {
        "passed": not errors,
        "documents": len(documents),
        "articles": len(rows),
        "domains": {
            domain: sum(1 for row in rows if row.get("domain") == domain)
            for domain in ("club_union", "student_council")
        },
        "errors": errors,
        "warnings": sorted(set(warnings)),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
