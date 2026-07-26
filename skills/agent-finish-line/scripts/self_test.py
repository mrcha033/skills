#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("finish_contract.py")


def run(*args: str, expected: int = 0) -> dict:
    result = subprocess.run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True)
    if result.returncode != expected:
        raise AssertionError(f"{args}: {result.returncode}\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout or result.stderr)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="finish-line-test-") as temporary:
        contract = str(Path(temporary) / "contract.json")
        run(
            "init",
            "--contract",
            contract,
            "--objective",
            "ship parser",
            "--deliverable",
            "tested parser",
            "--gate",
            "build:parser exists",
            "--gate",
            "test:tests pass",
        )
        run("record", "--contract", contract, "--gate", "build", "--result", "pass", "--evidence", "file")
        run(
            "record",
            "--contract",
            contract,
            "--gate",
            "test",
            "--result",
            "fail",
            "--evidence",
            "assertion",
            "--state-signature",
            "suite:assertion:v1",
        )
        repeated = run(
            "record",
            "--contract",
            contract,
            "--gate",
            "test",
            "--result",
            "fail",
            "--evidence",
            "same assertion",
            "--state-signature",
            "suite:assertion:v1",
            expected=2,
        )
        assert repeated["reason"] == "identical_failed_state"
        run(
            "record",
            "--contract",
            contract,
            "--gate",
            "test",
            "--result",
            "pass",
            "--evidence",
            "suite passed",
            "--strategy-change",
            "fixed parser",
        )
        completed = run(
            "complete",
            "--contract",
            contract,
            "--terminal-evidence",
            "commit abc123",
        )
        assert completed["completed"] and completed["state"] == "completed"
    print(json.dumps({"passed": True, "checks": 4}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
