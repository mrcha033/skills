#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import INDEX_PATH, load_jsonl, write_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description="Register content-verified indexed authorities in a case.")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--id", action="append", required=True)
    parser.add_argument("--index", type=Path, default=INDEX_PATH)
    args = parser.parse_args()
    index = {row["id"]: row for row in load_jsonl(args.index)}
    destination = args.case / "sources" / "sources.jsonl"
    registered = {row["id"]: row for row in load_jsonl(destination)}
    missing = [identifier for identifier in args.id if identifier not in index]
    if missing:
        print(json.dumps({"ok": False, "missing": missing}, ensure_ascii=False, indent=2))
        return 1
    for identifier in args.id:
        row = dict(index[identifier])
        row["registered_from_detail"] = True
        registered[identifier] = row
    write_jsonl(destination, sorted(registered.values(), key=lambda row: row["id"]))
    print(json.dumps({"ok": True, "registered": args.id, "total": len(registered)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
