from __future__ import annotations

import importlib.util
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = SKILL_ROOT / "scripts" / "lab_slides.py"
SPEC = importlib.util.spec_from_file_location("lab_slides_visual_contract", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
lab_slides = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab_slides)


def slide(number: int, family: str = "process-flow", *, basis: object = None, underfill: bool = False) -> dict:
    spec = {
        "anchor_type": "process",
        "family": family,
        "content_basis": basis if basis is not None else [f"claim-{number}"],
        "allow_underfill": underfill,
        "underfill_rationale": "Intentional sparse transition with one evidence anchor." if underfill else "",
    }
    return {
        "slide": number,
        "template_frame": {"role": "content"},
        "visual_contract": spec,
        "native_elements": [
            {"type": "shape", "geometry": "rect", "position": {"x": 10, "y": 10, "w": 50, "h": 50}}
        ],
    }


def frame_map(count: int) -> dict:
    return {
        "outputSlides": [
            {
                "outputSlide": number,
                "editTargets": [
                    {"action": "add", "zone": {"x": 0, "y": 0, "w": 100, "h": 100}}
                ],
            }
            for number in range(1, count + 1)
        ]
    }


class VisualContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {"qa": {"visual_contract": dict(lab_slides.DEFAULT_VISUAL_CONTRACT)}}

    def test_content_anchor_and_coverage_pass(self) -> None:
        report = lab_slides.visual_contract_audit(
            self.config, {"slides": [slide(1)]}, frame_map(1)
        )
        self.assertEqual(report["status"], "pass", report)
        self.assertGreaterEqual(report["checks"][0]["body_coverage_ratio"], 0.16)

    def test_missing_anchor_and_basis_fail(self) -> None:
        entry = slide(1, basis=[])
        entry.pop("visual_contract")
        report = lab_slides.visual_contract_audit(self.config, {"slides": [entry]}, frame_map(1))
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("missing visual_contract" in error for error in report["errors"]))

    def test_forbidden_family_and_repetition_fail(self) -> None:
        plans = {"slides": [slide(1, "card-grid"), slide(2, "card-grid"), slide(3, "card-grid")]}
        report = lab_slides.visual_contract_audit(self.config, plans, frame_map(3))
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("forbidden generic" in error for error in report["errors"]))
        self.assertTrue(any("repeats composition family" in error for error in report["errors"]))

    def test_repeated_rectangular_panel_sequence_is_blocked(self) -> None:
        plans = {"slides": [slide(1, "comparison-panel"), slide(2, "comparison-panel"), slide(3, "comparison-panel")]}
        report = lab_slides.visual_contract_audit(self.config, plans, frame_map(3))
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("repeats composition family" in error for error in report["errors"]))
        self.assertTrue(any("composition signature" in error for error in report["errors"]))

    def test_sparse_slide_requires_explicit_exception(self) -> None:
        sparse = slide(1)
        sparse["native_elements"] = []
        report = lab_slides.visual_contract_audit(self.config, {"slides": [sparse]}, frame_map(1))
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("underfill_rationale" in error for error in report["errors"]))
        sparse["visual_contract"]["allow_underfill"] = True
        sparse["visual_contract"]["underfill_rationale"] = "Intentional sparse transition with one evidence anchor."
        report = lab_slides.visual_contract_audit(self.config, {"slides": [sparse]}, frame_map(1))
        self.assertEqual(report["status"], "pass", report)

    def test_user_acceptance_is_sha_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            final = Path(temp_name) / "final.pptx"
            final.write_bytes(b"deck")
            deck_sha = lab_slides.sha256_file(final)
            config = {"qa": {"user_acceptance": {
                "required": True,
                "status": "accepted",
                "reviewer": "lab member",
                "accepted_at": "2026-07-23T12:00:00+09:00",
                "reviewed_deck_sha256": deck_sha,
            }}}
            self.assertEqual(lab_slides.user_acceptance_errors(config, final, deck_sha), [])
            config["qa"]["user_acceptance"]["reviewed_deck_sha256"] = "stale"
            self.assertTrue(any("does not match" in error for error in lab_slides.user_acceptance_errors(config, final, deck_sha)))

    def test_qa_command_blocks_pending_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            project = Path(temp_name)
            final = project / "final.pptx"
            final.write_bytes(b"deck")
            config = {
                "schema_version": 2,
                "deck": {"final_pptx": str(final)},
                "style": {"allowed_fonts": []},
                "notes": {"enabled": False},
                "qa": {
                    "expected_slides": 1,
                    "compatibility_probe": False,
                    "require_template_fidelity": False,
                    "require_review_reports": False,
                    "user_acceptance": {"required": True, "status": "pending"},
                },
            }
            config_path = project / "labdeck.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            package = {
                "zip_ok": True,
                "corrupt_member": None,
                "slide_count": 1,
                "fonts": [],
                "empty_placeholder_shapes": 0,
                "visible_text": "",
            }
            inspection = {"slide_count": 1, "notes": [], "notes_sha256": "notes", "source_manifest_sha256": "source"}
            completed = mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch.object(lab_slides, "audit_config", return_value={"status": "pass", "errors": [], "warnings": [], "slide_plan_entries": 0, "claim_count": 0}), \
                mock.patch.object(lab_slides, "pptx_package_stats", return_value=package), \
                mock.patch.object(lab_slides, "inspect_with_artifact_tool", return_value=inspection), \
                mock.patch.object(lab_slides, "find_presentations_skill", return_value=SKILL_ROOT), \
                mock.patch.object(lab_slides, "find_python", return_value=Path("/usr/bin/python3")), \
                mock.patch.object(lab_slides, "run_command", return_value=completed), \
                mock.patch.object(lab_slides, "render_deck", return_value=(project / "render", project / "montage.png", "render", "source")):
                result = lab_slides.command_qa(argparse.Namespace(config=str(config_path), skip_review_gate=False))
            self.assertEqual(result, 2)
            report = json.loads((project / "reports" / "qa-report.json").read_text(encoding="utf-8"))
            self.assertTrue(any("visual acceptance is pending" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
