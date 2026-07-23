from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]


class HelperScriptTests(unittest.TestCase):
    def run_self_test(self, script_name: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(SKILL_ROOT / "scripts" / script_name), "--self-test"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_strict_template_fidelity_self_test(self) -> None:
        result = self.run_self_test("check_locked_template.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"status": "pass"', result.stdout)

    def test_native_rebuild_manifest_self_test(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SKILL_ROOT / "scripts" / "native_rebuild_manifest.py"),
                "self-test",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SELF-TEST PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
