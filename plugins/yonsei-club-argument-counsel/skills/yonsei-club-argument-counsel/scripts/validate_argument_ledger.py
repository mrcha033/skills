#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from _common import effective_on, load_json, load_jsonl, utc_now, write_json

TYPES = {"direct_rule", "interpretation", "fact", "precedent", "policy", "external_law"}


def source_origin(source: dict) -> str:
    return source.get("independent_origin") or urlparse(source.get("source_url") or source.get("url", "")).netloc


def classify(proposition: dict, sources: dict[str, dict], as_of: date) -> tuple[str, list[str]]:
    reasons: list[str] = []
    supporting = [sources[item] for item in proposition["supporting_source_ids"]]
    adverse = [sources[item] for item in proposition["adverse_source_ids"]]
    if proposition.get("counter_refuted"):
        return "refuted", ["counter-search found a credible refutation"]
    if proposition.get("conflicting"):
        return "blocked", ["unresolved source conflict"]
    applicable = [
        source
        for source in supporting
        if source.get("content_verified") and effective_on(source, as_of)
    ]
    if len(applicable) != len(supporting):
        reasons.append("one or more supporting sources are unverified or inapplicable on the case date")
    proposition_type = proposition["proposition_type"]
    if proposition_type == "policy":
        return "arguable", ["normative policy proposition, not a controlling rule"]
    if not applicable:
        return "blocked", reasons or ["no applicable content-verified support"]
    if proposition_type == "direct_rule":
        internal = [
            source
            for source in applicable
            if source.get("target") in {"rule", "bylaw", "regulation"}
            and str(source.get("status", "")).startswith("current")
        ]
        if not internal:
            return "blocked", reasons + ["no current internal authority"]
        if adverse:
            reasons.append("adverse authorities were registered and must be answered in the brief")
        return "verified", reasons or ["current, applicable, content-verified internal authority"]
    if proposition_type == "interpretation":
        adopted = [
            source
            for source in applicable
            if source.get("target") == "interpretation" and source.get("adopted_by_steering_committee") is True
        ]
        if adopted:
            return "verified", ["official interpretation adopted by the Steering Committee"]
        return "arguable", ["interpretation is not shown as officially adopted"]
    if proposition_type == "fact":
        if proposition.get("disputed"):
            origins = {source_origin(source) for source in applicable if source_origin(source)}
            if len(origins) < 2:
                return "blocked", ["disputed fact lacks two independent origins"]
        primary = [
            source
            for source in applicable
            if source.get("target") != "user_evidence" and source.get("quality") in {"A", "B"}
        ]
        if primary:
            return "verified", ["fact supported by content-verified primary evidence"]
        return "blocked", ["fact is supported only by user assertion or weak evidence"]
    if proposition_type == "precedent":
        if not proposition.get("similarity_basis", "").strip():
            return "blocked", ["precedent lacks a stated similarity basis"]
        if any(source.get("target") in {"minutes", "decision"} for source in applicable):
            return "arguable", ["historical practice can support analogy but is not automatically controlling"]
        return "blocked", ["no minutes or decision record supports the claimed precedent"]
    if proposition_type == "external_law":
        if any(source.get("target") == "external_law" for source in applicable):
            return "arguable", ["external law retrieved; internal applicability still requires a gap/hierarchy analysis"]
        return "blocked", ["no detailed external-law authority"]
    return "blocked", ["unsupported proposition type"]


def validate_shape(proposition: dict, number: int) -> list[str]:
    errors: list[str] = []
    required = {
        "proposition_id",
        "text",
        "proposition_type",
        "supports_conclusion",
        "supporting_source_ids",
        "adverse_source_ids",
        "counter_search",
    }
    missing = sorted(required - proposition.keys())
    if missing:
        errors.append(f"ledger line {number}: missing {missing}")
    if proposition.get("proposition_type") not in TYPES:
        errors.append(f"ledger line {number}: invalid proposition_type")
    for field in ("supporting_source_ids", "adverse_source_ids"):
        if field in proposition and not isinstance(proposition[field], list):
            errors.append(f"ledger line {number}: {field} must be a list")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministically validate an argument ledger.")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--as-of")
    args = parser.parse_args()
    input_data = load_json(args.case / "input.json")
    as_of = date.fromisoformat(args.as_of or input_data["meeting_date"])
    source_rows = load_jsonl(args.case / "sources" / "sources.jsonl")
    ledger = load_jsonl(args.case / "artifacts" / "argument_ledger.jsonl")
    sources = {row.get("id"): row for row in source_rows if row.get("id")}
    hard_errors: list[str] = []
    process_errors: list[str] = []
    results: list[dict] = []
    seen: set[str] = set()
    for number, proposition in enumerate(ledger, 1):
        hard_errors.extend(validate_shape(proposition, number))
        proposition_id = proposition.get("proposition_id", f"line_{number}")
        if proposition_id in seen:
            hard_errors.append(f"duplicate proposition_id: {proposition_id}")
        seen.add(proposition_id)
        referenced = proposition.get("supporting_source_ids", []) + proposition.get("adverse_source_ids", [])
        missing_sources = sorted(set(referenced) - sources.keys())
        if missing_sources:
            hard_errors.append(f"{proposition_id}: unregistered source IDs {missing_sources}")
        if not str(proposition.get("counter_search", "")).strip():
            process_errors.append(f"{proposition_id}: missing counter_search")
        if missing_sources or validate_shape(proposition, number):
            continue
        status, reasons = classify(proposition, sources, as_of)
        output = dict(proposition)
        output["status"] = status
        output["reasons"] = reasons
        results.append(output)
    if not ledger:
        hard_errors.append("argument ledger is empty")
    outputs = args.case / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    groups = {
        "verified": [row for row in results if row["status"] == "verified"],
        "arguable": [row for row in results if row["status"] == "arguable"],
        "blocked": [row for row in results if row["status"] == "blocked"],
        "refuted": [row for row in results if row["status"] == "refuted"],
    }
    for name, rows in groups.items():
        write_json(outputs / f"{name}_propositions.json", rows)
    canonical = json.dumps(results, ensure_ascii=False, sort_keys=True).encode("utf-8")
    signature = hashlib.sha256(canonical).hexdigest()
    passed = not hard_errors and not process_errors
    state_path = args.case / "state.json"
    state = load_json(state_path)
    state["updated_at"] = utc_now()
    state["verification"] = {
        "passed": passed,
        "signature": signature if passed else None,
        "as_of": as_of.isoformat(),
        "counts": {name: len(rows) for name, rows in groups.items()},
        "hard_errors": hard_errors,
        "process_errors": process_errors,
    }
    state["current_phase"] = "synthesis" if passed else "ledger_validation"
    state["progress"]["ledger_validation"] = "completed" if passed else "failed"
    write_json(state_path, state)
    print(
        json.dumps(
            {
                "passed": passed,
                "signature": signature if passed else None,
                "counts": {name: len(rows) for name, rows in groups.items()},
                "hard_errors": hard_errors,
                "process_errors": process_errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if hard_errors:
        return 2
    if process_errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
