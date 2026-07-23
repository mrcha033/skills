from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = SKILL_ROOT / "scripts" / "lab_slides.py"
SPEC = importlib.util.spec_from_file_location("lab_slides_contracts_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
lab_slides = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab_slides)
TEMPLATE = SKILL_ROOT / "assets" / "lab-meeting-template.pptx"
SCAFFOLD = SKILL_ROOT / "assets" / "template-builder-scaffold.mjs"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def minimal_profile() -> dict:
    return {
        "schema_version": 1,
        "slide_count": 2,
        "slides": [
            {
                "source_slide": 1,
                "role": "cover",
                "title_target": {"id": "sh/title", "name": "PlaceHolder 1"},
                "subtitle_target": {"id": "sh/subtitle", "name": "PlaceHolder 2"},
                "eyebrow_target": {"id": "sh/subject", "name": "Meeting Subject"},
                "safe_content_zone": None,
            },
            {
                "source_slide": 2,
                "role": "content",
                "title_target": {"id": "sh/content-title", "name": "PlaceHolder 1"},
                "subtitle_target": None,
                "eyebrow_target": None,
                "safe_content_zone": {"x": 71.36, "y": 109.42, "w": 1137.22, "h": 545.01},
            },
        ],
    }


def create_auditable_project(root: Path, builder_text: str) -> tuple[dict, Path, Path]:
    project = root / "project"
    (project / "build").mkdir(parents=True)
    (project / "content").mkdir()
    (project / "work" / "template" / "template-inspect").mkdir(parents=True)
    builder = project / "build" / "builder.mjs"
    builder.write_text(builder_text, encoding="utf-8")
    starter = project / "work" / "template" / "template-starter.pptx"
    shutil.copy2(TEMPLATE, starter)
    (project / "work" / "template" / "template-inspect" / "template-manifest.json").write_text(
        "{}\n", encoding="utf-8"
    )

    plan = {
        "schema_version": 1,
        "slides": [
            {
                "slide": 1,
                "narrative_job": "opening thesis",
                "title_claim": "The supplied frame is the visual contract",
                "template_frame": {
                    "role": "cover",
                    "source_slide": 1,
                    "routing_rationale": "The first supplied slide is the approved cover frame.",
                },
                "visual_anchor": "The inherited cover typography and lockup",
                "coverage": ["opening"],
                "evidence_refs": [],
                "notes_ref": "slide-1",
            }
        ],
    }
    profile = minimal_profile()
    frame_map = lab_slides.build_template_frame_map(profile, plan)
    write_json(project / "content" / "slide-plan.json", plan)
    write_json(project / "content" / "template-profile.json", profile)
    write_json(project / "content" / "template-frame-map.json", frame_map)
    write_json(project / "content" / "claims.json", {"schema_version": 1, "claims": []})
    template_sha = lab_slides.sha256_file(TEMPLATE)
    write_json(
        project / "content" / "design-system.json",
        {
            "template_status": "approved",
            "template_sha256": template_sha,
            "visual_thesis": "One disciplined inherited lab canvas.",
            "system_declaration": "Duplicate the supplied source frames and edit only mapped targets.",
            "signature": "Locked lab chrome with native content.",
        },
    )
    config = {
        "schema_version": 2,
        "deck": {
            "communication_job": "The lab should understand why exact template inheritance protects clarity.",
            "source_pptx": str(TEMPLATE),
            "visual_reference": str(TEMPLATE),
        },
        "sources": [
            {"id": "source-deck", "path": str(TEMPLATE), "role": "existing-deck"},
            {"id": "visual-reference", "path": str(TEMPLATE), "role": "visual-language"},
        ],
        "template": {
            "mode": "exact-clone-edit",
            "pptx": str(TEMPLATE),
            "workspace": "work/template",
            "profile": "content/template-profile.json",
            "frame_map": "content/template-frame-map.json",
            "starter_pptx": "work/template/template-starter.pptx",
            "source_sha256": template_sha,
            "starter_sha256": lab_slides.sha256_file(starter),
        },
        "coverage": {"required": ["opening"]},
        "build": {"builder": "build/builder.mjs"},
    }
    config_path = project / "labdeck.json"
    write_json(config_path, config)
    return config, config_path, project


