#!/usr/bin/env python3
"""Deterministic project, evidence, finalization, and QA runner for lab decks."""

from __future__ import annotations

import argparse
import datetime as dt
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable
import xml.etree.ElementTree as ET
import zipfile


SKILL_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = SKILL_DIR / "assets"
HELPERS_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = ASSETS_DIR / "lab-meeting-template.pptx"
STYLE_MANIFEST = ASSETS_DIR / "style-manifest.json"
LEGACY_CUDA_REFERENCE = ASSETS_DIR / "cuda-visual-language-source.pptx"
DEFAULT_FORBIDDEN_PRESERVED_NOTES = ("긴장하지 말자",)
PROVENANCE_LABELS = {
    "cuda-events": "CUDA EVENTS",
    "nsight-compute": "NSIGHT COMPUTE",
    "nsight-systems": "NSIGHT SYSTEMS",
    "derived": "DERIVED",
    "code-derived": "CODE-DERIVED",
    "vendor-spec": "VENDOR SPEC",
    "inference": "INFERENCE",
}
SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
NOTES_RE = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")
TITLE_FIT_WIDTH_TOLERANCE = 1.01
DEFAULT_VISUAL_CONTRACT = {
    "enabled": True,
    "max_repeated_composition": 2,
    "min_body_coverage_ratio": 0.16,
    "require_anchor_spec": True,
    "require_content_basis": True,
    "forbidden_families": ["card-grid", "dashboard", "icon-grid", "pill-grid", "tile-grid"],
}


class LabDeckError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def newest_existing(paths: Iterable[Path]) -> Path | None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def find_node() -> Path | None:
    override = os.environ.get("LABDECK_NODE")
    if override and Path(override).exists():
        return Path(override)
    candidates = list(Path.home().glob(".cache/codex-runtimes/*/dependencies/node/bin/node"))
    found = newest_existing(candidates)
    if found:
        return found
    system = shutil.which("node")
    return Path(system) if system else None


def find_python() -> Path | None:
    override = os.environ.get("LABDECK_PYTHON")
    if override and Path(override).exists():
        return Path(override)
    candidates = list(Path.home().glob(".cache/codex-runtimes/*/dependencies/python/bin/python3"))
    found = newest_existing(candidates)
    if found:
        return found
    return Path(sys.executable) if sys.executable else None


def find_presentations_skill() -> Path | None:
    override = os.environ.get("PRESENTATIONS_SKILL_DIR")
    if override and (Path(override) / "SKILL.md").exists():
        return Path(override)
    candidates = list(
        Path.home().glob(
            ".cache/codex-runtimes/*/plugins/openai-primary-runtime/plugins/presentations/skills/presentations"
        )
    )
    candidates += list(
        Path.home().glob(
            ".codex/plugins/cache/openai-primary-runtime/*/skills/presentations"
        )
    )
    return newest_existing([path for path in candidates if (path / "SKILL.md").exists()])


def find_soffice() -> Path | None:
    override = os.environ.get("LABDECK_SOFFICE")
    if override and Path(override).exists():
        return Path(override)
    system = shutil.which("soffice")
    if system:
        return Path(system)
    app = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    return app if app.exists() else None


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    print("+ " + shlex.join(command), file=sys.stderr)
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if check and result.returncode != 0:
        detail = ""
        if capture:
            detail = f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        raise LabDeckError(f"command failed with exit {result.returncode}: {shlex.join(command)}{detail}")
    return result


