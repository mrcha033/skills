#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def gate_spec(value: str) -> tuple[str, str]:
    identifier, separator, label = value.partition(":")
    identifier = identifier.strip()
    label = label.strip()
    if not separator or not identifier or not label:
        raise argparse.ArgumentTypeError("gate must be ID:description")
    if not identifier.replace("-", "").replace("_", "").isalnum():
        raise argparse.ArgumentTypeError("gate ID must be alphanumeric with '-' or '_'")
    return identifier, label


def command_init(args: argparse.Namespace) -> int:
    if not 1 <= len(args.gate) <= 3:
        raise ValueError("declare one to three required gates")
    identifiers = [identifier for identifier, _ in args.gate]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("gate IDs must be unique")
    contract = {
        "schema_version": 1,
        "state": "active",
        "created_at": now(),
        "updated_at": now(),
        "objective": args.objective,
        "deliverable": args.deliverable,
        "max_attempts_per_gate": args.max_attempts,
        "gates": [
            {
                "id": identifier,
                "description": description,
                "status": "pending",
                "attempts": 0,
                "last_state_signature": None,
                "evidence": [],
            }
            for identifier, description in args.gate
        ],
        "backlog": args.backlog,
        "terminal_evidence": [],
        "history": [{"at": now(), "event": "initialized"}],
    }
    write(args.contract, contract)
    print(json.dumps({"created": str(args.contract), "next_gate": identifiers[0]}, ensure_ascii=False))
    return 0


def find_gate(contract: dict, identifier: str) -> dict:
    for gate in contract["gates"]:
        if gate["id"] == identifier:
            return gate
    raise ValueError(f"unknown gate: {identifier}")


def command_record(args: argparse.Namespace) -> int:
    contract = read(args.contract)
    if contract["state"] != "active":
        raise ValueError(f"contract is not active: {contract['state']}")
    gate = find_gate(contract, args.gate)
    if gate["status"] == "passed":
        raise ValueError(f"gate already passed: {args.gate}")
    if args.result == "fail" and not args.state_signature:
        raise ValueError("--state-signature is required for a failed attempt")
    if (
        args.result == "fail"
        and gate.get("last_state_signature") == args.state_signature
        and not args.strategy_change
    ):
        print(
            json.dumps(
                {
                    "recorded": False,
                    "reason": "identical_failed_state",
                    "required_action": "change relevant state or name a strategy change before retrying",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    gate["attempts"] += 1
    gate["evidence"].append(
        {
            "at": now(),
            "result": args.result,
            "evidence": args.evidence,
            "state_signature": args.state_signature,
            "strategy_change": args.strategy_change,
        }
    )
    gate["last_state_signature"] = args.state_signature
    gate["status"] = {"pass": "passed", "fail": "failed", "blocked": "blocked"}[args.result]
    if args.result == "blocked":
        contract["state"] = "blocked"
    elif args.result == "fail" and gate["attempts"] >= contract["max_attempts_per_gate"]:
        contract["state"] = "blocked"
        contract["blocking_reason"] = (
            f"gate {gate['id']} reached the declared attempt limit; change strategy or authority"
        )
    contract["updated_at"] = now()
    contract["history"].append(
        {"at": now(), "event": f"gate_{args.result}", "gate": gate["id"]}
    )
    write(args.contract, contract)
    print(
        json.dumps(
            {
                "recorded": True,
                "contract_state": contract["state"],
                "gate": gate["id"],
                "gate_status": gate["status"],
                "attempts": gate["attempts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if contract["state"] == "active" else 2


def status_payload(contract: dict) -> dict:
    pending = next((gate for gate in contract["gates"] if gate["status"] != "passed"), None)
    return {
        "state": contract["state"],
        "objective": contract["objective"],
        "deliverable": contract["deliverable"],
        "next_gate": pending,
        "passed_gates": [gate["id"] for gate in contract["gates"] if gate["status"] == "passed"],
        "backlog": contract["backlog"],
        "terminal_evidence": contract["terminal_evidence"],
    }


def command_status(args: argparse.Namespace) -> int:
    print(json.dumps(status_payload(read(args.contract)), ensure_ascii=False, indent=2))
    return 0


def command_complete(args: argparse.Namespace) -> int:
    contract = read(args.contract)
    unmet = [gate["id"] for gate in contract["gates"] if gate["status"] != "passed"]
    if unmet:
        print(json.dumps({"completed": False, "unmet_gates": unmet}, ensure_ascii=False, indent=2))
        return 2
    contract["state"] = "completed"
    contract["terminal_evidence"].extend(args.terminal_evidence)
    contract["updated_at"] = now()
    contract["history"].append({"at": now(), "event": "completed"})
    write(args.contract, contract)
    print(json.dumps({"completed": True, **status_payload(contract)}, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Create and enforce a bounded agent finish contract.")
    sub = root.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--contract", type=Path, required=True)
    init.add_argument("--objective", required=True)
    init.add_argument("--deliverable", required=True)
    init.add_argument("--gate", type=gate_spec, action="append", required=True)
    init.add_argument("--backlog", action="append", default=[])
    init.add_argument("--max-attempts", type=int, default=2, choices=range(1, 6))
    init.set_defaults(function=command_init)
    record = sub.add_parser("record")
    record.add_argument("--contract", type=Path, required=True)
    record.add_argument("--gate", required=True)
    record.add_argument("--result", required=True, choices=["pass", "fail", "blocked"])
    record.add_argument("--evidence", required=True)
    record.add_argument("--state-signature")
    record.add_argument("--strategy-change")
    record.set_defaults(function=command_record)
    status = sub.add_parser("status")
    status.add_argument("--contract", type=Path, required=True)
    status.set_defaults(function=command_status)
    complete = sub.add_parser("complete")
    complete.add_argument("--contract", type=Path, required=True)
    complete.add_argument("--terminal-evidence", action="append", required=True)
    complete.set_defaults(function=command_complete)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.function(args)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
