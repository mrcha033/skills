from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = SKILL_ROOT / "scripts" / "lab_slides.py"
SPEC = importlib.util.spec_from_file_location("lab_slides_title_fit_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
lab_slides = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab_slides)


class FixedMetricFont:
    def getlength(self, text: str) -> float:
        return float(len(text) * 10)

    def getmetrics(self) -> tuple[int, int]:
        return (16, 4)


def plan(title: str) -> dict:
    return {"slides": [{"slide": 1, "title_claim": title}]}


def frame_map() -> dict:
    return {
        "outputSlides": [
            {
                "outputSlide": 1,
                "editTargets": [
                    {
                        "action": "rewrite",
                        "contentRef": "title_claim",
                        "sourceName": "Actual Title",
                    }
                ],
            }
        ]
    }


def title_element(width: float = 200, height: float = 25) -> dict:
    return {
        "scope": "slide",
        "name": "Actual Title",
        "bbox": [0, 0, width, height],
        "resolvedTextStyle": {
            "typeface": "Test Sans",
            "fontSize": 20,
            "bold": True,
            "insets": {"left": 0, "right": 0, "top": 0, "bottom": 0},
        },
        "paragraphs": [
            {
                "lineSpacingPercent": 100000,
                "runs": [{"fontSize": 20, "typeface": "Test Sans", "bold": True}],
            }
        ],
    }


def write_layout(directory: Path, element: dict, inherited_width: float = 5) -> None:
    directory.mkdir(parents=True)
    payload = {
        "schema": "openai.presentation.layout/v4",
        "unit": "px",
        "elements": [element],
        "inheritedLayers": [
            {
                "elements": [
                    {
                        **title_element(width=inherited_width),
                        "scope": "layout",
                    }
                ]
            }
        ],
    }
    (directory / "starter-slide-01.layout.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


class TitleClaimFitPreflightTests(unittest.TestCase):
    def test_uses_actual_slide_element_and_ignores_inherited_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            layout_dir = Path(temp_name) / "layouts"
            write_layout(layout_dir, title_element(width=200), inherited_width=5)
            with mock.patch.object(
                lab_slides,
                "load_title_measurement_font",
                return_value=(FixedMetricFont(), "/fonts/TestSans-Bold.ttf", None),
            ):
                report = lab_slides.preflight_title_claim_fit(plan("short title"), frame_map(), layout_dir)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["checks"][0]["source_name"], "Actual Title")
        self.assertEqual(report["checks"][0]["maximum_lines"], 1)

    def test_clear_wrap_overflow_fails_before_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            layout_dir = Path(temp_name) / "layouts"
            write_layout(layout_dir, title_element(width=100, height=25))
            with mock.patch.object(
                lab_slides,
                "load_title_measurement_font",
                return_value=(FixedMetricFont(), "/fonts/TestSans-Bold.ttf", None),
            ):
                report = lab_slides.preflight_title_claim_fit(
                    plan("this title must wrap onto several lines"), frame_map(), layout_dir
                )

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["checks"][0]["status"], "overflow")
        self.assertGreater(report["checks"][0]["required_lines"], report["checks"][0]["maximum_lines"])
        self.assertIn("shorten the title", report["errors"][0])

    def test_unavailable_font_is_an_explicit_skip_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            layout_dir = Path(temp_name) / "layouts"
            write_layout(layout_dir, title_element())
            with mock.patch.object(
                lab_slides,
                "load_title_measurement_font",
                return_value=(None, None, "no local font file matched 'Test Sans'"),
            ):
                report = lab_slides.preflight_title_claim_fit(plan("short title"), frame_map(), layout_dir)

        self.assertEqual(report["status"], "warn")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["checks"][0]["status"], "skipped")
        self.assertIn("no local font file matched", report["warnings"][0])

    def test_unspaced_text_is_wrapped_character_wise(self) -> None:
        font = FixedMetricFont()
        lines, width = lab_slides._wrapped_line_count("가나다라마바사아자차", font, 35)
        self.assertEqual(lines, 4)
        self.assertEqual(width, 100)


if __name__ == "__main__":
    unittest.main()