def resolve_path(value: str | None, base: Path) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def relative_or_absolute(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def load_config(config_path: str | Path) -> tuple[dict[str, Any], Path, Path]:
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise LabDeckError(f"configuration not found: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LabDeckError(f"invalid configuration JSON: {path}: {error}") from error
    if not isinstance(config, dict):
        raise LabDeckError(f"configuration must be a JSON object: {path}")
    if config.get("schema_version") not in (1, 2):
        raise LabDeckError("labdeck.json must have schema_version 1 or 2")
    return config, path, path.parent


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LabDeckError(f"invalid JSON: {path}: {error}") from error


def default_template_integrity() -> dict[str, Any]:
    report: dict[str, Any] = {
        "template": str(DEFAULT_TEMPLATE),
        "style_manifest": str(STYLE_MANIFEST),
        "expected_sha256": None,
        "actual_sha256": None,
        "verified": False,
    }
    if not DEFAULT_TEMPLATE.exists():
        report["error"] = "bundled default template is missing"
        return report
    if not STYLE_MANIFEST.exists():
        report["error"] = "style manifest is missing"
        return report
    try:
        manifest = read_json(STYLE_MANIFEST)
        if not isinstance(manifest, dict):
            raise LabDeckError("style manifest must be a JSON object")
        expected_name = str(manifest.get("source", ""))
        expected_sha = str(manifest.get("source_sha256", "")).lower()
        actual_sha = sha256_file(DEFAULT_TEMPLATE)
        report.update(
            {
                "expected_source": expected_name,
                "expected_sha256": expected_sha or None,
                "actual_sha256": actual_sha,
                "verified": expected_name == DEFAULT_TEMPLATE.name and expected_sha == actual_sha,
            }
        )
        if not report["verified"]:
            report["error"] = "bundled template does not match assets/style-manifest.json"
    except (LabDeckError, OSError, ValueError) as error:
        report["error"] = str(error)
    return report


def require_default_template_integrity(path: Path) -> None:
    if path.resolve() != DEFAULT_TEMPLATE.resolve():
        return
    report = default_template_integrity()
    if not report.get("verified"):
        raise LabDeckError(str(report.get("error") or "bundled default template integrity check failed"))


def ensure_project_output(path: Path, project: Path, label: str) -> None:
    try:
        path.resolve().relative_to(project.resolve())
    except ValueError as error:
        raise LabDeckError(f"{label} must stay inside the project directory: {path}") from error
    if path.suffix.casefold() != ".pptx":
        raise LabDeckError(f"{label} must be a .pptx path: {path}")


def presentations_template_scripts() -> Path:
    skill = find_presentations_skill()
    if not skill:
        raise LabDeckError("bundled Presentations skill is unavailable; run doctor")
    scripts = skill / "template_following_scripts"
    required = (
        "inspect_template_deck.mjs",
        "validate_template_plan.mjs",
        "prepare_template_starter_deck.mjs",
        "check_template_fidelity.mjs",
    )
    missing = [name for name in required if not (scripts / name).exists()]
    if missing:
        raise LabDeckError("Presentations template helpers are missing: " + ", ".join(missing))
    return scripts


def template_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("template", {}) if config.get("schema_version") == 2 else {}


def template_mode(config: dict[str, Any]) -> bool:
    return template_config(config).get("mode") == "exact-clone-edit"


def template_paths(config: dict[str, Any], project: Path) -> dict[str, Path]:
    template = template_config(config)
    pptx = resolve_path(template.get("pptx") or config.get("deck", {}).get("visual_reference"), project)
    if not pptx:
        raise LabDeckError("template.pptx is required in template-native mode")
    workspace = resolve_path(template.get("workspace", "work/template"), project)
    profile = resolve_path(template.get("profile", "content/template-profile.json"), project)
    frame_map = resolve_path(template.get("frame_map", "content/template-frame-map.json"), project)
    starter = resolve_path(template.get("starter_pptx", "work/template/template-starter.pptx"), project)
    if not all((workspace, profile, frame_map, starter)):
        raise LabDeckError("template paths are incomplete")
    return {
        "pptx": pptx,
        "workspace": workspace,
        "inspect_dir": workspace / "template-inspect",
        "inspect_ndjson": workspace / "template-inspect/template-inspect.ndjson",
        "inspect_manifest": workspace / "template-inspect/template-manifest.json",
        "profile": profile,
        "frame_map": frame_map,
        "starter": starter,
        "starter_manifest": workspace / "template-starter.manifest.json",
        "starter_preview": workspace / "template-starter-preview",
        "starter_layout": workspace / "template-starter-layout",
        "starter_contact_sheet": workspace / "template-starter-contact-sheet.png",
    }


def _normalized_font_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _font_file_score(path: Path, family: str, bold: bool, italic: bool) -> tuple[int, int, str]:
    family_key = _normalized_font_name(family)
    stem_key = _normalized_font_name(path.stem)
    score = 0
    if stem_key == family_key:
        score += 100
    elif stem_key.startswith(family_key):
        score += 80
    elif family_key in stem_key:
        score += 60
    bold_tokens = ("bold", "semibold", "demibold", "black", "heavy", "bd")
    italic_tokens = ("italic", "oblique", "it")
    is_bold = any(token in stem_key.removeprefix(family_key) for token in bold_tokens)
    is_italic = any(token in stem_key.removeprefix(family_key) for token in italic_tokens)
    score += 20 if is_bold == bold else -20
    score += 10 if is_italic == italic else -10
    return (score, -len(str(path)), str(path))


@lru_cache(maxsize=128)
def resolve_title_font_path(typeface: str, bold: bool, italic: bool) -> Path | None:
    """Resolve a local font without substituting an unrelated family."""
    requested = Path(typeface).expanduser()
    if requested.is_file():
        return requested.resolve()

    roots = [
        Path("/System/Library/Fonts"),
        Path("/System/Library/Fonts/Supplemental"),
        Path("/Library/Fonts"),
        Path.home() / "Library/Fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".fonts",
        Path.home() / ".local/share/fonts",
    ]
    windows_root = os.environ.get("WINDIR")
    if windows_root:
        roots.append(Path(windows_root) / "Fonts")
    roots = [root for root in roots if root.exists()]

    family_key = _normalized_font_name(typeface)
    if not family_key:
        return None
    candidates: list[Path] = []
    for root in roots:
        for extension in ("*.ttf", "*.otf", "*.ttc"):
            for path in root.rglob(extension):
                stem_key = _normalized_font_name(path.stem)
                if family_key in stem_key or stem_key in family_key:
                    candidates.append(path)
    if not candidates:
        return None
    candidates.sort(key=lambda path: _font_file_score(path, typeface, bold, italic), reverse=True)
    return candidates[0]


def load_title_measurement_font(
    typeface: str,
    font_size_px: float,
    bold: bool,
    italic: bool,
) -> tuple[Any | None, str | None, str | None]:
    try:
        from PIL import ImageFont
    except ImportError:
        return None, None, "Pillow is unavailable"
    font_path = resolve_title_font_path(typeface, bold, italic)
    if not font_path:
        return None, None, f"no local font file matched {typeface!r}"
    try:
        font = ImageFont.truetype(str(font_path), max(1, int(round(font_size_px))))
    except (OSError, ValueError) as error:
        return None, str(font_path), f"font could not be loaded: {error}"
    return font, str(font_path), None


def _title_element_style(element: dict[str, Any], unit_scale: float) -> dict[str, Any] | None:
    paragraphs = element.get("paragraphs")
    paragraph = next((item for item in paragraphs or [] if isinstance(item, dict)), {})
    runs = paragraph.get("runs") if isinstance(paragraph, dict) else []
    run = next((item for item in runs or [] if isinstance(item, dict)), {})
    resolved = element.get("resolvedTextStyle")
    resolved = resolved if isinstance(resolved, dict) else {}
    typeface = str(run.get("typeface") or resolved.get("typeface") or "").strip()
    raw_size = run.get("fontSize") or resolved.get("fontSize")
    try:
        font_size_px = float(raw_size) * unit_scale
    except (TypeError, ValueError):
        return None
    if not typeface or not math.isfinite(font_size_px) or font_size_px <= 0:
        return None
    raw_spacing = paragraph.get("lineSpacingPercent") if isinstance(paragraph, dict) else None
    try:
        line_spacing = float(raw_spacing) / 100000.0 if raw_spacing is not None else 1.0
    except (TypeError, ValueError):
        line_spacing = 1.0
    if not math.isfinite(line_spacing) or line_spacing <= 0:
        line_spacing = 1.0
    run_bold = run.get("bold")
    run_italic = run.get("italic")
    return {
        "typeface": typeface,
        "font_size_px": font_size_px,
        "bold": bool(run_bold if isinstance(run_bold, bool) else resolved.get("bold", False)),
        "italic": bool(run_italic if isinstance(run_italic, bool) else resolved.get("italic", False)),
        "line_spacing": line_spacing,
        "insets": resolved.get("insets") if isinstance(resolved.get("insets"), dict) else {},
    }


def _wrapped_line_count(text: str, font: Any, usable_width: float) -> tuple[int, float]:
    explicit_lines = text.splitlines() or [""]
    wrapped_count = 0
    unwrapped_max = 0.0
    for explicit_line in explicit_lines:
        unwrapped_max = max(unwrapped_max, float(font.getlength(explicit_line)))
        if not explicit_line:
            wrapped_count += 1
            continue
        words = re.findall(r"\S+\s*", explicit_line)
        current = ""
        for token in words:
            candidate = current + token
            if float(font.getlength(candidate.rstrip())) <= usable_width:
                current = candidate
                continue
            if current:
                wrapped_count += 1
            current = token.lstrip()
            if float(font.getlength(current.rstrip())) <= usable_width:
                continue
            # PowerPoint can break CJK and long tokens. Count those character-wise.
            fragment = ""
            for character in current:
                candidate_fragment = fragment + character
                if fragment and float(font.getlength(candidate_fragment)) > usable_width:
                    wrapped_count += 1
                    fragment = character
                else:
                    fragment = candidate_fragment
            current = fragment
        if current or words:
            wrapped_count += 1
    return wrapped_count, unwrapped_max


def preflight_title_claim_fit(
    plan: dict[str, Any],
    frame_map: dict[str, Any],
    layout_dir: Path,
) -> dict[str, Any]:
    """Measure mapped title claims against their actual starter slide title objects."""
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    slides = {
        entry.get("slide"): entry
        for entry in plan.get("slides", [])
        if isinstance(entry, dict) and isinstance(entry.get("slide"), int)
    }
    mapped = frame_map.get("outputSlides", []) if isinstance(frame_map, dict) else []
    for mapping in mapped:
        if not isinstance(mapping, dict):
            continue
        slide_number = mapping.get("outputSlide")
        title_target = next(
            (
                target
                for target in mapping.get("editTargets", [])
                if isinstance(target, dict)
                and target.get("action") == "rewrite"
                and target.get("contentRef") == "title_claim"
            ),
            None,
        )
        if not title_target:
            continue
        title = str(slides.get(slide_number, {}).get("title_claim", ""))
        layout_path = layout_dir / f"starter-slide-{int(slide_number):02d}.layout.json"
        if not layout_path.exists():
            message = f"slide {slide_number} title-fit preflight is missing starter layout: {layout_path}"
            errors.append(message)
            checks.append({"slide": slide_number, "status": "error", "reason": message})
            continue
        try:
            layout = read_json(layout_path)
        except (LabDeckError, OSError, json.JSONDecodeError) as error:
            message = f"slide {slide_number} title-fit preflight could not read {layout_path}: {error}"
            errors.append(message)
            checks.append({"slide": slide_number, "status": "error", "reason": message})
            continue
        unit = str(layout.get("unit", "px")).casefold()
        unit_scale = 1.0 if unit == "px" else 96.0 / 72.0 if unit == "pt" else None
        if unit_scale is None:
            message = f"slide {slide_number} title-fit preflight skipped: unsupported layout unit {unit!r}"
            warnings.append(message)
            checks.append({"slide": slide_number, "status": "skipped", "reason": message})
            continue
        source_name = str(title_target.get("sourceName", ""))
        # Only inspect top-level slide elements. Inherited layout/master duplicates are deliberately excluded.
        matches = [
            element
            for element in layout.get("elements", [])
            if isinstance(element, dict)
            and element.get("name") == source_name
            and element.get("scope", "slide") == "slide"
        ]
        if len(matches) != 1:
            message = (
                f"slide {slide_number} title-fit preflight expected one slide-level element named "
                f"{source_name!r}, found {len(matches)}"
            )
            errors.append(message)
            checks.append({"slide": slide_number, "status": "error", "reason": message})
            continue
        element = matches[0]
        bbox = element.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            message = f"slide {slide_number} title-fit preflight found no valid bbox for {source_name!r}"
            errors.append(message)
            checks.append({"slide": slide_number, "status": "error", "reason": message})
            continue
        try:
            width = float(bbox[2]) * unit_scale
            height = float(bbox[3]) * unit_scale
        except (TypeError, ValueError):
            width = height = math.nan
        style = _title_element_style(element, unit_scale)
        if not style or not all(math.isfinite(value) and value > 0 for value in (width, height)):
            message = f"slide {slide_number} title-fit preflight skipped: usable title font metrics are unavailable"
            warnings.append(message)
            checks.append({"slide": slide_number, "status": "skipped", "reason": message})
            continue
        insets = style["insets"]
        try:
            inset_left = float(insets.get("left", 0)) * unit_scale
            inset_right = float(insets.get("right", 0)) * unit_scale
            inset_top = float(insets.get("top", 0)) * unit_scale
            inset_bottom = float(insets.get("bottom", 0)) * unit_scale
        except (TypeError, ValueError):
            inset_left = inset_right = inset_top = inset_bottom = 0.0
        usable_width = width - inset_left - inset_right
        usable_height = height - inset_top - inset_bottom
        if usable_width <= 0 or usable_height <= 0:
            message = f"slide {slide_number} title-fit preflight found a non-positive usable title box"
            errors.append(message)
            checks.append({"slide": slide_number, "status": "error", "reason": message})
            continue
        font, font_path, font_error = load_title_measurement_font(
            style["typeface"], style["font_size_px"], style["bold"], style["italic"]
        )
        if not font:
            message = f"slide {slide_number} title-fit preflight skipped: {font_error}"
            warnings.append(message)
            checks.append(
                {
                    "slide": slide_number,
                    "status": "skipped",
                    "typeface": style["typeface"],
                    "font_size_px": round(style["font_size_px"], 2),
                    "reason": message,
                }
            )
            continue
        measured_width_limit = usable_width * TITLE_FIT_WIDTH_TOLERANCE
        required_lines, unwrapped_width = _wrapped_line_count(title, font, measured_width_limit)
        try:
            ascent, descent = font.getmetrics()
            line_height = float(ascent + descent) * style["line_spacing"]
        except (AttributeError, TypeError, ValueError):
            line_height = style["font_size_px"] * 1.2 * style["line_spacing"]
        max_lines = max(0, int((usable_height * TITLE_FIT_WIDTH_TOLERANCE) // max(line_height, 1.0)))
        check = {
            "slide": slide_number,
            "status": "pass" if required_lines <= max_lines else "overflow",
            "source_name": source_name,
            "title": title,
            "typeface": style["typeface"],
            "font_path": font_path,
            "font_size_px": round(style["font_size_px"], 2),
            "usable_width_px": round(usable_width, 2),
            "usable_height_px": round(usable_height, 2),
            "unwrapped_width_px": round(unwrapped_width, 2),
            "required_lines": required_lines,
            "maximum_lines": max_lines,
        }
        checks.append(check)
        if required_lines > max_lines:
            errors.append(
                f"slide {slide_number} title_claim needs {required_lines} lines but mapped title box permits "
                f"{max_lines} ({unwrapped_width:.1f}px measured vs {usable_width:.1f}px usable at "
                f"{style['typeface']} {style['font_size_px']:.1f}px); shorten the title or select a compatible approved frame"
            )
    status = "fail" if errors else "warn" if warnings else "pass" if checks else "not-applicable"
    return {"status": status, "checks": checks, "errors": errors, "warnings": warnings}


def _visual_rect(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, dict):
        return None
    if {"left", "top", "width", "height"}.issubset(value):
        keys = ("left", "top", "width", "height")
    elif {"x", "y", "w", "h"}.issubset(value):
        keys = ("x", "y", "w", "h")
    else:
        return None
    try:
        rect = tuple(float(value[key]) for key in keys)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in rect) or rect[2] <= 0 or rect[3] <= 0:
        return None
    return rect


def _clip_visual_rect(rect: tuple[float, float, float, float], zone: tuple[float, float, float, float]) -> tuple[float, float, float, float] | None:
    left, top, width, height = rect
    zx, zy, zw, zh = zone
    right = min(left + width, zx + zw)
    bottom = min(top + height, zy + zh)
    clipped_left = max(left, zx)
    clipped_top = max(top, zy)
    if right <= clipped_left or bottom <= clipped_top:
        return None
    return clipped_left, clipped_top, right - clipped_left, bottom - clipped_top


def _visual_union_area(rects: list[tuple[float, float, float, float]]) -> float:
    """Compute a small exact rectangle union without treating overlap as extra coverage."""
    if not rects:
        return 0.0
    x_edges = sorted({left for left, _, _, _ in rects} | {left + width for left, _, width, _ in rects})
    area = 0.0
    for x0, x1 in zip(x_edges, x_edges[1:]):
        if x1 <= x0:
            continue
        intervals = []
        for left, top, width, height in rects:
            if left < x1 and left + width > x0:
                intervals.append((top, top + height))
        intervals.sort()
        y_area = 0.0
        current: tuple[float, float] | None = None
        for start, end in intervals:
            if current is None:
                current = (start, end)
            elif start > current[1]:
                y_area += current[1] - current[0]
                current = (start, end)
            else:
                current = (current[0], max(current[1], end))
        if current is not None:
            y_area += current[1] - current[0]
        area += (x1 - x0) * y_area
    return area


def _visual_composition_signature(entry: dict[str, Any]) -> str:
    elements = entry.get("native_elements", [])
    normalized = []
    if isinstance(elements, list):
        for element in elements:
            if not isinstance(element, dict):
                continue
            rect = _visual_rect(element.get("position") or element.get("bbox"))
            normalized.append(
                {
                    "type": str(element.get("type") or element.get("kind") or "unknown"),
                    "geometry": str(element.get("geometry", "")),
                    "rect": [round(item, 1) for item in rect] if rect else None,
                }
            )
    normalized.sort(key=lambda item: (item["rect"] or [math.inf] * 4, item["type"], item["geometry"]))
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()[:16]


def visual_contract_audit(
    config: dict[str, Any],
    plan: dict[str, Any],
    frame_map: dict[str, Any],
) -> dict[str, Any]:
    """Enforce an auditable anti-repetition and content-anchor contract for new projects."""
    configured = config.get("qa", {}).get("visual_contract")
    if not isinstance(configured, dict):
        return {
            "status": "legacy-warning",
            "checks": [],
            "errors": [],
            "warnings": ["visual contract is absent; rerun init to enable anti-repetition and content-anchor gates"],
        }
    settings = dict(DEFAULT_VISUAL_CONTRACT)
    settings.update(configured)
    if settings.get("enabled") is False:
        reason = str(settings.get("disabled_reason", "")).strip()
        if not reason:
            return {
                "status": "fail",
                "checks": [],
                "errors": ["qa.visual_contract.enabled=false requires a concrete disabled_reason"],
                "warnings": [],
            }
        return {"status": "disabled", "checks": [], "errors": [], "warnings": [f"visual contract disabled: {reason}"]}

    try:
        max_repeated = int(settings.get("max_repeated_composition", 2))
    except (TypeError, ValueError):
        max_repeated = 2
    try:
        minimum_coverage = float(settings.get("min_body_coverage_ratio", 0.16))
    except (TypeError, ValueError):
        minimum_coverage = 0.16
    if max_repeated < 1 or not 0 <= minimum_coverage <= 1:
        return {
            "status": "fail",
            "checks": [],
            "errors": ["qa.visual_contract thresholds are invalid"],
            "warnings": [],
        }
    forbidden_families = {
        str(value).strip().casefold()
        for value in settings.get("forbidden_families", [])
        if str(value).strip()
    }
    mappings = frame_map.get("outputSlides", []) if isinstance(frame_map, dict) else []
    mapping_by_slide = {
        item.get("outputSlide"): item
        for item in mappings
        if isinstance(item, dict) and isinstance(item.get("outputSlide"), int)
    }
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    signature_slides: dict[str, list[int]] = {}
    signature_specs: dict[str, list[dict[str, Any]]] = {}
    family_run: list[tuple[str, int]] = []
    for entry in plan.get("slides", []):
        if not isinstance(entry, dict):
            continue
        slide = entry.get("slide")
        if not isinstance(slide, int):
            continue
        role = str(entry.get("template_frame", {}).get("role", "content")).casefold()
        spec = entry.get("visual_contract")
        if settings.get("require_anchor_spec", True) and role not in {"cover", "divider"}:
            if not isinstance(spec, dict):
                errors.append(f"slide {slide} is missing visual_contract: declare anchor_type, family, content_basis, and underfill policy")
                spec = {}
            anchor_type = str(spec.get("anchor_type", "")).strip().casefold()
            family = str(spec.get("family", "")).strip().casefold()
            basis = spec.get("content_basis")
            basis_items = basis if isinstance(basis, list) else [basis] if isinstance(basis, str) else []
            basis_items = [str(item).strip() for item in basis_items if str(item).strip()]
            if not anchor_type:
                errors.append(f"slide {slide} visual_contract.anchor_type is required")
            if not family:
                errors.append(f"slide {slide} visual_contract.family is required")
            if family in forbidden_families:
                errors.append(f"slide {slide} uses forbidden generic composition family {family!r}")
            if settings.get("require_content_basis", True) and not basis_items:
                errors.append(f"slide {slide} visual_contract.content_basis must identify the supplied research/source basis")
            if family:
                if family_run and family_run[-1][0] == family:
                    family_run.append((family, family_run[-1][1] + 1))
                else:
                    family_run.append((family, 1))
                if family_run[-1][1] > max_repeated and not str(spec.get("repeat_group", "")).strip():
                    errors.append(f"slide {slide} repeats composition family {family!r} more than {max_repeated} times without repeat_group rationale")
        else:
            family_run.append(("", 1))

        signature = _visual_composition_signature(entry)
        signature_slides.setdefault(signature, []).append(slide)
        signature_specs.setdefault(signature, []).append(spec if isinstance(spec, dict) else {})
        mapping = mapping_by_slide.get(slide, {})
        add_targets = [item for item in mapping.get("editTargets", []) if isinstance(item, dict) and item.get("action") == "add"]
        zone = _visual_rect(add_targets[0].get("zone")) if add_targets else None
        rects = []
        for element in entry.get("native_elements", []) if isinstance(entry.get("native_elements", []), list) else []:
            rect = _visual_rect(element.get("position") or element.get("bbox")) if isinstance(element, dict) else None
            clipped = _clip_visual_rect(rect, zone) if rect and zone else None
            if clipped:
                rects.append(clipped)
        coverage_ratio = _visual_union_area(rects) / (zone[2] * zone[3]) if zone else None
        underfill_allowed = bool(spec.get("allow_underfill")) if isinstance(spec, dict) else False
        underfill_reason = str(spec.get("underfill_rationale", "")).strip() if isinstance(spec, dict) else ""
        if role not in {"cover", "divider", "closing", "appendix"} and coverage_ratio is not None and coverage_ratio < minimum_coverage:
            if not underfill_allowed or not underfill_reason:
                errors.append(
                    f"slide {slide} body coverage is {coverage_ratio:.3f}, below {minimum_coverage:.3f}; add a content-specific visual or an explicit underfill_rationale"
                )
        checks.append(
            {
                "slide": slide,
                "role": role,
                "composition_signature": signature,
                "composition_family": str(spec.get("family", "")) if isinstance(spec, dict) else None,
                "anchor_type": str(spec.get("anchor_type", "")) if isinstance(spec, dict) else None,
                "body_coverage_ratio": round(coverage_ratio, 4) if coverage_ratio is not None else None,
                "underfill_allowed": underfill_allowed,
            }
        )
    for signature, slides in signature_slides.items():
        specs = signature_specs.get(signature, [])
        has_repeat_rationale = any(str(item.get("repeat_group", "")).strip() for item in specs)
        if len(slides) > max_repeated and not has_repeat_rationale:
            errors.append(f"composition signature {signature} repeats on slides {slides}; use distinct compositions or an explicit repeat_group rationale")
    status = "fail" if errors else "pass" if checks else "not-applicable"
    return {"status": status, "checks": checks, "errors": errors, "warnings": warnings}


def user_acceptance_errors(config: dict[str, Any], final: Path, deck_sha: str) -> list[str]:
    contract = config.get("qa", {}).get("user_acceptance")
    if not isinstance(contract, dict):
        return []
    if contract.get("required", True) is False:
        reason = str(contract.get("disabled_reason", "")).strip()
        return [] if reason else ["qa.user_acceptance.required=false requires a disabled_reason"]
    status = str(contract.get("status", "pending")).strip().casefold()
    if status != "accepted":
        return ["user visual acceptance is pending; inspect the full-size renders and set qa.user_acceptance.status=accepted before handoff"]
    if str(contract.get("reviewed_deck_sha256", "")).casefold() != deck_sha.casefold():
        return ["qa.user_acceptance.reviewed_deck_sha256 does not match the current final PPTX"]
    if not str(contract.get("reviewer", "")).strip() or not str(contract.get("accepted_at", "")).strip():
        return ["accepted user visual review requires reviewer and accepted_at"]
    return []


def iter_layout_elements(layout: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for layer in layout.get("inheritedLayers", []):
        for element in layer.get("elements", []):
            if isinstance(element, dict):
                yield element


def _bbox_area(element: dict[str, Any]) -> float:
    bbox = element.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return 0.0
    try:
        return max(0.0, float(bbox[2])) * max(0.0, float(bbox[3]))
    except (TypeError, ValueError):
        return 0.0


def derive_template_profile(paths: dict[str, Path]) -> dict[str, Any]:
    manifest = read_json(paths["inspect_manifest"])
    records_by_slide: dict[int, list[dict[str, Any]]] = {}
    for line in paths["inspect_ndjson"].read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        slide_number = record.get("slide")
        if isinstance(slide_number, int):
            records_by_slide.setdefault(slide_number, []).append(record)

    slide_profiles: list[dict[str, Any]] = []
    theme: dict[str, Any] = {}
    for artifact in manifest.get("slideArtifacts", []):
        number = int(artifact["slide"])
        layout_path = Path(artifact["layoutPath"])
        layout = read_json(layout_path)
        if not theme:
            theme = layout.get("theme", {})
        records = records_by_slide.get(number, [])
        title = next(
            (record for record in records if record.get("kind") == "textbox" and record.get("placeholder") == "title"),
            None,
        )
        subtitle = next(
            (record for record in records if record.get("kind") == "textbox" and record.get("placeholder") == "subtitle"),
            None,
        )
        eyebrow = next(
            (
                record
                for record in records
                if record.get("kind") == "textbox"
                and str(record.get("text", "")).strip().casefold() == "meeting subject"
            ),
            None,
        )
        inherited = list(iter_layout_elements(layout))
        body_candidates = [
            element
            for element in inherited
            if "placeholder 2" in str(element.get("name", "")).casefold()
            or "body level one" in str(element.get("text", "")).casefold()
        ]
        body_candidates = [element for element in body_candidates if _bbox_area(element) > 0]
        body = max(body_candidates, key=_bbox_area) if body_candidates else None
        layout_type = str(layout.get("slide", {}).get("layoutType", "")).casefold()
        role = "cover" if number == 1 and layout_type == "title" else "content" if title and body else "reference"
        slide_profiles.append(
            {
                "source_slide": number,
                "role": role,
                "layout_id": layout.get("slide", {}).get("layoutId"),
                "layout_name": layout.get("slide", {}).get("layoutName"),
                "frame": layout.get("slide", {}).get("frame"),
                "preview": artifact.get("previewPath"),
                "title_target": title,
                "subtitle_target": subtitle,
                "eyebrow_target": eyebrow,
                "safe_content_zone": (
                    {"x": body["bbox"][0], "y": body["bbox"][1], "w": body["bbox"][2], "h": body["bbox"][3]}
                    if body and isinstance(body.get("bbox"), list)
                    else None
                ),
                "actual_elements": [
                    {
                        key: record.get(key)
                        for key in ("kind", "id", "name", "placeholder", "text", "bbox")
                        if record.get(key) is not None
                    }
                    for record in records
                    if record.get("kind") not in ("slide", "notes")
                ],
                "locked_inherited_elements": [
                    {key: element.get(key) for key in ("kind", "id", "name", "bbox") if element.get(key) is not None}
                    for element in inherited
                    if element is not body
                ],
            }
        )

    colors = theme.get("colors", {}) if isinstance(theme, dict) else {}
    font_scheme = theme.get("fontScheme", {}) if isinstance(theme, dict) else {}
    fonts = sorted(
        {
            font
            for font in manifest.get("fonts", [])
            if isinstance(font, str) and font and not font.startswith("+")
        }
        | {
            value
            for role in (font_scheme.get("majorFont", {}), font_scheme.get("minorFont", {}))
            for value in role.values()
            if isinstance(value, str) and value and not value.startswith("+")
        }
    )
    return {
        "schema_version": 1,
        "source_pptx": str(paths["pptx"]),
        "source_sha256": sha256_file(paths["pptx"]),
        "slide_count": int(manifest.get("slideCount", 0)),
        "frame": slide_profiles[0].get("frame") if slide_profiles else None,
        "theme": {"colors": colors, "fonts": fonts, "font_scheme": font_scheme},
        "slides": slide_profiles,
        "precedence": "user template objects and tokens override every generic design default",
        "authoring_contract": "duplicate mapped source slides; edit inherited targets; add only inside declared safe zones",
    }


def run_template_inspection(config: dict[str, Any], config_path: Path, project: Path) -> dict[str, Any]:
    if not template_mode(config):
        raise LabDeckError("inspect-template applies only to schema v2 exact-clone-edit projects")
    paths = template_paths(config, project)
    if not paths["pptx"].exists():
        raise LabDeckError(f"template PPTX not found: {paths['pptx']}")
    require_default_template_integrity(paths["pptx"])
    node = find_node()
    if not node:
        raise LabDeckError("Node runtime unavailable; run doctor")
    scripts = presentations_template_scripts()
    paths["workspace"].mkdir(parents=True, exist_ok=True)
    run_command(
        [
            str(node),
            str(scripts / "inspect_template_deck.mjs"),
            "--workspace",
            str(paths["workspace"]),
            "--pptx",
            str(paths["pptx"]),
        ]
    )
    profile = derive_template_profile(paths)
    write_json(paths["profile"], profile)
    config.setdefault("template", {})["source_sha256"] = profile["source_sha256"]
    template_fonts = list(profile.get("theme", {}).get("fonts", []))
    font_scheme = profile.get("theme", {}).get("font_scheme", {})
    raw_minor_fonts = font_scheme.get("minorFont", {}) if isinstance(font_scheme, dict) else {}
    minor_fonts = raw_minor_fonts if isinstance(raw_minor_fonts, dict) else {}
    preferred_body = next(
        (
            value
            for key in ("latin", "ea", "cs")
            if isinstance((value := minor_fonts.get(key)), str)
            and value
            and not value.startswith("+")
        ),
        template_fonts[0] if template_fonts else "Arial",
    )
    style = config.setdefault("style", {})
    style["body_font"] = preferred_body
    style["template_fonts"] = template_fonts
    allowed_fonts = sorted(set(template_fonts + [preferred_body, str(style.get("code_font", "Courier New"))]))
    style["allowed_fonts"] = allowed_fonts
    retained_integrity = default_template_integrity()
    retained_lab_template = bool(
        retained_integrity.get("verified")
        and profile["source_sha256"] == retained_integrity.get("actual_sha256")
    )
    if retained_lab_template:
        default_thesis = "A disciplined Yonsei S3 research canvas: quiet white space, locked navy/blue lab chrome, and one mechanism-first visual anchor per content slide."
        default_system = "Clone the supplied cover for slide 1 and the supplied content frame for all body slides; preserve the top rule, left rail, S3 mark, title geometry, Arial typography, and theme colors; use the inherited body zone for native editable research content."
        default_signature = "locked S3/Yonsei chrome plus a single editable technical mechanism inside the inherited body zone"
    else:
        default_thesis = "A disciplined research canvas that preserves the supplied template identity and gives each content slide one audience-readable visual anchor."
        default_system = f"Clone only inspected source frames; preserve inherited chrome, title geometry, {preferred_body} typography, and theme colors; add native editable research content only inside declared safe zones."
        default_signature = "locked inherited template chrome plus one editable technical mechanism inside the approved content zone"
    design_path = project / "content/design-system.json"
    design = read_json(design_path) if design_path.exists() else {}
    preserve_design_declaration = design.get("template_sha256") == profile["source_sha256"]
    design.update(
        {
            "template_status": "approved",
            "template_sha256": profile["source_sha256"],
            "visual_thesis": design.get("visual_thesis") if preserve_design_declaration and design.get("visual_thesis") else default_thesis,
            "system_declaration": design.get("system_declaration") if preserve_design_declaration and design.get("system_declaration") else default_system,
            "palette": profile.get("theme", {}).get("colors", {}),
            "typefaces": template_fonts,
            "signature": design.get("signature") if preserve_design_declaration and design.get("signature") else default_signature,
            "anti_patterns": [
                "generic card dashboards",
                "decorative pills or metric strips",
                "unapproved gradients and shadows",
                "full-slide raster wrappers in native mode",
                "a second template or the bundled CUDA style mixed into this deck",
            ],
        }
    )
    write_json(design_path, design)
    write_json(config_path, config)
    audit_lines = [
        "TEMPLATE AUDIT: COMPLETE",
        f"Source: {paths['pptx']}",
        f"SHA256: {profile['source_sha256']}",
        f"Slides: {profile['slide_count']}",
        "Fonts: " + (", ".join(template_fonts) or "theme references only"),
        "Palette: " + ", ".join(f"{key}={value}" for key, value in profile.get("theme", {}).get("colors", {}).items()),
        "Contract: duplicate source slides; edit inherited targets; add only inside declared safe zones.",
    ]
    for slide in profile.get("slides", []):
        audit_lines.append(
            f"Source slide {slide['source_slide']}: role={slide['role']} layout={slide.get('layout_name')} safe_content_zone={slide.get('safe_content_zone')}"
        )
    write_text(project / "reports/template-audit.txt", "\n".join(audit_lines) + "\n")
    return profile


def pptx_package_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise LabDeckError(f"PPTX not found: {path}")
    with zipfile.ZipFile(path) as archive:
        corrupt_member = archive.testzip()
        names = archive.namelist()
        slide_names = sorted(
            (name for name in names if SLIDE_RE.match(name)),
            key=lambda name: int(SLIDE_RE.match(name).group(1)),  # type: ignore[union-attr]
        )
        notes_names = sorted(
            (name for name in names if NOTES_RE.match(name)),
            key=lambda name: int(NOTES_RE.match(name).group(1)),  # type: ignore[union-attr]
        )
        fonts: set[str] = set()
        visible_text: list[str] = []
        text_runs: list[str] = []
        empty_placeholder_shapes = 0
        for name in slide_names:
            root = ET.fromstring(archive.read(name))
            shape_text: dict[int, list[str]] = {}
            for index, element in enumerate(root.iter()):
                if element.tag.endswith("}t") and element.text:
                    visible_text.append(element.text)
                    text_runs.append(element.text.strip())
                typeface = element.attrib.get("typeface")
                if typeface and not typeface.startswith("+"):
                    fonts.add(typeface)
                if element.tag.endswith("}sp"):
                    texts = [node.text for node in element.iter() if node.tag.endswith("}t") and node.text]
                    has_placeholder = any(node.tag.endswith("}ph") for node in element.iter())
                    if has_placeholder and not texts:
                        shape_text[index] = []
            empty_placeholder_shapes += len(shape_text)
        return {
            "zip_ok": corrupt_member is None,
            "corrupt_member": corrupt_member,
            "slide_count": len(slide_names),
            "notes_part_count": len(notes_names),
            "fonts": sorted(fonts),
            "visible_text": "\n".join(visible_text),
            "code_label_count": sum(1 for text in text_runs if text == "CODE"),
            "empty_placeholder_shapes": empty_placeholder_shapes,
        }


def pptx_notes_text_by_slide(path: Path) -> dict[int, str]:
    if not path.exists():
        raise LabDeckError(f"PPTX not found while checking inherited notes: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            output: dict[int, str] = {}
            slide_names = sorted(
                (name for name in names if SLIDE_RE.match(name)),
                key=lambda name: int(SLIDE_RE.match(name).group(1)),  # type: ignore[union-attr]
            )
            for slide_name in slide_names:
                slide_number = int(SLIDE_RE.match(slide_name).group(1))  # type: ignore[union-attr]
                rels_name = posixpath.join(
                    posixpath.dirname(slide_name),
                    "_rels",
                    posixpath.basename(slide_name) + ".rels",
                )
                note_part: str | None = None
                if rels_name in names:
                    rels_root = ET.fromstring(archive.read(rels_name))
                    for relationship in rels_root.iter():
                        if not relationship.tag.endswith("}Relationship"):
                            continue
                        if str(relationship.attrib.get("Type", "")).endswith("/notesSlide"):
                            target = relationship.attrib.get("Target")
                            if target:
                                note_part = posixpath.normpath(
                                    posixpath.join(posixpath.dirname(slide_name), target)
                                ).lstrip("/")
                            break
                if note_part and note_part in names:
                    note_root = ET.fromstring(archive.read(note_part))
                    texts = [
                        str(element.text).strip()
                        for element in note_root.iter()
                        if element.tag.endswith("}t") and element.text and str(element.text).strip()
                    ]
                    output[slide_number] = " ".join(texts).strip()
                else:
                    output[slide_number] = ""
            return output
    except (OSError, zipfile.BadZipFile, ET.ParseError) as error:
        raise LabDeckError(f"could not inspect inherited speaker notes in {path}: {error}") from error


def validate_notes_contract(
    config: dict[str, Any],
    project: Path,
    expected_slides: int,
    inherited_deck: Path | None,
) -> list[str]:
    notes_config = config.get("notes", {})
    if not notes_config.get("enabled", True):
        return []
    errors: list[str] = []
    notes_path = resolve_path(notes_config.get("path"), project)
    if not notes_path or not notes_path.exists():
        return [f"notes JSON missing: {notes_path}"]
    try:
        raw = read_json(notes_path)
    except LabDeckError as error:
        return [str(error)]
    entries = raw if isinstance(raw, list) else raw.get("slides") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return ["notes JSON must be an array or contain a slides array"]
    if len(entries) != expected_slides:
        errors.append(f"notes cover {len(entries)} slides, but slide plan has {expected_slides}")
    preserve_slides: list[tuple[int, dict[str, Any]]] = []
    for index, raw_entry in enumerate(entries, 1):
        if isinstance(raw_entry, str):
            continue
        if not isinstance(raw_entry, dict):
            errors.append(f"notes entry {index} must be a string or object")
            continue
        raw_slide = raw_entry.get("slide", index)
        if isinstance(raw_slide, bool):
            errors.append(f"notes entry {index} has invalid slide number")
            continue
        try:
            slide_number = int(raw_slide)
        except (TypeError, ValueError):
            errors.append(f"notes entry {index} has invalid slide number")
            continue
        if slide_number != index:
            errors.append(f"notes must be sequential; expected slide {index}, found {slide_number}")
        mode = str(raw_entry.get("mode", "replace")).strip().casefold()
        if mode not in {"replace", "preserve"}:
            errors.append(f"slide {slide_number} notes mode must be replace or preserve")
        elif mode == "preserve":
            rationale = str(
                raw_entry.get("preserve_rationale") or raw_entry.get("rationale") or ""
            ).strip()
            if not rationale or re.search(r"\b(?:todo|tbd|pending|sample)\b", rationale, re.IGNORECASE):
                errors.append(f"slide {slide_number} preserve-mode notes require a specific rationale")
            preserve_slides.append((slide_number, raw_entry))
    if preserve_slides:
        if not inherited_deck or not inherited_deck.exists():
            errors.append("preserve-mode notes require an inspectable inherited input deck")
            return errors
        try:
            inherited_notes = pptx_notes_text_by_slide(inherited_deck)
        except LabDeckError as error:
            errors.append(str(error))
            return errors
        configured_forbidden = notes_config.get(
            "preserve_forbidden_fragments", list(DEFAULT_FORBIDDEN_PRESERVED_NOTES)
        )
        forbidden = {
            fragment.strip().casefold()
            for fragment in DEFAULT_FORBIDDEN_PRESERVED_NOTES
            if fragment.strip()
        }
        if isinstance(configured_forbidden, list):
            forbidden.update(
                str(value).strip().casefold() for value in configured_forbidden if str(value).strip()
            )
        for slide_number, _ in preserve_slides:
            inherited = inherited_notes.get(slide_number, "").strip()
            if not inherited:
                errors.append(f"slide {slide_number} preserve-mode inherited note is empty")
                continue
            normalized = inherited.casefold()
            matched = next((fragment for fragment in sorted(forbidden) if fragment in normalized), None)
            if matched:
                errors.append(
                    f"slide {slide_number} preserve-mode note contains forbidden template/sample text: {matched!r}"
                )
    return errors


def setup_artifact_workspace(project_dir: Path) -> Path:
    skill = find_presentations_skill()
    node = find_node()
    if not skill or not node:
        raise LabDeckError("bundled Presentations skill or Node runtime is unavailable; run doctor")
    setup_script = skill / "container_tools" / "setup_artifact_tool_workspace.mjs"
    if not setup_script.exists():
        raise LabDeckError(f"Artifact Tool workspace helper missing: {setup_script}")
    run_command([str(node), str(setup_script), "--workspace", str(project_dir)])
    helper_dir = project_dir / ".labdeck-tools"
    helper_dir.mkdir(parents=True, exist_ok=True)
    for name in ("inject_notes.mjs", "inspect_deck.mjs"):
        source = HELPERS_DIR / name
        if not source.exists():
            raise LabDeckError(f"skill helper missing: {source}")
        shutil.copy2(source, helper_dir / name)
    return helper_dir


def inspect_with_artifact_tool(deck: Path, project_dir: Path) -> dict[str, Any]:
    node = find_node()
    if not node:
        raise LabDeckError("Node runtime unavailable")
    helper_dir = setup_artifact_workspace(project_dir)
    result = run_command(
        [str(node), str(helper_dir / "inspect_deck.mjs"), str(deck)],
        cwd=project_dir,
        capture=True,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise LabDeckError(f"deck inspector emitted invalid JSON: {result.stdout}") from error


def soffice_roundtrip(source: Path, destination: Path) -> None:
    """Create a disposable LibreOffice probe; this does not establish geometry fidelity."""
    soffice = find_soffice()
    if not soffice:
        raise LabDeckError("LibreOffice/soffice unavailable; run doctor or disable qa.compatibility_probe")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="labdeck-lo-") as temp_name:
        temp = Path(temp_name)
        output_dir = temp / "out"
        profile_dir = temp / "profile"
        output_dir.mkdir()
        profile_dir.mkdir()
        command = [
            str(soffice),
            "--headless",
            f"-env:UserInstallation={profile_dir.as_uri()}",
            "--convert-to",
            "pptx",
            "--outdir",
            str(output_dir),
            str(source),
        ]
        result = run_command(command, capture=True)
        candidate = output_dir / f"{source.stem}.pptx"
        if not candidate.exists():
            raise LabDeckError(
                f"LibreOffice did not produce {candidate.name}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        shutil.copy2(candidate, destination)


def command_doctor(_: argparse.Namespace) -> int:
    node = find_node()
    python = find_python()
    skill = find_presentations_skill()
    soffice = find_soffice()
    ssh = shutil.which("ssh")
    template_scripts = skill / "template_following_scripts" if skill else None
    artifact_helper = skill / "container_tools/setup_artifact_tool_workspace.mjs" if skill else None
    required_template_helpers = (
        "inspect_template_deck.mjs",
        "validate_template_plan.mjs",
        "prepare_template_starter_deck.mjs",
        "check_template_fidelity.mjs",
    )
    template_helpers_ok = bool(
        template_scripts
        and all((template_scripts / name).exists() for name in required_template_helpers)
    )
    default_integrity = default_template_integrity()
    core_checks = {
        "node": bool(node),
        "python": bool(python),
        "presentations_skill": bool(skill),
        "artifact_workspace_helper": bool(artifact_helper and artifact_helper.exists()),
        "template_helpers": template_helpers_ok,
        "default_template_integrity": bool(default_integrity.get("verified")),
    }
    optional_checks = {
        "libreoffice_package_notes_probe": bool(soffice),
        "ssh_gpu_measurements": bool(ssh),
    }
    missing_core = [name for name, available in core_checks.items() if not available]
    missing_optional = [name for name, available in optional_checks.items() if not available]
    report = {
        "skill_dir": str(SKILL_DIR),
        "node": str(node) if node else None,
        "python": str(python) if python else None,
        "soffice": str(soffice) if soffice else None,
        "ssh": ssh,
        "presentations_skill": str(skill) if skill else None,
        "artifact_workspace_helper": str(artifact_helper) if artifact_helper else None,
        "template_helpers": str(template_scripts) if template_scripts else None,
        "default_template": str(DEFAULT_TEMPLATE) if DEFAULT_TEMPLATE.exists() else None,
        "default_template_sha256": sha256_file(DEFAULT_TEMPLATE) if DEFAULT_TEMPLATE.exists() else None,
        "default_template_integrity": default_integrity,
        "core": {"checks": core_checks, "missing": missing_core},
        "optional": {
            "checks": optional_checks,
            "missing": missing_optional,
            "note": "LibreOffice is only a limited package/slide-count/speaker-note persistence probe; SSH is only needed for live GPU evidence.",
        },
        "status": "ok" if not missing_core else "missing-core-tools",
    }
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "ok" else 2


def refuse_overwrite(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise LabDeckError(f"refusing to overwrite existing file: {path}; pass --force to replace it")


def profile_slide(profile: dict[str, Any], role: str | None, source_slide: int | None) -> dict[str, Any]:
    slides = profile.get("slides", [])
    if not isinstance(slides, list) or any(not isinstance(slide, dict) for slide in slides):
        raise LabDeckError("template profile slides must be an array of objects")
    if source_slide:
        match = next((slide for slide in slides if slide.get("source_slide") == source_slide), None)
        if match:
            return match
        raise LabDeckError(f"template profile has no source slide {source_slide}")
    match = next((slide for slide in slides if slide.get("role") == role), None)
    if match:
        return match
    raise LabDeckError(f"template profile has no frame for role {role!r}; set template_frame.source_slide explicitly")


def default_edit_targets(frame: dict[str, Any]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for key, content_ref in (
        ("title_target", "title_claim"),
        ("subtitle_target", "subtitle"),
        ("eyebrow_target", "meeting_subject"),
    ):
        target = frame.get(key)
        if not isinstance(target, dict) or not target.get("id"):
            continue
        targets.append(
            {
                "action": "rewrite",
                "sourceElementId": target["id"],
                "sourceName": target.get("name"),
                "contentRef": content_ref,
            }
        )
    zone = frame.get("safe_content_zone")
    if frame.get("role") == "content" and isinstance(zone, dict):
        targets.append(
            {
                "action": "add",
                "newPrimitiveAllowed": True,
                "zone": zone,
                "reason": "The inherited layout defines this exact body placeholder region as the content canvas.",
                "mustNotOverlapInherited": True,
                "contentRef": "body",
            }
        )
    return targets


def declared_advanced_edit_actions(config: dict[str, Any], project: Path) -> set[str]:
    contract = config.get("build", {}).get("advanced_edit_contract")
    if contract in (None, False):
        return set()
    if not isinstance(contract, dict) or contract.get("enabled") is not True:
        raise LabDeckError("build.advanced_edit_contract must be an object with enabled=true")
    contract_id = str(contract.get("id", "")).strip()
    rationale = str(contract.get("rationale", "")).strip()
    raw_actions = contract.get("actions")
    if not contract_id or not rationale or not isinstance(raw_actions, list) or not raw_actions:
        raise LabDeckError("advanced edit contract requires nonempty id, rationale, and actions")
    actions = {str(action).strip() for action in raw_actions if str(action).strip()}
    if len(actions) != len(raw_actions):
        raise LabDeckError("advanced edit contract actions must be unique nonempty strings")
    builder = resolve_path(config.get("build", {}).get("builder"), project)
    if not builder or not builder.exists():
        raise LabDeckError("advanced edit contract requires an existing project builder")
    marker = f"LABDECK_ADVANCED_EDIT_CONTRACT: {contract_id}"
    if marker not in builder.read_text(encoding="utf-8"):
        raise LabDeckError(f"advanced edit contract is not declared by the builder; add marker {marker!r}")
    return actions


def validate_edit_targets(targets: Any, slide_number: int, advanced_actions: set[str]) -> list[dict[str, Any]]:
    if not isinstance(targets, list):
        raise LabDeckError(f"slide {slide_number} template_frame.edit_targets must be an array")
    validated: list[dict[str, Any]] = []
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            raise LabDeckError(f"slide {slide_number} edit target {index + 1} must be an object")
        action = str(target.get("action", "")).strip()
        if not action:
            raise LabDeckError(f"slide {slide_number} edit target {index + 1} is missing action")
        if action in advanced_actions:
            validated.append(target)
            continue
        if action not in {"rewrite", "add"}:
            raise LabDeckError(
                f"slide {slide_number} edit target action {action!r} is unsupported by the scaffold; "
                "declare a marked build.advanced_edit_contract for deck-specific handling"
            )
        if action == "rewrite":
            for field in ("sourceElementId", "sourceName", "contentRef"):
                if not str(target.get(field, "")).strip():
                    raise LabDeckError(
                        f"slide {slide_number} rewrite target {index + 1} requires nonempty {field}; "
                        "the scaffold resolves inherited objects by sourceName"
                    )
        else:
            zone = target.get("zone")
            if not isinstance(zone, dict):
                raise LabDeckError(f"slide {slide_number} add target {index + 1} requires a bounded zone")
            try:
                values = [float(zone[key]) for key in ("x", "y", "w", "h")]
            except (KeyError, TypeError, ValueError) as error:
                raise LabDeckError(
                    f"slide {slide_number} add target {index + 1} zone must contain numeric x/y/w/h"
                ) from error
            if not all(math.isfinite(value) for value in values):
                raise LabDeckError(f"slide {slide_number} add target {index + 1} zone values must be finite")
            if values[0] < 0 or values[1] < 0 or values[2] <= 0 or values[3] <= 0:
                raise LabDeckError(
                    f"slide {slide_number} add target {index + 1} zone requires non-negative x/y and positive w/h"
                )
            if target.get("newPrimitiveAllowed") is not True:
                raise LabDeckError(f"slide {slide_number} add target {index + 1} must set newPrimitiveAllowed=true")
            if target.get("mustNotOverlapInherited") is not True:
                raise LabDeckError(
                    f"slide {slide_number} add target {index + 1} must set mustNotOverlapInherited=true"
                )
            if not str(target.get("reason", "")).strip():
                raise LabDeckError(f"slide {slide_number} add target {index + 1} requires a reason")
        validated.append(target)
    return validated


def validate_frame_map_document(frame_map: Any, advanced_actions: set[str]) -> dict[str, Any]:
    if not isinstance(frame_map, dict):
        raise LabDeckError("template frame map must be a JSON object")
    output_slides = frame_map.get("outputSlides")
    omitted = frame_map.get("omittedSourceSlides")
    if not isinstance(output_slides, list) or not isinstance(omitted, list):
        raise LabDeckError("template frame map requires outputSlides and omittedSourceSlides arrays")
    for expected, entry in enumerate(output_slides, 1):
        if not isinstance(entry, dict):
            raise LabDeckError(f"template frame map output slide {expected} must be an object")
        if entry.get("outputSlide") != expected:
            raise LabDeckError(f"template frame map outputSlide must be sequential; expected {expected}")
        source_slide = entry.get("sourceSlide")
        if isinstance(source_slide, bool) or not isinstance(source_slide, int) or source_slide < 1:
            raise LabDeckError(f"template frame map slide {expected} has invalid sourceSlide")
        validate_edit_targets(entry.get("editTargets"), expected, advanced_actions)
    return frame_map


def build_template_frame_map(
    profile: dict[str, Any],
    plan: dict[str, Any],
    advanced_actions: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(profile, dict) or not isinstance(plan, dict):
        raise LabDeckError("template profile and slide plan must be JSON objects")
    raw_slides = plan.get("slides")
    if not isinstance(raw_slides, list):
        raise LabDeckError("slide plan must contain a slides array")
    actions = advanced_actions or set()
    output_slides: list[dict[str, Any]] = []
    used_source_slides: set[int] = set()
    slides: list[dict[str, Any]] = []
    for index, entry in enumerate(raw_slides, 1):
        if not isinstance(entry, dict):
            raise LabDeckError(f"slide plan entry {index} must be an object")
        number = entry.get("slide")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise LabDeckError(f"slide plan entry {index} has invalid slide number: {number!r}")
        slides.append(entry)
    slides.sort(key=lambda entry: entry["slide"])
    for expected, entry in enumerate(slides, 1):
        if entry.get("slide") != expected:
            raise LabDeckError(f"slide plan must be sequential from 1; expected slide {expected}")
        frame_spec = entry.get("template_frame")
        if not isinstance(frame_spec, dict):
            raise LabDeckError(f"slide {expected} template_frame must be an object in schema v2")
        role = str(frame_spec.get("role", "content"))
        source_slide = frame_spec.get("source_slide")
        if source_slide is not None:
            if isinstance(source_slide, bool):
                raise LabDeckError(f"slide {expected} template_frame.source_slide must be an integer")
            try:
                source_slide = int(source_slide)
            except (TypeError, ValueError) as error:
                raise LabDeckError(f"slide {expected} template_frame.source_slide must be an integer") from error
            if source_slide < 1:
                raise LabDeckError(f"slide {expected} template_frame.source_slide must be positive")
        frame = profile_slide(profile, role, source_slide)
        raw_frame_source = frame.get("source_slide")
        if isinstance(raw_frame_source, bool):
            raise LabDeckError(f"template profile frame for slide {expected} has invalid source_slide")
        try:
            frame_source = int(raw_frame_source)
        except (TypeError, ValueError) as error:
            raise LabDeckError(f"template profile frame for slide {expected} has invalid source_slide") from error
        if frame_source < 1:
            raise LabDeckError(f"template profile frame for slide {expected} has invalid source_slide")
        targets = frame_spec.get("edit_targets")
        if targets is None:
            targets = default_edit_targets(frame)
        targets = validate_edit_targets(targets, expected, actions)
        used_source_slides.add(frame_source)
        output_slides.append(
            {
                "outputSlide": expected,
                "sourceSlide": frame_source,
                "narrativeRole": str(entry.get("narrative_job", "")).strip(),
                "reuseMode": "duplicate-slide",
                "editTargets": targets,
                "layoutFamily": frame_spec.get("layout_family", role),
                "densityBudget": frame_spec.get("density_budget", "sparse" if role == "cover" else "medium"),
                "visualAnchor": entry.get("visual_anchor") or entry.get("mechanism_visual"),
                "routingRationale": frame_spec.get("routing_rationale"),
            }
        )
    try:
        profile_slide_count = int(profile.get("slide_count", 0))
    except (TypeError, ValueError) as error:
        raise LabDeckError("template profile slide_count must be an integer") from error
    if profile_slide_count < 0:
        raise LabDeckError("template profile slide_count must not be negative")
    omitted = [
        {"sourceSlide": number, "reason": "not selected by the approved narrative"}
        for number in range(1, profile_slide_count + 1)
        if number not in used_source_slides
    ]
    return validate_frame_map_document(
        {"outputSlides": output_slides, "omittedSourceSlides": omitted},
        actions,
    )


def command_inspect_template(args: argparse.Namespace) -> int:
    config, config_path, project = load_config(args.config)
    profile = run_template_inspection(config, config_path, project)
    paths = template_paths(config, project)
    print(json.dumps({"profile": str(paths["profile"]), "source_sha256": profile["source_sha256"]}, indent=2))
    return 0


def command_prepare_template(args: argparse.Namespace) -> int:
    config, config_path, project = load_config(args.config)
    if not template_mode(config):
        raise LabDeckError("prepare-template applies only to exact-clone-edit projects")
    paths = template_paths(config, project)
    if not paths["pptx"].exists():
        raise LabDeckError(f"template PPTX not found: {paths['pptx']}")
    require_default_template_integrity(paths["pptx"])
    current_source_sha = sha256_file(paths["pptx"])
    inspection_stale = not paths["profile"].exists() or not paths["inspect_manifest"].exists()
    if not inspection_stale:
        try:
            profile_source_sha = str(read_json(paths["profile"]).get("source_sha256", ""))
        except (AttributeError, LabDeckError):
            profile_source_sha = ""
        inspection_stale = (
            str(template_config(config).get("source_sha256", "")) != current_source_sha
            or profile_source_sha != current_source_sha
        )
    if inspection_stale:
        run_template_inspection(config, config_path, project)
        config, config_path, project = load_config(config_path)
        paths = template_paths(config, project)
    plan_path = project / "content/slide-plan.json"
    if not plan_path.exists():
        raise LabDeckError(f"slide plan missing: {plan_path}")
    profile = read_json(paths["profile"])
    plan = read_json(plan_path)
    advanced_actions = declared_advanced_edit_actions(config, project)
    frame_map = build_template_frame_map(profile, plan, advanced_actions)
    write_json(paths["frame_map"], frame_map)

    node = find_node()
    if not node:
        raise LabDeckError("Node runtime unavailable; run doctor")
    scripts = presentations_template_scripts()
    run_command(
        [
            str(node),
            str(scripts / "prepare_template_starter_deck.mjs"),
            "--workspace",
            str(paths["workspace"]),
            "--pptx",
            str(paths["pptx"]),
            "--map",
            str(paths["frame_map"]),
            "--out",
            str(paths["starter"]),
            "--preview-dir",
            str(paths["starter_preview"]),
            "--layout-dir",
            str(paths["starter_layout"]),
            "--contact-sheet",
            str(paths["starter_contact_sheet"]),
        ]
    )
    config.setdefault("deck", {})["expected_slides"] = len(frame_map["outputSlides"])
    config.setdefault("build", {})["input_pptx"] = relative_or_absolute(paths["starter"], project)
    config.setdefault("template", {})["frame_map_sha256"] = sha256_file(paths["frame_map"])
    config["template"]["starter_sha256"] = sha256_file(paths["starter"])
    config["template"]["starter_source_sha256"] = current_source_sha
    if not paths["starter_manifest"].exists():
        raise LabDeckError(f"template starter manifest was not generated: {paths['starter_manifest']}")
    config["template"]["starter_manifest_sha256"] = sha256_file(paths["starter_manifest"])
    write_json(config_path, config)
    deviation_log = project / "reports/deviation-log.txt"
    if not deviation_log.exists():
        write_text(
            deviation_log,
            "TEMPLATE DEVIATIONS\n\nNone. Add one line per intentional change outside inherited edit targets.\n",
        )
    print(paths["starter"])
    return 0


def command_init(args: argparse.Namespace) -> int:
    project = Path(args.project_dir).expanduser().resolve()
    config_path = project / "labdeck.json"
    refuse_overwrite(config_path, args.force)
    for subdir in (
        "content",
        "build",
        "evidence/raw",
        "evidence/normalized",
        "output",
        "render",
        "reports",
        "work",
    ):
        (project / subdir).mkdir(parents=True, exist_ok=True)

    source = Path(args.source_pptx).expanduser().resolve()
    if not source.exists():
        raise LabDeckError(f"source PPTX not found: {source}")
    template_arg = args.template_pptx or args.reference_pptx
    reference = Path(template_arg).expanduser().resolve() if template_arg else DEFAULT_TEMPLATE
    if not reference.exists():
        raise LabDeckError(f"template PPTX not found: {reference}")
    require_default_template_integrity(reference)
    output_name = args.output_name or f"{source.stem}-lab-meeting-final.pptx"
    if not output_name.lower().endswith(".pptx"):
        output_name += ".pptx"

    config = {
        "schema_version": 2,
        "deck": {
            "title": args.title or source.stem,
            "audience": "research lab meeting",
            "communication_job": "",
            "duration_minutes": 30,
            "authoring_mode": "html-assisted-native" if args.html_prototype else "template-native",
            "source_pptx": str(source),
            "visual_reference": str(reference),
            "expected_slides": None,
            "final_pptx": f"output/{output_name}",
        },
        "sources": [
            {"id": "source-deck", "path": str(source), "role": "existing-deck"},
            {"id": "visual-reference", "path": str(reference), "role": "visual-language"},
        ],
        "template": {
            "mode": "exact-clone-edit",
            "pptx": str(reference),
            "workspace": "work/template",
            "profile": "content/template-profile.json",
            "frame_map": "content/template-frame-map.json",
            "starter_pptx": "work/template/template-starter.pptx",
            "source_sha256": "",
            "preserve_unmapped_objects": True,
            "allow_bounded_insertions": True,
            "html_prototype": {
                "allowed": bool(args.html_prototype),
                "role": "disposable visual prototype only",
                "disposable": bool(args.html_prototype),
                "final_source": False,
                "direct_conversion": False,
                "editable_body": True,
                "allow_full_slide_raster": False,
                "html_paths": ["work/html-prototype/index.html"] if args.html_prototype else [],
                "render_paths": ["work/html-prototype/prototype.png"] if args.html_prototype else [],
                "manifest": "content/native-rebuild-manifest.json" if args.html_prototype else None,
            },
        },
        "coverage": {"required": []},
        "kernel_sequence": {
            "comparison_mode": "one-change-per-slide",
            "require_exact_code_delta": True,
            "require_mechanism_visual": True,
            "require_previous_kernel_reference": True,
        },
        "measurements": {
            "enabled": False,
            "host": "l40s-yunm",
            "physical_device": 3,
            "expected_gpu_name": "NVIDIA L40S",
            "expected_uuid": "",
            "timing": {
                "tool": "cuda-events",
                "fresh_processes": 3,
                "warmups": 10,
                "samples": 101,
                "statistic": "median-of-process-medians",
            },
            "nsight_compute": {"separate_run": True, "allowed_use": "mechanism-attribution-only"},
            "nsight_systems": {"separate_run": True, "allowed_use": "pass-decomposition-only"},
        },
        "build": {
            "builder": "build/builder.mjs",
            "input_pptx": "work/template/template-starter.pptx",
            "artifact_pptx": "work/deck-artifact.pptx",
            "visual_pptx": "work/deck-visual.pptx",
            "compatibility_roundtrip": False,
        },
        "notes": {
            "enabled": True,
            "path": "content/notes.json",
            "minimum_words": 30,
            "maximum_words": 190,
            "visible": True,
            "preserve_forbidden_fragments": list(DEFAULT_FORBIDDEN_PRESERVED_NOTES),
            "role_ranges": {
                "cover": [20, 80],
                "divider": [20, 90],
                "closing": [30, 100],
                "appendix": [40, 120],
                "mechanism": [90, 190],
                "evidence": [90, 190],
                "default": [60, 170],
            },
        },
        "style": {
            "body_font": "Arial",
            "code_font": "Courier New",
            "allowed_fonts": ["Arial", "Courier New"],
            "minimum_body_pt": 16,
            "template_fonts": [],
            "technical_shapes_editable": True,
            "decorative_programmatic_shapes": False,
            "preserve_editable_lab_lockup": True,
        },
        "qa": {
            "required_text": [],
            "compatibility_probe": True,
            "require_review_reports": True,
            "require_template_fidelity": True,
            "render_width": 1600,
            "render_height": 900,
            "visual_contract": dict(DEFAULT_VISUAL_CONTRACT),
            "user_acceptance": {
                "required": True,
                "status": "pending",
                "reviewer": "",
                "accepted_at": "",
                "reviewed_deck_sha256": "",
            },
        },
    }
    write_json(config_path, config)

    write_text(
        project / "content/outline.md",
        "# Deck outline\n\n## Communication job\n\nBy the end, **[audience]** should **[outcome]** because **[central takeaway]**.\n\n## Coverage matrix\n\n| Required topic | Planned slides | Source IDs | Evidence IDs | Status |\n|---|---:|---|---|---|\n\n## Slide sequence\n\n1. Opening thesis\n",
    )
    write_json(
        project / "content/slide-plan.json",
        {
            "schema_version": 1,
            "comparison_contract": "one-change-per-slide",
            "slides": [],
        },
    )
    write_json(project / "content/template-frame-map.json", {"outputSlides": [], "omittedSourceSlides": []})
    write_json(
        project / "content/design-system.json",
        {
            "template_status": "pending-inspection",
            "template_sha256": "",
            "visual_thesis": "",
            "system_declaration": "",
            "palette": {},
            "typefaces": [],
            "signature": "",
            "anti_patterns": [],
        },
    )
    write_json(project / "content/claims.json", {"schema_version": 1, "claims": []})
    write_json(project / "content/notes.json", {"schema_version": 1, "slides": []})
    builder_scaffold = ASSETS_DIR / "template-builder-scaffold.mjs"
    if not builder_scaffold.exists():
        raise LabDeckError(f"builder scaffold missing: {builder_scaffold}")
    shutil.copy2(builder_scaffold, project / "build/builder.mjs")
    for pass_name, title in (
        ("pass-a.md", "Pass A: System Contract / Constraint Integrity"),
        ("pass-b.md", "Pass B: Audience Impact / Expressive Readability"),
    ):
        write_text(
            project / "reports" / pass_name,
            f"# {title}\n\nVERDICT: PENDING\nConfidence: Pending\nEvidence: PENDING\nDeck SHA256: PENDING\nRender manifest SHA256: PENDING\nSource manifest SHA256: PENDING\nUnresolved Critical: PENDING\nBlocking findings: PENDING\n\n## Checks\n\n- [ ] Template fidelity and inherited chrome: PENDING\n- [ ] Composition, hierarchy, and 3-5 second takeaway: PENDING\n- [ ] Typography, palette, CJK wrapping, and anti-slop: PENDING\n- [ ] Content truth, provenance, and editability: PENDING\n\n## Findings\n\n| Slide | Finding | Severity | Fix | Status |\n|---|---|---|---|---|\n",
        )
    write_text(
        project / "reports/design-debt.md",
        "# Design debt\n\n| Slide | Finding | Severity | Disposition |\n|---|---|---|---|\n",
    )
    run_template_inspection(config, config_path, project)
    print(config_path)
    return 0


def native_rebuild_contract_errors(config: dict[str, Any], config_path: Path, project: Path) -> list[str]:
    mode = str(config.get("deck", {}).get("authoring_mode", "")).strip()
    prototype = template_config(config).get("html_prototype")
    allowed = isinstance(prototype, dict) and prototype.get("allowed") is True
    if mode == "html-assisted-native":
        if not allowed:
            return ["html-assisted-native mode requires template.html_prototype.allowed=true"]
        manifest_value = prototype.get("manifest") or "content/native-rebuild-manifest.json"
        manifest_path = resolve_path(str(manifest_value), project)
        if not manifest_path or not manifest_path.exists():
            return [
                f"html-assisted-native manifest missing: {manifest_path}; run scripts/native_rebuild_manifest.py generate after prepare-template"
            ]
        python = find_python()
        validator = HELPERS_DIR / "native_rebuild_manifest.py"
        if not python or not validator.exists():
            return ["native rebuild manifest validator or Python runtime is unavailable"]
        result = run_command(
            [
                str(python),
                str(validator),
                "validate",
                "--config",
                str(config_path),
                "--manifest",
                str(manifest_path),
            ],
            capture=True,
            check=False,
        )
        if result.returncode != 0:
            return [
                "html-assisted-native manifest validation failed: "
                + (result.stderr or result.stdout or "unknown manifest validation error").strip()
            ]
        return []
    if allowed:
        return ["template.html_prototype.allowed=true requires deck.authoring_mode=html-assisted-native"]
    if mode != "template-native":
        return [f"unsupported template authoring mode: {mode!r}"]
    return []


def audit_config(config: dict[str, Any], config_path: Path, project: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    visual_contract_report: dict[str, Any] = {
        "status": "not-run",
        "checks": [],
        "errors": [],
        "warnings": [],
    }
    title_fit_preflight: dict[str, Any] = {
        "status": "not-run",
        "checks": [],
        "errors": [],
        "warnings": [],
    }
    manifest: list[dict[str, str]] = []
    deck = config.get("deck", {})
    source = resolve_path(deck.get("source_pptx"), project)
    reference = resolve_path(deck.get("visual_reference"), project)
    for label, path in (("source PPTX", source), ("visual reference", reference)):
        if not path or not path.exists():
            errors.append(f"missing {label}: {path}")

    communication_job = str(deck.get("communication_job", "")).strip()
    if config.get("schema_version") == 2 and (
        not communication_job or re.search(r"\[(?:audience|outcome|central takeaway)\]|\btodo\b", communication_job, re.IGNORECASE)
    ):
        errors.append("deck.communication_job must state the audience outcome and central takeaway")
    if config.get("schema_version") == 2 and template_mode(config):
        errors.extend(native_rebuild_contract_errors(config, config_path, project))

    design_path = project / "content/design-system.json"
    design: dict[str, Any] = {}
    if config.get("schema_version") == 2:
        if not design_path.exists():
            errors.append(f"missing design system declaration: {design_path}")
        else:
            design = read_json(design_path)
            for field in ("visual_thesis", "system_declaration", "signature"):
                value = str(design.get(field, "")).strip()
                if not value or re.search(r"\b(?:todo|pending|tbd)\b", value, re.IGNORECASE):
                    errors.append(f"design-system {field} is incomplete")
            if design.get("template_status") != "approved":
                errors.append("design-system template_status must be approved before authoring")

    for source_item in config.get("sources", []):
        path_value = source_item.get("path")
        if not path_value:
            errors.append(f"source {source_item.get('id', '<missing-id>')} has no path")
            continue
        if str(path_value).startswith(("http://", "https://")):
            warnings.append(f"remote source is not hash-pinned locally: {path_value}")
            continue
        path = resolve_path(path_value, project)
        if not path or not path.exists():
            errors.append(f"missing source {source_item.get('id')}: {path}")
            continue
        manifest.append(
            {
                "id": str(source_item.get("id", "")),
                "role": str(source_item.get("role", "")),
                "path": str(path),
                "sha256": sha256_file(path),
            }
        )

    plan_path = project / "content/slide-plan.json"
    if not plan_path.exists():
        errors.append(f"missing slide plan: {plan_path}")
        plan = {"slides": []}
    else:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    slides = plan.get("slides", [])
    if not slides:
        errors.append("slide plan is empty")
    required_topics = set(config.get("coverage", {}).get("required", []))
    if not required_topics:
        errors.append("coverage.required is empty")
    covered_topics: set[str] = set()
    seen_slide_numbers: set[int] = set()
    for entry in slides:
        number = entry.get("slide")
        if not isinstance(number, int) or number < 1:
            errors.append(f"slide-plan entry has invalid slide number: {number}")
            continue
        if number in seen_slide_numbers:
            errors.append(f"duplicate slide-plan entry: slide {number}")
        seen_slide_numbers.add(number)
        covered_topics.update(entry.get("coverage", []))
        for field in ("narrative_job", "title_claim", "template_frame", "evidence_refs", "notes_ref"):
            if field not in entry:
                errors.append(f"slide {number} is missing {field}")
        for field in ("narrative_job", "title_claim"):
            value = str(entry.get(field, ""))
            if re.search(r"\b(?:todo|tbd|pending|forward[- ]?test|preserve slide)\b", value, re.IGNORECASE):
                errors.append(f"slide {number} has placeholder {field}: {value}")
        if config.get("schema_version") == 2 and template_mode(config):
            frame = entry.get("template_frame")
            if not isinstance(frame, dict):
                errors.append(f"slide {number} template_frame must be an object with role/source routing")
            else:
                if not frame.get("role") and not frame.get("source_slide"):
                    errors.append(f"slide {number} template_frame needs role or source_slide")
                if not str(frame.get("routing_rationale", "")).strip():
                    errors.append(f"slide {number} template_frame is missing routing_rationale")
                if not str(entry.get("visual_anchor") or entry.get("mechanism_visual") or "").strip():
                    errors.append(f"slide {number} is missing a visual_anchor")
        if entry.get("previous_kernel") or entry.get("current_kernel"):
            for field in (
                "previous_kernel",
                "current_kernel",
                "unchanged",
                "changed_lines",
                "mechanism_visual",
                "evidence_refs",
            ):
                value = entry.get(field)
                if value in (None, "", []):
                    errors.append(f"incremental kernel slide {number} is missing {field}")
    missing_topics = sorted(required_topics - covered_topics)
    if missing_topics:
        errors.append("required coverage absent from slide plan: " + ", ".join(missing_topics))

    inherited_notes_deck = resolve_path(config.get("build", {}).get("input_pptx"), project)
    if not inherited_notes_deck or not inherited_notes_deck.exists():
        inherited_notes_deck = reference
    errors.extend(validate_notes_contract(config, project, len(slides), inherited_notes_deck))

    claims_path = project / "content/claims.json"
    if not claims_path.exists():
        errors.append(f"missing claims ledger: {claims_path}")
        claims = []
    else:
        claims = json.loads(claims_path.read_text(encoding="utf-8")).get("claims", [])
    seen_claims: set[str] = set()
    valid_claim_ids: set[str] = set()
    for claim in claims:
        claim_id = claim.get("id")
        if not claim_id:
            errors.append("claim is missing id")
            continue
        if claim_id in seen_claims:
            errors.append(f"duplicate claim id: {claim_id}")
        seen_claims.add(claim_id)
        valid_claim_ids.add(claim_id)
        kind = claim.get("kind")
        expected_label = PROVENANCE_LABELS.get(kind)
        if not expected_label:
            errors.append(f"claim {claim_id} has unsupported kind: {kind}")
        elif claim.get("slide_label") != expected_label:
            errors.append(
                f"claim {claim_id} label mismatch: expected {expected_label}, found {claim.get('slide_label')}"
            )
        for field in ("value", "unit", "source", "allowed_use"):
            if field not in claim:
                errors.append(f"claim {claim_id} is missing {field}")
        if kind in ("cuda-events", "nsight-compute", "nsight-systems") and not claim.get("run_id"):
            errors.append(f"measured claim {claim_id} is missing run_id")
        if kind == "nsight-compute" and "tim" in str(claim.get("allowed_use", "")).lower():
            errors.append(f"claim {claim_id} improperly uses Nsight Compute for timing")
        if kind == "nsight-systems" and "headline" in str(claim.get("allowed_use", "")).lower():
            errors.append(f"claim {claim_id} improperly uses Nsight Systems as headline timing")

    for entry in slides:
        for claim_id in entry.get("evidence_refs", []):
            if claim_id and claim_id not in valid_claim_ids:
                errors.append(f"slide {entry.get('slide')} references missing claim {claim_id}")

    if source and source.exists():
        source_text = pptx_package_stats(source)["visible_text"].casefold()
        measured_markers = ("cuda events", "nsight compute", "nsight systems")
        if any(marker in source_text for marker in measured_markers) and not claims:
            errors.append("source deck contains measured evidence labels, but the claims ledger is empty")

    if template_mode(config):
        try:
            paths = template_paths(config, project)
            require_default_template_integrity(paths["pptx"])
            for label, path in (
                ("template inspection manifest", paths["inspect_manifest"]),
                ("template profile", paths["profile"]),
                ("template frame map", paths["frame_map"]),
                ("template starter PPTX", paths["starter"]),
                ("template starter manifest", paths["starter_manifest"]),
            ):
                if not path.exists():
                    errors.append(f"missing {label}: {path}; run inspect-template/prepare-template")
            if paths["pptx"].exists():
                configured_sha = template_config(config).get("source_sha256")
                current_sha = sha256_file(paths["pptx"])
                if configured_sha != current_sha:
                    errors.append("template source hash changed; rerun inspect-template")
                if design and design.get("template_sha256") != current_sha:
                    errors.append("design-system template hash is stale")
            if paths["profile"].exists() and paths["frame_map"].exists() and slides:
                advanced_actions = declared_advanced_edit_actions(config, project)
                expected_map = build_template_frame_map(read_json(paths["profile"]), plan, advanced_actions)
                actual_map = read_json(paths["frame_map"])
                validate_frame_map_document(actual_map, advanced_actions)
                if canonical_json_bytes(expected_map) != canonical_json_bytes(actual_map):
                    errors.append("template frame map is stale relative to slide-plan.json; rerun prepare-template")
                if len(actual_map.get("outputSlides", [])) != len(slides):
                    errors.append("template frame map and slide plan have different slide counts")
                title_fit_preflight = preflight_title_claim_fit(plan, actual_map, paths["starter_layout"])
                errors.extend(title_fit_preflight["errors"])
                warnings.extend(title_fit_preflight["warnings"])
                visual_contract_report = visual_contract_audit(config, plan, actual_map)
                errors.extend(visual_contract_report["errors"])
                warnings.extend(visual_contract_report["warnings"])
            if paths["starter"].exists():
                configured_starter_sha = template_config(config).get("starter_sha256")
                if configured_starter_sha != sha256_file(paths["starter"]):
                    errors.append("template starter hash changed or is stale; rerun prepare-template")
                if template_config(config).get("starter_source_sha256") != template_config(config).get("source_sha256"):
                    errors.append("template starter was prepared from a different source hash; rerun prepare-template")
            if paths["starter_manifest"].exists():
                configured_manifest_sha = template_config(config).get("starter_manifest_sha256")
                if configured_manifest_sha != sha256_file(paths["starter_manifest"]):
                    errors.append("template starter manifest hash changed or is stale; rerun prepare-template")
            builder_path = resolve_path(config.get("build", {}).get("builder"), project)
            if not builder_path or not builder_path.exists():
                errors.append(f"missing builder: {builder_path}")
            else:
                builder_text = builder_path.read_text(encoding="utf-8")
                if "PresentationFile.importPptx" not in builder_text or "PresentationFile.exportPptx" not in builder_text:
                    errors.append("template-native builder must import the starter and export with PresentationFile")
                if re.search(r"\bPresentation\.create\s*\(|presentation\.slides\.add\s*\(", builder_text):
                    errors.append("template-native builder must not create a fresh presentation or add fresh slides")
        except (LabDeckError, OSError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"template contract could not be audited: {error}")

    qa_status = "fail" if errors else "pass"
    report = {
        "status": qa_status,
        "config": str(config_path),
        "project": str(project),
        "source_manifest": manifest,
        "slide_plan_entries": len(slides),
        "claim_count": len(claims),
        "required_coverage": sorted(required_topics),
        "covered_topics": sorted(covered_topics),
        "errors": errors,
        "warnings": warnings,
        "title_fit_preflight": title_fit_preflight,
        "visual_contract": visual_contract_report,
    }
    return report


def write_audit_report(project: Path, report: dict[str, Any]) -> None:
    write_json(project / "reports/audit-report.json", report)
    if isinstance(report.get("title_fit_preflight"), dict):
        write_json(project / "reports/title-fit-preflight.json", report["title_fit_preflight"])
    if isinstance(report.get("visual_contract"), dict):
        write_json(project / "reports/visual-contract.json", report["visual_contract"])
    lines = [
        f"AUDIT: {report['status'].upper()}",
        f"Slide-plan entries: {report['slide_plan_entries']}",
        f"Claims: {report['claim_count']}",
    ]
    lines += [f"ERROR: {item}" for item in report["errors"]]
    lines += [f"WARNING: {item}" for item in report["warnings"]]
    write_text(project / "reports/audit-report.txt", "\n".join(lines) + "\n")


def command_audit(args: argparse.Namespace) -> int:
    config, config_path, project = load_config(args.config)
    report = audit_config(config, config_path, project)
    write_audit_report(project, report)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in ("pass", "mechanical-pass") else 2


def command_finalize(args: argparse.Namespace) -> int:
    config, config_path, project = load_config(args.config)
    audit = audit_config(config, config_path, project)
    write_audit_report(project, audit)
    if audit["status"] != "pass" and not args.allow_incomplete_plan:
        raise LabDeckError("plan/claims audit failed; inspect reports/audit-report.txt")

    deck = config["deck"]
    build = config.get("build", {})
    source = resolve_path(deck.get("source_pptx"), project)
    input_deck = resolve_path(build.get("input_pptx") or deck.get("source_pptx"), project)
    builder = resolve_path(build.get("builder"), project)
    artifact = resolve_path(build.get("artifact_pptx"), project)
    visual = resolve_path(build.get("visual_pptx"), project)
    final = resolve_path(deck.get("final_pptx"), project)
    if not all((source, input_deck, builder, artifact, visual, final)):
        raise LabDeckError("source, build input, builder, artifact, visual, and final PPTX paths are required")
    if not source.exists():
        raise LabDeckError(f"source deck missing: {source}")
    if not input_deck.exists():
        raise LabDeckError(f"build input deck missing: {input_deck}; run prepare-template")
    if not builder.exists():
        raise LabDeckError(f"builder missing: {builder}")
    outputs = {
        "artifact PPTX": artifact,
        "visual PPTX": visual,
        "final PPTX": final,
    }
    for label, path in outputs.items():
        ensure_project_output(path, project, label)
        if path.exists() and not path.is_file():
            raise LabDeckError(f"{label} path exists but is not a file: {path}")
    if len({path.resolve() for path in outputs.values()}) != len(outputs):
        raise LabDeckError("artifact, visual, and final PPTX paths must be distinct")

    protected: dict[Path, str] = {
        source.resolve(): "source deck",
        input_deck.resolve(): "build input/starter deck",
    }
    if template_mode(config):
        template = template_paths(config, project)["pptx"]
        protected[template.resolve()] = "template deck"
    for source_item in config.get("sources", []):
        source_value = source_item.get("path") if isinstance(source_item, dict) else None
        if not source_value or str(source_value).startswith(("http://", "https://")):
            continue
        source_path = resolve_path(str(source_value), project)
        if source_path:
            protected[source_path.resolve()] = f"configured source {source_item.get('id', '<unknown>')}"
    for label, path in outputs.items():
        protected_label = protected.get(path.resolve())
        if protected_label:
            raise LabDeckError(f"{label} must not overwrite the {protected_label}: {path}")

    if build.get("compatibility_roundtrip", False):
        raise LabDeckError(
            "build.compatibility_roundtrip is unsupported: LibreOffice is only a disposable QA "
            "package/slide-count/speaker-note persistence probe and must not become the final geometry source"
        )

    plan_data = read_json(project / "content/slide-plan.json")
    if not isinstance(plan_data, dict) or not isinstance(plan_data.get("slides"), list):
        raise LabDeckError("slide plan must be an object containing a slides array")
    note_errors = validate_notes_contract(config, project, len(plan_data["slides"]), input_deck)
    if note_errors:
        raise LabDeckError("speaker-note contract failed: " + "; ".join(note_errors))

    for label, path in (("artifact PPTX", artifact), ("visual PPTX", visual)):
        if path.exists():
            if not path.is_file():
                raise LabDeckError(f"{label} path exists but is not a file: {path}")
            path.unlink()

    setup_artifact_workspace(project)
    node = find_node()
    assert node is not None
    artifact.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "INPUT_PPTX": str(input_deck),
            "OUTPUT_PPTX": str(artifact),
            "LABDECK_CONFIG": str(config_path),
            "LABDECK_CLAIMS": str(project / "content/claims.json"),
            "LABDECK_SLIDE_PLAN": str(project / "content/slide-plan.json"),
            "LABDECK_TEMPLATE_MAP": str(project / "content/template-frame-map.json"),
            "LABDECK_TEMPLATE_PROFILE": str(project / "content/template-profile.json"),
            "LABDECK_DESIGN_SYSTEM": str(project / "content/design-system.json"),
        }
    )
    prototype = template_config(config).get("html_prototype")
    if isinstance(prototype, dict) and prototype.get("allowed") is True:
        manifest_value = prototype.get("manifest") or "content/native-rebuild-manifest.json"
        manifest_path = resolve_path(str(manifest_value), project)
        if not manifest_path:
            raise LabDeckError("html-assisted-native manifest path is invalid")
        env["LABDECK_NATIVE_REBUILD_MANIFEST"] = str(manifest_path)
    run_command([str(node), str(builder)], cwd=project, env=env)
    if not artifact.is_file() or artifact.stat().st_size == 0:
        raise LabDeckError(f"builder did not create artifact PPTX: {artifact}")

    visual.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact, visual)

    notes_config = config.get("notes", {})
    final.parent.mkdir(parents=True, exist_ok=True)
    final_candidate = final.with_name(f".{final.stem}.labdeck-finalizing-{os.getpid()}.pptx")
    final_candidate_sidecar = Path(str(final_candidate) + ".inspect.ndjson")
    ensure_project_output(final_candidate, project, "temporary final PPTX")
    if final_candidate.exists():
        if not final_candidate.is_file():
            raise LabDeckError(f"temporary final path exists but is not a file: {final_candidate}")
        final_candidate.unlink()
    try:
        if notes_config.get("enabled", True):
            notes_path = resolve_path(notes_config.get("path"), project)
            if not notes_path or not notes_path.exists():
                raise LabDeckError(f"notes JSON missing: {notes_path}")
            helper = project / ".labdeck-tools/inject_notes.mjs"
            run_command(
                [str(node), str(helper), str(visual), str(final_candidate), str(notes_path), str(config_path)],
                cwd=project,
            )
        else:
            shutil.copy2(visual, final_candidate)
        if not final_candidate.is_file() or final_candidate.stat().st_size == 0:
            raise LabDeckError("finalization did not produce a nonempty candidate PPTX")
        try:
            os.replace(final_candidate, final)
        except OSError as error:
            raise LabDeckError(f"could not atomically publish final PPTX to {final}: {error}") from error
    finally:
        if final_candidate.exists():
            final_candidate.unlink()
        if final_candidate_sidecar.exists() and final_candidate_sidecar.is_file():
            final_candidate_sidecar.unlink()
    print(final)
    return 0


def html_prototype_files(config: dict[str, Any], project: Path) -> list[Path]:
    prototype = template_config(config).get("html_prototype")
    candidates: list[Path] = []
    if isinstance(prototype, dict):
        for key in ("path", "manifest", "entrypoint"):
            value = prototype.get(key)
            if isinstance(value, str) and value.strip():
                resolved = resolve_path(value, project)
                if resolved:
                    candidates.append(resolved)
        for key in ("files", "source_files", "artifacts", "html_paths", "html_files", "render_paths", "render_files"):
            values = prototype.get(key)
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, str) and value.strip():
                        resolved = resolve_path(value, project)
                        if resolved:
                            candidates.append(resolved)
        if prototype.get("allowed"):
            candidates.append(project / "work/html-prototype")
    output: set[Path] = set()
    for candidate in candidates:
        if candidate.is_file():
            output.add(candidate.resolve())
        elif candidate.is_dir():
            for path in candidate.rglob("*"):
                if not path.is_file() or any(part in {".git", "node_modules"} for part in path.parts):
                    continue
                output.add(path.resolve())
    return sorted(output, key=str)


def render_deck(deck: Path, project: Path, config: dict[str, Any]) -> tuple[Path, Path, str, str]:
    skill = find_presentations_skill()
    python = find_python()
    if not skill or not python:
        raise LabDeckError("bundled Presentations skill or Python runtime unavailable")
    render_dir = project / "render/final"
    if render_dir.exists():
        shutil.rmtree(render_dir)
    render_dir.mkdir(parents=True)
    qa = config.get("qa", {})
    width = str(qa.get("render_width", 1600))
    height = str(qa.get("render_height", 900))
    run_command(
        [
            str(python),
            str(skill / "container_tools/render_slides.py"),
            str(deck),
            "--output_dir",
            str(render_dir),
            "--width",
            width,
            "--height",
            height,
        ]
    )
    montage = project / "reports/review-montage.png"
    run_command(
        [
            str(python),
            str(skill / "container_tools/create_montage.py"),
            "--input_dir",
            str(render_dir),
            "--output_file",
            str(montage),
            "--label_mode",
            "number",
            "--fail_on_image_error",
        ]
    )
    images = sorted(path for path in render_dir.iterdir() if path.suffix.lower() == ".png")
    source_candidates = [
        project / "labdeck.json",
        project / "build/builder.mjs",
        project / "content/slide-plan.json",
        project / "content/design-system.json",
        project / "content/template-frame-map.json",
        project / "content/claims.json",
        project / "content/notes.json",
    ]
    if template_mode(config):
        paths = template_paths(config, project)
        source_candidates.extend(
            [
                paths["pptx"],
                paths["inspect_manifest"],
                paths["profile"],
                paths["starter"],
                paths["starter_manifest"],
            ]
        )
        if paths["pptx"].resolve() == DEFAULT_TEMPLATE.resolve():
            source_candidates.append(STYLE_MANIFEST)
    source_candidates.extend(html_prototype_files(config, project))
    source_candidates = list(dict.fromkeys(path.resolve() for path in source_candidates))
    source_manifest = {
        relative_or_absolute(path, project): sha256_file(path)
        for path in source_candidates
        if path.exists()
    }
    source_manifest_path = project / "reports/source-fingerprints.json"
    write_json(source_manifest_path, source_manifest)
    source_manifest_sha = sha256_file(source_manifest_path)
    manifest = {
        "deck": str(deck),
        "deck_sha256": sha256_file(deck),
        "source_manifest_sha256": source_manifest_sha,
        "images": {path.name: sha256_file(path) for path in images},
    }
    manifest_path = project / "reports/render-manifest.json"
    write_json(manifest_path, manifest)
    return render_dir, montage, sha256_file(manifest_path), source_manifest_sha


def review_report_errors(project: Path, deck_sha: str, render_manifest_sha: str, source_manifest_sha: str) -> list[str]:
    errors: list[str] = []
    for filename, label in (("pass-a.md", "Pass A"), ("pass-b.md", "Pass B")):
        path = project / "reports" / filename
        if not path.exists():
            errors.append(f"missing {label} report: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        required = [
            "VERDICT: PASS",
            f"Deck SHA256: {deck_sha}",
            f"Render manifest SHA256: {render_manifest_sha}",
            f"Source manifest SHA256: {source_manifest_sha}",
            "Evidence:",
            "Unresolved Critical: 0",
            "Blocking findings: None",
            "## Checks",
            "| Slide | Finding | Severity | Fix | Status |",
        ]
        for token in required:
            if token not in text:
                errors.append(f"{label} report is missing or stale: {token}")
    return errors


def notes_range_for_slide(config: dict[str, Any], plan_entry: dict[str, Any]) -> tuple[int, int]:
    notes = config.get("notes", {})
    explicit = plan_entry.get("notes_word_range")
    if isinstance(explicit, list) and len(explicit) == 2:
        return int(explicit[0]), int(explicit[1])
    frame = plan_entry.get("template_frame")
    role_text = " ".join(
        str(value)
        for value in (
            frame.get("role") if isinstance(frame, dict) else "",
            plan_entry.get("narrative_job", ""),
            plan_entry.get("layout_family", ""),
        )
    ).casefold()
    ranges = notes.get("role_ranges", {})
    for role in ("cover", "divider", "closing", "appendix", "mechanism", "evidence"):
        if role in role_text and isinstance(ranges.get(role), list) and len(ranges[role]) == 2:
            return int(ranges[role][0]), int(ranges[role][1])
    default = ranges.get("default")
    if isinstance(default, list) and len(default) == 2:
        return int(default[0]), int(default[1])
    return int(notes.get("minimum_words", 30)), int(notes.get("maximum_words", 190))


def run_template_fidelity_check(config: dict[str, Any], project: Path, final: Path) -> dict[str, Any]:
    paths = template_paths(config, project)
    scripts = presentations_template_scripts()
    node = find_node()
    if not node:
        raise LabDeckError("Node runtime unavailable; run doctor")
    final_workspace = project / "work/final-template-check"
    if final_workspace.exists():
        shutil.rmtree(final_workspace)
    final_workspace.mkdir(parents=True)
    run_command(
        [
            str(node),
            str(scripts / "inspect_template_deck.mjs"),
            "--workspace",
            str(final_workspace),
            "--pptx",
            str(final),
        ]
    )
    final_layout = final_workspace / "template-inspect/layouts"
    report_path = project / "qa/template-fidelity-check.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.exists():
        if not report_path.is_file():
            raise LabDeckError(f"template fidelity report path is not a file: {report_path}")
        report_path.unlink()
    result = run_command(
        [
            str(node),
            str(scripts / "check_template_fidelity.mjs"),
            "--workspace",
            str(project),
            "--starter-pptx",
            str(paths["starter"]),
            "--final-pptx",
            str(final),
            "--map",
            str(paths["frame_map"]),
            "--starter-layout-dir",
            str(paths["starter_layout"]),
            "--final-layout-dir",
            str(final_layout),
            "--edit-dir",
            str(project),
        ],
        capture=True,
        check=False,
    )
    if report_path.exists():
        try:
            report = read_json(report_path)
        except LabDeckError as error:
            report = {
                "status": "fail",
                "issues": [
                    {
                        "severity": "fail",
                        "id": "template-fidelity-report-invalid",
                        "message": str(error),
                    }
                ],
            }
    else:
        try:
            report = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            report = {
                "status": "fail",
                "issues": [
                    {
                        "severity": "fail",
                        "id": "template-fidelity-runner-failed",
                        "message": result.stderr or result.stdout or "unknown template fidelity failure",
                    }
                ],
            }
    if not isinstance(report, dict):
        report = {
            "status": "fail",
            "issues": [
                {
                    "severity": "fail",
                    "id": "template-fidelity-report-shape-invalid",
                    "message": "template fidelity checker did not return a JSON object",
                }
            ],
        }
    issues = report.get("issues")
    if not isinstance(issues, list):
        issues = []
        report["issues"] = issues
    if result.returncode != 0:
        report["status"] = "fail"
        issues.append(
            {
                "severity": "fail",
                "id": "template-fidelity-nonzero-exit",
                "message": f"template fidelity checker exited {result.returncode}: "
                + (result.stderr or result.stdout or "no diagnostic output").strip(),
            }
        )
    elif report.get("status") != "pass":
        report["status"] = "fail"
    report["exit_code"] = result.returncode
    report["report_path"] = str(report_path) if report_path.exists() else None
    strict_script = HELPERS_DIR / "check_locked_template.py"
    strict_report_path = project / "qa/strict-template-fidelity.json"
    if strict_report_path.exists():
        if not strict_report_path.is_file():
            raise LabDeckError(f"strict template fidelity report path is not a file: {strict_report_path}")
        strict_report_path.unlink()
    python = find_python()
    if not python or not strict_script.exists():
        strict_report: dict[str, Any] = {
            "status": "fail",
            "issues": [
                {
                    "id": "strict-template-fidelity-unavailable",
                    "message": "strict locked-template checker or Python runtime is unavailable",
                }
            ],
        }
        strict_exit = 2
    else:
        strict_result = run_command(
            [
                str(python),
                str(strict_script),
                "--starter-pptx",
                str(paths["starter"]),
                "--final-pptx",
                str(final),
                "--map",
                str(paths["frame_map"]),
                "--plan",
                str(project / "content/slide-plan.json"),
                "--starter-layout-dir",
                str(paths["starter_layout"]),
                "--final-layout-dir",
                str(final_layout),
                "--out",
                str(strict_report_path),
            ],
            capture=True,
            check=False,
        )
        strict_exit = strict_result.returncode
        if strict_report_path.exists():
            try:
                strict_report = read_json(strict_report_path)
            except LabDeckError as error:
                strict_report = {"status": "fail", "issues": [{"id": "strict-report-invalid", "message": str(error)}]}
        else:
            try:
                strict_report = json.loads(strict_result.stdout)
            except (json.JSONDecodeError, TypeError):
                strict_report = {
                    "status": "fail",
                    "issues": [
                        {
                            "id": "strict-checker-failed",
                            "message": strict_result.stderr or strict_result.stdout or "strict checker produced no report",
                        }
                    ],
                }
    if not isinstance(strict_report, dict):
        strict_report = {
            "status": "fail",
            "issues": [{"id": "strict-report-shape-invalid", "message": "strict checker report must be a JSON object"}],
        }
        strict_exit = strict_exit or 2
    strict_report["exit_code"] = strict_exit
    strict_report["report_path"] = str(strict_report_path) if strict_report_path.exists() else None
    report["strict_locked_template"] = strict_report
    if strict_exit != 0 or strict_report.get("status") != "pass":
        report["status"] = "fail"
        issues.append(
            {
                "severity": "fail",
                "id": "strict-locked-template-fidelity-failed",
                "message": "strict starter/final structure, locked-object, and bounded-addition comparison failed",
            }
        )
    return report


def command_qa(args: argparse.Namespace) -> int:
    config, config_path, project = load_config(args.config)
    audit = audit_config(config, config_path, project)
    write_audit_report(project, audit)
    errors = list(audit["errors"])
    warnings = list(audit["warnings"])
    final = resolve_path(config.get("deck", {}).get("final_pptx"), project)
    if not final or not final.exists():
        raise LabDeckError(f"final PPTX missing: {final}")
    package = pptx_package_stats(final)
    if not package["zip_ok"]:
        errors.append(f"PPTX ZIP corruption at {package['corrupt_member']}")
    expected_slides = config.get("deck", {}).get("expected_slides")
    if expected_slides is not None and package["slide_count"] != expected_slides:
        errors.append(f"expected {expected_slides} slides, found {package['slide_count']}")
    allowed_fonts = set(config.get("style", {}).get("allowed_fonts", []))
    unexpected_fonts = sorted(set(package["fonts"]) - allowed_fonts) if allowed_fonts else []
    if unexpected_fonts:
        errors.append("unexpected slide fonts: " + ", ".join(unexpected_fonts))
    if package["empty_placeholder_shapes"]:
        message = f"OOXML contains {package['empty_placeholder_shapes']} empty structural placeholder shapes"
        if template_mode(config):
            errors.append(message)
        else:
            warnings.append(message + "; inspect whether they are intentional")
    visible_text = package["visible_text"].casefold()
    for token in config.get("qa", {}).get("required_text", []):
        if str(token).casefold() not in visible_text:
            errors.append(f"required visible text is missing: {token}")
    claims_path = project / "content/claims.json"
    claim_count = 0
    if claims_path.exists():
        claim_count = len(json.loads(claims_path.read_text(encoding="utf-8")).get("claims", []))
    measured_markers = ("cuda events", "nsight compute", "nsight systems")
    if any(marker in visible_text for marker in measured_markers) and claim_count == 0:
        errors.append("final deck contains measured evidence labels, but the claims ledger is empty")
    if package.get("code_label_count", 0):
        errors.append(
            f"final deck contains {package['code_label_count']} bare CODE provenance labels; use CODE-DERIVED"
        )

    inspection = inspect_with_artifact_tool(final, project)
    notes_config = config.get("notes", {})
    if notes_config.get("enabled", True):
        plan_path = project / "content/slide-plan.json"
        plan_entries = read_json(plan_path).get("slides", []) if plan_path.exists() else []
        notes_data_path = resolve_path(notes_config.get("path"), project)
        raw_notes = read_json(notes_data_path) if notes_data_path and notes_data_path.exists() else []
        note_entries = raw_notes if isinstance(raw_notes, list) else raw_notes.get("slides", [])
        note_modes = {
            int(entry.get("slide", index + 1)): str(entry.get("mode", "replace"))
            for index, entry in enumerate(note_entries)
            if isinstance(entry, dict)
        }
        if inspection["slide_count"] != package["slide_count"]:
            errors.append("Artifact Tool and OOXML disagree on slide count")
        for note in inspection["notes"]:
            if not note["visible"]:
                errors.append(f"slide {note['slide']} notes are hidden")
            if note_modes.get(note["slide"]) == "preserve":
                if note["words"] == 0:
                    errors.append(f"slide {note['slide']} preserve-mode notes are empty")
                continue
            plan_entry = plan_entries[note["slide"] - 1] if note["slide"] <= len(plan_entries) else {}
            minimum, maximum = notes_range_for_slide(config, plan_entry)
            if note["words"] < minimum or note["words"] > maximum:
                errors.append(
                    f"slide {note['slide']} notes have {note['words']} words; expected {minimum}–{maximum}"
                )

    skill = find_presentations_skill()
    python = find_python()
    if not skill or not python:
        raise LabDeckError("bundled Presentations skill or Python runtime unavailable")
    overflow_result = run_command(
        [
            str(python),
            str(skill / "container_tools/slides_test.py"),
            str(final),
            "--width",
            str(config.get("qa", {}).get("render_width", 1600)),
            "--height",
            str(config.get("qa", {}).get("render_height", 900)),
        ],
        capture=True,
        check=False,
    )
    if overflow_result.returncode != 0:
        errors.append("slides_test.py reported overflow or failed")

    render_dir, montage, render_manifest_sha, source_manifest_sha = render_deck(final, project, config)
    deck_sha = sha256_file(final)

    fidelity: dict[str, Any] = {"enabled": False}
    if template_mode(config) and config.get("qa", {}).get("require_template_fidelity", True):
        fidelity = run_template_fidelity_check(config, project, final)
        fidelity["enabled"] = True
        if fidelity.get("status") != "pass" or fidelity.get("exit_code") != 0:
            errors.append("Presentations template-fidelity check failed")

    compatibility: dict[str, Any] = {"enabled": False}
    if config.get("qa", {}).get("compatibility_probe", True):
        probe = project / "reports/compatibility-probe.pptx"
        soffice_roundtrip(final, probe)
        probe_package = pptx_package_stats(probe)
        probe_inspection = inspect_with_artifact_tool(probe, project)
        compatibility = {
            "enabled": True,
            "probe": str(probe),
            "scope": "limited package integrity, slide-count, and speaker-note persistence probe only",
            "geometry_checked": False,
            "geometry_fidelity_claim": "none; LibreOffice may change layout geometry and this probe is never promoted to the final deck",
            "package_zip_ok": probe_package["zip_ok"],
            "notes_digest_before": inspection["notes_sha256"],
            "notes_digest_after": probe_inspection["notes_sha256"],
            "notes_digest_match": inspection["notes_sha256"] == probe_inspection["notes_sha256"],
            "slide_count_match": inspection["slide_count"] == probe_inspection["slide_count"],
        }
        if not compatibility["package_zip_ok"]:
            errors.append("LibreOffice compatibility probe produced a corrupt PPTX package")
        if not compatibility["notes_digest_match"]:
            errors.append("speaker-note digest changed in LibreOffice compatibility probe")
        if not compatibility["slide_count_match"]:
            errors.append("slide count changed in LibreOffice compatibility probe")

    if config.get("qa", {}).get("require_review_reports", True) and not args.skip_review_gate:
        errors.extend(review_report_errors(project, deck_sha, render_manifest_sha, source_manifest_sha))
    elif args.skip_review_gate:
        warnings.append("independent visual review gate skipped; this is not a handoff-ready QA pass")

    acceptance_contract = config.get("qa", {}).get("user_acceptance")
    acceptance_report: dict[str, Any] = {
        "required": False,
        "status": "legacy",
        "reviewer": "",
        "accepted_at": "",
        "reviewed_deck_sha256": "",
    }
    if isinstance(acceptance_contract, dict):
        acceptance_report.update(acceptance_contract)
        if not args.skip_review_gate:
            errors.extend(user_acceptance_errors(config, final, deck_sha))
        else:
            warnings.append("user acceptance gate skipped; this is not a handoff-ready QA pass")

    report = {
        "status": "pass" if not errors else "fail",
        "config": str(config_path),
        "final_pptx": str(final),
        "deck_sha256": deck_sha,
        "render_manifest_sha256": render_manifest_sha,
        "source_manifest_sha256": source_manifest_sha,
        "render_dir": str(render_dir),
        "montage": str(montage),
        "package": {key: value for key, value in package.items() if key != "visible_text"},
        "notes": inspection,
        "template_fidelity": fidelity,
        "compatibility": compatibility,
        "user_acceptance": acceptance_report,
        "slides_test": {
            "exit_code": overflow_result.returncode,
            "stdout": overflow_result.stdout,
            "stderr": overflow_result.stderr,
        },
        "errors": errors,
        "warnings": warnings,
    }
    write_json(project / "reports/qa-report.json", report)
    lines = [
        f"QA: {report['status'].upper()}",
        f"Final: {final}",
        f"Deck SHA256: {deck_sha}",
        f"Render manifest SHA256: {render_manifest_sha}",
        f"Source manifest SHA256: {source_manifest_sha}",
        f"Slides: {package['slide_count']}",
        f"Notes digest: {inspection['notes_sha256']}",
        f"Montage: {montage}",
    ]
    if compatibility.get("enabled"):
        lines.append("Compatibility scope: package/slide-count/speaker-note persistence only; geometry not checked")
    lines += [f"ERROR: {item}" for item in errors]
    lines += [f"WARNING: {item}" for item in warnings]
    write_text(project / "reports/qa-report.txt", "\n".join(lines) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 2


def command_gpu(args: argparse.Namespace) -> int:
    config, _, project = load_config(args.config)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise LabDeckError("GPU command missing; place it after --")
    joined = " ".join(command)
    if re.search(
        r"(?i)(?:password|passwd|token|api[-_]?key|secret|authorization|cookie)(?:\s|=|:)",
        joined,
    ):
        raise LabDeckError("GPU commands must not contain secret-bearing arguments; pass secrets through an approved remote secret mechanism")
    if "CUDA_VISIBLE_DEVICES" in joined or "CUDA_DEVICE_ORDER" in joined:
        raise LabDeckError("do not override GPU-selection variables; the Device-3 wrapper owns them")
    measurements = config.get("measurements", {})
    if not measurements.get("enabled", False) and not args.dry_run:
        raise LabDeckError("measurements.enabled is false; enable it in labdeck.json before a live GPU run")
    host = str(measurements.get("host", "l40s-yunm"))
    device = int(measurements.get("physical_device", 3))
    if host != "l40s-yunm" or device != 3:
        raise LabDeckError("this skill permits only ssh l40s-yunm physical Device 3")
    expected_name = str(measurements.get("expected_gpu_name", "NVIDIA L40S"))
    expected_uuid = str(measurements.get("expected_uuid", ""))
    remote_user_command = shlex.join(command)
    expected_uuid_check = (
        f"[[ \"$GPU_UUID\" == {shlex.quote(expected_uuid)} ]] || "
        f"{{ echo 'Unexpected GPU UUID:' \"$GPU_UUID\" >&2; exit 43; }}"
        if expected_uuid
        else ":"
    )
    remote_script = f"""set -euo pipefail
GPU_INDEX=3
GPU_NAME=$(nvidia-smi -i \"$GPU_INDEX\" --query-gpu=name --format=csv,noheader | head -n 1 | xargs)
GPU_UUID=$(nvidia-smi -i \"$GPU_INDEX\" --query-gpu=uuid --format=csv,noheader | head -n 1 | xargs)
DRIVER=$(nvidia-smi -i \"$GPU_INDEX\" --query-gpu=driver_version --format=csv,noheader | head -n 1 | xargs)
[[ \"$GPU_NAME\" == *{shlex.quote(expected_name)}* ]] || {{ echo 'Unexpected GPU:' \"$GPU_NAME\" >&2; exit 42; }}
{expected_uuid_check}
PIDS=$(nvidia-smi -i \"$GPU_INDEX\" --query-compute-apps=pid --format=csv,noheader,nounits 2>/dev/null | tr -d ' ' | grep -E '^[0-9]+$' || true)
[[ -z \"$PIDS\" ]] || {{ echo 'Physical Device 3 is busy; PIDs:' \"$PIDS\" >&2; exit 44; }}
echo \"LABDECK_GPU_BEFORE index=$GPU_INDEX name=$GPU_NAME uuid=$GPU_UUID driver=$DRIVER\"
trap 'nvidia-smi -i 3 --query-gpu=index,name,uuid,utilization.gpu,memory.used --format=csv,noheader | sed \"s/^/LABDECK_GPU_AFTER /\"' EXIT
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES=3
{remote_user_command}
"""
    ssh_command = ["ssh", host, "bash -lc " + shlex.quote(remote_script)]
    if args.dry_run:
        print(shlex.join(ssh_command))
        return 0
    result = run_command(ssh_command, capture=True, check=False)
    def redact_sensitive_text(value: str) -> str:
        redacted = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        redacted = re.sub(
            r"(?i)\b(password|passwd|token|api[-_]?key|secret|authorization|cookie)\s*([:=])\s*[^\s,;]+",
            r"\1\2[REDACTED]",
            redacted,
        )
        return re.sub(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", "[REDACTED_GITHUB_TOKEN]", redacted)

    safe_stdout = redact_sensitive_text(result.stdout or "")
    safe_stderr = redact_sensitive_text(result.stderr or "")
    if safe_stdout:
        print(safe_stdout, end="")
    if safe_stderr:
        print(safe_stderr, end="", file=sys.stderr)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = project / "evidence/raw" / f"{timestamp}-l40s-device3.json"
    write_json(
        log_path,
        {
            "timestamp_utc": timestamp,
            "host": host,
            "physical_device": device,
            "expected_gpu_name": expected_name,
            "expected_uuid": expected_uuid or None,
            "command": command,
            "ssh_command_sha256": hashlib.sha256(shlex.join(ssh_command).encode("utf-8")).hexdigest(),
            "exit_code": result.returncode,
            "stdout": safe_stdout,
            "stderr": safe_stderr,
        },
    )
    print(f"evidence log: {log_path}", file=sys.stderr)
    return result.returncode


def command_run(args: argparse.Namespace) -> int:
    config, _, _ = load_config(args.config)
    if template_mode(config):
        prepare_status = command_prepare_template(argparse.Namespace(config=args.config))
        if prepare_status:
            return prepare_status
    audit_status = command_audit(argparse.Namespace(config=args.config))
    if audit_status:
        return audit_status
    finalize_status = command_finalize(
        argparse.Namespace(config=args.config, allow_incomplete_plan=False)
    )
    if finalize_status:
        return finalize_status
    return command_qa(
        argparse.Namespace(config=args.config, skip_review_gate=args.skip_review_gate)
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    doctor = subparsers.add_parser("doctor", help="locate bundled deck runtimes and tools")
    doctor.set_defaults(func=command_doctor)

    init = subparsers.add_parser("init", help="create a declarative deck project")
    init.add_argument("--project-dir", required=True)
    init.add_argument("--source-pptx", required=True)
    init.add_argument("--template-pptx", help="exact PPTX template to clone and edit")
    init.add_argument("--reference-pptx", help="deprecated alias for --template-pptx")
    init.add_argument("--output-name")
    init.add_argument("--title")
    init.add_argument(
        "--html-prototype",
        action="store_true",
        help="allow a disposable HTML design prototype; the final deck still builds natively",
    )
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=command_init)

    inspect_template = subparsers.add_parser(
        "inspect-template",
        help="inspect every template slide and derive the hash-pinned visual/layout contract",
    )
    inspect_template.add_argument("config")
    inspect_template.set_defaults(func=command_inspect_template)

    prepare_template = subparsers.add_parser(
        "prepare-template",
        help="map planned slides to source frames and build the exact-clone starter deck",
    )
    prepare_template.add_argument("config")
    prepare_template.set_defaults(func=command_prepare_template)

    audit = subparsers.add_parser("audit", help="validate sources, coverage, claims, and kernel deltas")
    audit.add_argument("config")
    audit.set_defaults(func=command_audit)

    finalize = subparsers.add_parser("finalize", help="build natively and attach notes")
    finalize.add_argument("config")
    finalize.add_argument(
        "--allow-incomplete-plan",
        action="store_true",
        help="intermediate development only; never use for handoff",
    )
    finalize.set_defaults(func=command_finalize)

    qa = subparsers.add_parser("qa", help="mechanical, render, notes, compatibility, and review gates")
    qa.add_argument("config")
    qa.add_argument(
        "--skip-review-gate",
        action="store_true",
        help="prepare/check mechanical evidence only; output is not handoff-ready",
    )
    qa.set_defaults(func=command_qa)

    gpu = subparsers.add_parser("gpu", help="run one recorded command on physical L40S Device 3")
    gpu.add_argument("config")
    gpu.add_argument("--dry-run", action="store_true")
    gpu.add_argument("command", nargs="+")
    gpu.set_defaults(func=command_gpu)

    run = subparsers.add_parser("run", help="audit, finalize, and QA without implicitly measuring")
    run.add_argument("config")
    run.add_argument("--skip-review-gate", action="store_true")
    run.set_defaults(func=command_run)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except LabDeckError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
