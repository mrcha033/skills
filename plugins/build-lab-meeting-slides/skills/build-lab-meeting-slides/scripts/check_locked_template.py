#!/usr/bin/env python3
"""Strict, read-only template fidelity checker for exact-clone/edit decks.

This checker supplements the bundled Presentations fidelity gate.  It compares
the prepared starter deck with the final deck, resolves volatile OOXML
relationship IDs to semantic targets, and permits only frame-map rewrites plus
slide-plan native additions inside a declared insertion zone.

It never writes to either PPTX package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import posixpath
import re
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET


SCHEMA = "labdeck.locked-template-fidelity/v1"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"

BBOX_TOLERANCE_PX = 0.75
NUMBER_PRECISION = 4

RELATIONSHIP_ATTRS = {
    f"{{{REL_NS}}}id",
    f"{{{REL_NS}}}embed",
    f"{{{REL_NS}}}link",
}

CORE_PART_PATTERNS = (
    re.compile(r"^ppt/slideMasters/slideMaster\d+\.xml$"),
    re.compile(r"^ppt/slideMasters/theme/theme\d+\.xml$"),
    re.compile(r"^ppt/slideLayouts/slideLayout\d+\.xml$"),
    re.compile(r"^ppt/theme/theme\d+\.xml$"),
    re.compile(r"^ppt/tableStyles\.xml$"),
)

VOLATILE_JSON_KEYS = {"aid", "assetId"}
TEXT_JSON_KEYS = {"text", "textPreview", "plainText", "rawText"}


class CheckError(RuntimeError):
    """Fatal checker input or package error."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def local_name(expanded_name: str) -> str:
    return expanded_name.rsplit("}", 1)[-1]


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value if value is not None else ""))
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ").strip()


def normalize_number(value: float) -> float | int:
    rounded = round(float(value), NUMBER_PRECISION)
    if math.isclose(rounded, round(rounded), abs_tol=10 ** (-NUMBER_PRECISION)):
        return int(round(rounded))
    return rounded


def normalize_json(
    value: Any,
    *,
    drop_text: bool = False,
    drop_keys: frozenset[str] = frozenset(),
) -> Any:
    """Remove inspector-only churn while preserving semantic visual fields."""

    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            item = value[key]
            if key in VOLATILE_JSON_KEYS or key in drop_keys:
                continue
            if drop_text and (key in TEXT_JSON_KEYS or key == "textLayout"):
                continue
            if key == "autoFitScale" and isinstance(item, (int, float)) and math.isclose(float(item), 1.0):
                continue
            if key == "bulletCharacter" and (item is None or item == ""):
                continue
            normalized[key] = normalize_json(item, drop_text=drop_text, drop_keys=drop_keys)
        return normalized
    if isinstance(value, list):
        return [normalize_json(item, drop_text=drop_text, drop_keys=drop_keys) for item in value]
    if isinstance(value, float):
        return normalize_number(value)
    return value


def json_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(payload.encode("utf-8"))


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CheckError(f"missing JSON input: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CheckError(f"invalid JSON input {path}: {exc}") from exc


def issue(issue_id: str, message: str, **context: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"id": issue_id, "message": message}
    result.update({key: value for key, value in context.items() if value is not None})
    return result


def issue_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("id", ""),
        int(item.get("slide", 0) or 0),
        str(item.get("part", "")),
        str(item.get("name", "")),
        str(item.get("message", "")),
    )


@dataclass(frozen=True)
class Relationship:
    rel_id: str
    rel_type: str
    target: str
    target_mode: str
    resolved_target: str | None


