#!/usr/bin/env python3
"""Create and validate the HTML-assisted native rebuild boundary.

The manifest proves that HTML and its renders are disposable references while
the final slide body is reconstructed from declared native elements in the
hash-pinned template starter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA = "labdeck.native-rebuild/v1"
DEFAULT_MANIFEST = "content/native-rebuild-manifest.json"
DEFAULT_PLAN = "content/slide-plan.json"
HTML_SUFFIXES = {".html", ".htm"}
RENDER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
STYLE_KEYS = {
    "style",
    "textStyle",
    "fill",
    "line",
    "geometry",
    "font",
    "color",
    "opacity",
    "cornerRadius",
    "shadow",
}
ASSET_KEYS = {
    "asset",
    "asset_path",
    "file",
    "image",
    "image_path",
    "path",
    "render_path",
    "source_path",
    "src",
}


class ManifestError(RuntimeError):
    """Raised when the native-rebuild contract is missing or stale."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ManifestError(f"missing {label}: {path}") from error
    except json.JSONDecodeError as error:
        raise ManifestError(f"invalid JSON in {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must contain a JSON object: {path}")
    return value


def resolve_path(value: str | Path, project: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project / path
    return path.resolve()


def display_path(path: Path, project: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return str(path)


def require_file(path: Path, label: str, suffixes: set[str] | None = None) -> None:
    if not path.is_file():
        raise ManifestError(f"missing {label}: {path}")
    if suffixes is not None and path.suffix.lower() not in suffixes:
        allowed = ", ".join(sorted(suffixes))
        raise ManifestError(f"{label} must use one of [{allowed}]: {path}")


def file_record(path: Path, project: Path) -> dict[str, str]:
    return {"path": display_path(path, project), "sha256": sha256_file(path)}


def require_hash_pin(value: Any, path: Path, label: str) -> str:
    pin = str(value or "").lower()
    if len(pin) != 64 or any(character not in "0123456789abcdef" for character in pin):
        raise ManifestError(f"{label} must be a lowercase SHA-256 value")
    current = sha256_file(path)
    if pin != current:
        raise ManifestError(
            f"{label} is stale for {path}: configured {pin}, current {current}"
        )
    return current


def require_bool(contract: dict[str, Any], key: str, expected: bool) -> None:
    if contract.get(key) is not expected:
        raise ManifestError(
            f"template.html_prototype.{key} must be {str(expected).lower()}"
        )


def require_contract(config: dict[str, Any]) -> dict[str, bool]:
    deck = config.get("deck")
    if not isinstance(deck, dict) or deck.get("authoring_mode") != "html-assisted-native":
        raise ManifestError("deck.authoring_mode must be html-assisted-native")
    template = config.get("template")
    if not isinstance(template, dict):
        raise ManifestError("template must be an object")
    prototype = template.get("html_prototype")
    if not isinstance(prototype, dict):
        raise ManifestError("template.html_prototype must be an object")
    require_bool(prototype, "allowed", True)
    require_bool(prototype, "disposable", True)
    require_bool(prototype, "final_source", False)
    require_bool(prototype, "direct_conversion", False)
    require_bool(prototype, "editable_body", True)
    require_bool(prototype, "allow_full_slide_raster", False)
    return {
        "disposable": True,
        "final_source": False,
        "direct_conversion": False,
        "editable_body": True,
        "allow_full_slide_raster": False,
    }


def as_path_values(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [str(value)]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ManifestError(f"{label} must be a path string or list of path strings")


def unique_paths(values: Iterable[str], project: Path) -> list[Path]:
    resolved: dict[str, Path] = {}
    for value in values:
        path = resolve_path(value, project)
        resolved[str(path)] = path
    return [resolved[key] for key in sorted(resolved)]


def prototype_paths(
    config: dict[str, Any],
    project: Path,
    html_args: Sequence[str],
    render_args: Sequence[str],
) -> tuple[list[Path], list[Path]]:
    prototype = config["template"]["html_prototype"]
    html_values: list[str] = []
    render_values: list[str] = []
    for key in ("html_paths", "html_files"):
        html_values.extend(as_path_values(prototype.get(key), f"template.html_prototype.{key}"))
    for key in ("render_paths", "render_files"):
        render_values.extend(as_path_values(prototype.get(key), f"template.html_prototype.{key}"))
    html_values.extend(html_args)
    render_values.extend(render_args)
    html_paths = unique_paths(html_values, project)
    render_paths = unique_paths(render_values, project)
    if not html_paths:
        raise ManifestError("declare at least one prototype HTML file")
    if not render_paths:
        raise ManifestError("declare at least one prototype render file")
    for path in html_paths:
        require_file(path, "prototype HTML", HTML_SUFFIXES)
    for path in render_paths:
        require_file(path, "prototype render", RENDER_SUFFIXES)
    return html_paths, render_paths


def normalized_number(value: Any, label: str, *, positive: bool) -> int | float:
    if isinstance(value, bool):
        raise ManifestError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ManifestError(f"{label} must be numeric") from error
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        condition = "positive" if positive else "non-negative"
        raise ManifestError(f"{label} must be finite and {condition}")
    return int(number) if number.is_integer() else number


def normalize_bbox(element: dict[str, Any], label: str) -> dict[str, int | float]:
    raw = element.get("bbox", element.get("position"))
    if not isinstance(raw, dict):
        raise ManifestError(f"{label} must declare bbox or position")
    aliases = {
        "left": ("left", "x"),
        "top": ("top", "y"),
        "width": ("width", "w"),
        "height": ("height", "h"),
    }
    result: dict[str, int | float] = {}
    for output_key, candidates in aliases.items():
        matches = [raw[key] for key in candidates if key in raw]
        if len(matches) != 1:
            raise ManifestError(f"{label}.{output_key} must be declared exactly once")
        result[output_key] = normalized_number(
            matches[0],
            f"{label}.{output_key}",
            positive=output_key in {"width", "height"},
        )
    return result


def element_record(element: Any, slide: int, index: int) -> dict[str, Any]:
    label = f"slide {slide} native_elements[{index}]"
    if not isinstance(element, dict):
        raise ManifestError(f"{label} must be an object")
    name = str(element.get("name") or "").strip()
    if not name:
        raise ManifestError(f"{label}.name must be a non-empty stable name")
    kind = str(element.get("kind", element.get("type", ""))).strip()
    if not kind:
        raise ManifestError(f"{label}.kind or .type must be a non-empty string")
    bbox = normalize_bbox(element, label)
    style_payload = {key: element[key] for key in sorted(STYLE_KEYS) if key in element}
    content_payload = {
        key: value
        for key, value in sorted(element.items())
        if key not in STYLE_KEYS | {"name", "kind", "type", "bbox", "position"}
    }
    return {
        "name": name,
        "kind": kind,
        "bbox": bbox,
        "content_sha256": sha256_json(content_payload),
        "style_sha256": sha256_json(style_payload),
        "spec_sha256": sha256_json(element),
    }


def collect_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(collect_strings(item))
        return result
    if isinstance(value, dict):
        result = []
        for item in value.values():
            result.extend(collect_strings(item))
        return result
    return []


def element_asset_values(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    result: list[str] = []
    for key, item in value.items():
        if key in ASSET_KEYS:
            result.extend(collect_strings(item))
        if isinstance(item, (dict, list)):
            if isinstance(item, dict):
                result.extend(element_asset_values(item))
            else:
                for child in item:
                    result.extend(element_asset_values(child))
    return result


def final_asset_values(config: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for section, label in (
        (config, "final_assets"),
        (config.get("deck"), "final_assets"),
        (config.get("build"), "final_assets"),
        (config.get("build"), "assets"),
        (config.get("template", {}).get("html_prototype"), "final_assets"),
    ):
        if isinstance(section, dict) and label in section:
            values.extend(collect_strings(section[label]))
    for slide in plan.get("slides", []):
        if not isinstance(slide, dict):
            continue
        elements = slide.get("native_elements", [])
        if not isinstance(elements, list):
            continue
        for element in elements:
            values.extend(element_asset_values(element))
    return values


def resolved_asset_values(values: Iterable[str], project: Path) -> dict[Path, str]:
    result: dict[Path, str] = {}
    for value in values:
        if value.startswith(("data:", "http://", "https://")):
            continue
        result[resolve_path(value, project)] = value
    return result


def reject_render_as_final_asset(
    config: dict[str, Any],
    plan: dict[str, Any],
    render_paths: Sequence[Path],
    project: Path,
) -> list[str]:
    declarations = resolved_asset_values(final_asset_values(config, plan), project)
    prototype_renders = set(render_paths)
    render_hashes = {sha256_file(path) for path in prototype_renders}
    conflicts = set(prototype_renders & set(declarations))
    for path in declarations:
        if path.is_file() and sha256_file(path) in render_hashes:
            conflicts.add(path)
    conflicts = sorted(conflicts, key=str)
    if conflicts:
        details = ", ".join(display_path(path, project) for path in conflicts)
        raise ManifestError(
            "prototype render is declared as a final/native asset; rebuild it with "
            f"editable native elements instead: {details}"
        )
    return sorted(display_path(path, project) for path in declarations)


def template_inputs(
    config: dict[str, Any], project: Path
) -> tuple[dict[str, Any], Path, Path, Path]:
    template = config.get("template")
    if not isinstance(template, dict):
        raise ManifestError("template must be an object")
    required = {
        "pptx": "template PPTX",
        "starter_pptx": "template starter PPTX",
        "frame_map": "template frame map",
    }
    paths: dict[str, Path] = {}
    for key, label in required.items():
        value = template.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ManifestError(f"template.{key} must be a path string")
        paths[key] = resolve_path(value, project)
        require_file(paths[key], label)
    require_hash_pin(template.get("source_sha256"), paths["pptx"], "template.source_sha256")
    require_hash_pin(template.get("starter_sha256"), paths["starter_pptx"], "template.starter_sha256")
    require_hash_pin(template.get("frame_map_sha256"), paths["frame_map"], "template.frame_map_sha256")
    return template, paths["pptx"], paths["starter_pptx"], paths["frame_map"]


def build_slide_records(
    plan: dict[str, Any], frame_map: dict[str, Any]
) -> list[dict[str, Any]]:
    slides = plan.get("slides")
    outputs = frame_map.get("outputSlides")
    if not isinstance(slides, list) or not slides:
        raise ManifestError("slide plan must contain a non-empty slides array")
    if not isinstance(outputs, list) or len(outputs) != len(slides):
        raise ManifestError("template frame map must align one-to-one with slide plan")
    mapped: dict[int, dict[str, Any]] = {}
    for entry in outputs:
        if not isinstance(entry, dict) or not isinstance(entry.get("outputSlide"), int):
            raise ManifestError("every frame-map outputSlides entry needs outputSlide")
        number = entry["outputSlide"]
        if number in mapped:
            raise ManifestError(f"duplicate frame-map output slide {number}")
        mapped[number] = entry
    result: list[dict[str, Any]] = []
    seen_slides: set[int] = set()
    for slide_entry in slides:
        if not isinstance(slide_entry, dict) or not isinstance(slide_entry.get("slide"), int):
            raise ManifestError("every slide-plan entry needs an integer slide number")
        number = slide_entry["slide"]
        if number < 1 or number in seen_slides:
            raise ManifestError(f"invalid or duplicate slide-plan slide {number}")
        seen_slides.add(number)
        if number not in mapped:
            raise ManifestError(f"slide {number} has no matching frame-map output")
        elements = slide_entry.get("native_elements", [])
        if not isinstance(elements, list):
            raise ManifestError(f"slide {number} native_elements must be an array")
        element_records = [
            element_record(element, number, index)
            for index, element in enumerate(elements)
        ]
        names = [element["name"] for element in element_records]
        if len(names) != len(set(names)):
            raise ManifestError(f"slide {number} native element names must be unique")
        map_entry = mapped[number]
        edit_targets = map_entry.get("editTargets")
        if not isinstance(edit_targets, list):
            raise ManifestError(f"slide {number} frame-map editTargets must be an array")
        add_targets = [
            target
            for target in edit_targets
            if isinstance(target, dict) and target.get("action") == "add"
        ]
        if elements and len(add_targets) != 1:
            raise ManifestError(f"slide {number} native elements require exactly one bounded add target")
        zone_record: dict[str, int | float] | None = None
        if add_targets:
            raw_zone = add_targets[0].get("zone")
            if not isinstance(raw_zone, dict):
                raise ManifestError(f"slide {number} add target requires a zone object")
            zone_record = {
                "left": normalized_number(raw_zone.get("x"), f"slide {number} zone.x", positive=False),
                "top": normalized_number(raw_zone.get("y"), f"slide {number} zone.y", positive=False),
                "width": normalized_number(raw_zone.get("w"), f"slide {number} zone.w", positive=True),
                "height": normalized_number(raw_zone.get("h"), f"slide {number} zone.h", positive=True),
            }
            zone_right = float(zone_record["left"]) + float(zone_record["width"])
            zone_bottom = float(zone_record["top"]) + float(zone_record["height"])
            for element in element_records:
                bbox = element["bbox"]
                right = float(bbox["left"]) + float(bbox["width"])
                bottom = float(bbox["top"]) + float(bbox["height"])
                if (
                    float(bbox["left"]) < float(zone_record["left"])
                    or float(bbox["top"]) < float(zone_record["top"])
                    or right > zone_right
                    or bottom > zone_bottom
                ):
                    raise ManifestError(
                        f"slide {number} native element {element['name']!r} escapes the frame-map add zone"
                    )
        result.append(
            {
                "slide": number,
                "template_source_slide": map_entry.get("sourceSlide"),
                "frame_map_entry_sha256": sha256_json(map_entry),
                "bounded_add_zone": zone_record,
                "native_elements_sha256": sha256_json(elements),
                "native_elements": element_records,
            }
        )
    return result


def build_manifest(
    config_path: Path,
    html_args: Sequence[str] = (),
    render_args: Sequence[str] = (),
) -> dict[str, Any]:
    config_path = config_path.expanduser().resolve()
    project = config_path.parent
    require_file(config_path, "config JSON")
    config = read_json(config_path, "config")
    contract = require_contract(config)
    _, template_pptx, starter_pptx, frame_map_path = template_inputs(config, project)
    content = config.get("content")
    if content is None:
        plan_value = DEFAULT_PLAN
    elif isinstance(content, dict):
        plan_value = content.get("slide_plan", DEFAULT_PLAN)
    else:
        raise ManifestError("content must be an object when provided")
    if not isinstance(plan_value, str):
        raise ManifestError("content.slide_plan must be a path string when provided")
    plan_path = resolve_path(plan_value, project)
    require_file(plan_path, "slide plan")
    plan = read_json(plan_path, "slide plan")
    frame_map = read_json(frame_map_path, "template frame map")
    html_paths, render_paths = prototype_paths(
        config, project, html_args, render_args
    )
    declarations = reject_render_as_final_asset(
        config, plan, render_paths, project
    )
    return {
        "schema": SCHEMA,
        "authoring_mode": "html-assisted-native",
        "contract": contract,
        "inputs": {
            "config": file_record(config_path, project),
            "slide_plan": file_record(plan_path, project),
            "template_pptx": file_record(template_pptx, project),
            "starter_pptx": file_record(starter_pptx, project),
            "template_frame_map": file_record(frame_map_path, project),
            "prototype_html": [file_record(path, project) for path in html_paths],
            "prototype_renders": [file_record(path, project) for path in render_paths],
        },
        "declared_final_assets": declarations,
        "slides": build_slide_records(plan, frame_map),
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def manifest_prototype_args(
    manifest: dict[str, Any], project: Path
) -> tuple[list[str], list[str]]:
    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict):
        raise ManifestError("manifest.inputs must be an object")
    result: list[list[str]] = [[], []]
    for result_index, key in enumerate(("prototype_html", "prototype_renders")):
        records = inputs.get(key)
        if not isinstance(records, list):
            raise ManifestError(f"manifest.inputs.{key} must be an array")
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                raise ManifestError(f"manifest.inputs.{key} has an invalid file record")
            result[result_index].append(str(resolve_path(record["path"], project)))
    return result[0], result[1]


def command_generate(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    project = config_path.parent
    manifest = build_manifest(config_path, args.prototype_html, args.prototype_render)
    output = resolve_path(args.output or DEFAULT_MANIFEST, project)
    write_json(output, manifest)
    print(f"WROTE {output}")
    print(f"MANIFEST SHA256 {sha256_file(output)}")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    project = config_path.parent
    manifest_path = resolve_path(args.manifest or DEFAULT_MANIFEST, project)
    manifest = read_json(manifest_path, "native rebuild manifest")
    if manifest.get("schema") != SCHEMA:
        raise ManifestError(f"manifest schema must be {SCHEMA}")
    html_paths, render_paths = manifest_prototype_args(manifest, project)
    expected = build_manifest(config_path, html_paths, render_paths)
    if canonical_bytes(manifest) != canonical_bytes(expected):
        raise ManifestError(
            "native rebuild manifest is stale or its declared contract changed; "
            "rerun generate"
        )
    print(f"VALID {manifest_path}")
    print(f"MANIFEST SHA256 {sha256_file(manifest_path)}")
    return 0


def command_self_test(_: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="labdeck-native-rebuild-") as temporary:
        project = Path(temporary)
        (project / "content").mkdir()
        (project / "work/template").mkdir(parents=True)
        (project / "work/html").mkdir(parents=True)
        template = project / "template.pptx"
        starter = project / "work/template/template-starter.pptx"
        frame_map = project / "content/template-frame-map.json"
        plan = project / "content/slide-plan.json"
        html = project / "work/html/slide-1.html"
        render = project / "work/html/slide-1.png"
        template.write_bytes(b"template")
        starter.write_bytes(b"starter")
        write_json(
            frame_map,
            {
                "outputSlides": [
                    {
                        "outputSlide": 1,
                        "sourceSlide": 2,
                        "editTargets": [
                            {
                                "action": "add",
                                "zone": {"x": 70, "y": 100, "w": 1140, "h": 550},
                            }
                        ],
                    }
                ]
            },
        )
        write_json(
            plan,
            {
                "slides": [
                    {
                        "slide": 1,
                        "native_elements": [
                            {
                                "name": "claim",
                                "type": "text",
                                "position": {
                                    "left": 80,
                                    "top": 120,
                                    "width": 600,
                                    "height": 80,
                                },
                                "text": "Native rebuild",
                                "style": {"fontSize": 24},
                            }
                        ],
                    }
                ]
            },
        )
        html.write_text("<html><body>prototype</body></html>", encoding="utf-8")
        render.write_bytes(b"not-a-real-png-but-hashable")
        config_path = project / "labdeck.json"
        config = {
            "deck": {"authoring_mode": "html-assisted-native"},
            "template": {
                "pptx": "template.pptx",
                "source_sha256": sha256_file(template),
                "starter_pptx": "work/template/template-starter.pptx",
                "starter_sha256": sha256_file(starter),
                "frame_map": "content/template-frame-map.json",
                "frame_map_sha256": sha256_file(frame_map),
                "html_prototype": {
                    "allowed": True,
                    "disposable": True,
                    "final_source": False,
                    "direct_conversion": False,
                    "editable_body": True,
                    "allow_full_slide_raster": False,
                    "html_paths": ["work/html/slide-1.html"],
                    "render_paths": ["work/html/slide-1.png"],
                },
            },
        }
        write_json(config_path, config)
        manifest_path = project / DEFAULT_MANIFEST
        write_json(manifest_path, build_manifest(config_path))
        manifest = read_json(manifest_path, "self-test manifest")
        html_paths, render_paths = manifest_prototype_args(manifest, project)
        if canonical_bytes(manifest) != canonical_bytes(
            build_manifest(config_path, html_paths, render_paths)
        ):
            raise ManifestError("self-test manifest did not validate")
        html.write_text("<html><body>changed</body></html>", encoding="utf-8")
        if canonical_bytes(manifest) == canonical_bytes(
            build_manifest(config_path, html_paths, render_paths)
        ):
            raise ManifestError("self-test failed to detect changed prototype")
        html.write_text("<html><body>prototype</body></html>", encoding="utf-8")
        plan_value = read_json(plan, "self-test slide plan")
        plan_value["slides"][0]["native_elements"][0]["asset_path"] = (
            "work/html/slide-1.png"
        )
        write_json(plan, plan_value)
        try:
            build_manifest(config_path)
        except ManifestError as error:
            if "prototype render is declared as a final/native asset" not in str(error):
                raise
        else:
            raise ManifestError("self-test failed to reject prototype render reuse")
    print("SELF-TEST PASS")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description=(
            "Generate or validate the hash-pinned HTML-assisted native rebuild manifest."
        )
    )
    subparsers = root.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="write a current native rebuild manifest")
    generate.add_argument("--config", required=True, help="path to labdeck.json")
    generate.add_argument(
        "--prototype-html",
        action="append",
        default=[],
        help="prototype HTML file; repeatable and additive with config paths",
    )
    generate.add_argument(
        "--prototype-render",
        action="append",
        default=[],
        help="prototype render file; repeatable and additive with config paths",
    )
    generate.add_argument(
        "--output",
        help=f"manifest path relative to the project (default: {DEFAULT_MANIFEST})",
    )
    generate.set_defaults(function=command_generate)

    validate = subparsers.add_parser("validate", help="fail if the manifest or any input is stale")
    validate.add_argument("--config", required=True, help="path to labdeck.json")
    validate.add_argument(
        "--manifest",
        help=f"manifest path relative to the project (default: {DEFAULT_MANIFEST})",
    )
    validate.set_defaults(function=command_validate)

    self_test = subparsers.add_parser("self-test", help="run a dependency-free contract smoke test")
    self_test.set_defaults(function=command_self_test)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.function(args))
    except ManifestError as error:
        print(f"ERROR: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
