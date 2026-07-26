#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

SEARCH_TARGETS = {"law", "eflaw", "elaw", "prec", "detc", "expc", "admrul", "ordin", "trty", "lstrm"}


def request_json(path: str, params: dict[str, str]) -> dict:
    base = os.environ.get("KSKILL_PROXY_BASE_URL", "https://k-skill-proxy.nomadamas.org").rstrip("/")
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "yonsei-counsel/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()
        content_type = response.headers.get("Content-Type", "")
    if len(data) < 2 or ("json" not in content_type.lower() and data[:1] not in {b"{", b"["}):
        raise RuntimeError(f"invalid upstream body: content-type={content_type!r}, size={len(data)}")
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise RuntimeError("upstream returned non-JSON or challenge content") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Search and retrieve Korean law through the k-skill proxy.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    search = subparsers.add_parser("search")
    search.add_argument("--target", required=True, choices=sorted(SEARCH_TARGETS))
    search.add_argument("--query", required=True)
    search.add_argument("--display", default="20")
    detail = subparsers.add_parser("detail")
    detail.add_argument("--target", required=True, choices=sorted(SEARCH_TARGETS))
    detail.add_argument("--id", required=True)
    detail.add_argument("--article-code")
    args = parser.parse_args()
    try:
        if args.command == "search":
            payload = request_json(
                "/v1/korean-law/search",
                {"target": args.target, "query": args.query, "display": args.display},
            )
            output = {
                "mode": "search",
                "target": args.target,
                "query": args.query,
                "result": payload,
                "detail_required_before_reliance": True,
                "zero_results_do_not_prove_no_authority": True,
            }
        else:
            params = {"target": args.target, "ID": args.id}
            if args.article_code:
                params["JO"] = args.article_code
            payload = request_json("/v1/korean-law/detail", params)
            output = {
                "mode": "detail",
                "target": args.target,
                "id": args.id,
                "result": payload,
                "content_retrieved": True,
                "authority_scope": "external_korean_law_not_internal_union_rule",
            }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