class SchemaAndTemplateContractTests(unittest.TestCase):
    def test_load_config_accepts_only_supported_schema_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "labdeck.json"
            write_json(path, {"schema_version": 2})
            config, _, _ = lab_slides.load_config(path)
            self.assertEqual(config["schema_version"], 2)

            write_json(path, {"schema_version": 3})
            with self.assertRaisesRegex(lab_slides.LabDeckError, "schema_version 1 or 2"):
                lab_slides.load_config(path)

    def test_exact_clone_mode_requires_schema_two(self) -> None:
        self.assertTrue(lab_slides.template_mode({"schema_version": 2, "template": {"mode": "exact-clone-edit"}}))
        self.assertFalse(lab_slides.template_mode({"schema_version": 1, "template": {"mode": "exact-clone-edit"}}))
        self.assertFalse(lab_slides.template_mode({"schema_version": 2, "template": {"mode": "html-roundtrip"}}))

    def test_init_defaults_to_template_native_without_libreoffice_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            project = Path(temp_name) / "new-deck"
            args = argparse.Namespace(
                project_dir=str(project),
                source_pptx=str(TEMPLATE),
                template_pptx=str(TEMPLATE),
                reference_pptx=None,
                output_name="result.pptx",
                title="Regression deck",
                html_prototype=False,
                force=False,
            )
            with mock.patch.object(lab_slides, "run_template_inspection", return_value={}):
                self.assertEqual(lab_slides.command_init(args), 0)

            config = json.loads((project / "labdeck.json").read_text(encoding="utf-8"))
            self.assertEqual(config["schema_version"], 2)
            self.assertEqual(config["deck"]["authoring_mode"], "template-native")
            self.assertEqual(config["template"]["mode"], "exact-clone-edit")
            self.assertFalse(config["build"]["compatibility_roundtrip"])
            self.assertFalse(config["template"]["html_prototype"]["final_source"])
            self.assertEqual(config["build"]["input_pptx"], "work/template/template-starter.pptx")

    def test_html_prototype_persists_an_enforced_html_assisted_native_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            project = Path(temp_name) / "html-deck"
            args = argparse.Namespace(
                project_dir=str(project),
                source_pptx=str(TEMPLATE),
                template_pptx=str(TEMPLATE),
                reference_pptx=None,
                output_name="result.pptx",
                title="HTML-assisted regression deck",
                html_prototype=True,
                force=False,
            )
            with mock.patch.object(lab_slides, "run_template_inspection", return_value={}):
                self.assertEqual(lab_slides.command_init(args), 0)

            config = json.loads((project / "labdeck.json").read_text(encoding="utf-8"))
            prototype = config["template"]["html_prototype"]
            self.assertEqual(config["deck"]["authoring_mode"], "html-assisted-native")
            self.assertTrue(prototype["allowed"])
            self.assertTrue(prototype["disposable"])
            self.assertFalse(prototype["final_source"])
            self.assertFalse(prototype["direct_conversion"])
            self.assertFalse(prototype["allow_full_slide_raster"])
            errors = lab_slides.native_rebuild_contract_errors(
                config,
                project / "labdeck.json",
                project,
            )
            self.assertTrue(any("manifest missing" in error for error in errors), errors)

    def test_builder_scaffold_imports_starter_and_cannot_create_fresh_slides(self) -> None:
        text = SCAFFOLD.read_text(encoding="utf-8")
        self.assertIn("PresentationFile.importPptx", text)
        self.assertIn("PresentationFile.exportPptx", text)
        self.assertNotIn("Presentation.create(", text)
        self.assertNotIn("presentation.slides.add(", text)
        self.assertIn("escapes its inherited content zone", text)
        self.assertIn("fontSizePt", text)
        self.assertIn("style.minimum_body_pt", text)
        self.assertIn("PT_TO_CSS_PX", text)
        self.assertNotIn("CUSTOM_SLIDE_BUILDERS", text)
        self.assertIn('spec.type === "line"', text)
        self.assertIn('geometry: "line"', text)

    def test_audit_rejects_a_fresh_deck_builder(self) -> None:
        unsafe_builder = """
const presentation = Presentation.create({ width: 1280, height: 720 });
presentation.slides.add();
await PresentationFile.importPptx(input);
await PresentationFile.exportPptx(presentation);
"""
        with tempfile.TemporaryDirectory() as temp_name:
            config, config_path, project = create_auditable_project(Path(temp_name), unsafe_builder)

            report = lab_slides.audit_config(config, config_path, project)

        self.assertTrue(
            any("must not create a fresh presentation or add fresh slides" in error for error in report["errors"]),
            report["errors"],
        )

    def test_audit_accepts_import_export_spine_without_fresh_deck_error(self) -> None:
        safe_builder = """
const presentation = await PresentationFile.importPptx(input);
await PresentationFile.exportPptx(presentation);
"""
        with tempfile.TemporaryDirectory() as temp_name:
            config, config_path, project = create_auditable_project(Path(temp_name), safe_builder)

            report = lab_slides.audit_config(config, config_path, project)

        self.assertFalse(
            any("must not create a fresh presentation or add fresh slides" in error for error in report["errors"]),
            report["errors"],
        )

    def test_audit_propagates_title_fit_overflow_as_a_blocking_error(self) -> None:
        safe_builder = """
const presentation = await PresentationFile.importPptx(input);
await PresentationFile.exportPptx(presentation);
"""
        fit_report = {
            "status": "fail",
            "checks": [{"slide": 1, "status": "overflow"}],
            "errors": ["slide 1 title_claim needs 2 lines but mapped title box permits 1"],
            "warnings": [],
        }
        with tempfile.TemporaryDirectory() as temp_name:
            config, config_path, project = create_auditable_project(Path(temp_name), safe_builder)
            with mock.patch.object(lab_slides, "preflight_title_claim_fit", return_value=fit_report):
                report = lab_slides.audit_config(config, config_path, project)

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["title_fit_preflight"], fit_report)
        self.assertIn(fit_report["errors"][0], report["errors"])


if __name__ == "__main__":
    unittest.main()
