from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = SKILL_ROOT / "scripts" / "lab_slides.py"
SPEC = importlib.util.spec_from_file_location("lab_slides_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
lab_slides = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab_slides)


def sample_profile() -> dict:
    safe_zone = {"x": 71.36, "y": 109.42, "w": 1137.22, "h": 545.01}
    return {
        "schema_version": 1,
        "slide_count": 2,
        "slides": [
            {
                "source_slide": 1,
                "role": "cover",
                "title_target": {"id": "sh/cover-title", "name": "PlaceHolder 1"},
                "subtitle_target": {"id": "sh/cover-subtitle", "name": "PlaceHolder 2"},
                "eyebrow_target": {"id": "sh/cover-eyebrow", "name": "Meeting Subject"},
                "safe_content_zone": None,
            },
            {
                "source_slide": 2,
                "role": "content",
                "title_target": {"id": "sh/content-title", "name": "PlaceHolder 1"},
                "subtitle_target": None,
                "eyebrow_target": None,
                "safe_content_zone": safe_zone,
            },
        ],
    }


def slide_entry(number: int, role: str, source_slide: int) -> dict:
    return {
        "slide": number,
        "narrative_job": "opening" if role == "cover" else "mechanism evidence",
        "title_claim": f"Slide {number}",
        "template_frame": {
            "role": role,
            "source_slide": source_slide,
            "routing_rationale": f"Use the supplied {role} frame.",
        },
        "visual_anchor": "One native editable mechanism",
        "evidence_refs": [],
        "notes_ref": f"slide-{number}",
    }


class TemplateFrameMapTests(unittest.TestCase):
    def test_content_default_add_is_bounded_by_inherited_safe_zone(self) -> None:
        frame = sample_profile()["slides"][1]

        targets = lab_slides.default_edit_targets(frame)

        additions = [target for target in targets if target["action"] == "add"]
        self.assertEqual(len(additions), 1)
        addition = additions[0]
        self.assertEqual(addition["zone"], frame["safe_content_zone"])
        self.assertIs(addition["newPrimitiveAllowed"], True)
        self.assertIs(addition["mustNotOverlapInherited"], True)
        self.assertTrue(addition["reason"].strip())
        self.assertEqual(addition["contentRef"], "body")

    def test_cover_default_targets_rewrite_only(self) -> None:
        targets = lab_slides.default_edit_targets(sample_profile()["slides"][0])

        self.assertEqual([target["action"] for target in targets], ["rewrite", "rewrite", "rewrite"])
        self.assertEqual(
            [target["contentRef"] for target in targets],
            ["title_claim", "subtitle", "meeting_subject"],
        )

    def test_longer_deck_reuses_only_the_declared_content_frame(self) -> None:
        plan = {
            "slides": [
                slide_entry(1, "cover", 1),
                slide_entry(2, "content", 2),
                slide_entry(3, "content", 2),
                slide_entry(4, "content", 2),
            ]
        }

        frame_map = lab_slides.build_template_frame_map(sample_profile(), plan)

        self.assertEqual(
            [entry["sourceSlide"] for entry in frame_map["outputSlides"]],
            [1, 2, 2, 2],
        )
        self.assertTrue(all(entry["reuseMode"] == "duplicate-slide" for entry in frame_map["outputSlides"]))
        self.assertEqual(frame_map["omittedSourceSlides"], [])
        for entry in frame_map["outputSlides"][1:]:
            insertion = next(target for target in entry["editTargets"] if target["action"] == "add")
            self.assertEqual(insertion["zone"], sample_profile()["slides"][1]["safe_content_zone"])

    def test_nonsequential_output_slides_are_rejected(self) -> None:
        plan = {"slides": [slide_entry(1, "cover", 1), slide_entry(3, "content", 2)]}

        with self.assertRaisesRegex(lab_slides.LabDeckError, "sequential from 1"):
            lab_slides.build_template_frame_map(sample_profile(), plan)

    def test_unknown_source_slide_is_rejected(self) -> None:
        plan = {"slides": [slide_entry(1, "cover", 99)]}

        with self.assertRaisesRegex(lab_slides.LabDeckError, "no source slide 99"):
            lab_slides.build_template_frame_map(sample_profile(), plan)


class RoleAwareNoteRangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "notes": {
                "minimum_words": 30,
                "maximum_words": 190,
                "role_ranges": {
                    "cover": [20, 80],
                    "divider": [20, 90],
                    "closing": [30, 100],
                    "appendix": [40, 120],
                    "mechanism": [90, 190],
                    "evidence": [90, 190],
                    "default": [60, 170],
                },
            }
        }

    def test_cover_uses_shorter_range(self) -> None:
        entry = {"template_frame": {"role": "cover"}, "narrative_job": "opening thesis"}
        self.assertEqual(lab_slides.notes_range_for_slide(self.config, entry), (20, 80))

    def test_mechanism_and_evidence_roles_use_full_explanation_range(self) -> None:
        mechanism = {"template_frame": {"role": "content"}, "narrative_job": "mechanism walkthrough"}
        evidence = {"template_frame": {"role": "content"}, "narrative_job": "evidence and limitations"}
        self.assertEqual(lab_slides.notes_range_for_slide(self.config, mechanism), (90, 190))
        self.assertEqual(lab_slides.notes_range_for_slide(self.config, evidence), (90, 190))

    def test_default_and_explicit_override(self) -> None:
        default_entry = {"template_frame": {"role": "content"}, "narrative_job": "context"}
        explicit_entry = {**default_entry, "notes_word_range": [45, 75]}
        self.assertEqual(lab_slides.notes_range_for_slide(self.config, default_entry), (60, 170))
        self.assertEqual(lab_slides.notes_range_for_slide(self.config, explicit_entry), (45, 75))

    def test_legacy_range_is_used_when_role_ranges_are_absent(self) -> None:
        config = {"notes": {"minimum_words": 25, "maximum_words": 140}}
        entry = {"template_frame": {"role": "content"}, "narrative_job": "context"}
        self.assertEqual(lab_slides.notes_range_for_slide(config, entry), (25, 140))


if __name__ == "__main__":
    unittest.main()
