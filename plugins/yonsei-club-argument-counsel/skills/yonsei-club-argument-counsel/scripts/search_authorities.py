#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from _common import DOMAINS, INDEX_PATH, TARGETS, effective_on, load_jsonl, tokenize


def score_rows(rows: list[dict], query: str) -> list[tuple[float, dict]]:
    query_tokens = tokenize(query)
    base_tokens = [
        token
        for token in re.sub(r"[^0-9A-Za-z가-힣]+", " ", query.lower()).split()
        if len(token) > 1
    ]
    if not query_tokens:
        return []
    document_frequency: Counter[str] = Counter()
    row_tokens: list[list[str]] = []
    for row in rows:
        tokens = tokenize(
            " ".join(
                [
                    row.get("document_title", ""),
                    row.get("article", ""),
                    row.get("title", ""),
                    row.get("full_text", ""),
                ]
            )
        )
        row_tokens.append(tokens)
        document_frequency.update(set(tokens))
    average_length = sum(map(len, row_tokens)) / max(len(row_tokens), 1)
    scored: list[tuple[float, dict]] = []
    query_compact = "".join(query.split()).lower()
    for row, tokens in zip(rows, row_tokens):
        frequencies = Counter(tokens)
        length = len(tokens)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            if not frequency:
                continue
            inverse = math.log(1 + (len(rows) - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5))
            denominator = frequency + 1.2 * (0.25 + 0.75 * length / max(average_length, 1))
            score += inverse * (frequency * 2.2 / denominator)
        searchable = "".join(
            [row.get("article", ""), row.get("title", ""), row.get("full_text", "")]
        ).replace(" ", "").lower()
        if base_tokens and not any(token in searchable for token in base_tokens):
            continue
        if query_compact and query_compact in searchable:
            score += 8.0
        if score > 0:
            scored.append((score, row))
    return sorted(scored, key=lambda item: (-item[0], item[1]["id"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover relevant Yonsei governance authorities.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--domain", default="all", choices=["all", *sorted(DOMAINS)])
    parser.add_argument("--target", default="all", choices=["all", *sorted(TARGETS)])
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--index", type=Path, default=INDEX_PATH)
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of)
    rows = [
        row
        for row in load_jsonl(args.index)
        if (args.domain == "all" or row.get("domain") == args.domain)
        and (args.target == "all" or row.get("target") == args.target)
        and effective_on(row, as_of)
    ]
    scored = score_rows(rows, args.query)[: max(args.limit, 0)]
    results = []
    for score, row in scored:
        text = row["full_text"]
        results.append(
            {
                "id": row["id"],
                "document_id": row["document_id"],
                "document_title": row["document_title"],
                "domain": row["domain"],
                "target": row["target"],
                "article": row["article"],
                "title": row["title"],
                "page": row["page"],
                "score": round(score, 4),
                "snippet": text[:360] + ("…" if len(text) > 360 else ""),
                "content_verified": row["content_verified"],
                "detail_required_before_reliance": True,
            }
        )
    output = {
        "query": args.query,
        "domain": args.domain,
        "target": args.target,
        "as_of": args.as_of,
        "result_count": len(results),
        "results": results,
        "search_is_discovery_only": True,
    }
    if not results:
        output["warning"] = (
            "No indexed match was found. This does not establish that no rule exists; "
            "broaden the terms, search adjacent procedures/remedies, and inspect neighboring articles."
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
