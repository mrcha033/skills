#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

from _common import (
    MANIFEST_PATH,
    SKILL_DIR,
    load_json,
    resolve_skill_path,
    sha256_bytes,
    sha256_file,
    utc_now,
    validate_pdf_bytes,
)


def download(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; YonseiClubArgumentCounsel/1.0)",
            "Accept": "application/pdf,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()
        content_type = response.headers.get("Content-Type", "")
    ok, errors = validate_pdf_bytes(data)
    if "pdf" not in content_type.lower() and not data.startswith(b"%PDF"):
        errors.append(f"unexpected content type: {content_type}")
    if not ok or errors:
        raise RuntimeError("; ".join(errors))
    return data


def check_archive_freshness(manifest: dict) -> dict:
    url = manifest["official_archive_url"]
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; YonseiClubArgumentCounsel/1.0)"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    text = data.decode(charset, errors="replace")
    lowered = text.lower()
    errors: list[str] = []
    if len(text) < 3000:
        errors.append(f"implausibly short archive body: {len(text)} characters")
    for marker in ("access denied", "just a moment", "captcha"):
        if marker in lowered:
            errors.append(f"challenge marker found: {marker}")
    matches = re.findall(r"총동아리연합회칙\((\d{2})\.(\d{2})\.(\d{2})\.?\)", text)
    dates = sorted(f"20{year}-{month}-{day}" for year, month, day in matches)
    current_rule = next(
        item
        for item in manifest["documents"]
        if item["domain"] == "club_union"
        and item["target"] == "rule"
        and item["status"] == "current"
    )
    newest = dates[-1] if dates else None
    if not newest:
        errors.append("could not find any dated rules entry in the official archive")
    elif newest != current_rule["adopted_on"]:
        errors.append(
            f"official archive latest dated rules entry is {newest}, "
            f"but manifest records {current_rule['adopted_on']}"
        )
    return {"url": url, "latest_rules_date": newest, "ok": not errors, "errors": errors}


def unique_documents(manifest: dict) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    results: list[dict] = []
    for document in manifest["documents"]:
        key = (document["download_url"], document["local_pdf"])
        if key not in seen:
            seen.add(key)
            results.append(document)
    return results


def check(manifest: dict, remote: bool) -> tuple[list[dict], bool]:
    results: list[dict] = []
    passed = True
    for document in unique_documents(manifest):
        path = resolve_skill_path(document["local_pdf"])
        row = {
            "document_id": document["document_id"],
            "path": str(path),
            "exists": path.exists(),
            "expected_sha256": document["expected_sha256"],
        }
        if path.exists():
            row["local_sha256"] = sha256_file(path)
            row["local_match"] = row["local_sha256"] == document["expected_sha256"]
        else:
            row["local_match"] = False
        if remote:
            try:
                data = download(document["download_url"])
                row["remote_sha256"] = sha256_bytes(data)
                row["remote_match"] = row["remote_sha256"] == document["expected_sha256"]
            except Exception as exc:
                row["remote_error"] = str(exc)
                row["remote_match"] = False
        row["ok"] = bool(row["local_match"] and (not remote or row.get("remote_match")))
        passed = passed and row["ok"]
        results.append(row)
    if remote:
        try:
            archive = check_archive_freshness(manifest)
        except Exception as exc:
            archive = {"url": manifest["official_archive_url"], "ok": False, "errors": [str(exc)]}
        passed = passed and archive["ok"]
        results.append({"document_id": "OFFICIAL-ARCHIVE-FRESHNESS", **archive})
    return results, passed


def update(manifest: dict, accept_changed: bool) -> list[dict]:
    results: list[dict] = []
    for document in unique_documents(manifest):
        data = download(document["download_url"])
        digest = sha256_bytes(data)
        expected = document["expected_sha256"]
        if digest != expected and not accept_changed:
            raise RuntimeError(
                f"{document['document_id']}: official attachment changed "
                f"({expected} -> {digest}); inspect it and rerun with --accept-changed"
            )
        path = resolve_skill_path(document["local_pdf"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        temporary.replace(path)
        results.append(
            {
                "document_id": document["document_id"],
                "path": str(path),
                "sha256": digest,
                "changed_from_manifest": digest != expected,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or refresh official Yonsei governance PDFs.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Validate local copies and remote attachment hashes.")
    group.add_argument("--update", action="store_true", help="Download verified official attachments.")
    parser.add_argument("--local-only", action="store_true", help="Skip remote checks with --check.")
    parser.add_argument(
        "--accept-changed",
        action="store_true",
        help="Download a changed official attachment after manual inspection; does not edit the manifest.",
    )
    args = parser.parse_args()
    manifest = load_json(MANIFEST_PATH)
    if args.check:
        results, passed = check(manifest, remote=not args.local_only)
        print(json.dumps({"checked_at": utc_now(), "passed": passed, "results": results}, ensure_ascii=False, indent=2))
        return 0 if passed else 1
    results = update(manifest, accept_changed=args.accept_changed)
    print(json.dumps({"updated_at": utc_now(), "skill_dir": str(SKILL_DIR), "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
