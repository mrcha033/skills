#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from _common import MANIFEST_PATH, REFERENCES_DIR, load_json


LINEAGES_PATH = REFERENCES_DIR / "source-lineages.json"
ROOT_ROLES = {"official_profile", "official_site"}


def drive_file_id(url: str) -> str | None:
    parsed = urlparse(url)
    values = parse_qs(parsed.query).get("id")
    return values[0] if values else None


def validate(manifest: dict, registry: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    if registry.get("schema_version") != 1:
        errors.append("source-lineages.json schema_version must be 1")
    documents = {row["document_id"]: row for row in manifest.get("documents", [])}
    if not documents:
        errors.append("source manifest contains no documents")

    lineage_ids: set[str] = set()
    covered_by: dict[str, str] = {}
    node_urls: set[str] = set()
    domain_counts: dict[str, int] = {}
    for lineage in registry.get("lineages", []):
        lineage_id = lineage.get("lineage_id")
        domain = lineage.get("domain")
        if not lineage_id or lineage_id in lineage_ids:
            errors.append(f"missing or duplicate lineage_id: {lineage_id!r}")
            continue
        lineage_ids.add(lineage_id)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        allowed_hosts = set(lineage.get("allowed_hosts") or [])
        if not allowed_hosts:
            errors.append(f"{lineage_id}: allowed_hosts must not be empty")
        nodes = lineage.get("nodes") or []
        lineage_node_urls = {
            str(node.get("url") or "").rstrip("/") for node in nodes
        }
        node_map = {node.get("node_id"): node for node in nodes if node.get("node_id")}
        if len(node_map) != len(nodes):
            errors.append(f"{lineage_id}: missing or duplicate node_id")
        roots = [node for node in nodes if node.get("parent_id") is None]
        if len(roots) != 1:
            errors.append(f"{lineage_id}: expected exactly one root node")
        elif roots[0].get("role") not in ROOT_ROLES:
            errors.append(f"{lineage_id}: root must be an authority-controlled anchor")
        if not any(node.get("role") == "repository" for node in nodes):
            errors.append(f"{lineage_id}: no repository node")

        for node in nodes:
            node_id = node.get("node_id")
            parent = node.get("parent_id")
            url = node.get("url", "")
            if parent is not None and parent not in node_map:
                errors.append(f"{lineage_id}:{node_id}: unknown parent_id {parent!r}")
            if not url.startswith("https://"):
                errors.append(f"{lineage_id}:{node_id}: URL must use https")
            elif urlparse(url).hostname not in allowed_hosts:
                errors.append(
                    f"{lineage_id}:{node_id}: host {urlparse(url).hostname!r} "
                    "is outside allowed_hosts"
                )
            if not node.get("positive_evidence"):
                errors.append(f"{lineage_id}:{node_id}: positive_evidence is required")
            if str(node.get("role") or "").startswith("non_rules_"):
                if node.get("reliance_allowed") is not False:
                    errors.append(
                        f"{lineage_id}:{node_id}: non-rules node must set "
                        "reliance_allowed=false"
                    )
            node_urls.add(url.rstrip("/"))

            visited: set[str] = set()
            cursor = node
            while cursor.get("parent_id") is not None:
                cursor_id = cursor.get("node_id")
                if cursor_id in visited:
                    errors.append(f"{lineage_id}:{node_id}: parent cycle detected")
                    break
                visited.add(cursor_id)
                cursor = node_map.get(cursor.get("parent_id"), {})
                if not cursor:
                    break

        for document_id in lineage.get("covered_document_ids", []):
            if document_id not in documents:
                errors.append(f"{lineage_id}: unknown covered document {document_id}")
                continue
            if document_id in covered_by:
                errors.append(
                    f"{document_id}: covered by both {covered_by[document_id]} and {lineage_id}"
                )
            covered_by[document_id] = lineage_id
            if documents[document_id].get("domain") != domain:
                errors.append(f"{document_id}: lineage domain does not match manifest")
            for field in ("source_page_url", "download_url"):
                document_url = documents[document_id].get(field)
                if document_url and urlparse(document_url).hostname not in allowed_hosts:
                    errors.append(
                        f"{document_id}: {field} host "
                        f"{urlparse(document_url).hostname!r} is outside allowed_hosts"
                    )

        artifact_ids = lineage.get("artifact_file_ids") or {}
        for document_id, expected_file_id in artifact_ids.items():
            document = documents.get(document_id)
            if not document:
                errors.append(f"{lineage_id}: artifact ID references unknown document {document_id}")
                continue
            actual_file_id = drive_file_id(document.get("download_url", ""))
            if actual_file_id != expected_file_id:
                errors.append(
                    f"{document_id}: Drive file ID mismatch "
                    f"({actual_file_id!r} != {expected_file_id!r})"
                )

        expected_catalog_ids = set(lineage.get("catalog_expected_file_ids") or [])
        excluded_rows = lineage.get("excluded_artifacts") or []
        excluded_ids: set[str] = set()
        for excluded in excluded_rows:
            file_id = excluded.get("file_id")
            if not file_id or file_id in excluded_ids:
                errors.append(f"{lineage_id}: excluded artifact has missing or duplicate file_id")
                continue
            excluded_ids.add(file_id)
            if not excluded.get("reason") or not excluded.get("review_when"):
                errors.append(
                    f"{lineage_id}:{file_id}: exclusion requires reason and review_when"
                )
            if not excluded.get("trigger_terms"):
                errors.append(
                    f"{lineage_id}:{file_id}: excluded artifact requires trigger_terms"
                )
        if expected_catalog_ids:
            classified = set(artifact_ids.values()) | excluded_ids
            if expected_catalog_ids != classified:
                missing_classification = sorted(expected_catalog_ids - classified)
                stale_classification = sorted(classified - expected_catalog_ids)
                if missing_classification:
                    errors.append(
                        f"{lineage_id}: unclassified catalog file IDs: {missing_classification}"
                    )
                if stale_classification:
                    errors.append(
                        f"{lineage_id}: classified file IDs absent from expected catalog: "
                        f"{stale_classification}"
                    )

        artifact_entry_ids = lineage.get("artifact_entry_ids") or {}
        for document_id, expected_entry_id in artifact_entry_ids.items():
            document = documents.get(document_id)
            if not document:
                errors.append(
                    f"{lineage_id}: archive entry references unknown document {document_id}"
                )
                continue
            parsed_source = urlparse(document.get("source_page_url", ""))
            actual_values = parse_qs(parsed_source.query).get("idx") or []
            actual_entry_id = f"idx={actual_values[0]}" if actual_values else None
            if actual_entry_id != expected_entry_id:
                errors.append(
                    f"{document_id}: archive entry mismatch "
                    f"({actual_entry_id!r} != {expected_entry_id!r})"
                )

        expected_entry_ids = set(lineage.get("catalog_expected_entry_ids") or [])
        excluded_entry_rows = lineage.get("excluded_catalog_entries") or []
        excluded_entry_ids: set[str] = set()
        for excluded in excluded_entry_rows:
            entry_id = excluded.get("entry_id")
            if not entry_id or entry_id in excluded_entry_ids:
                errors.append(
                    f"{lineage_id}: excluded catalog entry has missing or duplicate entry_id"
                )
                continue
            excluded_entry_ids.add(entry_id)
            if not excluded.get("reason") or not excluded.get("review_when"):
                errors.append(
                    f"{lineage_id}:{entry_id}: exclusion requires reason and review_when"
                )
            if not (
                excluded.get("trigger_terms")
                or excluded.get("effective_from")
                or excluded.get("effective_to")
            ):
                errors.append(
                    f"{lineage_id}:{entry_id}: excluded catalog entry requires "
                    "trigger_terms or an effective date boundary"
                )
        if expected_entry_ids:
            classified_entries = set(artifact_entry_ids.values()) | excluded_entry_ids
            if expected_entry_ids != classified_entries:
                missing_classification = sorted(expected_entry_ids - classified_entries)
                stale_classification = sorted(classified_entries - expected_entry_ids)
                if missing_classification:
                    errors.append(
                        f"{lineage_id}: unclassified catalog entries: "
                        f"{missing_classification}"
                    )
                if stale_classification:
                    errors.append(
                        f"{lineage_id}: classified entries absent from expected catalog: "
                        f"{stale_classification}"
                    )

        live_expected_markers: set[str] = set()
        live_exact_drive_ids: set[str] = set()
        live_exact_archive_sets: list[set[str]] = []
        for check in lineage.get("live_checks", []):
            check_id = check.get("check_id")
            url = check.get("url", "").rstrip("/")
            if not check_id or not check.get("url"):
                errors.append(f"{lineage_id}: live check requires check_id and url")
            elif urlparse(check["url"]).hostname not in allowed_hosts:
                errors.append(
                    f"{lineage_id}:{check_id}: live-check host "
                    f"{urlparse(check['url']).hostname!r} is outside allowed_hosts"
                )
            if url not in lineage_node_urls:
                warnings.append(
                    f"{lineage_id}:{check_id}: live-check URL is a derived probe, not a registry node"
                )
            positive = (
                check.get("selectors")
                or check.get("require_any_text")
                or check.get("expect_final_url_contains")
            )
            if not positive:
                errors.append(f"{lineage_id}:{check_id}: no positive acceptance proof")
            live_expected_markers.update(check.get("expect_candidate_contains") or [])
            live_exact_drive_ids.update(
                check.get("catalog_exact_drive_item_ids") or []
            )
            if "catalog_exact_archive_entry_ids" in check:
                if not check.get("catalog_title_pattern"):
                    errors.append(
                        f"{lineage_id}:{check_id}: exact archive inventory "
                        "requires catalog_title_pattern"
                    )
                live_exact_archive_sets.append(
                    set(check.get("catalog_exact_archive_entry_ids") or [])
                )
        undiscovered_catalog_ids = sorted(
            file_id
            for file_id in expected_catalog_ids
            if not any(file_id in marker for marker in live_expected_markers)
        )
        if undiscovered_catalog_ids:
            errors.append(
                f"{lineage_id}: live checks do not enumerate catalog file IDs: "
                f"{undiscovered_catalog_ids}"
            )
        exact_unchecked_file_ids = sorted(expected_catalog_ids - live_exact_drive_ids)
        if exact_unchecked_file_ids:
            errors.append(
                f"{lineage_id}: catalog file IDs lack exact-inventory checks: "
                f"{exact_unchecked_file_ids}"
            )
        if expected_entry_ids and expected_entry_ids not in live_exact_archive_sets:
            errors.append(
                f"{lineage_id}: no live archive check exactly enumerates "
                "catalog_expected_entry_ids"
            )

    missing = sorted(set(documents) - set(covered_by))
    if missing:
        errors.append(f"documents without source lineage: {', '.join(missing)}")
    extra = sorted(set(covered_by) - set(documents))
    if extra:
        errors.append(f"lineage covers unknown documents: {', '.join(extra)}")

    required_manifest_nodes = {
        "student_council_official_profile_url": "student-council official profile",
        "student_council_rules_publication_post_url": "student-council rules publication post",
        "student_council_rules_reaffirmation_post_url": "student-council rules reaffirmation post",
        "student_council_rules_shortlink_url": "student-council rules shortlink",
        "student_council_current_linktree_url": "student-council current non-rules Linktree",
        "student_council_drive_url": "student-council Drive",
        "official_archive_url": "club-union archive",
    }
    for field, label in required_manifest_nodes.items():
        value = manifest.get(field)
        if not value:
            errors.append(f"manifest {label} is missing")
        elif value.rstrip("/") not in node_urls:
            errors.append(f"manifest {label} is absent from the lineage graph")

    return {
        "schema_version": 1,
        "passed": not errors,
        "documents": len(documents),
        "covered_documents": len(covered_by),
        "lineages": len(lineage_ids),
        "domains": domain_counts,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    manifest = load_json(MANIFEST_PATH)
    registry = load_json(LINEAGES_PATH)
    result = validate(manifest, registry)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
