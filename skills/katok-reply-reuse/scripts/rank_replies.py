#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^0-9A-Za-z가-힣 ]+", " ", value.lower())).strip()


def tokens(value: str) -> set[str]:
    return {part for part in normalize(value).split() if part}


def bigrams(value: str) -> Counter[str]:
    compact = normalize(value).replace(" ", "")
    return Counter(compact[index : index + 2] for index in range(max(0, len(compact) - 1)))


def counter_dice(left: Counter[str], right: Counter[str]) -> float:
    overlap = sum((left & right).values())
    total = sum(left.values()) + sum(right.values())
    return 2 * overlap / total if total else 0.0


def score(query: str, record: dict) -> float:
    target = f"{record.get('context', '')} {record.get('intent', '')}"
    query_tokens = tokens(query)
    target_tokens = tokens(target)
    union = query_tokens | target_tokens
    jaccard = len(query_tokens & target_tokens) / len(union) if union else 0.0
    return round(0.55 * counter_dice(bigrams(query), bigrams(target)) + 0.45 * jaccard, 6)


def load_records(path: Path) -> list[dict]:
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        missing = {"id", "context", "reply", "author_is_user"} - set(record)
        if missing:
            raise ValueError(f"line {number}: missing {sorted(missing)}")
        if record["author_is_user"] is not True:
            continue
        records.append(record)
    return records


def slots(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        key, separator, replacement = value.partition("=")
        if not separator or not key:
            raise ValueError("slot must be key=value")
        result[key] = replacement
    return result


def substitute(reply: str, replacements: dict[str, str]) -> tuple[str, list[str]]:
    changed = []
    for key, value in replacements.items():
        marker = "{{" + key + "}}"
        if marker in reply:
            reply = reply.replace(marker, value)
            changed.append(key)
    return reply, changed


def rank(path: Path, query: str, limit: int, threshold: float, replacements: dict[str, str]) -> dict:
    ranked = sorted(
        ((score(query, record), record) for record in load_records(path)),
        key=lambda item: (-item[0], str(item[1]["id"])),
    )
    candidates = []
    for value, record in ranked[: max(0, limit)]:
        reply, changed = substitute(record["reply"], replacements)
        candidates.append(
            {
                "id": record["id"],
                "score": value,
                "strong_match": value >= threshold,
                "mode": "slot_fill" if changed else "exact",
                "reply": reply,
                "changed_slots": changed,
                "timestamp": record.get("timestamp"),
                "chat_label": record.get("chat_label"),
            }
        )
    strong = [candidate for candidate in candidates if candidate["strong_match"]]
    return {
        "query": query,
        "threshold": threshold,
        "candidate_count": len(candidates),
        "recommended": strong[0] if strong else None,
        "candidates": candidates,
        "fallback": None,
        "abstained": not strong,
    }


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="reply-reuse-test-") as temporary:
        path = Path(temporary) / "candidates.jsonl"
        rows = [
            {
                "id": "mine-1",
                "context": "오늘 저녁 약속 몇 시에 만날까",
                "reply": "{{time}}쯤 보자! 장소는 전에 갔던 데 어때",
                "author_is_user": True,
                "timestamp": "2026-01-01T12:00:00+09:00",
            },
            {
                "id": "other-1",
                "context": "오늘 저녁 약속",
                "reply": "타인의 답장",
                "author_is_user": False,
            },
        ]
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        result = rank(path, "오늘 저녁 몇 시에 만날까", 3, 0.2, {"time": "7시"})
        assert result["recommended"]["id"] == "mine-1"
        assert result["recommended"]["reply"].startswith("7시")
        assert all(row["id"] != "other-1" for row in result["candidates"])
        none = rank(path, "논문 심사 결과가 나왔어", 3, 0.8, {})
        assert none["abstained"] and none["fallback"] is None
    print(json.dumps({"passed": True, "checks": 4}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank authorized user-authored reply exemplars.")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--query")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=0.28)
    parser.add_argument("--slot", action="append", default=[])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.input or args.query is None:
        parser.error("--input and --query are required unless --self-test is used")
    try:
        result = rank(args.input, args.query, args.limit, args.threshold, slots(args.slot))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
