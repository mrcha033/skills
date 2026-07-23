from __future__ import annotations

import json
from pathlib import Path
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SOURCE_EVALS_PATH = SKILL_ROOT / "evals" / "evals.json"
PACKAGED_EVALS_PATH = SKILL_ROOT / "tests" / "fixtures" / "evals.json"
EVALS_PATH = SOURCE_EVALS_PATH if SOURCE_EVALS_PATH.exists() else PACKAGED_EVALS_PATH


class EvalMetadataTests(unittest.TestCase):
    def test_packaged_eval_fixture_matches_source_when_both_exist(self) -> None:
        if SOURCE_EVALS_PATH.exists():
            self.assertEqual(
                json.loads(SOURCE_EVALS_PATH.read_text(encoding="utf-8")),
                json.loads(PACKAGED_EVALS_PATH.read_text(encoding="utf-8")),
            )

    def test_eval_metadata_is_complete_and_references_existing_inputs(self) -> None:
        metadata = json.loads(EVALS_PATH.read_text(encoding="utf-8"))

        self.assertEqual(metadata["skill_name"], "build-lab-meeting-slides")
        self.assertEqual(len(metadata["evals"]), 3)
        ids = [case["id"] for case in metadata["evals"]]
        self.assertEqual(len(ids), len(set(ids)))
        for case in metadata["evals"]:
            self.assertTrue(case["prompt"].strip())
            self.assertTrue(case["expected_output"].strip())
            self.assertGreaterEqual(len(case["expectations"]), 5)
            for relative_path in case["files"]:
                self.assertTrue((SKILL_ROOT / relative_path).is_file(), relative_path)

    def test_eval_set_covers_all_three_authoring_routes(self) -> None:
        prompts = "\n".join(
            case["prompt"] + "\n" + case["expected_output"]
            for case in json.loads(EVALS_PATH.read_text(encoding="utf-8"))["evals"]
        ).casefold()

        self.assertIn("exact clone/edit", prompts)
        self.assertIn("12-slide", prompts)
        self.assertIn("html-assisted-native", prompts)


if __name__ == "__main__":
    unittest.main()