class PptxPackage:
    """Small read-only OOXML package reader with relationship resolution."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        try:
            self.archive = zipfile.ZipFile(self.path, "r")
        except (FileNotFoundError, zipfile.BadZipFile) as exc:
            raise CheckError(f"invalid PPTX package: {self.path}") from exc
        self.names = frozenset(self.archive.namelist())
        self._relationships: dict[str, dict[str, Relationship]] = {}
        self._digest_cache: dict[str, str] = {}

    def close(self) -> None:
        self.archive.close()

    def read(self, part: str) -> bytes:
        try:
            return self.archive.read(part)
        except KeyError as exc:
            raise CheckError(f"missing PPTX part {part} in {self.path}") from exc

    def part_sha256(self, part: str) -> str:
        if part not in self._digest_cache:
            self._digest_cache[part] = sha256_bytes(self.read(part))
        return self._digest_cache[part]

    @staticmethod
    def rels_part(source_part: str) -> str:
        directory = posixpath.dirname(source_part)
        filename = posixpath.basename(source_part)
        return posixpath.join(directory, "_rels", f"{filename}.rels")

    @staticmethod
    def resolve_target(source_part: str, target: str, target_mode: str) -> str | None:
        if target_mode.casefold() == "external":
            return None
        if target.startswith("/"):
            return posixpath.normpath(target.lstrip("/"))
        return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))

    def relationships(self, source_part: str) -> dict[str, Relationship]:
        if source_part in self._relationships:
            return self._relationships[source_part]
        rels_path = self.rels_part(source_part)
        relationships: dict[str, Relationship] = {}
        if rels_path in self.names:
            try:
                root = ET.fromstring(self.read(rels_path))
            except ET.ParseError as exc:
                raise CheckError(f"invalid relationships XML {rels_path} in {self.path}") from exc
            for element in root:
                if local_name(element.tag) != "Relationship":
                    continue
                rel_id = element.attrib.get("Id", "")
                rel_type = element.attrib.get("Type", "")
                target = element.attrib.get("Target", "")
                target_mode = element.attrib.get("TargetMode", "Internal")
                relationships[rel_id] = Relationship(
                    rel_id=rel_id,
                    rel_type=rel_type,
                    target=target,
                    target_mode=target_mode,
                    resolved_target=self.resolve_target(source_part, target, target_mode),
                )
        self._relationships[source_part] = relationships
        return relationships

    def relationship_token(self, relationship: Relationship) -> str:
        if relationship.resolved_target is None:
            target = f"external:{relationship.target}"
        elif relationship.resolved_target in self.names and "/media/" in relationship.resolved_target:
            target = f"media-sha256:{self.part_sha256(relationship.resolved_target)}"
        else:
            target = relationship.resolved_target
        return "|".join((relationship.rel_type, target or "", relationship.target_mode.casefold()))

    def relationship_graph(self, source_part: str) -> list[str]:
        return sorted(self.relationship_token(rel) for rel in self.relationships(source_part).values())

    def xml_signature(self, part: str) -> Any:
        try:
            root = ET.fromstring(self.read(part))
        except ET.ParseError as exc:
            raise CheckError(f"invalid XML part {part} in {self.path}") from exc
        relationships = self.relationships(part)

        def walk(element: ET.Element) -> Any:
            attributes: list[tuple[str, Any]] = []
            for key, raw_value in sorted(element.attrib.items()):
                value: Any = raw_value
                if key in RELATIONSHIP_ATTRS:
                    relation = relationships.get(raw_value)
                    value = self.relationship_token(relation) if relation else f"unresolved:{raw_value}"
                attributes.append((key, value))
            raw_text = element.text or ""
            text = "" if not raw_text.strip() else unicodedata.normalize("NFC", raw_text)
            children = [walk(child) for child in list(element)]
            return (element.tag, attributes, text, children)

        return walk(root)

    def semantic_part_digest(self, part: str) -> str:
        return json_digest(
            {
                "xml": self.xml_signature(part),
                "relationships": self.relationship_graph(part),
            }
        )


def is_core_part(part: str) -> bool:
    return any(pattern.fullmatch(part) for pattern in CORE_PART_PATTERNS)


def core_part_category(part: str) -> str:
    if "/theme/" in part or part.startswith("ppt/theme/"):
        return "theme"
    if part.startswith("ppt/slideMasters/"):
        return "master"
    if part.startswith("ppt/slideLayouts/"):
        return "layout"
    return "table-styles"


def compare_template_core(
    starter: PptxPackage,
    final: PptxPackage,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    starter_parts = sorted(part for part in starter.names if is_core_part(part))
    final_parts = sorted(part for part in final.names if is_core_part(part))
    starter_set = set(starter_parts)
    final_set = set(final_parts)
    if starter_set != final_set:
        issues.append(
            issue(
                "template-core-part-set-changed",
                "Master, layout, theme, or table-style package parts changed.",
                missing=sorted(starter_set - final_set),
                extra=sorted(final_set - starter_set),
            )
        )

    starter_digests: dict[str, str] = {}
    final_digests: dict[str, str] = {}
    for part in sorted(starter_set & final_set):
        starter_digest = starter.semantic_part_digest(part)
        final_digest = final.semantic_part_digest(part)
        starter_digests[part] = starter_digest
        final_digests[part] = final_digest
        if starter_digest == final_digest:
            continue
        category = core_part_category(part)
        issues.append(
            issue(
                f"{category}-mutated",
                f"Locked template {category} semantics changed.",
                part=part,
                starterDigest=starter_digest,
                finalDigest=final_digest,
            )
        )

    return {
        "starterPartCount": len(starter_parts),
        "finalPartCount": len(final_parts),
        "starterDigest": json_digest(starter_digests),
        "finalDigest": json_digest(final_digests),
    }


def presentation_info(package: PptxPackage) -> dict[str, Any]:
    part = "ppt/presentation.xml"
    try:
        root = ET.fromstring(package.read(part))
    except ET.ParseError as exc:
        raise CheckError(f"invalid presentation.xml in {package.path}") from exc
    relationships = package.relationships(part)
    size: tuple[int, int] | None = None
    slide_paths: list[str] = []
    for element in root.iter():
        name = local_name(element.tag)
        if name == "sldSz":
            try:
                size = (int(element.attrib["cx"]), int(element.attrib["cy"]))
            except (KeyError, ValueError) as exc:
                raise CheckError(f"invalid slide size in {package.path}") from exc
        elif name == "sldId":
            rel_id = element.attrib.get(f"{{{REL_NS}}}id")
            relation = relationships.get(rel_id or "")
            if relation and relation.resolved_target:
                slide_paths.append(relation.resolved_target)
            else:
                slide_paths.append(f"unresolved:{rel_id}")
    if size is None:
        raise CheckError(f"presentation.xml has no slide size in {package.path}")
    return {"size": size, "slides": slide_paths}


def slide_layout_target(package: PptxPackage, slide_part: str) -> str | None:
    targets = [
        rel.resolved_target
        for rel in package.relationships(slide_part).values()
        if rel.rel_type.endswith("/slideLayout")
    ]
    if len(targets) != 1:
        return None
    return targets[0]


def slide_display_flags(package: PptxPackage, slide_part: str) -> dict[str, str]:
    try:
        root = ET.fromstring(package.read(slide_part))
    except ET.ParseError as exc:
        raise CheckError(f"invalid slide XML {slide_part} in {package.path}") from exc
    protected = {"show", "showMasterSp", "showMasterPhAnim"}
    return {local_name(key): value for key, value in root.attrib.items() if local_name(key) in protected}


def compare_presentation_routing(
    starter: PptxPackage,
    final: PptxPackage,
    frame_map: dict[str, Any],
    plan: dict[str, Any],
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    starter_info = presentation_info(starter)
    final_info = presentation_info(final)
    map_slides = frame_map.get("outputSlides")
    plan_slides = plan.get("slides")
    if not isinstance(map_slides, list):
        raise CheckError("template frame map outputSlides must be an array")
    if not isinstance(plan_slides, list):
        raise CheckError("slide plan slides must be an array")

    expected_count = len(map_slides)
    counts = {
        "starter": len(starter_info["slides"]),
        "final": len(final_info["slides"]),
        "frameMap": expected_count,
        "plan": len(plan_slides),
    }
    if len(set(counts.values())) != 1:
        issues.append(issue("slide-count-mismatch", "Starter, final, frame-map, and plan slide counts differ.", counts=counts))
    if starter_info["size"] != final_info["size"]:
        issues.append(
            issue(
                "slide-size-mutated",
                "Final slide size differs from the prepared starter.",
                starter=list(starter_info["size"]),
                final=list(final_info["size"]),
            )
        )
    expected_numbers = list(range(1, expected_count + 1))
    map_numbers = [entry.get("outputSlide") for entry in map_slides if isinstance(entry, dict)]
    if map_numbers != expected_numbers:
        issues.append(
            issue(
                "frame-map-slide-order-invalid",
                "Frame-map outputSlide values must be contiguous and ordered from one.",
                actual=map_numbers,
            )
        )
    if starter_info["slides"] != final_info["slides"]:
        issues.append(
            issue(
                "slide-order-routing-mismatch",
                "Final slide relationship order differs from the starter.",
                starter=starter_info["slides"],
                final=final_info["slides"],
            )
        )

    common_count = min(len(starter_info["slides"]), len(final_info["slides"]), expected_count)
    routes: list[dict[str, Any]] = []
    for index in range(common_count):
        slide_number = index + 1
        starter_slide = starter_info["slides"][index]
        final_slide = final_info["slides"][index]
        starter_layout = slide_layout_target(starter, starter_slide)
        final_layout = slide_layout_target(final, final_slide)
        routes.append({"slide": slide_number, "starter": starter_layout, "final": final_layout})
        if starter_layout is None or final_layout is None or starter_layout != final_layout:
            issues.append(
                issue(
                    "slide-layout-rerouted",
                    "Final slide no longer resolves to the starter slide layout.",
                    slide=slide_number,
                    starter=starter_layout,
                    final=final_layout,
                )
            )
        starter_flags = slide_display_flags(starter, starter_slide)
        final_flags = slide_display_flags(final, final_slide)
        if starter_flags != final_flags:
            issues.append(
                issue(
                    "slide-master-display-mutated",
                    "Slide flags that control inherited master visibility changed.",
                    slide=slide_number,
                    starter=starter_flags,
                    final=final_flags,
                )
            )
    return {
        "counts": counts,
        "starterSizeEmu": list(starter_info["size"]),
        "finalSizeEmu": list(final_info["size"]),
        "routes": routes,
    }


def layout_slide_number(path: Path) -> int | None:
    match = re.search(r"(\d+)(?=\.layout\.json$)", path.name)
    return int(match.group(1)) if match else None


def read_layouts(directory: Path) -> dict[int, dict[str, Any]]:
    if not directory.is_dir():
        raise CheckError(f"missing layout directory: {directory}")
    layouts: dict[int, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.layout.json")):
        number = layout_slide_number(path)
        if number is None:
            continue
        if number in layouts:
            raise CheckError(f"duplicate layout JSON for slide {number} in {directory}")
        value = load_json(path)
        if not isinstance(value, dict):
            raise CheckError(f"layout JSON must be an object: {path}")
        layouts[number] = value
    if not layouts:
        raise CheckError(f"no layout JSON files found in {directory}")
    return layouts


def normalized_layout_header(layout: dict[str, Any]) -> Any:
    slide = layout.get("slide") or {}
    return normalize_json(
        {
            "layoutId": slide.get("layoutId"),
            "layoutName": slide.get("layoutName"),
            "layoutType": slide.get("layoutType"),
            "masterLayoutId": slide.get("masterLayoutId"),
            "frame": slide.get("frame"),
        }
    )


def normalized_inherited_layers(layout: dict[str, Any]) -> Any:
    return normalize_json(layout.get("inheritedLayers", []))


def compare_layout_contract(
    starter_layouts: dict[int, dict[str, Any]],
    final_layouts: dict[int, dict[str, Any]],
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    starter_numbers = sorted(starter_layouts)
    final_numbers = sorted(final_layouts)
    if starter_numbers != final_numbers:
        issues.append(
            issue(
                "layout-json-slide-set-changed",
                "Starter and final layout JSON slide sets differ.",
                starter=starter_numbers,
                final=final_numbers,
            )
        )
    results: list[dict[str, Any]] = []
    for slide in sorted(set(starter_numbers) & set(final_numbers)):
        starter = starter_layouts[slide]
        final = final_layouts[slide]
        starter_header = normalized_layout_header(starter)
        final_header = normalized_layout_header(final)
        starter_theme = normalize_json(starter.get("theme", {}))
        final_theme = normalize_json(final.get("theme", {}))
        starter_inherited = normalized_inherited_layers(starter)
        final_inherited = normalized_inherited_layers(final)
        if starter_header != final_header:
            issues.append(
                issue(
                    "layout-json-routing-mutated",
                    "Layout JSON slide frame, layout, or master routing changed.",
                    slide=slide,
                    starter=starter_header,
                    final=final_header,
                )
            )
        if starter_theme != final_theme:
            issues.append(
                issue(
                    "layout-json-theme-mutated",
                    "Resolved layout theme changed.",
                    slide=slide,
                    starterDigest=json_digest(starter_theme),
                    finalDigest=json_digest(final_theme),
                )
            )
        if starter_inherited != final_inherited:
            issues.append(
                issue(
                    "inherited-layer-mutated",
                    "Inherited layout or master layer content changed.",
                    slide=slide,
                    starterDigest=json_digest(starter_inherited),
                    finalDigest=json_digest(final_inherited),
                )
            )
        results.append(
            {
                "slide": slide,
                "headerDigest": json_digest(final_header),
                "themeDigest": json_digest(final_theme),
                "inheritedDigest": json_digest(final_inherited),
            }
        )
    return {"slides": results}


def content_value(plan_entry: dict[str, Any], reference: str) -> Any:
    content = plan_entry.get("content")
    if isinstance(content, dict) and reference in content:
        return content[reference]
    return plan_entry.get(reference)


def element_name(element: dict[str, Any]) -> str:
    return str(element.get("name") or "").strip()


def element_text(element: dict[str, Any]) -> str:
    for key in ("text", "plainText", "rawText", "textPreview"):
        if key in element and element[key] is not None:
            return normalize_text(element[key])
    return ""


def element_bbox(element: dict[str, Any]) -> tuple[float, float, float, float] | None:
    raw = element.get("bbox")
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        values = tuple(float(item) for item in raw)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in values):
        return None
    return values


def position_bbox(spec: dict[str, Any]) -> tuple[float, float, float, float] | None:
    position = spec.get("position")
    if not isinstance(position, dict):
        return None
    try:
        values = tuple(float(position[key]) for key in ("left", "top", "width", "height"))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in values):
        return None
    return values


def bboxes_close(
    left: tuple[float, float, float, float] | None,
    right: tuple[float, float, float, float] | None,
    tolerance: float = BBOX_TOLERANCE_PX,
) -> bool:
    if left is None or right is None:
        return False
    return all(abs(a - b) <= tolerance for a, b in zip(left, right))


def normalized_zone(target: dict[str, Any]) -> tuple[float, float, float, float] | None:
    zone = target.get("zone")
    if not isinstance(zone, dict):
        return None
    try:
        values = tuple(float(zone[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    x, y, width, height = values
    if not all(math.isfinite(item) for item in values) or x < 0 or y < 0 or width <= 0 or height <= 0:
        return None
    return values


def bbox_inside_zone(
    bbox: tuple[float, float, float, float] | None,
    zone: tuple[float, float, float, float] | None,
    tolerance: float = BBOX_TOLERANCE_PX,
) -> bool:
    if bbox is None or zone is None:
        return False
    x, y, width, height = bbox
    zx, zy, zw, zh = zone
    return (
        width > 0
        and height > 0
        and x >= zx - tolerance
        and y >= zy - tolerance
        and x + width <= zx + zw + tolerance
        and y + height <= zy + zh + tolerance
    )


def semantic_object_signature(element: dict[str, Any], *, drop_text: bool) -> Any:
    return normalize_json(
        element,
        drop_text=drop_text,
        drop_keys=frozenset({"id"}),
    )


def expected_kind_matches(expected: str, actual_element: dict[str, Any]) -> bool:
    expected_kind = expected.casefold().strip()
    actual_kind = str(actual_element.get("kind") or actual_element.get("type") or "").casefold()
    geometry = str(actual_element.get("geometry") or "").casefold()
    if expected_kind in {"text", "textbox"}:
        return actual_kind in {"text", "textbox", "shape"} and bool(element_text(actual_element)) and geometry in {"", "rect", "textbox"}
    if expected_kind == "connector":
        return actual_kind in {"connector", "line", "shape"}
    if expected_kind == "line":
        return actual_kind in {"connector", "line", "shape"} and geometry in {"line", "connector"}
    return expected_kind == actual_kind


def is_large_image(
    element: dict[str, Any],
    frame: dict[str, Any],
    zone: tuple[float, float, float, float] | None,
) -> bool:
    kind = str(element.get("kind") or element.get("type") or "").casefold()
    if kind != "image":
        return False
    bbox = element_bbox(element)
    if bbox is None:
        return True
    _, _, width, height = bbox
    slide_width = float(frame.get("width") or 0)
    slide_height = float(frame.get("height") or 0)
    slide_area = slide_width * slide_height
    area = width * height
    if slide_area > 0 and area >= slide_area * 0.5:
        return True
    if zone:
        zone_area = zone[2] * zone[3]
        if zone_area > 0 and area >= zone_area * 0.8:
            return True
    return False


def plan_entries_by_slide(plan: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for entry in plan.get("slides", []):
        if not isinstance(entry, dict):
            continue
        try:
            slide = int(entry.get("slide"))
        except (TypeError, ValueError):
            continue
        result[slide] = entry
    return result


def map_entries_by_slide(frame_map: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for entry in frame_map.get("outputSlides", []):
        if not isinstance(entry, dict):
            continue
        try:
            slide = int(entry.get("outputSlide"))
        except (TypeError, ValueError):
            continue
        result[slide] = entry
    return result


def choose_locked_match(
    starter_element: dict[str, Any],
    final_elements: list[dict[str, Any]],
    used_final: set[int],
) -> int | None:
    name = element_name(starter_element)
    candidates = [
        index
        for index, element in enumerate(final_elements)
        if index not in used_final and element_name(element) == name
    ]
    if len(candidates) == 1:
        return candidates[0]
    starter_id = starter_element.get("id")
    exact_id = [index for index in candidates if final_elements[index].get("id") == starter_id]
    if len(exact_id) == 1:
        return exact_id[0]
    return None


def compare_slide_objects(
    starter_layouts: dict[int, dict[str, Any]],
    final_layouts: dict[int, dict[str, Any]],
    frame_map: dict[str, Any],
    plan: dict[str, Any],
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    map_entries = map_entries_by_slide(frame_map)
    plan_entries = plan_entries_by_slide(plan)
    results: list[dict[str, Any]] = []

    for slide in sorted(set(starter_layouts) & set(final_layouts)):
        starter_elements = [item for item in starter_layouts[slide].get("elements", []) if isinstance(item, dict)]
        final_elements = [item for item in final_layouts[slide].get("elements", []) if isinstance(item, dict)]
        map_entry = map_entries.get(slide)
        plan_entry = plan_entries.get(slide)
        if map_entry is None:
            issues.append(issue("missing-frame-map-entry", "No frame-map entry exists for final slide.", slide=slide))
            continue
        if plan_entry is None:
            issues.append(issue("missing-plan-entry", "No slide-plan entry exists for final slide.", slide=slide))
            continue

        targets = map_entry.get("editTargets")
        if not isinstance(targets, list):
            issues.append(issue("frame-map-targets-invalid", "Frame-map editTargets must be an array.", slide=slide))
            targets = []
        rewrite_targets = [item for item in targets if isinstance(item, dict) and item.get("action") == "rewrite"]
        add_targets = [item for item in targets if isinstance(item, dict) and item.get("action") == "add"]
        if len(add_targets) > 1:
            issues.append(issue("frame-map-add-zone-invalid", "At most one bounded add target is supported per slide.", slide=slide))
        add_target = add_targets[0] if len(add_targets) == 1 else None
        zone = normalized_zone(add_target) if add_target else None
        if add_target and (
            zone is None
            or add_target.get("newPrimitiveAllowed") is not True
            or add_target.get("mustNotOverlapInherited") is not True
        ):
            issues.append(
                issue(
                    "frame-map-add-contract-invalid",
                    "Add target must have a valid zone and explicit primitive/overlap guards.",
                    slide=slide,
                )
            )

        used_starter: set[int] = set()
        used_final: set[int] = set()
        rewrite_count = 0
        for target in rewrite_targets:
            source_name = str(target.get("sourceName") or "").strip()
            reference = str(target.get("contentRef") or "").strip()
            starter_matches = [index for index, item in enumerate(starter_elements) if element_name(item) == source_name]
            final_matches = [index for index, item in enumerate(final_elements) if element_name(item) == source_name]
            if not source_name or len(starter_matches) != 1 or len(final_matches) != 1:
                issues.append(
                    issue(
                        "mapped-target-ambiguous",
                        "Mapped rewrite target must resolve exactly once in starter and final layouts.",
                        slide=slide,
                        name=source_name or None,
                        starterMatches=len(starter_matches),
                        finalMatches=len(final_matches),
                    )
                )
                continue
            starter_index = starter_matches[0]
            final_index = final_matches[0]
            used_starter.add(starter_index)
            used_final.add(final_index)
            rewrite_count += 1
            starter_style = semantic_object_signature(starter_elements[starter_index], drop_text=True)
            final_style = semantic_object_signature(final_elements[final_index], drop_text=True)
            if starter_style != final_style:
                issues.append(
                    issue(
                        "mapped-rewrite-style-mutated",
                        "Mapped rewrite changed geometry or visual styling, not only text.",
                        slide=slide,
                        name=source_name,
                        starterDigest=json_digest(starter_style),
                        finalDigest=json_digest(final_style),
                    )
                )
            expected = content_value(plan_entry, reference)
            if not reference or expected is None:
                issues.append(
                    issue(
                        "mapped-content-source-missing",
                        "Mapped contentRef does not resolve in the slide plan.",
                        slide=slide,
                        name=source_name,
                        contentRef=reference or None,
                    )
                )
            elif element_text(final_elements[final_index]) != normalize_text(expected):
                issues.append(
                    issue(
                        "mapped-rewrite-content-mismatch",
                        "Final mapped text does not match its slide-plan contentRef.",
                        slide=slide,
                        name=source_name,
                        contentRef=reference,
                        expected=normalize_text(expected),
                        actual=element_text(final_elements[final_index]),
                    )
                )

        locked_count = 0
        for starter_index, starter_element in enumerate(starter_elements):
            if starter_index in used_starter:
                continue
            locked_count += 1
            final_index = choose_locked_match(starter_element, final_elements, used_final)
            name = element_name(starter_element)
            if final_index is None:
                issues.append(
                    issue(
                        "locked-object-missing",
                        "Unmapped starter object is missing or ambiguous in the final slide.",
                        slide=slide,
                        name=name or None,
                    )
                )
                continue
            used_final.add(final_index)
            starter_signature = semantic_object_signature(starter_element, drop_text=False)
            final_signature = semantic_object_signature(final_elements[final_index], drop_text=False)
            if starter_signature != final_signature:
                issues.append(
                    issue(
                        "locked-object-mutated",
                        "Unmapped starter object changed in the final slide.",
                        slide=slide,
                        name=name or None,
                        starterDigest=json_digest(starter_signature),
                        finalDigest=json_digest(final_signature),
                    )
                )

        additions = [(index, item) for index, item in enumerate(final_elements) if index not in used_final]
        declared_raw = plan_entry.get("native_elements", [])
        declared = declared_raw if isinstance(declared_raw, list) else []
        if not isinstance(declared_raw, list):
            issues.append(issue("declared-additions-invalid", "native_elements must be an array.", slide=slide))
        declared_by_name: dict[str, dict[str, Any]] = {}
        for spec in declared:
            if not isinstance(spec, dict):
                issues.append(issue("declared-additions-invalid", "native_elements entries must be objects.", slide=slide))
                continue
            name = str(spec.get("name") or "").strip()
            if not name or name in declared_by_name:
                issues.append(
                    issue(
                        "declared-addition-name-invalid",
                        "Every native addition must have a unique non-empty name.",
                        slide=slide,
                        name=name or None,
                    )
                )
                continue
            declared_by_name[name] = spec

        found_declared: set[str] = set()
        frame = (final_layouts[slide].get("slide") or {}).get("frame") or {}
        for _, addition in additions:
            name = element_name(addition)
            spec = declared_by_name.get(name)
            if spec is None:
                failure_id = "undeclared-large-image" if is_large_image(addition, frame, zone) else "undeclared-addition"
                issues.append(
                    issue(
                        failure_id,
                        "Final slide contains an addition not declared in native_elements.",
                        slide=slide,
                        name=name or None,
                        kind=addition.get("kind"),
                        bbox=addition.get("bbox"),
                    )
                )
                continue
            found_declared.add(name)
            if add_target is None or zone is None:
                issues.append(
                    issue(
                        "addition-without-zone",
                        "Declared native addition has no valid frame-map insertion zone.",
                        slide=slide,
                        name=name,
                    )
                )
            elif not bbox_inside_zone(element_bbox(addition), zone):
                issues.append(
                    issue(
                        "addition-outside-zone",
                        "Native addition escapes its declared template body zone.",
                        slide=slide,
                        name=name,
                        bbox=addition.get("bbox"),
                        zone={"x": zone[0], "y": zone[1], "w": zone[2], "h": zone[3]},
                    )
                )
            expected_kind = str(spec.get("type") or spec.get("kind") or "")
            if not expected_kind_matches(expected_kind, addition):
                issues.append(
                    issue(
                        "declared-addition-kind-mismatch",
                        "Native addition kind differs from the slide plan.",
                        slide=slide,
                        name=name,
                        expected=expected_kind,
                        actual=addition.get("kind"),
                    )
                )
            expected_bbox = position_bbox(spec)
            actual_bbox = element_bbox(addition)
            if not bboxes_close(expected_bbox, actual_bbox):
                issues.append(
                    issue(
                        "declared-addition-bbox-mismatch",
                        "Native addition position differs from the slide plan.",
                        slide=slide,
                        name=name,
                        expected=list(expected_bbox) if expected_bbox else None,
                        actual=list(actual_bbox) if actual_bbox else None,
                        tolerancePx=BBOX_TOLERANCE_PX,
                    )
                )
            if "text" in spec and element_text(addition) != normalize_text(spec.get("text")):
                issues.append(
                    issue(
                        "declared-addition-text-mismatch",
                        "Native addition text differs from the slide plan.",
                        slide=slide,
                        name=name,
                        expected=normalize_text(spec.get("text")),
                        actual=element_text(addition),
                    )
                )
            if is_large_image(addition, frame, zone) and spec.get("allow_large_raster") is not True:
                issues.append(
                    issue(
                        "large-raster-not-explicitly-allowed",
                        "Large image addition requires allow_large_raster: true and a non-native fallback claim.",
                        slide=slide,
                        name=name,
                        bbox=addition.get("bbox"),
                    )
                )

        for name in sorted(set(declared_by_name) - found_declared):
            issues.append(
                issue(
                    "declared-addition-missing",
                    "Slide-plan native element is missing from the final slide.",
                    slide=slide,
                    name=name,
                )
            )

        results.append(
            {
                "slide": slide,
                "rewriteCount": rewrite_count,
                "lockedCount": locked_count,
                "additionCount": len(additions),
                "declaredAdditionCount": len(declared_by_name),
            }
        )
    return {"slides": results}


def normalize_layout_id(value: Any) -> str:
    return posixpath.normpath(str(value or "").lstrip("/"))


def compare_layout_json_to_package_routes(
    final_package: PptxPackage,
    final_layouts: dict[int, dict[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    info = presentation_info(final_package)
    for slide, slide_part in enumerate(info["slides"], start=1):
        layout = final_layouts.get(slide)
        if layout is None:
            continue
        package_target = slide_layout_target(final_package, slide_part)
        json_target = normalize_layout_id((layout.get("slide") or {}).get("layoutId"))
        if normalize_layout_id(package_target) != json_target:
            issues.append(
                issue(
                    "layout-json-package-route-mismatch",
                    "Final layout JSON does not match the PPTX slide-layout relationship.",
                    slide=slide,
                    package=package_target,
                    layoutJson=(layout.get("slide") or {}).get("layoutId"),
                )
            )


def validate_report(report: dict[str, Any]) -> None:
    issues = report.get("issues")
    if not isinstance(issues, list):
        raise CheckError("internal report validation failed: issues must be an array")
    expected_status = "fail" if issues else "pass"
    if report.get("status") != expected_status or report.get("issueCount") != len(issues):
        raise CheckError("internal report validation failed: inconsistent status or issueCount")
    if issues != sorted(issues, key=issue_sort_key):
        raise CheckError("internal report validation failed: issues are not deterministically sorted")
    for item in issues:
        issue_id = item.get("id")
        if not isinstance(issue_id, str) or not re.fullmatch(r"[a-z0-9-]+", issue_id):
            raise CheckError(f"internal report validation failed: invalid issue id {issue_id!r}")


def run_check(args: argparse.Namespace) -> dict[str, Any]:
    starter_path = Path(args.starter_pptx).expanduser().resolve()
    final_path = Path(args.final_pptx).expanduser().resolve()
    map_path = Path(args.map_path).expanduser().resolve()
    plan_path = Path(args.plan).expanduser().resolve()
    starter_layout_dir = Path(args.starter_layout_dir).expanduser().resolve()
    final_layout_dir = Path(args.final_layout_dir).expanduser().resolve()

    frame_map = load_json(map_path)
    plan = load_json(plan_path)
    if not isinstance(frame_map, dict) or not isinstance(plan, dict):
        raise CheckError("frame map and slide plan must both contain JSON objects")
    starter_layouts = read_layouts(starter_layout_dir)
    final_layouts = read_layouts(final_layout_dir)

    issues: list[dict[str, Any]] = []
    starter_package = PptxPackage(starter_path)
    final_package = PptxPackage(final_path)
    try:
        presentation_result = compare_presentation_routing(starter_package, final_package, frame_map, plan, issues)
        core_result = compare_template_core(starter_package, final_package, issues)
        layout_result = compare_layout_contract(starter_layouts, final_layouts, issues)
        compare_layout_json_to_package_routes(final_package, final_layouts, issues)
        object_result = compare_slide_objects(starter_layouts, final_layouts, frame_map, plan, issues)
    finally:
        starter_package.close()
        final_package.close()

    issues.sort(key=issue_sort_key)
    report = {
        "schema": SCHEMA,
        "status": "fail" if issues else "pass",
        "starterPptx": str(starter_path),
        "starterSha256": sha256_file(starter_path),
        "finalPptx": str(final_path),
        "finalSha256": sha256_file(final_path),
        "mapPath": str(map_path),
        "mapSha256": sha256_file(map_path),
        "planPath": str(plan_path),
        "planSha256": sha256_file(plan_path),
        "starterLayoutDir": str(starter_layout_dir),
        "finalLayoutDir": str(final_layout_dir),
        "checks": {
            "presentation": presentation_result,
            "templateCore": core_result,
            "layoutJson": layout_result,
            "slideObjects": object_result,
        },
        "issueCount": len(issues),
        "issues": issues,
    }
    validate_report(report)
    return report


def run_self_test() -> dict[str, Any]:
    tests: list[dict[str, Any]] = []

    def check(name: str, condition: bool) -> None:
        if not condition:
            raise CheckError(f"self-test failed: {name}")
        tests.append({"name": name, "status": "pass"})

    check("text-normalization", normalize_text("A\u00a0B\r\n") == "A B")
    check("bbox-contained", bbox_inside_zone((96, 142, 1080, 34), (71.36, 109.42, 1137.22, 545.01)))
    check("bbox-rejected", not bbox_inside_zone((0, 0, 1280, 720), (71.36, 109.42, 1137.22, 545.01)))
    check(
        "volatile-json-normalization",
        normalize_json({"aid": "a", "assetId": "old", "autoFitScale": 1, "value": 3.0}) == {"value": 3},
    )
    check(
        "text-kind-normalization",
        expected_kind_matches("text", {"kind": "shape", "geometry": "rect", "text": "content"}),
    )
    return {"schema": SCHEMA, "status": "pass", "tests": tests}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--starter-pptx", dest="starter_pptx")
    result.add_argument("--final-pptx", dest="final_pptx")
    result.add_argument("--map", dest="map_path")
    result.add_argument("--plan")
    result.add_argument("--starter-layout-dir", dest="starter_layout_dir")
    result.add_argument("--final-layout-dir", dest="final_layout_dir")
    result.add_argument("--out")
    result.add_argument("--self-test", action="store_true", help="Run pure-function contract checks and exit.")
    return result


def write_report(path: Path | None, report: dict[str, Any]) -> None:
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)


def main() -> int:
    argument_parser = parser()
    args = argument_parser.parse_args()
    if args.self_test:
        try:
            write_report(Path(args.out).expanduser().resolve() if args.out else None, run_self_test())
            return 0
        except CheckError as exc:
            write_report(None, {"schema": SCHEMA, "status": "fail", "error": str(exc)})
            return 2

    required = (
        "starter_pptx",
        "final_pptx",
        "map_path",
        "plan",
        "starter_layout_dir",
        "final_layout_dir",
        "out",
    )
    missing = [name.replace("_", "-") for name in required if not getattr(args, name)]
    if missing:
        argument_parser.error("missing required arguments: " + ", ".join(f"--{name}" for name in missing))

    out_path = Path(args.out).expanduser().resolve()
    try:
        report = run_check(args)
    except (CheckError, OSError, ValueError) as exc:
        report = {
            "schema": SCHEMA,
            "status": "fail",
            "issueCount": 1,
            "issues": [issue("checker-error", str(exc))],
        }
        write_report(out_path, report)
        return 2
    write_report(out_path, report)
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
