#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import re
import subprocess
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
from validate_source_lineage import LINEAGES_PATH, validate as validate_source_lineage


def download(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; YonseiClubArgumentCounsel/1.0)",
            "Accept": "application/pdf,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
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
    with urllib.request.urlopen(request, timeout=20) as response:
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


def check(manifest: dict, remote: bool) -> tuple[list[dict], bool, dict]:
    def check_document(document: dict) -> dict:
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
        return row

    documents = unique_documents(manifest)
    if remote:
        with ThreadPoolExecutor(max_workers=min(8, len(documents))) as executor:
            results = list(executor.map(check_document, documents))
    else:
        results = [check_document(document) for document in documents]
    integrity_passed = all(row["ok"] for row in results)
    catalog = {"checked": False, "passed": True, "result": None}
    if remote:
        try:
            archive = check_archive_freshness(manifest)
        except Exception as exc:
            archive = {"url": manifest["official_archive_url"], "ok": False, "errors": [str(exc)]}
        catalog = {"checked": True, "passed": archive["ok"], "result": archive}
        results.append({"document_id": "OFFICIAL-ARCHIVE-FRESHNESS", **archive})
    return results, integrity_passed, catalog


def check_live_lineage() -> dict:
    script = SKILL_DIR / "scripts" / "check_official_paths.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "exit_code": 20,
            "error": "official-path checks exceeded the 90-second global bound",
            "failure_gate": {
                "complete": False,
                "untried_routes": ["retry the bounded live-lineage check once"],
            },
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {
            "passed": False,
            "error": "check_official_paths.py returned invalid JSON",
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:],
        }
    payload["exit_code"] = result.returncode
    return payload


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
        "--live-lineage",
        action="store_true",
        help=(
            "Also trace the authority-controlled publication paths through deterministic "
            "public access with optional Insane Search fallback."
        ),
    )
    parser.add_argument(
        "--accept-changed",
        action="store_true",
        help="Download a changed official attachment after manual inspection; does not edit the manifest.",
    )
    args = parser.parse_args()
    if args.local_only and args.live_lineage:
        parser.error("--local-only and --live-lineage cannot be combined")
    manifest = load_json(MANIFEST_PATH)
    if args.check:
        if args.live_lineage:
            with ThreadPoolExecutor(max_workers=2) as executor:
                check_future = executor.submit(
                    check,
                    manifest,
                    not args.local_only,
                )
                lineage_future = executor.submit(check_live_lineage)
                results, integrity_passed, catalog = check_future.result()
                live_lineage = lineage_future.result()
        else:
            results, integrity_passed, catalog = check(
                manifest,
                remote=not args.local_only,
            )
            live_lineage = {
                "checked": False,
                "passed": None,
                "reason": "run --check --live-lineage before substantive current-rule use",
            }
        lineage_registry = validate_source_lineage(manifest, load_json(LINEAGES_PATH))
        remote = not args.local_only
        catalog_current = bool(catalog["passed"]) if catalog["checked"] else None
        lineage_current = (
            bool(live_lineage.get("passed")) if args.live_lineage else None
        )
        local_validation_passed = bool(
            integrity_passed and lineage_registry["passed"]
        )
        substantive_use_allowed = bool(
            remote
            and local_validation_passed
            and catalog_current is True
            and lineage_current is True
        )
        passed = bool(
            local_validation_passed
            and (
                not remote
                or (
                    catalog_current is True
                    and lineage_current is True
                )
            )
        )
        payload = {
            "checked_at": utc_now(),
            "passed": passed,
            "integrity_passed": integrity_passed,
            "catalog_current": catalog_current,
            "lineage_current": lineage_current,
            "substantive_use_allowed": substantive_use_allowed,
            "integrity": {"passed": integrity_passed},
            "catalog": catalog,
            "lineage": {
                "registry": lineage_registry,
                "live": live_lineage,
            },
            "results": results,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if passed else 1
    results = update(manifest, accept_changed=args.accept_changed)
    print(json.dumps({"updated_at": utc_now(), "skill_dir": str(SKILL_DIR), "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
