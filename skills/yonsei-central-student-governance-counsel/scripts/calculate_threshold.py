#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate a deterministic voting or seconding threshold.")
    parser.add_argument("--basis", required=True, choices=["registered", "present", "voting", "units"])
    parser.add_argument("--count", required=True, type=int)
    parser.add_argument(
        "--rule",
        required=True,
        choices=["majority", "two-thirds", "one-third", "one-fifth", "three-quarters", "unanimous"],
    )
    args = parser.parse_args()
    if args.count < 0:
        parser.error("--count must be non-negative")
    formulas = {
        "majority": lambda n: math.floor(n / 2) + 1,
        "two-thirds": lambda n: math.ceil(2 * n / 3),
        "one-third": lambda n: math.ceil(n / 3),
        "one-fifth": lambda n: math.ceil(n / 5),
        "three-quarters": lambda n: math.ceil(3 * n / 4),
        "unanimous": lambda n: n,
    }
    required = formulas[args.rule](args.count) if args.count else 0
    print(
        json.dumps(
            {
                "basis": args.basis,
                "denominator": args.count,
                "rule": args.rule,
                "required": required,
                "note": "Confirm that the selected denominator matches the controlling provision.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
