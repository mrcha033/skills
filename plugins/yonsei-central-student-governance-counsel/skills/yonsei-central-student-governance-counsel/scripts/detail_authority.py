#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import INDEX_PATH, load_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieve a full, content-verified authority by identifier.")
    parser.add_argument("--id")
    parser.add_argument("--document-id")
    parser.add_argument("--article")
    parser.add_argument("--index", type=Path, default=INDEX_PATH)
    args = parser.parse_args()
    if not args.id and not (args.document_id and args.article):
        parser.error("provide --id or both --document-id and --article")
    rows = load_jsonl(args.index)
    matches = []
    for row in rows:
        if args.id and row["id"] == args.id:
            matches.append(row)
        elif not args.id and row["document_id"] == args.document_id and row["article"] == args.article:
            matches.append(row)
    if not matches:
        print(
            json.dumps(
                {
                    "found": False,
                    "error": "authority_not_found",
                    "message": "Search identifiers first; absence here does not prove absence of a rule.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "found": True,
                "result_count": len(matches),
                "authorities": matches,
                "reliance_allowed": all(row.get("content_verified") for row in matches),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
