// LEGACY CUDA-SPECIFIC EXAMPLE ONLY. Do not use this builder for a supplied
// template. New projects must start from template-builder-scaffold.mjs so the
// exact-clone frame map, inherited chrome, and bounded insertion zones survive.

import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { FileBlob, PresentationFile } from "@oai/artifact-tool";

process.on("uncaughtException", (error) => {
  console.error(`FATAL: ${error?.name ?? "Error"}: ${error?.message ?? String(error)}`);
  process.exit(1);
});
process.on("unhandledRejection", (error) => {
  console.error(`FATAL: ${error?.name ?? "Error"}: ${error?.message ?? String(error)}`);
  process.exit(1);
});

const here = path.dirname(fileURLToPath(import.meta.url));
const inputPath = process.env.INPUT_PPTX
  ? path.resolve(process.env.INPUT_PPTX)
  : path.resolve(here, "../../../../outputs/parallel-reduction-prefix-sum-libraries-l40s-hardware-visuals-artifact.pptx");
const outputPath = process.env.OUTPUT_PPTX
  ? path.resolve(process.env.OUTPUT_PPTX)
  : path.resolve(here, "../../../../outputs/parallel-reduction-prefix-sum-libraries-l40s-cuda-visual-language-artifact.pptx");

// Visual tokens sampled from the Introduction to CUDA deck.  The reference is
// intentionally used for visual language only; all device facts below remain
// L40S-specific and are sourced separately.
const NAVY = "#001233";
const STRUCT = "#012345";
const DARK_BLUE = "#002855";
const BLUE = "#0353A4";
const ROYAL = "#0000B3";
const HW_BLUE = "#78BAFD";
const PALE_BLUE = "#BCDCFE";
const PALEST_BLUE = "#D2E1FE";
const LAVENDER = "#C2C2FF";
const PALE_LAVENDER = "#E0E0FF";
const MEMORY = "#DDE0E6";
const MID = "#BBC1CD";
const CYAN = "#33CCCC";
const DATA_CYAN = "#00B0F0";
const VIOLET = "#6666FF";
const RED = "#FF0000";
const WHITE = "#FFFFFF";
const MUTED = "#5C677D";
const BODY = "Arial";
const CODE = "Courier New";

const presentation = await PresentationFile.importPptx(await FileBlob.load(inputPath));
if (presentation.slides.count !== 26) throw new Error(`Expected 26 slides, found ${presentation.slides.count}`);

function getSlide(index) {
  return presentation.slides.getItem(index);
}

const EMU_PER_PX = 9525;

function convertImportedPoint(point) {
  if (!point) return point;
  return Object.fromEntries(Object.entries(point).map(([key, value]) => [key, value / EMU_PER_PX]));
}

function convertImportedPaths(paths) {
  return paths.map((customPath) => ({
    id: customPath.id,
    width: customPath.width ?? customPath.widthEmu / EMU_PER_PX,
    height: customPath.height ?? customPath.heightEmu / EMU_PER_PX,
    commands: customPath.commands.map((command) => ({
      ...(command.moveTo ? { moveTo: convertImportedPoint(command.moveTo) } : {}),
      ...(command.lineTo ? { lineTo: convertImportedPoint(command.lineTo) } : {}),
      ...(command.cubicBezTo ? { cubicBezTo: convertImportedPoint(command.cubicBezTo) } : {}),
      ...(command.quadBezTo ? { quadBezTo: convertImportedPoint(command.quadBezTo) } : {}),
      ...(command.arcTo ? { arcTo: command.arcTo } : {}),
      ...(command.close ? { close: {} } : {}),
    })),
  }));
}

// The user's reference language uses genuine DrawingML freeforms, not a
// standard PowerPoint "sketch" flag.  Capture its own double-loop circle and
// double-outline box before any source body is cleared, then reuse them.
const sketchCircleSource = getSlide(2).shapes.items.find((item) => item.name === "Oval 569");
const sketchBoxSource = getSlide(3).shapes.items.find((item) => item.name === "Flowchart: Process 2");
if (!sketchCircleSource?.customPaths || !sketchBoxSource?.customPaths) {
  throw new Error("Missing inherited freehand geometry templates on slides 3–4");
}
const SKETCH_CIRCLE_PATHS = convertImportedPaths(sketchCircleSource.customPaths);
const SKETCH_BOX_PATHS = convertImportedPaths(sketchBoxSource.customPaths);

function isTitleShape(item) {
  return String(item.id) === "123" || item.name === "PlaceHolder 1" || item.name === "Title 1";
}

function isPageNumberShape(item) {
  const name = item.name ?? "";
  const value = item.text ? String(item.text).trim() : "";
  return name.endsWith("page-number") || (name === "PlaceHolder 2" && /^\d+$/.test(value));
}

function clearBody(index) {
  const s = getSlide(index);
  for (const item of [...s.shapes.items]) {
    const keepTitle = isTitleShape(item);
    const keepPage = isPageNumberShape(item);
    if (!keepTitle && !keepPage) item.delete();
  }
  for (const item of [...s.images.items]) item.delete();
  // Imported native charts live outside slide.shapes.  The earlier reduction
  // findings deck used charts on slides 12, 14, and 15; remove those before
  // composing the hand-drawn replacements so no legacy axes or bars survive.
  for (const item of [...(s.charts?.items ?? [])]) s.charts.deleteById(item.id);
  for (const item of [...(s.tables?.items ?? [])]) s.tables.deleteById(item.id);
}

const titleStyle = {
  fontSize: 40,
  typeface: BODY,
  color: NAVY,
  bold: true,
  alignment: "left",
  verticalAlignment: "middle",
  autoFit: "shrinkText",
  wrap: "square",
  insets: { left: 4.8, right: 4.8, top: 4.8, bottom: 4.8 },
};

function setTitle(index, text, fontSize = 40) {
  const title = getSlide(index).shapes.items.find((item) => isTitleShape(item));
  if (!title) throw new Error(`Missing title on slide ${index + 1}`);
  title.text = text;
  title.text.style = { ...titleStyle, fontSize };
}

function addText(index, name, text, position, style = {}) {
  const item = getSlide(index).shapes.add({
    geometry: "textbox",
    name,
    position,
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  item.text = text;
  item.text.style = {
    fontSize: 16,
    typeface: BODY,
    color: NAVY,
    bold: false,
    alignment: "center",
    verticalAlignment: "middle",
    autoFit: "shrinkText",
    wrap: "square",
    insets: { left: 2, right: 2, top: 1, bottom: 1 },
    ...style,
  };
  return item;
}

function addRect(index, name, position, options = {}) {
  const item = getSlide(index).shapes.add({
    geometry: options.geometry ?? "rect",
    name,
    position,
    fill: options.fill ?? WHITE,
    line: options.line ?? { style: "solid", fill: options.lineColor ?? STRUCT, width: options.lineWidth ?? 1.5 },
  });
  if (options.text !== undefined) {
    item.text = options.text;
    item.text.style = {
      fontSize: options.fontSize ?? 15,
      typeface: options.typeface ?? BODY,
      color: options.textColor ?? NAVY,
      bold: options.bold ?? false,
      alignment: options.alignment ?? "center",
      verticalAlignment: options.verticalAlignment ?? "middle",
      autoFit: "shrinkText",
      wrap: "square",
      insets: options.insets ?? { left: 4, right: 4, top: 2, bottom: 2 },
    };
  }
  return item;
}

function addSketchBox(index, name, position, options = {}) {
  const item = getSlide(index).shapes.add({
    geometry: "custom",
    name,
    position,
    fill: options.fill ?? "none",
    line: options.line ?? { style: options.lineStyle ?? "solid", fill: options.lineColor ?? BLUE, width: options.lineWidth ?? 2.2 },
    customPaths: structuredClone(SKETCH_BOX_PATHS),
  });
  if (options.text !== undefined) {
    item.text = options.text;
    item.text.style = {
      fontSize: options.fontSize ?? 18,
      typeface: options.typeface ?? BODY,
      color: options.textColor ?? NAVY,
      bold: options.bold ?? false,
      alignment: options.alignment ?? "center",
      verticalAlignment: options.verticalAlignment ?? "middle",
      autoFit: "shrinkText",
      wrap: "square",
      insets: options.insets ?? { left: 7, right: 7, top: 4, bottom: 4 },
    };
  }
  return item;
}

function addSketchCircle(index, name, position, options = {}) {
  const item = getSlide(index).shapes.add({
    geometry: "custom",
    name,
    position,
    fill: options.fill ?? "none",
    line: options.line ?? { style: options.lineStyle ?? "solid", fill: options.lineColor ?? BLUE, width: options.lineWidth ?? 2.2 },
    customPaths: structuredClone(SKETCH_CIRCLE_PATHS),
  });
  if (options.text !== undefined) {
    item.text = options.text;
    item.text.style = {
      fontSize: options.fontSize ?? 18,
      typeface: options.typeface ?? BODY,
      color: options.textColor ?? NAVY,
      bold: options.bold ?? false,
      alignment: "center",
      verticalAlignment: "middle",
      autoFit: "shrinkText",
      wrap: "square",
      insets: { left: 2, right: 2, top: 1, bottom: 1 },
    };
  }
  return item;
}

function addRoughLine(index, name, position, color = RED, lineWidth = 4) {
  const width = Math.max(position.width, 1);
  const height = Math.max(position.height, 8);
  const makePath = (offset) => ({
    width,
    height,
    commands: [
      { moveTo: { x: 0, y: height * (0.50 + offset) } },
      { lineTo: { x: width * 0.16, y: height * (0.36 - offset) } },
      { lineTo: { x: width * 0.34, y: height * (0.58 + offset) } },
      { lineTo: { x: width * 0.54, y: height * (0.42 - offset) } },
      { lineTo: { x: width * 0.74, y: height * (0.60 + offset) } },
      { lineTo: { x: width, y: height * (0.46 - offset) } },
    ],
  });
  return getSlide(index).shapes.add({
    geometry: "custom",
    name,
    position: { ...position, height },
    fill: "none",
    line: { style: "solid", fill: color, width: lineWidth },
    customPaths: [makePath(0.05), makePath(-0.05)],
  });
}

function addCross(index, name, position, color = RED, lineWidth = 5) {
  return [
    addRule(index, `${name}-a`, position.left, position.top, position.width, position.height, color, lineWidth),
    addRule(index, `${name}-b`, position.left, position.top + position.height, position.width, -position.height, color, lineWidth),
  ];
}

function addRule(index, name, left, top, width, height = 0, color = BLUE, lineWidth = 1.8, style = "solid") {
  return getSlide(index).shapes.add({
    geometry: "line",
    name,
    position: { left, top, width, height },
    fill: "none",
    line: { style, fill: color, width: lineWidth },
  });
}

function connect(index, from, to, color = BLUE, options = {}) {
  const item = getSlide(index).shapes.connect(from, to, {
    kind: options.kind ?? "straight",
    fromSide: options.fromSide,
    toSide: options.toSide,
    line: { style: options.style ?? "solid", fill: color, width: options.width ?? 2 },
    tail: { type: options.head ?? "triangle", width: options.headWidth ?? "sm", length: options.headLength ?? "sm" },
  });
  item.name = options.name ?? `s${index + 1}-flow-${from.name ?? from.id}-${to.name ?? to.id}`;
  item.bringToFront();
  return item;
}

function bringForward(items) {
  for (const item of items.flat(Infinity)) item?.bringToFront?.();
}

function addClaim(index, name, text, position, options = {}) {
  return addText(index, name, text, position, {
    fontSize: options.fontSize ?? 24,
    typeface: options.typeface ?? BODY,
    color: options.color ?? RED,
    bold: true,
    alignment: options.alignment ?? "center",
    verticalAlignment: options.verticalAlignment ?? "middle",
    insets: { left: 0, right: 0, top: 0, bottom: 0 },
  });
}

function addSource(index, number, text) {
  return addText(index, `s${number}-source`, text, { left: 92, top: 648, width: 1108, height: 18 }, {
    fontSize: 9.2,
    color: MUTED,
    alignment: "left",
    autoFit: "shrinkText",
    insets: { left: 0, right: 0, top: 0, bottom: 0 },
  });
}

function addCaption(index, name, text, position, options = {}) {
  return addText(index, name, text, position, {
    fontSize: options.fontSize ?? 12,
    typeface: options.typeface ?? BODY,
    color: options.color ?? MUTED,
    bold: options.bold ?? true,
    alignment: options.alignment ?? "left",
    verticalAlignment: options.verticalAlignment ?? "middle",
    insets: { left: 0, right: 0, top: 0, bottom: 0 },
  });
}

function addArray(index, prefix, values, position, options = {}) {
  const count = values.length;
  const gap = options.gap ?? 6;
  const cellWidth = options.cellWidth ?? ((position.width - gap * (count - 1)) / count);
  const total = count * cellWidth + gap * (count - 1);
  const start = position.left + (position.width - total) / 2;
  const cells = [];
  const highlights = new Set(options.highlights ?? []);
  const inactive = new Set(options.inactive ?? []);
  for (let i = 0; i < count; i += 1) {
    const highlighted = highlights.has(i);
    const muted = inactive.has(i);
    const cell = addRect(index, `${prefix}-${i}`, {
      left: start + i * (cellWidth + gap),
      top: position.top,
      width: cellWidth,
      height: position.height,
    }, {
      fill: muted ? MEMORY : (highlighted ? (options.highlightFill ?? CYAN) : (options.fill ?? PALE_BLUE)),
      line: {
        style: muted ? "dashed" : "solid",
        fill: highlighted ? (options.highlightColor ?? RED) : (muted ? MID : (options.color ?? BLUE)),
        width: highlighted ? 2.4 : 1.3,
      },
      text: String(values[i]),
      fontSize: options.fontSize ?? 13,
      typeface: options.typeface ?? CODE,
      textColor: muted ? MUTED : NAVY,
      bold: highlighted || options.bold,
      insets: { left: 1, right: 1, top: 0, bottom: 0 },
    });
    cells.push(cell);
  }
  if (options.label) {
    addCaption(index, `${prefix}-label`, options.label, {
      left: position.left - (options.labelWidth ?? 115),
      top: position.top,
      width: options.labelWidth ?? 105,
      height: position.height,
    }, {
      fontSize: options.labelSize ?? 12,
      color: options.labelColor ?? BLUE,
      alignment: "right",
    });
  }
  return cells;
}

// Same interface as addArray, but every semantic value cell uses the
// reference deck's genuine doubled freehand box path.  Dense hardware
// micro-grids (warp slots and matrix tiles) intentionally keep their clean
// geometry; this helper is for values and scopes the presenter points at.
function addSketchArray(index, prefix, values, position, options = {}) {
  const count = values.length;
  const gap = options.gap ?? 6;
  const cellWidth = options.cellWidth ?? ((position.width - gap * (count - 1)) / count);
  const total = count * cellWidth + gap * (count - 1);
  const start = position.left + (position.width - total) / 2;
  const highlights = new Set(options.highlights ?? []);
  const inactive = new Set(options.inactive ?? []);
  const cells = addSketchRow(index, prefix, values, {
    left: start,
    top: position.top,
    cellWidth,
    cellHeight: position.height,
    gap,
    fontSize: options.fontSize ?? 16,
    typeface: options.typeface ?? CODE,
    fill: options.fill ?? PALE_BLUE,
    lineColor: options.color ?? BLUE,
    lineWidth: options.lineWidth ?? 1.8,
    bold: options.bold,
    styleFor: (i) => {
      const highlighted = highlights.has(i);
      const muted = inactive.has(i);
      return {
        fill: muted ? MEMORY : (highlighted ? (options.highlightFill ?? CYAN) : (options.fill ?? PALE_BLUE)),
        lineColor: highlighted ? (options.highlightColor ?? RED) : (muted ? MID : (options.color ?? BLUE)),
        lineWidth: highlighted ? 2.5 : (options.lineWidth ?? 1.8),
        lineStyle: muted ? "dashed" : "solid",
        textColor: muted ? MUTED : NAVY,
        bold: highlighted || options.bold,
      };
    },
  });
  if (options.label) {
    addCaption(index, `${prefix}-label`, options.label, {
      left: position.left - (options.labelWidth ?? 115),
      top: position.top,
      width: options.labelWidth ?? 105,
      height: position.height,
    }, {
      fontSize: options.labelSize ?? 16,
      color: options.labelColor ?? BLUE,
      alignment: "right",
    });
  }
  return cells;
}

function addLaneStrip(index, prefix, position, options = {}) {
  const count = options.count ?? 32;
  const gap = options.gap ?? 2;
  const width = (position.width - gap * (count - 1)) / count;
  const selected = new Set(options.selected ?? []);
  const cells = [];
  for (let i = 0; i < count; i += 1) {
    const isSelected = selected.has(i);
    const cell = addRect(index, `${prefix}-${i}`, {
      left: position.left + i * (width + gap),
      top: position.top,
      width,
      height: position.height,
    }, {
      fill: isSelected ? (options.selectedFill ?? CYAN) : (options.fill ?? DARK_BLUE),
      line: { style: "solid", fill: isSelected ? RED : (options.lineColor ?? PALE_BLUE), width: isSelected ? 2.2 : 0.8 },
      text: options.numbered && (i % 8 === 0 || i === count - 1) ? String(i) : "",
      fontSize: 7.5,
      typeface: CODE,
      textColor: isSelected ? NAVY : WHITE,
      bold: isSelected,
      insets: { left: 0, right: 0, top: 0, bottom: 0 },
    });
    cells.push(cell);
  }
  return cells;
}

function addWarpSlots(index, prefix, position, options = {}) {
  const rows = options.rows ?? 6;
  const cols = options.cols ?? 8;
  const gapX = options.gapX ?? 4;
  const gapY = options.gapY ?? 5;
  const cellWidth = (position.width - gapX * (cols - 1)) / cols;
  const cellHeight = (position.height - gapY * (rows - 1)) / rows;
  const waitRows = new Set(options.waitRows ?? []);
  const emptyRows = new Set(options.emptyRows ?? []);
  const cells = [];
  for (let r = 0; r < rows; r += 1) {
    const row = [];
    for (let c = 0; c < cols; c += 1) {
      const waiting = waitRows.has(r);
      const empty = emptyRows.has(r);
      row.push(addRect(index, `${prefix}-${r}-${c}`, {
        left: position.left + c * (cellWidth + gapX),
        top: position.top + r * (cellHeight + gapY),
        width: cellWidth,
        height: cellHeight,
      }, {
        fill: empty ? "none" : (waiting ? PALE_LAVENDER : (options.fill ?? DARK_BLUE)),
        line: {
          style: empty ? "dashed" : "solid",
          fill: empty ? MID : (waiting ? RED : PALE_BLUE),
          width: waiting ? 1.5 : 0.9,
        },
      }));
    }
    cells.push(row);
  }
  return cells;
}

function addBracket(index, name, position, color = BLUE, lineWidth = 5) {
  addRule(index, `${name}-top`, position.left, position.top, position.width, 0, color, lineWidth);
  addRule(index, `${name}-side`, position.left, position.top, 0, position.height, color, lineWidth);
  addRule(index, `${name}-bottom`, position.left, position.top + position.height, position.width, 0, color, lineWidth);
}

async function rebuildEditableTitleLabLockup() {
  const titleLayout = presentation.layouts.getByName("TITLE");
  if (!titleLayout) throw new Error("Missing TITLE layout for the title-page lab lockup");

  // The inherited lockup was authored for Roboto Condensed.  When the deck is
  // opened with Arial, its narrow master text box wraps "Software" into the
  // already-overlapping LABORATORY line.  Remove only that lockup from the
  // layout and rebuild it on slide 1 so it is selectable and editable in
  // Normal view.
  const inheritedLockupShapeNames = new Set([
    "Google Shape;22;p2", // spaced LABORATORY
    "Google Shape;23;p2", // Scalable System Software
    "Google Shape;24;p2", // divider
  ]);
  for (const item of [...titleLayout.shapes.items]) {
    if (inheritedLockupShapeNames.has(item.name)) item.delete();
  }
  for (const item of [...titleLayout.images.items]) {
    if (item.name === "Google Shape;16;p2") item.delete();
  }

  const slide = getSlide(0);
  for (const item of [...slide.shapes.items]) {
    if (item.name?.startsWith("s1-lab-")) item.delete();
  }
  for (const item of [...slide.images.items]) {
    if (item.alt === "S3 laboratory mark") item.delete();
  }

  const markPath = path.resolve(
    here,
    "../../cuda-hardware-reference/template-inspect/assets/ppt/media/image1.png",
  );
  slide.images.add({
    blob: await fs.readFile(markPath),
    contentType: "image/png",
    alt: "S3 laboratory mark",
    fit: "cover",
    position: { left: 629.10, top: 580.42, width: 51.17, height: 41.23 },
    crop: { left: 0.086, top: 0.17401, right: 0.11111, bottom: 0.17855 },
  });
  addText(0, "s1-lab-name", "Scalable System Software", {
    left: 690.7, top: 576.8, width: 282, height: 28,
  }, {
    fontSize: 18.7,
    typeface: BODY,
    color: NAVY,
    bold: true,
    alignment: "left",
    wrap: "none",
    autoFit: "shrinkText",
    insets: { left: 0, right: 0, top: 0, bottom: 0 },
  });
  addRule(0, "s1-lab-divider", 685.9, 607.0, 290, 0, "#A3ABBC", 0.8);
  addText(0, "s1-lab-subtitle", "L   A   B   O   R   A   T   O   R   Y", {
    left: 690.7, top: 609.0, width: 282, height: 19,
  }, {
    fontSize: 13.3,
    typeface: BODY,
    color: "#99A3B8",
    alignment: "left",
    wrap: "none",
    autoFit: "shrinkText",
    insets: { left: 0, right: 0, top: 0, bottom: 0 },
  });
}

function addStall(index, name, position, text = "STALL") {
  return addRect(index, name, position, {
    geometry: "irregularSeal1",
    fill: WHITE,
    line: { style: "solid", fill: RED, width: 2.3 },
    text,
    fontSize: 16,
    typeface: CODE,
    textColor: RED,
    bold: true,
    insets: { left: 2, right: 2, top: 2, bottom: 2 },
  });
}

function addMatrix(index, prefix, left, top, options = {}) {
  const n = options.n ?? 3;
  const cell = options.cell ?? 22;
  const gap = options.gap ?? 3;
  const cells = [];
  for (let r = 0; r < n; r += 1) {
    for (let c = 0; c < n; c += 1) {
      cells.push(addRect(index, `${prefix}-${r}-${c}`, {
        left: left + c * (cell + gap),
        top: top + r * (cell + gap),
        width: cell,
        height: cell,
      }, {
        fill: options.fill ?? PALE_BLUE,
        line: { style: "solid", fill: options.color ?? BLUE, width: 1 },
      }));
    }
  }
  if (options.label) addCaption(index, `${prefix}-label`, options.label, { left: left - 4, top: top - 26, width: n * (cell + gap), height: 24 }, { fontSize: options.labelSize ?? 12, color: options.color ?? BLUE, alignment: "center" });
  return cells;
}

function addStackedSheets(index, prefix, position, options = {}) {
  const count = options.count ?? 4;
  const offsetX = options.offsetX ?? 10;
  const offsetY = options.offsetY ?? -7;
  const sheets = [];
  for (let i = count - 1; i >= 0; i -= 1) {
    sheets.push(addRect(index, `${prefix}-${i}`, {
      left: position.left + i * offsetX,
      top: position.top + i * offsetY,
      width: position.width,
      height: position.height,
    }, {
      fill: i === 0 ? (options.fill ?? PALE_BLUE) : (options.backFill ?? PALEST_BLUE),
      line: { style: "solid", fill: options.color ?? STRUCT, width: i === 0 ? 1.8 : 1.0 },
      text: i === 0 ? (options.text ?? "") : "",
      fontSize: options.fontSize ?? 13,
      typeface: options.typeface ?? CODE,
      textColor: options.textColor ?? NAVY,
      bold: true,
    }));
  }
  return sheets;
}

// Preserve the reference deck's broad pale hardware regions without adding a
// single opaque mask-sized rectangle over the inherited template frame.
function addSegmentedFill(index, prefix, position, fill, rows) {
  const bandHeight = position.height / rows;
  // PowerPoint/LibreOffice can rasterize adjacent fractional-height shapes with
  // a one-pixel antialiasing seam.  Let each later band overlap the previous
  // one by one point; identical fills make the join visually continuous while
  // the overall segmented region keeps the same outer bounds.
  const overlap = 1;
  const bands = [];
  for (let r = 0; r < rows; r += 1) {
    const bandTop = position.top + r * bandHeight;
    bands.push(addRect(index, `${prefix}-${r}`, {
      left: position.left,
      top: bandTop - (r === 0 ? 0 : overlap),
      width: position.width,
      height: bandHeight + (r === 0 ? 0 : overlap),
    }, {
      fill,
      line: { style: "solid", fill: "none", width: 0 },
    }));
  }
  return bands;
}

function addSketchRow(index, prefix, values, options) {
  const items = [];
  const cellWidth = options.cellWidth ?? 58;
  const cellHeight = options.cellHeight ?? 42;
  const gap = options.gap ?? 12;
  for (let i = 0; i < values.length; i += 1) {
    const style = options.styleFor?.(i, values[i]) ?? {};
    items.push(addSketchBox(index, `${prefix}-${i}`, {
      left: options.left + i * (cellWidth + gap),
      top: options.top,
      width: cellWidth,
      height: cellHeight,
    }, {
      fill: style.fill ?? options.fill ?? WHITE,
      lineColor: style.lineColor ?? options.lineColor ?? BLUE,
      lineWidth: style.lineWidth ?? options.lineWidth ?? 1.9,
      lineStyle: style.lineStyle ?? options.lineStyle ?? "solid",
      text: style.text ?? String(values[i]),
      fontSize: style.fontSize ?? options.fontSize ?? 17,
      typeface: style.typeface ?? options.typeface ?? CODE,
      textColor: style.textColor ?? options.textColor ?? NAVY,
      bold: style.bold ?? options.bold ?? false,
    }));
  }
  return items;
}

function addSketchLaneRow(index, prefix, labels, options) {
  const items = [];
  const diameter = options.diameter ?? 46;
  const gap = options.gap ?? 24;
  for (let i = 0; i < labels.length; i += 1) {
    const style = options.styleFor?.(i, labels[i]) ?? {};
    items.push(addSketchCircle(index, `${prefix}-${i}`, {
      left: options.left + i * (diameter + gap),
      top: options.top,
      width: diameter,
      height: diameter,
    }, {
      fill: style.fill ?? options.fill ?? WHITE,
      lineColor: style.lineColor ?? options.lineColor ?? BLUE,
      lineWidth: style.lineWidth ?? options.lineWidth ?? 2,
      lineStyle: style.lineStyle ?? options.lineStyle ?? "solid",
      text: style.text ?? String(labels[i]),
      fontSize: style.fontSize ?? options.fontSize ?? 17,
      typeface: style.typeface ?? options.typeface ?? CODE,
      textColor: style.textColor ?? options.textColor ?? NAVY,
      bold: style.bold ?? options.bold ?? false,
    }));
  }
  return items;
}

function addReductionLead(index, text) {
  return addText(index, `s${index + 1}-lead`, text, { left: 76, top: 104, width: 1118, height: 42 }, {
    fontSize: 20.5,
    color: NAVY,
    bold: true,
    alignment: "left",
    verticalAlignment: "middle",
    insets: { left: 0, right: 0, top: 0, bottom: 0 },
  });
}

function addKernelResult(index, timing, evidence, options = {}) {
  addText(index, `s${index + 1}-timing`, timing, { left: 90, top: 580, width: 1100, height: 34 }, {
    fontSize: options.timingFontSize ?? 23.5,
    typeface: CODE,
    color: RED,
    bold: true,
    alignment: "center",
    insets: { left: 0, right: 0, top: 0, bottom: 0 },
  });
  addText(index, `s${index + 1}-evidence`, evidence, { left: 95, top: 615, width: 1090, height: 29 }, {
    fontSize: options.evidenceFontSize ?? 15.5,
    color: NAVY,
    bold: true,
    alignment: "center",
    insets: { left: 0, right: 0, top: 0, bottom: 0 },
  });
}

function addReductionSource(index, text = "Physical L40S Device 3 • 2 GiB FP32 input • CUDA events: 3 fresh processes × (10 warmups + 101 samples) • Nsight Compute first-pass counters profiled separately.") {
  return addSource(index, index + 1, text);
}

function addPairArrow(index, from, to, color = BLUE, width = 2.6, options = {}) {
  const arrow = getSlide(index).shapes.connect(from, to, {
    kind: options.kind ?? "straight",
    fromSide: options.fromSide ?? "bottom",
    toSide: options.toSide ?? "top",
    line: { style: options.style ?? "solid", fill: color, width },
    tail: { type: "triangle", width: options.headWidth ?? "sm", length: options.headLength ?? "sm" },
  });
  arrow.name = options.name ?? `s${index + 1}-arrow-${from.name}-${to.name}`;
  return arrow;
}

function addLaneCanvasLabels(index, labels) {
  for (const [text, top] of labels) {
    addText(index, `s${index + 1}-label-${text.replace(/\W+/g, "-")}-${top}`, text, { left: 78, top, width: 190, height: 34 }, {
      fontSize: 18,
      typeface: CODE,
      color: MUTED,
      bold: true,
      alignment: "right",
      insets: { left: 0, right: 6, top: 0, bottom: 0 },
    });
  }
}

// Slide 2 — continuous device → SM → warp zoom.
await rebuildEditableTitleLabLockup();

{
  const index = 1;
  clearBody(index);
  setTitle(index, "L40S Hardware Scope");

  addCaption(index, "s2-context", "The limits used later: device traffic, SM residency, and warp-local communication", { left: 76, top: 105, width: 1080, height: 30 }, { fontSize: 18, color: NAVY });

  const device = addRect(index, "s2-device-shell", { left: 78, top: 165, width: 280, height: 360 }, {
    fill: "none", line: { style: "solid", fill: STRUCT, width: 2.0 },
  });
  addSegmentedFill(index, "s2-device-fill", { left: 80, top: 167, width: 276, height: 356 }, PALE_BLUE, 7);
  addText(index, "s2-device-label", "L40S", { left: 100, top: 182, width: 236, height: 34 }, { fontSize: 26, typeface: CODE, color: NAVY, bold: true });
  addCaption(index, "s2-sm-count", "142 SMs", { left: 100, top: 218, width: 236, height: 26 }, { fontSize: 18, typeface: CODE, color: BLUE, alignment: "center" });
  const deviceTiles = [];
  for (let r = 0; r < 5; r += 1) {
    for (let c = 0; c < 4; c += 1) {
      const selected = r === 1 && c === 3;
      deviceTiles.push(addRect(index, `s2-sm-tile-${r}-${c}`, { left: 112 + c * 55, top: 255 + r * 35, width: 42, height: 24 }, {
        fill: selected ? CYAN : HW_BLUE,
        line: { style: "solid", fill: selected ? RED : BLUE, width: selected ? 2.3 : 1 },
        text: "",
        insets: { left: 0, right: 0, top: 0, bottom: 0 },
      }));
    }
  }
  addRule(index, "s2-memory-rail-a", 106, 441, 224, 0, BLUE, 5.5);
  addRule(index, "s2-memory-rail-b", 106, 452, 224, 0, BLUE, 5.5);
  const memory = addRect(index, "s2-device-memory", { left: 103, top: 470, width: 230, height: 43 }, {
    fill: MEMORY, line: { style: "solid", fill: STRUCT, width: 1.4 },
    text: "48 GB GDDR6  |  864 GB/s", fontSize: 15.5, typeface: CODE, textColor: NAVY, bold: true,
  });

  const sm = addRect(index, "s2-sm-shell", { left: 420, top: 155, width: 445, height: 390 }, {
    fill: "none", line: { style: "solid", fill: STRUCT, width: 2.0 },
  });
  addText(index, "s2-sm-label", "ONE REPRESENTATIVE SM", { left: 440, top: 170, width: 405, height: 30 }, { fontSize: 20, typeface: CODE, color: NAVY, bold: true });
  const sched = [];
  for (let i = 0; i < 4; i += 1) {
    sched.push(addRect(index, `s2-scheduler-${i}`, { left: 449 + i * 94, top: 210, width: 80, height: 32 }, {
      fill: LAVENDER, line: { style: "solid", fill: STRUCT, width: 1 }, text: "",
    }));
  }
  addCaption(index, "s2-scheduler-label", "4 WARP SCHEDULERS", { left: 449, top: 212, width: 362, height: 28 }, { fontSize: 16, typeface: CODE, color: NAVY, alignment: "center" });
  const slots = addWarpSlots(index, "s2-warp-slot", { left: 460, top: 260, width: 365, height: 144 }, { rows: 6, cols: 8 });
  addCaption(index, "s2-slot-label", "48 resident warp slots = 1,536 threads", { left: 455, top: 407, width: 375, height: 30 }, { fontSize: 16.5, typeface: CODE, color: BLUE, alignment: "center" });
  addRect(index, "s2-registers", { left: 459, top: 444, width: 175, height: 39 }, {
    fill: CYAN, line: { style: "solid", fill: STRUCT, width: 1.2 }, text: "REGISTERS", fontSize: 16, typeface: CODE, bold: true,
  });
  addRect(index, "s2-shared", { left: 650, top: 444, width: 175, height: 39 }, {
    fill: MEMORY, line: { style: "solid", fill: STRUCT, width: 1.2 }, text: "SHARED • 100 KB\n32 BANKS", fontSize: 14.5, typeface: CODE, bold: true,
  });
  addRect(index, "s2-execution", { left: 459, top: 495, width: 366, height: 34 }, {
    fill: HW_BLUE, line: { style: "solid", fill: STRUCT, width: 1.2 }, text: "CUDA LANES  /  MMA PATHS", fontSize: 16, typeface: CODE, bold: true,
  });

  const warp = addRect(index, "s2-warp-shell", { left: 935, top: 196, width: 240, height: 310 }, {
    fill: "none", line: { style: "solid", fill: STRUCT, width: 2.0 },
  });
  addText(index, "s2-warp-title", "WARP", { left: 957, top: 215, width: 196, height: 48 }, { fontSize: 34, typeface: CODE, color: RED, bold: true });
  const lanes = addLaneStrip(index, "s2-lane", { left: 960, top: 278, width: 190, height: 126 }, { count: 32, gap: 2, selected: [31], numbered: false });
  addCaption(index, "s2-lane-label", "32 lanes issue one instruction", { left: 945, top: 410, width: 220, height: 32 }, { fontSize: 16.5, typeface: CODE, color: NAVY, alignment: "center" });
  addRect(index, "s2-shuffle", { left: 960, top: 448, width: 190, height: 48 }, {
    fill: PALE_LAVENDER, line: { style: "solid", fill: BLUE, width: 1.3 }, text: "__shfl_sync\nregister exchange", fontSize: 14.5, typeface: CODE, bold: true,
  });

  connect(index, deviceTiles[7], sm, BLUE, { fromSide: "right", toSide: "left", width: 5.5 });
  connect(index, slots[0][7], warp, BLUE, { fromSide: "right", toSide: "left", width: 5.5 });
  // Keep the filled device shell behind its labels and memory rails.
  bringForward([deviceTiles, memory, sched, slots, lanes]);
  addClaim(index, "s2-causal", "traffic → residency → communication", { left: 310, top: 565, width: 660, height: 36 }, { fontSize: 23 });
  addCaption(index, "s2-event-protocol", "CUDA EVENTS • reduction: 3 × (10 warmups + 101 samples) • scan: 3 × (20 warmups + 101 samples) • medians", { left: 96, top: 605, width: 1095, height: 20 }, { fontSize: 11.5, typeface: CODE, color: BLUE, alignment: "center" });
  addCaption(index, "s2-profiler-protocol", "NSIGHT COMPUTE • first-pass counters  |  NSIGHT SYSTEMS • pass traces  |  separate profiling runs on physical L40S Device 3", { left: 96, top: 626, width: 1095, height: 20 }, { fontSize: 11.5, typeface: CODE, color: VIOLET, alignment: "center" });
}

// Slide 3 — the source reduction tree, redrawn with the reference deck's
// double-loop circles and one explicit synchronization story.
{
  const index = 2;
  clearBody(index);
  setTitle(index, "Naïve shared-memory tree: log₂B synchronized levels", 37);
  addReductionLead(index, "Within one B-value block tile, each stage halves the live values; the next stage waits at a block barrier.");

  const row0 = [3, 1, 7, 0, 4, 1, 6, 3].map((value, i) => addSketchCircle(index, `s3-input-${i}`, {
    left: 115 + i * 118, top: 165, width: 70, height: 70,
  }, { text: String(value), fontSize: 23, typeface: CODE, lineWidth: 2.6 }));
  const row1 = [4, 7, 5, 9].map((value, i) => addSketchCircle(index, `s3-l1-${i}`, {
    left: 174 + i * 236, top: 290, width: 72, height: 72,
  }, { text: String(value), fontSize: 23, typeface: CODE, lineWidth: 2.6 }));
  const row2 = [11, 14].map((value, i) => addSketchCircle(index, `s3-l2-${i}`, {
    left: 292 + i * 472, top: 415, width: 76, height: 76,
  }, { text: String(value), fontSize: 23, typeface: CODE, lineWidth: 2.6 }));
  const result = addSketchCircle(index, "s3-result", { left: 528, top: 535, width: 82, height: 82 }, {
    fill: PALE_BLUE, text: "25", fontSize: 26, typeface: CODE, lineWidth: 3.0,
  });

  for (let i = 0; i < 4; i += 1) {
    addPairArrow(index, row0[2 * i], row1[i], BLUE, 2.7);
    addPairArrow(index, row0[2 * i + 1], row1[i], BLUE, 2.7);
  }
  for (let i = 0; i < 2; i += 1) {
    addPairArrow(index, row1[2 * i], row2[i], BLUE, 2.7);
    addPairArrow(index, row1[2 * i + 1], row2[i], BLUE, 2.7);
  }
  addPairArrow(index, row2[0], result, BLUE, 2.9);
  addPairArrow(index, row2[1], result, BLUE, 2.9);
  bringForward([row0, row1, row2, result]);

  addBracket(index, "s3-sync-brace", { left: 1000, top: 274, width: 20, height: 324 }, RED, 4.0);
  addText(index, "s3-stage1", "stage 1\n4 adds + barrier", { left: 1025, top: 282, width: 190, height: 62 }, { fontSize: 18, typeface: CODE, color: RED, bold: true, alignment: "left" });
  addText(index, "s3-stage2", "stage 2\n2 adds + barrier", { left: 1025, top: 410, width: 190, height: 62 }, { fontSize: 18, typeface: CODE, color: RED, bold: true, alignment: "left" });
  addText(index, "s3-stage3", "stage 3\n1 add + barrier", { left: 1025, top: 530, width: 190, height: 62 }, { fontSize: 18, typeface: CODE, color: RED, bold: true, alignment: "left" });
  addClaim(index, "s3-conclusion", "8-value tile → 1 partial in log₂8 = 3 synchronized levels", { left: 150, top: 626, width: 900, height: 38 }, { fontSize: 25 });
}

// Slide 4 — no overlapping bubble or repeated tree: one cost model, one code
// block, and one dominant data path.
{
  const index = 3;
  clearBody(index);
  setTitle(index, "2 GiB reduction is DRAM-bound: ≈1 add per 4-byte input", 37);
  addReductionLead(index, "N FP32 inputs stream once from DRAM; they produce only N−1 adds and a tiny partial-sum output.");

  const input = addSketchBox(index, "s4-dram-input", { left: 105, top: 190, width: 275, height: 100 }, {
    fill: PALE_BLUE, text: "DRAM INPUT\nx[i]  •  4 B", fontSize: 24, typeface: CODE, bold: true, lineWidth: 2.8,
  });
  const add = addSketchCircle(index, "s4-add", { left: 515, top: 195, width: 92, height: 92 }, {
    fill: CYAN, text: "+", fontSize: 36, typeface: CODE, bold: true, lineWidth: 3.0,
  });
  const partial = addSketchBox(index, "s4-partial", { left: 735, top: 190, width: 260, height: 100 }, {
    fill: PALE_LAVENDER, text: "PARTIAL SUM\n≈ 4 B / block", fontSize: 23, typeface: CODE, bold: true, lineWidth: 2.7,
  });
  addPairArrow(index, input, add, BLUE, 5.2, { fromSide: "right", toSide: "left" });
  addPairArrow(index, add, partial, BLUE, 5.2, { fromSide: "right", toSide: "left" });
  bringForward([input, add, partial]);
  addClaim(index, "s4-intensity", "≈ 0.25 add / B", { left: 1010, top: 205, width: 190, height: 68 }, { fontSize: 27, alignment: "center" });
  addRoughLine(index, "s4-stream-line", { left: 105, top: 315, width: 890, height: 12 }, RED, 6.0);
  addClaim(index, "s4-stream-claim", "critical path: stream N inputs once", { left: 260, top: 326, width: 650, height: 48 }, { fontSize: 31 });

  const code = addSketchBox(index, "s4-code", { left: 90, top: 405, width: 500, height: 130 }, {
    fill: WHITE, text: "s[tid] += s[tid+s];\n__syncthreads();", fontSize: 25, typeface: CODE, textColor: NAVY,
    alignment: "left", verticalAlignment: "middle", lineWidth: 2.6,
    insets: { left: 30, right: 20, top: 10, bottom: 8 },
  });
  addRoughLine(index, "s4-code-highlight", { left: 130, top: 463, width: 310, height: 10 }, RED, 4.0);
  const sharedStep = addSketchBox(index, "s4-shared-step", { left: 665, top: 395, width: 515, height: 150 }, {
    fill: "none", lineColor: RED, lineWidth: 3.0,
    text: "shared-tree step\n4 B read + 4 B read + 4 B write = 12 B\n+ one block barrier", fontSize: 21, typeface: CODE, textColor: RED, bold: true,
  });
  bringForward([code, sharedStep]);
  addText(index, "s4-targets", "activate contiguous lanes   •   cut shared-memory transactions   •   halve first-pass blocks", { left: 115, top: 575, width: 1050, height: 42 }, {
    fontSize: 19, color: BLUE, bold: true, alignment: "center",
  });
  addReductionSource(index, "Arithmetic intensity uses global input bytes; the 12 B callout is on-chip shared-memory traffic per naïve tree update. L40S measurements begin on slide 6.");
}

// Slide 5 — the global contract as a clean launch-to-launch funnel.
{
  const index = 4;
  clearBody(index);
  setTitle(index, "Each block emits one partial sum; another kernel reduces those", 37);
  addReductionLead(index, "A block-wide barrier cannot synchronize blocks, so the grid must write partials and launch again.");

  const inputs = addSketchRow(index, "s5-input", [3, 1, 7, 0, 4, 1, 6, 3], {
    left: 270, top: 175, cellWidth: 64, cellHeight: 44, gap: 14, fontSize: 18, lineWidth: 2.0,
  });
  const groups = [];
  for (let g = 0; g < 2; g += 1) {
    groups.push(addSketchBox(index, `s5-block-${g}`, { left: 255 + g * 312, top: 158, width: 328, height: 82 }, {
      fill: "none", lineColor: BLUE, lineWidth: 2.7,
    }));
  }
  const partials = [11, 14].map((value, i) => addSketchCircle(index, `s5-partial-${i}`, {
    left: 380 + i * 312, top: 315, width: 82, height: 82,
  }, { fill: PALE_BLUE, text: String(value), fontSize: 24, typeface: CODE, lineWidth: 2.8 }));
  for (let i = 0; i < 2; i += 1) addPairArrow(index, groups[i], partials[i], BLUE, 4.5);
  bringForward([inputs, groups, partials]);
  addText(index, "s5-block-label", "kernel #1: one block → one partial", { left: 360, top: 252, width: 560, height: 34 }, { fontSize: 21, typeface: CODE, color: RED, bold: true });
  addRoughLine(index, "s5-boundary-1", { left: 255, top: 292, width: 640, height: 10 }, RED, 4.5);

  const nextKernel = addSketchBox(index, "s5-next-kernel", { left: 460, top: 430, width: 360, height: 72 }, {
    fill: PALE_LAVENDER, text: "kernel #2  •  reduce partials", fontSize: 22, typeface: CODE, bold: true, lineWidth: 2.8,
  });
  for (const partial of partials) addPairArrow(index, partial, nextKernel, BLUE, 3.8);
  const finalSum = addSketchCircle(index, "s5-final", { left: 596, top: 530, width: 88, height: 88 }, {
    fill: CYAN, text: "25", fontSize: 27, typeface: CODE, bold: true, lineWidth: 3.0,
  });
  addPairArrow(index, nextKernel, finalSum, BLUE, 4.8);
  bringForward([nextKernel, finalSum]);
  addClaim(index, "s5-repeat", "launch again until one value remains", { left: 760, top: 522, width: 420, height: 68 }, { fontSize: 29, alignment: "left" });
  addText(index, "s5-launches", "2 GiB K1–K6: 4 kernel launches", { left: 835, top: 585, width: 350, height: 34 }, { fontSize: 18, typeface: CODE, color: NAVY, bold: true, alignment: "left" });
  addReductionSource(index);
}

// Slide 6 — K1 establishes the canonical eight-lane canvas.
{
  const index = 5;
  clearBody(index);
  setTitle(index, "K1 scatters the work: every other lane is inactive", 38);
  addReductionLead(index, "The four required adds run in lanes 0, 2, 4, and 6 because the modulo predicate selects sparse lane IDs.");
  addLaneCanvasLabels(index, [["shared values", 185], ["thread lane", 321], ["destinations", 450]]);

  const values = addSketchRow(index, "s6-values", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 300, top: 180, cellWidth: 60, cellHeight: 46, gap: 20, fontSize: 18,
  });
  const active = new Set([0, 2, 4, 6]);
  const lanes = addSketchLaneRow(index, "s6-lanes", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 304, top: 315, diameter: 52, gap: 28, fontSize: 18,
    styleFor: (i) => active.has(i)
      ? { fill: CYAN, lineColor: BLUE, lineWidth: 2.5, bold: true }
      : { fill: WHITE, lineColor: MID, textColor: MUTED, lineWidth: 1.6 },
  });
  const dest = addSketchRow(index, "s6-dest", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 300, top: 445, cellWidth: 60, cellHeight: 46, gap: 20, fontSize: 18,
    styleFor: (i) => active.has(i)
      ? { fill: PALE_BLUE, lineColor: BLUE, lineWidth: 2.4, bold: true }
      : { fill: WHITE, lineColor: MID, textColor: MID, lineWidth: 1.4, lineStyle: "dashed" },
  });
  for (let pair = 0; pair < 4; pair += 1) {
    const laneIndex = pair * 2;
    addPairArrow(index, values[2 * pair], lanes[laneIndex], BLUE, 2.6);
    addPairArrow(index, values[2 * pair + 1], lanes[laneIndex], BLUE, 2.6);
    addPairArrow(index, lanes[laneIndex], dest[laneIndex], BLUE, 2.8);
  }
  bringForward([values, lanes, dest]);
  for (const inactive of [1, 3, 5, 7]) {
    addCross(index, `s6-inactive-${inactive}`, { left: 313 + inactive * 80, top: 324, width: 34, height: 34 }, RED, 2.4);
  }
  addSketchBox(index, "s6-half-warp", { left: 958, top: 302, width: 220, height: 82 }, {
    fill: "none", lineColor: RED, lineWidth: 3.2, text: "½ warp\ninactive", fontSize: 25, typeface: CODE, textColor: RED, bold: true,
  });
  addSketchBox(index, "s6-code", { left: 215, top: 510, width: 850, height: 56 }, {
    fill: WHITE, text: "if ((tid % (2*s)) == 0)  s[tid] += s[tid+s];", fontSize: 20, typeface: CODE, bold: true, lineWidth: 2.2,
  });
  addRoughLine(index, "s6-modulo", { left: 400, top: 550, width: 62, height: 8 }, RED, 4.0);
  addKernelResult(index,
    "CUDA EVENTS • 4.501504 ms  |  DERIVED • 477 logical GB/s  |  CODE • 4 launches",
    "CODE • 47 predicated warp add-paths/block  |  NSIGHT COMPUTE • 4.188B instructions • DRAM 63.59% peak-sustained",
    { timingFontSize: 22.5 });
  addReductionSource(index);
}

// Slide 7 — exact K1→K2 comparison on the same x-coordinates: only executing
// lane IDs change; the four destination cells remain fixed.
{
  const index = 6;
  clearBody(index);
  setTitle(index, "K2 changes lane IDs—not the four destinations", 38);
  addReductionLead(index, "Pairs (0,1), (2,3), (4,5), (6,7) stay fixed; their destination starts remain 0,2,4,6 while executing lanes compact to 0–3.");
  addLaneCanvasLabels(index, [["same source pairs", 175], ["K1 lanes (ghost)", 275], ["K2 lanes", 362], ["same destinations", 465]]);
  const values = addSketchRow(index, "s7-values", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 300, top: 170, cellWidth: 60, cellHeight: 44, gap: 20, fontSize: 17,
  });
  const k1Active = new Set([0, 2, 4, 6]);
  const ghost = addSketchLaneRow(index, "s7-k1", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 304, top: 270, diameter: 48, gap: 32, fontSize: 16,
    styleFor: (i) => k1Active.has(i)
      ? { fill: MEMORY, lineColor: MUTED, textColor: MUTED, lineWidth: 1.5 }
      : { fill: WHITE, lineColor: MID, textColor: MID, lineWidth: 1.2, lineStyle: "dashed" },
  });
  const k2Active = new Set([0, 1, 2, 3]);
  const current = addSketchLaneRow(index, "s7-k2", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 304, top: 355, diameter: 48, gap: 32, fontSize: 17,
    styleFor: (i) => k2Active.has(i)
      ? { fill: CYAN, lineColor: BLUE, lineWidth: 2.5, bold: true }
      : { fill: WHITE, lineColor: MID, textColor: MID, lineWidth: 1.2 },
  });
  const destIndices = new Set([0, 2, 4, 6]);
  const dest = addSketchRow(index, "s7-dest", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 300, top: 460, cellWidth: 60, cellHeight: 44, gap: 20, fontSize: 17,
    styleFor: (i) => destIndices.has(i)
      ? { fill: PALE_BLUE, lineColor: BLUE, lineWidth: 2.3, bold: true }
      : { fill: WHITE, lineColor: MID, textColor: MID, lineWidth: 1.2, lineStyle: "dashed" },
  });
  const pairFrames = [0, 1, 2, 3].map((pair) => addSketchBox(index, `s7-pair-${pair}`, {
    left: 292 + pair * 160, top: 160, width: 142, height: 64,
  }, { fill: "none", lineColor: BLUE, lineWidth: 1.7 }));
  for (let pair = 0; pair < 4; pair += 1) {
    addPairArrow(index, pairFrames[pair], current[pair], BLUE, 2.8);
    addPairArrow(index, current[pair], dest[pair * 2], BLUE, 2.8);
    addPairArrow(index, ghost[pair * 2], dest[pair * 2], MID, 1.4, { style: "dashed" });
  }
  bringForward([values, pairFrames, ghost, current, dest]);
  addSketchBox(index, "s7-contiguous", { left: 292, top: 345, width: 300, height: 68 }, {
    fill: "none", lineColor: RED, lineWidth: 3.0,
  });
  addText(index, "s7-contiguous-label", "contiguous lanes 0–3", { left: 918, top: 357, width: 270, height: 40 }, { fontSize: 22, typeface: CODE, color: RED, bold: true, alignment: "left" });
  addSketchBox(index, "s7-code", { left: 205, top: 515, width: 870, height: 50 }, {
    fill: WHITE, text: "index = 2*s*tid;  if (index < B)  s[index] += s[index+s];", fontSize: 19.5, typeface: CODE, bold: true, lineWidth: 2.0,
  });
  addKernelResult(index,
    "CUDA EVENTS • 4.501504 → 3.101696 ms  |  DERIVED • −31.10% • 1.451×",
    "CODE • warp add-paths 47→12/block  |  NSIGHT COMPUTE • instructions −51.68% • DRAM 63.59→88.20% peak-sustained",
    { evidenceFontSize: 14.5 });
  addReductionSource(index);
}

// Slide 8 — lanes stay fixed; only the destination run changes.
{
  const index = 7;
  clearBody(index);
  setTitle(index, "K3 keeps lanes 0–3 and makes writes contiguous", 38);
  addReductionLead(index, "The active lanes do not change; destinations move from s[0,2,4,6] to the contiguous run s[0..3].");
  addLaneCanvasLabels(index, [["same lanes 0–3", 205], ["K2 destinations", 330], ["K3 destinations", 445]]);
  const active = new Set([0, 1, 2, 3]);
  const lanes = addSketchLaneRow(index, "s8-lanes", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 304, top: 200, diameter: 52, gap: 28, fontSize: 18,
    styleFor: (i) => active.has(i)
      ? { fill: CYAN, lineColor: BLUE, lineWidth: 2.5, bold: true }
      : { fill: WHITE, lineColor: MID, textColor: MID, lineWidth: 1.2 },
  });
  const previousSet = new Set([0, 2, 4, 6]);
  const previous = addSketchRow(index, "s8-prev-dest", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 300, top: 325, cellWidth: 60, cellHeight: 44, gap: 20, fontSize: 17,
    styleFor: (i) => previousSet.has(i)
      ? { fill: MEMORY, lineColor: MUTED, textColor: MUTED, lineWidth: 1.5 }
      : { fill: WHITE, lineColor: MID, textColor: MID, lineWidth: 1.1, lineStyle: "dashed" },
  });
  const current = addSketchRow(index, "s8-current-dest", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 300, top: 440, cellWidth: 60, cellHeight: 44, gap: 20, fontSize: 17,
    styleFor: (i) => active.has(i)
      ? { fill: PALE_BLUE, lineColor: BLUE, lineWidth: 2.4, bold: true }
      : { fill: WHITE, lineColor: MID, textColor: MID, lineWidth: 1.1, lineStyle: "dashed" },
  });
  for (let i = 0; i < 4; i += 1) {
    addPairArrow(index, lanes[i], previous[i * 2], MID, 1.5, { style: "dashed" });
    addPairArrow(index, lanes[i], current[i], BLUE, 2.9);
  }
  bringForward([lanes, previous, current]);
  addSketchBox(index, "s8-contiguous-dest", { left: 290, top: 430, width: 310, height: 64 }, { fill: "none", lineColor: RED, lineWidth: 3.2 });
  addText(index, "s8-red-label", "one contiguous destination run", { left: 910, top: 442, width: 290, height: 42 }, { fontSize: 22, typeface: CODE, color: RED, bold: true, alignment: "left" });
  addSketchBox(index, "s8-code", { left: 235, top: 515, width: 810, height: 50 }, {
    fill: WHITE, text: "if (tid < s)  s[tid] += s[tid+s];", fontSize: 20, typeface: CODE, bold: true, lineWidth: 2.0,
  });
  addKernelResult(index,
    "CUDA EVENTS • 3.101696 → 3.013632 ms  |  DERIVED • −2.84% • 1.029×",
    "NSIGHT COMPUTE • shared-load wavefronts 201.6M→52.5M • global DRAM traffic unchanged");
  addReductionSource(index);
}

// Slide 9 — same K3 tree, one new global input row.
{
  const index = 8;
  clearBody(index);
  setTitle(index, "K4 halves blocks by pre-adding two inputs per thread", 37);
  addReductionLead(index, "Only the load band changes: g[i] and g[i+B] merge before the unchanged K3 shared-memory tree.");
  addLaneCanvasLabels(index, [["global g[i]", 166], ["global g[i+B]", 244], ["thread", 348], ["shared s[tid]", 460]]);
  const rowA = addSketchRow(index, "s9-a", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 300, top: 160, cellWidth: 60, cellHeight: 42, gap: 20, fontSize: 16.5,
  });
  const rowB = addSketchRow(index, "s9-b", ["B+0", "B+1", "B+2", "B+3", "B+4", "B+5", "B+6", "B+7"], {
    left: 300, top: 238, cellWidth: 60, cellHeight: 42, gap: 20, fontSize: 14.5, fill: PALE_LAVENDER, lineColor: VIOLET,
  });
  const lanes = addSketchLaneRow(index, "s9-lanes", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 304, top: 340, diameter: 52, gap: 28, fontSize: 17,
    fill: CYAN, lineColor: BLUE, lineWidth: 2.5, bold: true,
  });
  const shared = addSketchRow(index, "s9-shared", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 300, top: 455, cellWidth: 60, cellHeight: 42, gap: 20, fontSize: 16.5, fill: PALE_BLUE,
  });
  for (let i = 0; i < 8; i += 1) {
    addPairArrow(index, rowA[i], lanes[i], BLUE, 2.2);
    addPairArrow(index, rowB[i], lanes[i], VIOLET, 2.2);
    addPairArrow(index, lanes[i], shared[i], BLUE, 2.5);
  }
  bringForward([rowA, rowB, lanes, shared]);
  addText(index, "s9-same-row", "same K3 input row", { left: 975, top: 160, width: 220, height: 42 }, { fontSize: 19, typeface: CODE, color: MUTED, bold: true, alignment: "left" });
  addSketchBox(index, "s9-new-row", { left: 288, top: 226, width: 660, height: 66 }, { fill: "none", lineColor: RED, lineWidth: 3.0 });
  addText(index, "s9-two-inputs", "NEW: g[i+B] row", { left: 975, top: 232, width: 220, height: 48 }, { fontSize: 21, typeface: CODE, color: RED, bold: true, alignment: "left" });
  addSketchBox(index, "s9-code", { left: 245, top: 515, width: 790, height: 50 }, {
    fill: WHITE, text: "s[tid] = in[i] + in[i+B];", fontSize: 20, typeface: CODE, bold: true, lineWidth: 2.0,
  });
  addKernelResult(index,
    "CUDA EVENTS • 3.013632 → 2.741248 ms  |  DERIVED • −9.04% • 1.099×",
    "CODE • first-pass blocks −50%  |  NSIGHT COMPUTE • instructions −45.96% • DRAM 90.40→97.44%");
  addReductionSource(index);
}

// Slide 10 — isolate the one region K5 changes: the final warp.
{
  const index = 9;
  clearBody(index);
  setTitle(index, "K5 cuts final-warp barriers; 2 GiB time stays flat", 37);
  addReductionLead(index, "After the shared +32 stage, the same final-warp add order uses register shuffles at offsets 16, 8, 4, 2, and 1.");

  addText(index, "s10-k4-title", "K4  •  shared-memory tail", { left: 125, top: 158, width: 430, height: 36 }, { fontSize: 23, typeface: CODE, color: MUTED, bold: true });
  addText(index, "s10-k5-title", "K5  •  register-shuffle tail", { left: 725, top: 158, width: 430, height: 36 }, { fontSize: 23, typeface: CODE, color: BLUE, bold: true });
  const k4Frame = addSketchBox(index, "s10-k4-frame", { left: 90, top: 195, width: 500, height: 340 }, { fill: "none", lineColor: BLUE, lineWidth: 2.6 });
  const k5Frame = addSketchBox(index, "s10-k5-frame", { left: 690, top: 195, width: 500, height: 340 }, { fill: "none", lineColor: BLUE, lineWidth: 2.6 });

  const sm8 = addSketchRow(index, "s10-sm8", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 130, top: 225, cellWidth: 42, cellHeight: 36, gap: 10, fontSize: 14.5, fill: MEMORY, lineColor: MUTED,
  });
  const sm4 = addSketchRow(index, "s10-sm4", [0, 1, 2, 3], {
    left: 220, top: 335, cellWidth: 52, cellHeight: 38, gap: 16, fontSize: 15.5, fill: PALE_BLUE,
  });
  for (let i = 0; i < 4; i += 1) {
    addPairArrow(index, sm8[2 * i], sm4[i], BLUE, 2.2);
    addPairArrow(index, sm8[2 * i + 1], sm4[i], BLUE, 2.2);
  }
  bringForward([sm8, sm4]);
  const barrier1 = addSketchBox(index, "s10-barrier1", { left: 180, top: 275, width: 310, height: 42 }, { fill: "none", lineColor: RED, lineWidth: 2.5, text: "shared read + add + write", fontSize: 17.5, typeface: CODE, textColor: RED, bold: true });
  const barrier2 = addSketchBox(index, "s10-barrier2", { left: 180, top: 395, width: 310, height: 42 }, { fill: "none", lineColor: RED, lineWidth: 2.5, text: "one __syncthreads() after stage", fontSize: 16.5, typeface: CODE, textColor: RED, bold: true });
  addText(index, "s10-k4-caption", "repeat shared-memory stage + one block barrier", { left: 125, top: 470, width: 430, height: 42 }, { fontSize: 18, color: MUTED, bold: true });

  const regs = addSketchRow(index, "s10-regs", [0, 1, 2, 3, 4, 5, 6, 7], {
    left: 730, top: 225, cellWidth: 42, cellHeight: 36, gap: 10, fontSize: 14.5, fill: PALE_BLUE,
  });
  const plus32 = addSketchBox(index, "s10-plus32", { left: 805, top: 280, width: 280, height: 42 }, {
    fill: "none", lineColor: RED, lineWidth: 2.3, text: "+32: shared read + add", fontSize: 17.5, typeface: CODE, textColor: RED, bold: true,
  });
  const offsets = [16, 8, 4, 2, 1].map((offset, i) => addSketchBox(index, `s10-offset-${offset}`, {
    left: 730 + i * 85, top: 345, width: 70, height: 46,
  }, { fill: i === 4 ? CYAN : PALE_LAVENDER, lineColor: i === 4 ? BLUE : VIOLET, text: `↓ ${offset}`, fontSize: 18, typeface: CODE, bold: true, lineWidth: 2.2 }));
  addPairArrow(index, plus32, offsets[0], RED, 2.6);
  for (let i = 0; i < offsets.length - 1; i += 1) addPairArrow(index, offsets[i], offsets[i + 1], VIOLET, 2.6, { fromSide: "right", toSide: "left" });
  const lane0 = addSketchCircle(index, "s10-lane0", { left: 903, top: 410, width: 80, height: 80 }, { fill: CYAN, text: "lane 0\nsum", fontSize: 17.5, typeface: CODE, bold: true, lineWidth: 2.8 });
  addPairArrow(index, offsets[4], lane0, BLUE, 3.5);
  bringForward([regs, plus32, offsets, lane0, k4Frame, k5Frame, barrier1, barrier2]);
  addText(index, "s10-k5-caption", "offsets 16→1: register exchange, no block barrier", { left: 725, top: 492, width: 430, height: 34 }, { fontSize: 17.5, color: BLUE, bold: true });
  addKernelResult(index,
    "CUDA EVENTS • 2.741248 → 2.740224 ms  |  DERIVED • −1.0 µs (−0.04%) • TIE",
    "CODE • tree barriers 8→2  |  NSIGHT COMPUTE • instructions −50.22% • K4 DRAM 97.44% peak-sustained",
    { timingFontSize: 22.5 });
  addReductionSource(index);
}

// Slide 11 — the mechanism stays fixed; only runtime loop control disappears.
{
  const index = 10;
  clearBody(index);
  setTitle(index, "K6 removes loop control; the data path does not change", 37);
  addReductionLead(index, "For B=256, the runtime upper-tree loop becomes fixed +128 and +64 stages; both __syncthreads() barriers remain.");

  const runtime = addSketchBox(index, "s11-runtime", { left: 90, top: 185, width: 390, height: 150 }, {
    fill: MEMORY, lineColor: MUTED, lineWidth: 2.5,
    text: "for (stride = 128;\n     stride > 32;\n     stride >>= 1)", fontSize: 20, typeface: CODE, textColor: MUTED, bold: true,
  });
  addCross(index, "s11-runtime-x", { left: 120, top: 210, width: 330, height: 100 }, RED, 5.0);
  addText(index, "s11-runtime-label", "runtime compare + branch", { left: 118, top: 345, width: 330, height: 36 }, { fontSize: 19, typeface: CODE, color: RED, bold: true });
  const fixed128 = addSketchBox(index, "s11-fixed128", { left: 105, top: 400, width: 160, height: 88 }, { fill: PALE_BLUE, text: "fixed +128\n+ barrier", fontSize: 19.5, typeface: CODE, bold: true, lineWidth: 2.6 });
  const fixed64 = addSketchBox(index, "s11-fixed64", { left: 300, top: 400, width: 160, height: 88 }, { fill: PALE_BLUE, text: "fixed +64\n+ barrier", fontSize: 19.5, typeface: CODE, bold: true, lineWidth: 2.6 });
  addPairArrow(index, runtime, fixed128, BLUE, 3.5);
  addPairArrow(index, fixed128, fixed64, BLUE, 3.5, { fromSide: "right", toSide: "left" });
  addText(index, "s11-barriers-stay", "__syncthreads() stays after both", { left: 105, top: 495, width: 355, height: 34 }, { fontSize: 17.5, typeface: CODE, color: RED, bold: true });

  const row8 = addSketchRow(index, "s11-row8", [0, 1, 2, 3, 4, 5, 6, 7], { left: 615, top: 205, cellWidth: 50, cellHeight: 40, gap: 14, fontSize: 16.5, fill: PALE_BLUE });
  const row4 = addSketchRow(index, "s11-row4", [0, 1, 2, 3], { left: 735, top: 335, cellWidth: 58, cellHeight: 42, gap: 18, fontSize: 17, fill: PALE_BLUE });
  const row2 = addSketchRow(index, "s11-row2", [0, 1], { left: 855, top: 455, cellWidth: 64, cellHeight: 44, gap: 24, fontSize: 18, fill: CYAN });
  for (let i = 0; i < 4; i += 1) {
    addPairArrow(index, row8[2 * i], row4[i], BLUE, 2.4);
    addPairArrow(index, row8[2 * i + 1], row4[i], BLUE, 2.4);
  }
  for (let i = 0; i < 2; i += 1) {
    addPairArrow(index, row4[2 * i], row2[i], BLUE, 2.6);
    addPairArrow(index, row4[2 * i + 1], row2[i], BLUE, 2.6);
  }
  bringForward([runtime, fixed128, fixed64, row8, row4, row2]);
  addSketchBox(index, "s11-same-arrows", { left: 585, top: 175, width: 610, height: 350 }, { fill: "none", lineColor: RED, lineWidth: 2.8 });
  addText(index, "s11-same", "SAME addresses  •  SAME arrows  •  SAME traffic", { left: 650, top: 520, width: 520, height: 36 }, { fontSize: 21, typeface: CODE, color: RED, bold: true });
  addKernelResult(index,
    "CUDA EVENTS • 2.740224 → 2.741120 ms  |  DERIVED • +0.9 µs (+0.03%) • TIE",
    "CODE • same geometry + two barriers  |  NSIGHT COMPUTE • instructions −32.96% • branches −70.59% • DRAM traffic unchanged",
    { timingFontSize: 22.5, evidenceFontSize: 14.5 });
  addReductionSource(index);
}

// Slide 12 — fixed DRAM roof, shrinking control stacks.
{
  const index = 11;
  clearBody(index);
  setTitle(index, "K5/K6 cut on-chip work; 2 GiB stays at 2.74 ms", 38);
  addReductionLead(index, "End-to-end time is fixed by the first-pass DRAM critical path; K4 already reaches 97.44% peak-sustained throughput.");
  addRoughLine(index, "s12-roof", { left: 135, top: 250, width: 1010, height: 12 }, RED, 7.0);
  addText(index, "s12-roof-label", "NSIGHT COMPUTE • FIRST-PASS DRAM = 97.44% PEAK-SUSTAINED AT K4", { left: 205, top: 205, width: 880, height: 42 }, { fontSize: 20.5, typeface: CODE, color: RED, bold: true });

  const timeData = [
    ["K4", "2.741248 ms", 235],
    ["K5", "2.740224 ms", 535],
    ["K6", "2.741120 ms", 835],
  ];
  for (const [label, time, left] of timeData) {
    addSketchCircle(index, `s12-time-${label}`, { left, top: 265, width: 150, height: 92 }, { fill: WHITE, lineColor: RED, lineWidth: 2.8, text: `${label}\n${time}`, fontSize: 19.5, typeface: CODE, textColor: NAVY, bold: true });
  }
  addClaim(index, "s12-flat", "CUDA EVENTS\ntime is flat", { left: 1010, top: 276, width: 185, height: 68 }, { fontSize: 20.5, alignment: "left" });
  addText(index, "s12-inst-title", "NSIGHT COMPUTE • first-pass executed instructions", { left: 195, top: 355, width: 600, height: 30 }, { fontSize: 18.5, typeface: CODE, color: NAVY, bold: true, alignment: "left" });

  const bars = [
    { label: "K4", value: "939.5M instructions", width: 590, top: 390, fill: "#64748B" },
    { label: "K5", value: "467.7M", width: 294, top: 455, fill: HW_BLUE },
    { label: "K6", value: "313.5M", width: 197, top: 520, fill: CYAN },
  ];
  for (const bar of bars) {
    addText(index, `s12-${bar.label}-label`, bar.label, { left: 90, top: bar.top + 4, width: 90, height: 36 }, { fontSize: 20, typeface: CODE, color: NAVY, bold: true, alignment: "right" });
    addSketchBox(index, `s12-${bar.label}-bar`, { left: 200, top: bar.top, width: bar.width, height: 44 }, { fill: bar.fill, lineColor: STRUCT, lineWidth: 1.8, text: bar.value, fontSize: 19, typeface: CODE, textColor: bar.label === "K4" ? WHITE : NAVY, bold: true, alignment: "left", insets: { left: 16, right: 8, top: 3, bottom: 3 } });
  }
  addText(index, "s12-control-counters", "NSIGHT COMPUTE\nbranch inst. 84.9M → 35.7M → 10.5M\n\nCODE\ntree barriers 8 → 2 → 2", { left: 805, top: 382, width: 385, height: 128 }, { fontSize: 16.5, typeface: CODE, color: NAVY, bold: true, alignment: "center" });
  addSketchBox(index, "s12-cache-check", { left: 795, top: 515, width: 395, height: 74 }, { fill: PALE_LAVENDER, lineColor: VIOLET, lineWidth: 2.2, text: "CUDA EVENTS • 64 MiB warm-cache\n54.0 → 32.8 → 31.7 µs\n61 samples • fits 96 MiB L2", fontSize: 15.5, typeface: CODE, bold: true });
  addReductionSource(index, "2 GiB: CUDA-event medians from 3 × (10 warmups + 101 samples) • Nsight Compute first-pass counters separate • 64 MiB is a separate Device 3 CUDA-event control.");
}

// Slide 13 — one-shot K6 assignment versus capped, reused K7 blocks.
{
  const index = 12;
  clearBody(index);
  setTitle(index, "K7 reuses 568 blocks—and increases DRAM service", 38);
  addReductionLead(index, "The persistent cap assigns each thread four distant two-load streams, accumulates them, then repeats with i += 4G.");
  addText(index, "s13-k6-title", "K6  •  one pair / thread / block", { left: 105, top: 158, width: 430, height: 36 }, { fontSize: 22, typeface: CODE, color: BLUE, bold: true });
  addText(index, "s13-k7-title", "K7  •  four streams / reused block", { left: 695, top: 158, width: 455, height: 36 }, { fontSize: 22, typeface: CODE, color: RED, bold: true });
  addSketchBox(index, "s13-k6-count", { left: 100, top: 205, width: 450, height: 55 }, { fill: PALE_BLUE, text: "1,048,576 one-shot first-pass blocks", fontSize: 19, typeface: CODE, bold: true, lineWidth: 2.2 });
  const k6Pair = addSketchBox(index, "s13-k6-pair", { left: 175, top: 305, width: 180, height: 70 }, { fill: WHITE, text: "i\ni + B", fontSize: 19, typeface: CODE, bold: true, lineWidth: 2.2 });
  const k6Sum = addSketchCircle(index, "s13-k6-sum", { left: 390, top: 300, width: 82, height: 82 }, { fill: PALE_BLUE, text: "sum", fontSize: 18, typeface: CODE, bold: true, lineWidth: 2.3 });
  const k6Out = addSketchBox(index, "s13-k6-out", { left: 250, top: 440, width: 180, height: 66 }, { fill: PALE_BLUE, text: "s[tid]", fontSize: 20, typeface: CODE, bold: true, lineWidth: 2.4 });
  addPairArrow(index, k6Pair, k6Sum, BLUE, 3.2, { fromSide: "right", toSide: "left" });
  addPairArrow(index, k6Sum, k6Out, BLUE, 3.2);

  addSketchBox(index, "s13-k7-count", { left: 650, top: 205, width: 530, height: 55 }, { fill: PALE_LAVENDER, lineColor: RED, text: "568 blocks = 142 SMs × 4 blocks/SM", fontSize: 19, typeface: CODE, textColor: RED, bold: true, lineWidth: 2.6 });
  addText(index, "s13-defs", "B = blockDim.x   •   G = 2B × gridDim.x", { left: 685, top: 264, width: 455, height: 28 }, { fontSize: 16.5, typeface: CODE, color: MUTED, bold: true });
  const pairLabels = ["i\n+ (i+B)", "i+G\n+ (i+G+B)", "i+2G\n+ (i+2G+B)", "i+3G\n+ (i+3G+B)"];
  const pairs = pairLabels.map((label, i) => addSketchBox(index, `s13-pair-${i}`, { left: 635 + i * 137, top: 295, width: 120, height: 68 }, { fill: i === 0 ? WHITE : PALE_LAVENDER, lineColor: i === 0 ? BLUE : VIOLET, text: label, fontSize: 16, typeface: CODE, bold: true, lineWidth: 2.0 }));
  const sums = [0, 1, 2, 3].map((value, i) => addSketchCircle(index, `s13-sum-${i}`, { left: 665 + i * 132, top: 390, width: 78, height: 72 }, { fill: i === 0 ? PALE_BLUE : PALE_LAVENDER, lineColor: i === 0 ? BLUE : VIOLET, text: `sum${value}`, fontSize: 16, typeface: CODE, bold: true, lineWidth: 2.0 }));
  const out = addSketchBox(index, "s13-out", { left: 830, top: 475, width: 200, height: 62 }, { fill: PALE_BLUE, text: "s[tid]", fontSize: 20, typeface: CODE, bold: true, lineWidth: 2.4 });
  for (let i = 0; i < 4; i += 1) {
    addPairArrow(index, pairs[i], sums[i], i === 0 ? BLUE : VIOLET, 2.4);
    addPairArrow(index, sums[i], out, i === 0 ? BLUE : VIOLET, 2.4);
  }
  const repeat = addSketchBox(index, "s13-repeat", { left: 1045, top: 470, width: 140, height: 62 }, { fill: "none", lineColor: RED, text: "repeat\ni += 4G", fontSize: 18, typeface: CODE, textColor: RED, bold: true, lineWidth: 2.6 });
  addText(index, "s13-values-thread", "≈ 3,692 input values / thread", { left: 700, top: 545, width: 420, height: 32 }, { fontSize: 18, typeface: CODE, color: RED, bold: true });
  bringForward([k6Pair, k6Sum, k6Out, pairs, sums, out, repeat]);
  addKernelResult(index,
    "CUDA EVENTS • K6 2.741120 → cap4 K7 2.975744 ms (+8.56%)  |  no-cap K7 2.741248 ms",
    "NSIGHT COMPUTE • first pass: logical sectors 67.109M unchanged • physical DRAM sectors 71.767→78.488M (+9.37%)",
    { timingFontSize: 20.5, evidenceFontSize: 14.5 });
  addReductionSource(index, "CUDA events: capped K6/K7 = 3 × (10 warmups + 101 samples); no-cap K7 = 1 × (10 warmups + 101) • Nsight Compute first-pass counters separate.");
}

// Slide 14 — causal control: the cap, not the grid-stride code, moves traffic.
{
  const index = 13;
  clearBody(index);
  setTitle(index, "The cap—not grid-stride code—causes K7’s regression", 38);
  addReductionLead(index, "The same 67.109M logical first-pass requests require more physical DRAM service only under the aggressive cap.");
  addText(index, "s14-axis-title", "NSIGHT COMPUTE • first-pass physical DRAM-read sectors (M)", { left: 120, top: 170, width: 690, height: 36 }, { fontSize: 18.5, typeface: CODE, color: NAVY, bold: true });
  const barData = [
    { label: "K6", value: "71.767M", width: 500, top: 220, fill: HW_BLUE, color: BLUE },
    { label: "K7 cap4", value: "78.488M", width: 550, top: 340, fill: "#F3A45F", color: RED },
    { label: "K7 no cap", value: "71.731M", width: 500, top: 460, fill: HW_BLUE, color: BLUE },
  ];
  for (const bar of barData) {
    addText(index, `s14-${bar.label}-label`, bar.label, { left: 70, top: bar.top + 8, width: 130, height: 36 }, { fontSize: 20, typeface: CODE, color: NAVY, bold: true, alignment: "right" });
    addSketchBox(index, `s14-${bar.label}-bar`, { left: 220, top: bar.top, width: bar.width, height: 55 }, { fill: bar.fill, lineColor: bar.color, lineWidth: 2.0, text: bar.value, fontSize: 22, typeface: CODE, textColor: NAVY, bold: true, alignment: "right", insets: { left: 8, right: 18, top: 4, bottom: 4 } });
  }
  addText(index, "s14-delta", "NSIGHT COMPUTE +9.37% sectors  →  CUDA EVENTS +8.56% time", { left: 245, top: 286, width: 595, height: 42 }, { fontSize: 16.5, typeface: CODE, color: RED, bold: true });
  addSketchBox(index, "s14-service", { left: 835, top: 200, width: 350, height: 118 }, { fill: PALE_LAVENDER, lineColor: VIOLET, text: "NSIGHT COMPUTE • K6 → K7 cap4\noccupancy 91.22→66.52%\nlong-scoreboard stall\n75.78→97.91%", fontSize: 15.5, typeface: CODE, bold: true, lineWidth: 2.3 });
  addSketchBox(index, "s14-trace", { left: 835, top: 335, width: 350, height: 95 }, { fill: WHITE, lineColor: RED, text: "NSIGHT SYSTEMS • pass split\nfirst pass +231.748 µs\ntail −6.432 µs", fontSize: 16.5, typeface: CODE, textColor: RED, bold: true, lineWidth: 2.3 });
  addSketchBox(index, "s14-control", { left: 835, top: 446, width: 350, height: 104 }, { fill: PALE_BLUE, text: "SAME CODE • NO CAP\nNSIGHT COMPUTE • 91.25% occupancy\nNSIGHT COMPUTE • 71.731M sectors\nCUDA EVENTS • 2.741248 ms", fontSize: 14.5, typeface: CODE, bold: true, lineWidth: 2.3 });
  addClaim(index, "s14-claim", "same code + no cap → traffic and time return to K6", { left: 185, top: 565, width: 900, height: 42 }, { fontSize: 27 });
  addText(index, "s14-caveat", "Nsight Compute localizes the effect below L2; it does not identify one unique DRAM row/burst mechanism.", { left: 175, top: 610, width: 930, height: 28 }, { fontSize: 16.5, color: MUTED, alignment: "center" });
  addReductionSource(index, "CUDA-event timing, Nsight Compute first-pass counters, and the Nsight Systems pass split are separate runs; no-cap K7 is a 1 × 101 control.");
}

// Slide 15 — one measured ladder and one rule.
{
  const index = 14;
  clearBody(index);
  setTitle(index, "A CUDA optimization helps only when it shortens the critical path", 37);
  addReductionLead(index, "CUDA EVENTS • physical L40S Device 3 • fresh 2 GiB ladder • 3 processes × 101 samples.");
  const kernels = [
    { k: "K1", ms: "4.502", bw: "477", fill: MEMORY },
    { k: "K2", ms: "3.102", bw: "692", fill: PALE_BLUE },
    { k: "K3", ms: "3.014", bw: "713", fill: PALE_BLUE },
    { k: "K4", ms: "2.741", bw: "783", fill: CYAN },
    { k: "K5", ms: "2.740", bw: "784", fill: CYAN },
    { k: "K6", ms: "2.741", bw: "783", fill: CYAN },
    { k: "K7", ms: "2.976", bw: "722", fill: "#F3A45F", lineColor: RED },
  ];
  const nodes = kernels.map((kernel, i) => addSketchBox(index, `s15-${kernel.k}`, {
    left: 65 + i * 174, top: 255, width: 135, height: 95,
  }, { fill: kernel.fill, lineColor: kernel.lineColor ?? BLUE, lineWidth: i === 6 ? 3.0 : 2.4, text: `${kernel.k}\n${kernel.ms} ms`, fontSize: 21, typeface: CODE, bold: true }));
  const gains = ["−31.10%", "−2.84%", "−9.04%", "TIE", "TIE", "+8.56%"];
  for (let i = 0; i < nodes.length - 1; i += 1) {
    addPairArrow(index, nodes[i], nodes[i + 1], i === 5 ? RED : BLUE, i === 5 ? 4.0 : 3.0, { fromSide: "right", toSide: "left" });
    addText(index, `s15-gain-${i}`, gains[i], { left: 190 + i * 174, top: 205, width: 110, height: 35 }, { fontSize: 17.5, typeface: CODE, color: i >= 3 ? RED : BLUE, bold: true });
  }
  bringForward(nodes);
  kernels.forEach((kernel, i) => addText(index, `s15-bw-${kernel.k}`, `${kernel.bw} GB/s`, { left: 65 + i * 174, top: 365, width: 135, height: 31 }, { fontSize: 16.5, typeface: CODE, color: i === 6 ? RED : NAVY, bold: true }));
  addSketchBox(index, "s15-plateau", { left: 575, top: 243, width: 490, height: 120 }, { fill: "none", lineColor: RED, lineWidth: 3.2 });
  addText(index, "s15-plateau-label", "DRAM plateau", { left: 700, top: 405, width: 250, height: 38 }, { fontSize: 23, typeface: CODE, color: RED, bold: true });
  addRoughLine(index, "s15-rule-line", { left: 165, top: 485, width: 950, height: 12 }, RED, 6.0);
  addClaim(index, "s15-rule", "REMOVE THE CURRENT BOTTLENECK — OR TIME WILL NOT MOVE", { left: 125, top: 515, width: 1030, height: 62 }, { fontSize: 31 });
  addText(index, "s15-rule-note", "K1→K2 removes control waste • K2→K3 removes bank conflict • K3→K4 removes blocks • K5/K6 hide below DRAM • K7 adds DRAM service", { left: 120, top: 585, width: 1040, height: 40 }, { fontSize: 17.5, color: NAVY, bold: true, alignment: "center" });
  addReductionSource(index, "CUDA-event times drive the ladder and derived effective GB/s; Nsight Compute first-pass counter evidence is shown explicitly on the K1–K7 detail slides.");
}

// Slide 16 — same input, different output contract.
{
  const index = 15;
  clearBody(index);
  setTitle(index, "From Reduction to Prefix Sum");
  addCaption(index, "s16-contract", "same associative operator (+), different output contract", { left: 77, top: 110, width: 850, height: 30 }, { fontSize: 18, color: NAVY });

  const input = addSketchArray(index, "s16-input", [3, 1, 7, 0, 4, 1, 6, 3], { left: 240, top: 160, width: 800, height: 42 }, {
    label: "input", labelWidth: 100, labelSize: 16, cellWidth: 72, gap: 16, fill: PALE_BLUE, color: BLUE, fontSize: 17,
  });
  addSketchBox(index, "s16-op", { left: 1048, top: 156, width: 140, height: 50 }, {
    fill: PALE_LAVENDER, lineColor: BLUE, lineWidth: 2.2, text: "operator +", fontSize: 16.5, typeface: CODE, bold: true,
  });

  const reduce = addSketchBox(index, "s16-reduce-result", { left: 148, top: 320, width: 150, height: 92 }, {
    fill: DARK_BLUE, lineColor: STRUCT, lineWidth: 2.8, text: "25", fontSize: 36, typeface: CODE, textColor: WHITE, bold: true,
  });
  addText(index, "s16-reduce-label", "REDUCE", { left: 112, top: 275, width: 220, height: 34 }, { fontSize: 22, typeface: CODE, color: BLUE, bold: true });
  addCaption(index, "s16-reduce-formula", "Σ x[i]", { left: 130, top: 423, width: 185, height: 28 }, { fontSize: 17, typeface: CODE, color: NAVY, alignment: "center" });
  addClaim(index, "s16-one-value", "1 VALUE", { left: 108, top: 468, width: 230, height: 42 }, { fontSize: 30 });

  addText(index, "s16-scan-label", "SCAN", { left: 500, top: 248, width: 510, height: 34 }, { fontSize: 22, typeface: CODE, color: BLUE, bold: true });
  const inclusive = addSketchArray(index, "s16-inclusive", [3, 4, 11, 11, 15, 16, 22, 25], { left: 485, top: 316, width: 655, height: 46 }, {
    label: "inclusive", labelWidth: 115, labelSize: 16, cellWidth: 58, gap: 12, fill: CYAN, color: BLUE, fontSize: 16,
  });
  const exclusive = addSketchArray(index, "s16-exclusive", [0, 3, 4, 11, 11, 15, 16, 22], { left: 485, top: 411, width: 655, height: 46 }, {
    label: "exclusive", labelWidth: 115, labelSize: 16, cellWidth: 58, gap: 12, fill: PALE_LAVENDER, color: VIOLET, fontSize: 16, highlights: [0], highlightFill: WHITE,
  });
  // A collector bar shows all inputs collapsing to one reduction result.
  addRule(index, "s16-reduce-collector", 298, 221, 685, 0, BLUE, 3.2);
  addRule(index, "s16-reduce-collector-left", 298, 202, 0, 20, BLUE, 3.2);
  addRule(index, "s16-reduce-collector-right", 983, 202, 0, 20, BLUE, 3.2);
  const reduceHub = addRect(index, "s16-reduce-hub", { left: 442, top: 216, width: 8, height: 8 }, {
    fill: "none", line: { style: "solid", fill: "none", width: 0 },
  });
  connect(index, reduceHub, reduce, BLUE, { kind: "elbow", fromSide: "bottom", toSide: "top", width: 3.5 });
  connect(index, input[7], inclusive[7], BLUE, { kind: "elbow", fromSide: "bottom", toSide: "top", width: 3.5 });
  addRoughLine(index, "s16-shift-line", { left: 509, top: 382, width: 550, height: 12 }, RED, 2.5);
  addRule(index, "s16-shift-end", 1059, 388, -18, 12, RED, 2.5);
  addClaim(index, "s16-shift", "exclusive = inclusive shifted right + identity 0", { left: 535, top: 474, width: 590, height: 38 }, { fontSize: 20 });
  addClaim(index, "s16-n-values", "N PREFIX VALUES", { left: 560, top: 531, width: 510, height: 44 }, { fontSize: 29 });
  bringForward([input, reduce, inclusive, exclusive]);
  addCaption(index, "s16-equivalence", "inclusive.back() = reduce(x)     |     exclusive[i+1] = inclusive[i]", { left: 300, top: 596, width: 860, height: 32 }, { fontSize: 16, typeface: CODE, color: NAVY, alignment: "center" });
}

// Slide 17 — Kogge-Stone. Lane x-coordinates and first/final rows are reused on slide 18.
{
  const index = 16;
  clearBody(index);
  setTitle(index, "Kogge-Stone Scan");
  addCaption(index, "s17-subtitle", "Every stage doubles the dependency reach", { left: 77, top: 110, width: 650, height: 30 }, { fontSize: 18, color: NAVY });
  const left = 290;
  const width = 720;
  const ys = [160, 260, 360, 460];
  const specs = [
    [3, 1, 7, 0, 4, 1, 6, 3],
    [3, 4, 8, 7, 4, 5, 7, 9],
    [3, 4, 11, 11, 12, 12, 11, 14],
    [3, 4, 11, 11, 15, 16, 22, 25],
  ];
  const labels = ["input", "d = 1", "d = 2", "d = 4"];
  const rows = specs.map((values, r) => addSketchArray(index, `s17-row-${r}`, values, { left, top: ys[r], width, height: 42 }, {
    label: labels[r], labelWidth: 105, cellWidth: 60, gap: 16, fill: r === 0 ? PALE_BLUE : PALEST_BLUE,
    highlights: r === 0 ? [] : [7], highlightFill: CYAN, fontSize: 16, labelSize: 16,
  }));
  const distances = [1, 2, 4];
  for (let r = 1; r < rows.length; r += 1) {
    const d = distances[r - 1];
    for (let j = d; j < 8; j += 1) connect(index, rows[r - 1][j - d], rows[r][j], DATA_CYAN, { fromSide: "bottom", toSide: "top", width: 1.35, headWidth: "sm", headLength: "sm" });
  }
  bringForward(rows);
  addBracket(index, "s17-stage-brace", { left: 190, top: 159, width: 42, height: 339 }, BLUE, 5.5);
  addText(index, "s17-stage-count", "log₂8\n= 3 stages", { left: 82, top: 270, width: 110, height: 92 }, { fontSize: 18, typeface: BODY, color: BLUE, bold: true });
  addClaim(index, "s17-reach-2", "reach 2", { left: 1020, top: 258, width: 150, height: 42 }, { fontSize: 20 });
  addClaim(index, "s17-reach-4", "reach 4", { left: 1020, top: 358, width: 150, height: 42 }, { fontSize: 20 });
  addClaim(index, "s17-reach-8", "reach 8", { left: 1020, top: 458, width: 150, height: 42 }, { fontSize: 20 });
  addText(index, "s17-code", "next[i] = cur[i] + cur[i−d]   (when i ≥ d)", { left: 240, top: 522, width: 800, height: 42 }, { fontSize: 17, typeface: CODE, color: NAVY, bold: true });
  addClaim(index, "s17-result", "17 combines  •  minimum depth", { left: 360, top: 575, width: 560, height: 40 }, { fontSize: 24 });
}

// Slide 18 — Brent-Kung on the same lane canvas.
{
  const index = 17;
  clearBody(index);
  setTitle(index, "Brent-Kung Scan");
  addCaption(index, "s18-subtitle", "Same lanes; sparse up-sweep and down-sweep trade work for depth", { left: 77, top: 110, width: 900, height: 30 }, { fontSize: 18, color: NAVY });
  const left = 290;
  const width = 720;
  const ys = [160, 220, 280, 340, 400, 460];
  const specs = [
    { label: "input", values: [3, 1, 7, 0, 4, 1, 6, 3], changed: [], phase: BLUE },
    { label: "up 1", values: [3, 4, 7, 7, 4, 5, 6, 9], changed: [1, 3, 5, 7], phase: DATA_CYAN },
    { label: "up 2", values: [3, 4, 7, 11, 4, 5, 6, 14], changed: [3, 7], phase: DATA_CYAN },
    { label: "up 3", values: [3, 4, 7, 11, 4, 5, 6, 25], changed: [7], phase: DATA_CYAN },
    { label: "down 1", values: [3, 4, 7, 11, 4, 16, 6, 25], changed: [5], phase: VIOLET },
    { label: "down 2", values: [3, 4, 11, 11, 15, 16, 22, 25], changed: [2, 4, 6], phase: VIOLET },
  ];
  const rows = specs.map((spec, r) => addSketchArray(index, `s18-row-${r}`, spec.values, { left, top: ys[r], width, height: 38 }, {
    label: spec.label, labelWidth: 105, cellWidth: 60, gap: 16, fill: r === 0 ? PALE_BLUE : PALEST_BLUE,
    highlights: spec.changed, highlightFill: r <= 3 ? CYAN : PALE_LAVENDER, highlightColor: RED,
    labelColor: spec.phase, fontSize: 15.5, labelSize: 15.5,
  }));
  const links = [
    [0, 0, 1, 1, DATA_CYAN], [0, 2, 1, 3, DATA_CYAN], [0, 4, 1, 5, DATA_CYAN], [0, 6, 1, 7, DATA_CYAN],
    [1, 1, 2, 3, DATA_CYAN], [1, 5, 2, 7, DATA_CYAN], [2, 3, 3, 7, DATA_CYAN],
    [3, 3, 4, 5, VIOLET], [4, 1, 5, 2, VIOLET], [4, 3, 5, 4, VIOLET], [4, 5, 5, 6, VIOLET],
  ];
  for (const [fr, fc, tr, tc, color] of links) connect(index, rows[fr][fc], rows[tr][tc], color, { fromSide: "bottom", toSide: "top", width: 1.55 });
  bringForward(rows);
  addBracket(index, "s18-stage-brace", { left: 190, top: 159, width: 42, height: 335 }, BLUE, 5.5);
  addText(index, "s18-stage-count", "2log₂8−1\n= 5 stages", { left: 70, top: 268, width: 120, height: 92 }, { fontSize: 18, typeface: BODY, color: BLUE, bold: true });
  addClaim(index, "s18-up", "UP-SWEEP", { left: 1010, top: 232, width: 175, height: 42 }, { fontSize: 19, color: DATA_CYAN });
  addClaim(index, "s18-down", "DOWN-SWEEP", { left: 1005, top: 405, width: 185, height: 42 }, { fontSize: 19, color: VIOLET });
  addClaim(index, "s18-result", "11 combines  •  6 fewer than Kogge-Stone", { left: 310, top: 548, width: 670, height: 44 }, { fontSize: 23 });
  addSource(index, 18, "Canonical inclusive Brent-Kung shown. The lecture PDF's 6-round / 14-add up-down tree is Blelloch-style exclusive scan.");
}

// Slide 19 — same 48-slot SM scale, two barrier-hiding regimes.
{
  const index = 18;
  clearBody(index);
  setTitle(index, "Barrier Hiding on L40S", 38);
  addCaption(index, "s19-subtitle", "One SM has 48 resident warp slots; block size decides whether another block can run", { left: 77, top: 110, width: 1050, height: 30 }, { fontSize: 18, color: NAVY });

  function occupancyState(prefix, left, title, waitRows, emptyRows, options = {}) {
    addText(index, `${prefix}-title`, title, { left, top: 150, width: 500, height: 34 }, { fontSize: 20, typeface: CODE, color: options.color ?? BLUE, bold: true });
    addSketchBox(index, `${prefix}-sm`, { left, top: 195, width: 500, height: 302 }, { fill: "none", lineColor: STRUCT, lineWidth: 2.5 });
    for (let i = 0; i < 4; i += 1) addSketchBox(index, `${prefix}-sched-${i}`, { left: left + 24 + i * 116, top: 210, width: 100, height: 28 }, { fill: LAVENDER, lineColor: STRUCT, lineWidth: 1.5, text: "" });
    addCaption(index, `${prefix}-sched-label`, "4 WARP SCHEDULERS", { left: left + 24, top: 211, width: 448, height: 26 }, { fontSize: 15.5, typeface: CODE, color: NAVY, alignment: "center" });
    const cells = addWarpSlots(index, `${prefix}-slot`, { left: left + 72, top: 260, width: 356, height: 146 }, { waitRows, emptyRows });
    addSketchBox(index, `${prefix}-register-band`, { left: left + 72, top: 426, width: 356, height: 36 }, { fill: CYAN, lineColor: STRUCT, lineWidth: 2.0, text: "RESIDENT WARP STATE", fontSize: 15.5, typeface: CODE, bold: true });
    return cells;
  }

  const leftSlots = occupancyState("s19-left", 82, "B = 256  |  6 blocks × 8 warps", [0], [], { color: BLUE });
  const rightSlots = occupancyState("s19-right", 675, "B = 1024  |  1 block × 32 warps", [0, 1, 2, 3], [4, 5], { color: RED });
  for (let r = 0; r < 6; r += 1) {
    addCaption(index, `s19-left-block-${r}`, `B${r}`, { left: 96, top: 259 + r * 25.2, width: 50, height: 22 }, { fontSize: 14, typeface: CODE, color: r === 0 ? RED : BLUE, alignment: "right" });
  }
  addCaption(index, "s19-right-block", "B0", { left: 690, top: 303, width: 50, height: 45 }, { fontSize: 14, typeface: CODE, color: RED, alignment: "right" });
  const emptyLabel = addCaption(index, "s19-empty", "16 EMPTY SLOTS — no second block", { left: 754, top: 354, width: 342, height: 44 }, { fontSize: 14.5, typeface: CODE, color: MUTED, alignment: "center" });
  addStall(index, "s19-stall", { left: 1091, top: 246, width: 88, height: 75 }, "STALL");
  addClaim(index, "s19-left-cause", "5 other blocks stay READY", { left: 136, top: 463, width: 390, height: 28 }, { fontSize: 18, color: BLUE });
  addClaim(index, "s19-right-cause", "no second block → barrier exposed", { left: 731, top: 463, width: 400, height: 28 }, { fontSize: 18 });
  addText(index, "s19-left-metrics", "CUDA EVENTS • KS ≈ BK: 0.821 ms\nNSIGHT COMPUTE • active warps 88.06%", { left: 90, top: 510, width: 482, height: 56 }, { fontSize: 15, typeface: CODE, color: NAVY, bold: true });
  addText(index, "s19-right-metrics", "CUDA EVENTS • KS 0.875 ms • BK 1.130 ms\nNSIGHT COMPUTE • active warps ≈65.7%", { left: 683, top: 510, width: 482, height: 56 }, { fontSize: 15, typeface: CODE, color: NAVY, bold: true });
  addClaim(index, "s19-speedup", "Kogge-Stone 1.292× faster", { left: 737, top: 563, width: 370, height: 32 }, { fontSize: 20 });
  addCaption(index, "s19-controls", "CUDA EVENTS • 8 MiB control: BK/KS = 1.44× (B256), 1.42× (B1024)\nNSIGHT COMPUTE • padding cuts bank conflicts 15.4×  |  CUDA EVENTS • BK time only −5.7%", { left: 95, top: 600, width: 1090, height: 42 }, { fontSize: 13.5, typeface: CODE, color: MUTED, alignment: "center" });
  bringForward([leftSlots, rightSlots, emptyLabel]);
  addSource(index, 19, "L40S Device 3 | 512 MiB inclusive block scans | CUDA-event medians; Nsight Compute counters separate | BK uses 32-bank padding.");
}

// Slide 20 — one shared memory rail, one versus two full-array passes.
{
  const index = 19;
  clearBody(index);
  setTitle(index, "Device-Wide Scan Traffic");
  addCaption(index, "s20-subtitle", "At 268,435,456 FP32 elements, global bytes moved dominate the local prefix network", { left: 77, top: 110, width: 1050, height: 30 }, { fontSize: 18, color: NAVY });

  const input = addSketchBox(index, "s20-input", { left: 85, top: 245, width: 135, height: 160 }, { fill: MEMORY, lineColor: STRUCT, lineWidth: 2.8, text: "INPUT\n1 GiB", fontSize: 20, typeface: CODE, bold: true });
  const output = addSketchBox(index, "s20-output", { left: 1060, top: 245, width: 135, height: 160 }, { fill: MEMORY, lineColor: STRUCT, lineWidth: 2.8, text: "OUTPUT\n1 GiB", fontSize: 20, typeface: CODE, bold: true });
  addRoughLine(index, "s20-memory-rail-top", { left: 82, top: 421, width: 1116, height: 12 }, BLUE, 6);
  addRoughLine(index, "s20-memory-rail-bottom", { left: 82, top: 435, width: 1116, height: 12 }, BLUE, 6);
  addCaption(index, "s20-memory-label", "DEVICE MEMORY TRAFFIC RAIL", { left: 445, top: 448, width: 390, height: 28 }, { fontSize: 16, typeface: CODE, color: BLUE, alignment: "center" });

  const cub = addSketchBox(index, "s20-cub", { left: 520, top: 178, width: 230, height: 84 }, { fill: CYAN, lineColor: BLUE, lineWidth: 2.8, text: "CUB DeviceScan\nsingle-pass look-back", fontSize: 16, typeface: CODE, bold: true });
  connect(index, input, cub, DATA_CYAN, { fromSide: "top", toSide: "left", kind: "elbow", width: 6 });
  connect(index, cub, output, DATA_CYAN, { fromSide: "right", toSide: "top", kind: "elbow", width: 6 });
  addText(index, "s20-cub-metrics", "8 B/element  •  3.316 ms  •  647.5 GB/s", { left: 400, top: 270, width: 470, height: 34 }, { fontSize: 17, typeface: CODE, color: BLUE, bold: true });
  addCaption(index, "s20-copy", "D2D copy 3.280 ms  •  CUB within 1.1%", { left: 405, top: 306, width: 460, height: 28 }, { fontSize: 16, typeface: CODE, color: NAVY, alignment: "center" });

  const k1 = addSketchBox(index, "s20-k1", { left: 335, top: 496, width: 210, height: 70 }, { fill: PALE_BLUE, lineColor: BLUE, lineWidth: 2.4, text: "KERNEL 1\nlocal scan + write", fontSize: 16, typeface: CODE, bold: true });
  const temp = addSketchBox(index, "s20-temp", { left: 572, top: 496, width: 215, height: 70 }, { fill: MEMORY, lineColor: STRUCT, lineWidth: 2.4, text: "GLOBAL PREFIX\nBUFFER • 1 GiB", fontSize: 15.5, typeface: CODE, bold: true });
  const k3 = addSketchBox(index, "s20-k3", { left: 814, top: 496, width: 210, height: 70 }, { fill: PALE_LAVENDER, lineColor: VIOLET, lineWidth: 2.4, text: "KERNEL 3\nread + uniform add", fontSize: 16, typeface: CODE, bold: true });
  connect(index, input, k1, BLUE, { fromSide: "bottom", toSide: "left", kind: "elbow", width: 5 });
  connect(index, k1, temp, BLUE, { fromSide: "right", toSide: "left", width: 5 });
  connect(index, temp, k3, RED, { fromSide: "right", toSide: "left", width: 5 });
  connect(index, k3, output, RED, { fromSide: "right", toSide: "bottom", kind: "elbow", width: 5 });
  addClaim(index, "s20-extra-pass", "+ one full-array read + write", { left: 612, top: 568, width: 500, height: 35 }, { fontSize: 21 });
  addText(index, "s20-custom-metrics", "16 B/element  •  KS256 6.236 ms  •  BK256 6.262 ms  •  0.4% apart", { left: 230, top: 596, width: 840, height: 34 }, { fontSize: 16, typeface: CODE, color: NAVY, bold: true });
  addClaim(index, "s20-prediction", "2 × copy = 6.560 ms  ≈  custom 6.236 ms  →  CUB gap 1.881×", { left: 258, top: 335, width: 765, height: 45 }, { fontSize: 20 });
  bringForward([input, output, cub, k1, temp, k3]);
  addSource(index, 20, "L40S Device 3 | FP32 inclusive | logical payload 2 GiB = 1 GiB input + 1 GiB output | effective bandwidth counts 8 logical B/element.");
}

// Slide 21 — warp portrait with five register-shuffle distances.
{
  const index = 20;
  clearBody(index);
  setTitle(index, "Warp-Level Scan");
  addCaption(index, "s21-subtitle", "Kogge-Stone communication becomes five register shuffles inside one 32-lane warp", { left: 77, top: 110, width: 1030, height: 30 }, { fontSize: 18, color: NAVY });
  addBracket(index, "s21-warp-brace", { left: 92, top: 165, width: 45, height: 102 }, BLUE, 6);
  addClaim(index, "s21-warp-label", "WARP = 32 LANES", { left: 140, top: 150, width: 520, height: 40 }, { fontSize: 27 });
  const lanes = addLaneStrip(index, "s21-lane", { left: 165, top: 205, width: 930, height: 55 }, { count: 32, gap: 3, selected: [31], numbered: false });
  addCaption(index, "s21-lane-0", "lane 0", { left: 145, top: 262, width: 90, height: 28 }, { fontSize: 15.5, typeface: CODE, color: BLUE, alignment: "center" });
  addCaption(index, "s21-lane-31", "lane 31", { left: 1015, top: 174, width: 100, height: 28 }, { fontSize: 15.5, typeface: CODE, color: RED, alignment: "center" });
  addCaption(index, "s21-reg-label", "lane registers", { left: 1098, top: 207, width: 100, height: 48 }, { fontSize: 16, typeface: CODE, color: NAVY, alignment: "left" });

  const distances = [1, 2, 4, 8, 16];
  const colors = [DATA_CYAN, DATA_CYAN, DATA_CYAN, VIOLET, VIOLET];
  const laneCellWidth = (930 - 3 * 31) / 32;
  const laneStep = laneCellWidth + 3;
  const destinationX = 165 + 31 * laneStep + laneCellWidth / 2;
  addRule(index, "s21-destination-guide", destinationX, 260, 0, 258, RED, 1.8, "dashed");
  for (let r = 0; r < distances.length; r += 1) {
    const d = distances[r];
    const label = addSketchBox(index, `s21-d-${d}`, { left: 150, top: 300 + r * 48, width: 105, height: 32 }, { fill: r < 3 ? PALE_BLUE : PALE_LAVENDER, lineColor: colors[r], lineWidth: 1.8, text: `d = ${d}`, fontSize: 16, typeface: CODE, bold: true, insets: { left: 3, right: 3, top: 1, bottom: 1 } });
    const sourceX = 165 + (31 - d) * laneStep + laneCellWidth / 2;
    const y = 315 + r * 48;
    addRule(index, `s21-source-guide-${d}`, sourceX, 260, 0, y - 260, colors[r], 1.6);
    const start = addRect(index, `s21-arrow-start-${d}`, { left: sourceX - 2, top: y - 2, width: 4, height: 4 }, { fill: "none", line: { style: "solid", fill: "none", width: 0 } });
    const end = addRect(index, `s21-arrow-end-${d}`, { left: destinationX - 2, top: y - 2, width: 4, height: 4 }, { fill: "none", line: { style: "solid", fill: "none", width: 0 } });
    connect(index, start, end, colors[r], { fromSide: "right", toSide: "left", width: 2.3 });
    label.bringToFront();
  }

  const zoom = addSketchBox(index, "s21-zoom-frame", { left: 170, top: 525, width: 550, height: 108 }, { fill: "none", lineColor: STRUCT, lineWidth: 1.8 });
  addCaption(index, "s21-zoom-title", "8-LANE NUMERICAL ZOOM", { left: 185, top: 529, width: 250, height: 25 }, { fontSize: 16, typeface: CODE, color: BLUE });
  const zoomInput = addSketchArray(index, "s21-zoom-input", [3, 1, 7, 0, 4, 1, 6, 3], { left: 225, top: 559, width: 460, height: 30 }, { label: "in", labelWidth: 40, labelSize: 14.5, cellWidth: 44, gap: 8, fontSize: 14.5, fill: PALE_BLUE, lineWidth: 1.5 });
  const zoomOut = addSketchArray(index, "s21-zoom-out", [3, 4, 11, 11, 15, 16, 22, 25], { left: 225, top: 596, width: 460, height: 30 }, { label: "out", labelWidth: 40, labelSize: 14.5, cellWidth: 44, gap: 8, fontSize: 14.5, fill: CYAN, highlights: [7], lineWidth: 1.5 });
  addText(index, "s21-code", "for (d=1; d<32; d<<=1) {\n  y = __shfl_up_sync(mask, x, d);\n  if (lane >= d) x += y;\n}", { left: 750, top: 525, width: 420, height: 108 }, { fontSize: 16.5, typeface: CODE, color: NAVY, bold: true, alignment: "left", verticalAlignment: "top", insets: { left: 8, right: 4, top: 4, bottom: 2 } });
  addClaim(index, "s21-cause", "register exchange • no block barrier", { left: 505, top: 268, width: 650, height: 34 }, { fontSize: 22 });
  bringForward([lanes, zoom, zoomInput, zoomOut]);
  addSource(index, 21, "Source: NVIDIA CUDA C Programming Guide — warp shuffle and synchronization. Warp size on L40S (CC 8.9) is 32 lanes.");
}

// Slide 22 — block scope and kernel-wide boundaries on one hierarchy.
{
  const index = 21;
  clearBody(index);
  setTitle(index, "Block-to-Device Scan");
  addCaption(index, "s22-subtitle", "Shared-memory barriers coordinate one block; device-wide scan crosses kernel boundaries", { left: 77, top: 110, width: 1020, height: 30 }, { fontSize: 18, color: NAVY });

  addText(index, "s22-k1", "KERNEL 1  •  local scans inside each block", { left: 105, top: 150, width: 730, height: 30 }, { fontSize: 18, typeface: CODE, color: BLUE, bold: true, alignment: "left" });
  const b0 = addSketchBox(index, "s22-block0", { left: 105, top: 190, width: 445, height: 138 }, { fill: PALEST_BLUE, lineColor: STRUCT, lineWidth: 2.0 });
  const b1 = addSketchBox(index, "s22-block1", { left: 705, top: 190, width: 445, height: 138 }, { fill: PALEST_BLUE, lineColor: STRUCT, lineWidth: 2.0 });
  addCaption(index, "s22-b0-label", "THREAD BLOCK 0", { left: 120, top: 194, width: 180, height: 24 }, { fontSize: 16, typeface: CODE, color: BLUE });
  addCaption(index, "s22-b1-label", "THREAD BLOCK 1", { left: 720, top: 194, width: 180, height: 24 }, { fontSize: 16, typeface: CODE, color: BLUE });
  const b0in = addSketchArray(index, "s22-b0in", [1, 2, 3, 4], { left: 185, top: 224, width: 310, height: 32 }, { label: "input", labelWidth: 60, labelSize: 16, cellWidth: 51, gap: 11, fontSize: 16, lineWidth: 1.6 });
  const b0local = addSketchArray(index, "s22-b0local", [0, 1, 3, 6], { left: 185, top: 271, width: 310, height: 32 }, { label: "local", labelWidth: 60, labelSize: 16, cellWidth: 51, gap: 11, fontSize: 16, fill: CYAN, lineWidth: 1.6 });
  const b1in = addSketchArray(index, "s22-b1in", [5, 6, 7, 8], { left: 785, top: 224, width: 310, height: 32 }, { label: "input", labelWidth: 60, labelSize: 16, cellWidth: 51, gap: 11, fontSize: 16, lineWidth: 1.6 });
  const b1local = addSketchArray(index, "s22-b1local", [0, 5, 11, 18], { left: 785, top: 271, width: 310, height: 32 }, { label: "local", labelWidth: 60, labelSize: 16, cellWidth: 51, gap: 11, fontSize: 16, fill: CYAN, lineWidth: 1.6 });
  addSketchBox(index, "s22-b0-smem", { left: 435, top: 304, width: 97, height: 23 }, { fill: MEMORY, lineColor: STRUCT, lineWidth: 1.5, text: "shared", fontSize: 16, typeface: CODE, bold: true, insets: { left: 0, right: 0, top: 0, bottom: 0 } });
  addSketchBox(index, "s22-b1-smem", { left: 1035, top: 304, width: 97, height: 23 }, { fill: MEMORY, lineColor: STRUCT, lineWidth: 1.5, text: "shared", fontSize: 16, typeface: CODE, bold: true, insets: { left: 0, right: 0, top: 0, bottom: 0 } });
  addClaim(index, "s22-barrier", "BLOCK BARRIER • __syncthreads() STOPS HERE", { left: 330, top: 338, width: 620, height: 36 }, { fontSize: 18.5, typeface: CODE });
  addRule(index, "s22-block-limit-a", 551, 184, 0, 151, RED, 2.2, "dashed");
  addRule(index, "s22-block-limit-b", 703, 184, 0, 151, RED, 2.2, "dashed");

  addRoughLine(index, "s22-kernel-boundary-1", { left: 90, top: 386, width: 1100, height: 9 }, RED, 3.0);
  addText(index, "s22-k2", "KERNEL 2  •  scan block totals in global memory", { left: 105, top: 403, width: 700, height: 30 }, { fontSize: 18, typeface: CODE, color: BLUE, bold: true, alignment: "left" });
  const totals = addSketchArray(index, "s22-totals", [10, 26], { left: 385, top: 442, width: 200, height: 38 }, { label: "totals", labelWidth: 80, labelSize: 16, cellWidth: 66, gap: 16, fontSize: 16, fill: MEMORY, color: STRUCT, lineWidth: 1.7 });
  const offsets = addSketchArray(index, "s22-offsets", [0, 10], { left: 755, top: 442, width: 200, height: 38 }, { label: "offsets", labelWidth: 80, labelSize: 16, cellWidth: 66, gap: 16, fontSize: 16, fill: LAVENDER, color: VIOLET, lineWidth: 1.7 });
  connect(index, totals[1], offsets[0], BLUE, { fromSide: "right", toSide: "left", width: 3.5 });
  addRoughLine(index, "s22-kernel-boundary-2", { left: 90, top: 491, width: 1100, height: 9 }, RED, 3.0);
  addText(index, "s22-k3", "KERNEL 3  •  add each block offset and write the grid result", { left: 105, top: 506, width: 820, height: 30 }, { fontSize: 18, typeface: CODE, color: BLUE, bold: true, alignment: "left" });
  const result = addSketchArray(index, "s22-result", [0, 1, 3, 6, 10, 15, 21, 28], { left: 270, top: 552, width: 760, height: 42 }, { label: "grid", labelWidth: 75, labelSize: 16, cellWidth: 60, gap: 16, fontSize: 16, fill: CYAN, highlights: [4], highlightFill: PALE_LAVENDER, lineWidth: 1.6 });
  addRule(index, "s22-result-block-boundary", 645, 545, 0, 54, RED, 2.0, "dashed");
  addClaim(index, "s22-offset-add", "+10 offset", { left: 610, top: 598, width: 170, height: 30 }, { fontSize: 16 });
  // Keep the filled block shells behind labels and shared-memory bands.
  bringForward([b0in, b0local, b1in, b1local, totals, offsets, result]);
  addSource(index, 22, "Sources: NVIDIA CUDA C Programming Guide and CUB DeviceScan documentation. Values are an exact two-block exclusive-scan example.");
}

// Slide 23 — one SM, two operation routes, one invalid branch.
{
  const index = 22;
  clearBody(index);
  setTitle(index, "Tensor Core Scope");
  addCaption(index, "s23-subtitle", "The operation contract decides which execution path inside the SM is eligible", { left: 77, top: 110, width: 970, height: 30 }, { fontSize: 18, color: NAVY });
  const sm = addSketchBox(index, "s23-sm-shell", { left: 250, top: 165, width: 790, height: 390 }, { fill: "none", lineColor: STRUCT, lineWidth: 2.2 });
  addText(index, "s23-sm-title", "ONE SM", { left: 280, top: 178, width: 180, height: 34 }, { fontSize: 24, typeface: CODE, color: NAVY, bold: true });
  for (let i = 0; i < 4; i += 1) addSketchBox(index, `s23-sched-${i}`, { left: 485 + i * 118, top: 185, width: 100, height: 29 }, { fill: LAVENDER, lineColor: STRUCT, lineWidth: 1.4, text: "", insets: { left: 1, right: 1, top: 0, bottom: 0 } });
  addCaption(index, "s23-sched-label", "4 WARP SCHEDULERS", { left: 485, top: 186, width: 454, height: 27 }, { fontSize: 16, typeface: CODE, color: NAVY, alignment: "center" });

  const prefixFrame = addSketchBox(index, "s23-prefix-frame", { left: 58, top: 278, width: 205, height: 47 }, { fill: "none", lineColor: RED, lineWidth: 1.8 });
  const prefixIn = addArray(index, "s23-prefix", [3, 1, 7, 0, 4, 1, 6, 3], { left: 70, top: 285, width: 190, height: 32 }, { cellWidth: 18, gap: 4, fontSize: 16, fill: PALE_BLUE });
  addCaption(index, "s23-prefix-label", "PREFIX ROW", { left: 70, top: 248, width: 190, height: 30 }, { fontSize: 16, typeface: CODE, color: RED, alignment: "center" });
  const cudaPath = addSketchBox(index, "s23-cuda-path", { left: 300, top: 250, width: 340, height: 230 }, { fill: "none", lineColor: BLUE, lineWidth: 1.9 });
  addSegmentedFill(index, "s23-cuda-fill", { left: 302, top: 252, width: 336, height: 226 }, PALEST_BLUE, 6);
  const cudaLanes = addLaneStrip(index, "s23-cuda-lane", { left: 335, top: 287, width: 270, height: 78 }, { count: 32, gap: 2 });
  addSketchBox(index, "s23-register-band", { left: 335, top: 382, width: 270, height: 36 }, { fill: CYAN, lineColor: STRUCT, lineWidth: 1.6, text: "REGISTERS + SHUFFLE", fontSize: 16, typeface: CODE, bold: true, insets: { left: 3, right: 3, top: 1, bottom: 1 } });
  addSketchBox(index, "s23-shared-band", { left: 335, top: 428, width: 270, height: 36 }, { fill: MEMORY, lineColor: STRUCT, lineWidth: 1.6, text: "SHARED MEMORY + BARRIER", fontSize: 16, typeface: CODE, bold: true, insets: { left: 3, right: 3, top: 1, bottom: 1 } });
  addCaption(index, "s23-cuda-label", "CUDA LANE PATH", { left: 350, top: 255, width: 240, height: 28 }, { fontSize: 16, typeface: CODE, color: BLUE, alignment: "center" });

  const tensor = addSketchBox(index, "s23-tensor", { left: 705, top: 250, width: 280, height: 230 }, { fill: "none", lineColor: VIOLET, lineWidth: 1.9 });
  addSegmentedFill(index, "s23-tensor-fill", { left: 707, top: 252, width: 276, height: 226 }, PALE_LAVENDER, 6);
  const a = addMatrix(index, "s23-A", 745, 306, { label: "A", labelSize: 16, color: BLUE, fill: PALE_BLUE, cell: 20 });
  const b = addMatrix(index, "s23-B", 842, 306, { label: "B", labelSize: 16, color: BLUE, fill: PALE_BLUE, cell: 20 });
  addText(index, "s23-mma", "MMA", { left: 790, top: 402, width: 110, height: 38 }, { fontSize: 25, typeface: CODE, color: VIOLET, bold: true });
  addCaption(index, "s23-tensor-label", "TENSOR CORE TILE PATH", { left: 720, top: 255, width: 250, height: 28 }, { fontSize: 16, typeface: CODE, color: VIOLET, alignment: "center" });

  connect(index, prefixIn[7], cudaPath, BLUE, { fromSide: "right", toSide: "left", width: 4 });
  addRule(index, "s23-invalid-branch", 245, 320, 545, 76, RED, 3.5, "dashed");
  const invalidX = addText(index, "s23-invalid-x", "X", { left: 645, top: 342, width: 62, height: 62 }, { fontSize: 45, typeface: BODY, color: RED, bold: true });
  addClaim(index, "s23-invalid", "SCAN ≠ MMA TILE", { left: 680, top: 498, width: 330, height: 45 }, { fontSize: 27 });
  addText(index, "s23-matrix-formula", "D = A × B + C", { left: 1038, top: 257, width: 166, height: 42 }, { fontSize: 16, typeface: CODE, color: NAVY, bold: true });
  addCaption(index, "s23-peak", "L40S peak:\n733 dense FP8 TFLOPS\nMMA contracts only", { left: 1038, top: 325, width: 165, height: 96 }, { fontSize: 16, typeface: CODE, color: VIOLET, alignment: "center" });
  addClaim(index, "s23-rule", "hardware peak follows the matching branch", { left: 320, top: 580, width: 650, height: 40 }, { fontSize: 23 });
  // Execution-region fills stay behind their labels, bands, and the red X.
  bringForward([sm, prefixFrame, prefixIn, cudaPath, tensor, cudaLanes, a, b, invalidX]);
  addSource(index, 23, "NVIDIA CUDA C Programming Guide (WMMA); NVIDIA L40S specifications. Schematic tiles; FP8 peak is not a scan roof.");
}

// Slide 24 — library contracts route to different optimized hardware paths.
{
  const index = 23;
  clearBody(index);
  setTitle(index, "cuBLAS and cuDNN");
  addCaption(index, "s24-subtitle", "Libraries optimize tiling, precision, layout, fusion, and hardware paths.", { left: 77, top: 110, width: 980, height: 30 }, { fontSize: 18, color: NAVY });

  addText(index, "s24-cublas-title", "cuBLAS / cuBLASLt", { left: 90, top: 175, width: 280, height: 38 }, { fontSize: 23, typeface: CODE, color: BLUE, bold: true, alignment: "left" });
  addText(index, "s24-gemm", "C = α op(A) op(B) + β C", { left: 90, top: 220, width: 390, height: 38 }, { fontSize: 18, typeface: CODE, color: NAVY, bold: true, alignment: "left" });
  const a = addMatrix(index, "s24-A", 120, 300, { label: "A", labelSize: 16, color: BLUE, fill: PALE_BLUE, cell: 25 });
  const b = addMatrix(index, "s24-B", 250, 300, { label: "B", labelSize: 16, color: BLUE, fill: PALE_BLUE, cell: 25 });
  const c = addMatrix(index, "s24-C", 380, 300, { label: "C", labelSize: 16, color: VIOLET, fill: PALE_LAVENDER, cell: 25 });
  const gemmPlan = addSketchBox(index, "s24-gemm-plan", { left: 520, top: 285, width: 225, height: 115 }, { fill: CYAN, lineColor: BLUE, lineWidth: 2.0, text: "tile + dtype + layout\n→ execution plan\n→ Tensor Core path", fontSize: 16, typeface: CODE, bold: true });
  connect(index, c[8], gemmPlan, BLUE, { fromSide: "right", toSide: "left", width: 4.5 });

  addText(index, "s24-cudnn-title", "cuDNN", { left: 90, top: 455, width: 280, height: 38 }, { fontSize: 23, typeface: CODE, color: RED, bold: true, alignment: "left" });
  const conv = addSketchBox(index, "s24-conv", { left: 190, top: 500, width: 140, height: 50 }, { fill: PALE_LAVENDER, lineColor: VIOLET, lineWidth: 1.8, text: "convolution", fontSize: 16, bold: true });
  const bias = addSketchBox(index, "s24-bias", { left: 370, top: 500, width: 110, height: 50 }, { fill: PALE_LAVENDER, lineColor: VIOLET, lineWidth: 1.8, text: "bias", fontSize: 16, bold: true });
  const act = addSketchBox(index, "s24-act", { left: 520, top: 500, width: 135, height: 50 }, { fill: PALE_LAVENDER, lineColor: VIOLET, lineWidth: 1.8, text: "activation", fontSize: 16, bold: true });
  const plan = addSketchBox(index, "s24-plan", { left: 710, top: 482, width: 220, height: 84 }, { fill: CYAN, lineColor: BLUE, lineWidth: 2.0, text: "operation graph\n→ heuristics\n→ fused plan", fontSize: 16, typeface: CODE, bold: true });
  connect(index, conv, bias, VIOLET, { fromSide: "right", toSide: "left", width: 3 });
  connect(index, bias, act, VIOLET, { fromSide: "right", toSide: "left", width: 3 });
  connect(index, act, plan, BLUE, { fromSide: "right", toSide: "left", width: 4.5 });

  const gpuSpine = addSketchBox(index, "s24-gpu", { left: 1000, top: 250, width: 180, height: 300 }, { fill: PALE_BLUE, lineColor: STRUCT, lineWidth: 2.2, text: "GPU\n\nCUDA lanes\nTensor Core tiles\nshared / global memory", fontSize: 17, typeface: CODE, textColor: NAVY, bold: true });
  connect(index, gemmPlan, gpuSpine, BLUE, { fromSide: "right", toSide: "left", width: 5 });
  connect(index, plan, gpuSpine, BLUE, { fromSide: "right", toSide: "left", width: 5 });
  addBracket(index, "s24-contract-brace", { left: 930, top: 265, width: 35, height: 270 }, BLUE, 5.5);
  addClaim(index, "s24-claim", "the contract gives the library room to choose", { left: 220, top: 585, width: 730, height: 42 }, { fontSize: 23 });
  bringForward([a, b, c, gemmPlan, conv, bias, act, plan, gpuSpine]);
  addSource(index, 24, "Sources: NVIDIA cuBLAS 13.x documentation; NVIDIA cuDNN graph frontend and execution-plan overview.");
}

// Slide 25 — scope as nested hardware, not a UI ladder.
{
  const index = 24;
  clearBody(index);
  setTitle(index, "Thrust and CUB");
  addCaption(index, "s25-subtitle", "The same algebra can be packaged at warp, block, device, or range scope", { left: 77, top: 110, width: 920, height: 30 }, { fontSize: 18, color: NAVY });

  const range = addSketchBox(index, "s25-range", { left: 90, top: 155, width: 745, height: 420 }, { fill: "none", lineColor: STRUCT, lineWidth: 2.2 });
  addText(index, "s25-range-label", "RANGE / CONTAINER", { left: 112, top: 168, width: 260, height: 32 }, { fontSize: 20, typeface: CODE, color: RED, bold: true, alignment: "left" });
  addText(index, "s25-thrust", "Thrust: reduce / scan", { left: 430, top: 168, width: 375, height: 32 }, { fontSize: 18, typeface: CODE, color: NAVY, bold: true, alignment: "right" });

  const device = addSketchBox(index, "s25-device", { left: 150, top: 220, width: 620, height: 300 }, { fill: "none", lineColor: BLUE, lineWidth: 2.0 });
  addSegmentedFill(index, "s25-device-fill", { left: 152, top: 222, width: 616, height: 296 }, PALEST_BLUE, 12);
  addText(index, "s25-device-label", "DEVICE", { left: 168, top: 230, width: 125, height: 28 }, { fontSize: 17, typeface: CODE, color: BLUE, bold: true, alignment: "left" });
  addText(index, "s25-device-api", "CUB DeviceScan / DeviceReduce", { left: 400, top: 228, width: 345, height: 32 }, { fontSize: 16, typeface: CODE, color: NAVY, bold: true, alignment: "right" });
  const blocks = [];
  for (let i = 0; i < 6; i += 1) {
    blocks.push(addSketchBox(index, `s25-grid-block-${i}`, { left: 185 + (i % 3) * 175, top: 282 + Math.floor(i / 3) * 92, width: 145, height: 66 }, { fill: HW_BLUE, lineColor: STRUCT, lineWidth: 1.8, text: `block ${i}`, fontSize: 16, typeface: CODE, bold: true }));
  }
  addText(index, "s25-block-api", "CUB BlockScan", { left: 195, top: 450, width: 180, height: 30 }, { fontSize: 16, typeface: CODE, color: NAVY, bold: true });
  const warpStack = addStackedSheets(index, "s25-warps", { left: 430, top: 432, width: 205, height: 44 }, { count: 4, offsetX: 10, offsetY: -7, fill: DARK_BLUE, backFill: PALE_BLUE, text: "warp 0", color: STRUCT, fontSize: 16, textColor: WHITE });
  addText(index, "s25-warp-api", "CUB WarpScan / shuffles", { left: 390, top: 483, width: 310, height: 32 }, { fontSize: 16, typeface: CODE, color: NAVY, bold: true });
  addBracket(index, "s25-nesting", { left: 850, top: 175, width: 40, height: 380 }, BLUE, 6);
  addClaim(index, "s25-claim", "abstraction grows outward\ncontrol grows inward", { left: 900, top: 250, width: 260, height: 110 }, { fontSize: 25, alignment: "left" });
  addText(index, "s25-code", "thrust::inclusive_scan(\n  d.begin(), d.end(),\n  out.begin());", { left: 870, top: 400, width: 325, height: 96 }, { fontSize: 16, typeface: CODE, color: NAVY, bold: true, alignment: "left", verticalAlignment: "top", insets: { left: 4, right: 2, top: 3, bottom: 2 } });
  addCaption(index, "s25-baseline", "Start here. Descend only when measurement shows the missing contract.", { left: 845, top: 510, width: 350, height: 62 }, { fontSize: 16, color: BLUE, alignment: "center" });
  // Preserve the nesting labels by leaving the filled device shell behind.
  bringForward([range, device, blocks, warpStack]);
  addSource(index, 25, "NVIDIA CCCL: Thrust overview; CUB WarpScan, BlockScan, DeviceScan, and DeviceReduce.");
}

// Slide 26 — illustrated branch decision and large closing statement.
{
  const index = 25;
  clearBody(index);
  setTitle(index, "Choosing the Right Abstraction");
  addClaim(index, "s26-question", "What are the data shape and scope?", { left: 245, top: 130, width: 790, height: 56 }, { fontSize: 31 });

  const hub = addSketchBox(index, "s26-hub", { left: 598, top: 203, width: 84, height: 46 }, { fill: DARK_BLUE, lineColor: STRUCT, lineWidth: 1.8, text: "route", fontSize: 16, typeface: CODE, textColor: WHITE, bold: true, insets: { left: 2, right: 2, top: 1, bottom: 1 } });
  const branches = [];

  const range = addSketchBox(index, "s26-range", { left: 80, top: 330, width: 235, height: 105 }, { fill: "none", lineColor: BLUE, lineWidth: 2.0 });
  addArray(index, "s26-range-cells", [1, 2, 3, 4, 5, 6], { left: 105, top: 348, width: 185, height: 28 }, { cellWidth: 24, gap: 6, fontSize: 16, fill: CYAN });
  addText(index, "s26-range-tool", "range reduce / scan\nThrust or CUB Device", { left: 95, top: 380, width: 205, height: 48 }, { fontSize: 16, typeface: CODE, color: NAVY, bold: true });
  branches.push(range);

  const matrix = addSketchBox(index, "s26-matrix", { left: 375, top: 330, width: 235, height: 105 }, { fill: "none", lineColor: BLUE, lineWidth: 2.0 });
  const ma = addMatrix(index, "s26-mat-a", 405, 350, { color: BLUE, fill: PALE_BLUE, cell: 12, gap: 2 });
  const mb = addMatrix(index, "s26-mat-b", 470, 350, { color: BLUE, fill: PALE_BLUE, cell: 12, gap: 2 });
  addText(index, "s26-matrix-tool", "dense GEMM\ncuBLAS / cuBLASLt", { left: 390, top: 380, width: 205, height: 48 }, { fontSize: 16, typeface: CODE, color: NAVY, bold: true });
  branches.push(matrix);

  const graph = addSketchBox(index, "s26-graph", { left: 670, top: 330, width: 235, height: 105 }, { fill: "none", lineColor: VIOLET, lineWidth: 2.0 });
  const g1 = addSketchBox(index, "s26-g1", { left: 690, top: 348, width: 52, height: 29 }, { fill: PALE_LAVENDER, lineColor: VIOLET, lineWidth: 1.4, text: "conv", fontSize: 16, bold: true, insets: { left: 1, right: 1, top: 0, bottom: 0 } });
  const g2 = addSketchBox(index, "s26-g2", { left: 755, top: 348, width: 52, height: 29 }, { fill: PALE_LAVENDER, lineColor: VIOLET, lineWidth: 1.4, text: "bias", fontSize: 16, bold: true, insets: { left: 1, right: 1, top: 0, bottom: 0 } });
  const g3 = addSketchBox(index, "s26-g3", { left: 820, top: 348, width: 58, height: 29 }, { fill: PALE_LAVENDER, lineColor: VIOLET, lineWidth: 1.4, text: "act", fontSize: 16, bold: true, insets: { left: 1, right: 1, top: 0, bottom: 0 } });
  connect(index, g1, g2, VIOLET, { fromSide: "right", toSide: "left", width: 1.8 });
  connect(index, g2, g3, VIOLET, { fromSide: "right", toSide: "left", width: 1.8 });
  addText(index, "s26-graph-tool", "DNN operation graph\ncuDNN", { left: 685, top: 380, width: 205, height: 48 }, { fontSize: 16, typeface: CODE, color: NAVY, bold: true });
  branches.push(graph);

  const custom = addSketchBox(index, "s26-custom", { left: 965, top: 330, width: 235, height: 105 }, { fill: "none", lineColor: RED, lineWidth: 2.5 });
  addText(index, "s26-custom-code", "<<<grid, block>>>", { left: 985, top: 347, width: 195, height: 32 }, { fontSize: 16, typeface: CODE, color: RED, bold: true });
  addText(index, "s26-custom-tool", "unsupported fusion\ncustom CUDA", { left: 980, top: 380, width: 205, height: 48 }, { fontSize: 16, typeface: CODE, color: NAVY, bold: true });
  branches.push(custom);

  for (const branch of branches) connect(index, hub, branch, branch === custom ? RED : BLUE, { kind: "elbow", fromSide: "bottom", toSide: "top", width: 3.0 });
  bringForward([hub, branches, ma, mb, g1, g2, g3]);
  addText(index, "s26-matrix-check", "Matrix contract = supported shape + precision + layout → Tensor Core", { left: 235, top: 468, width: 810, height: 40 }, { fontSize: 17, typeface: CODE, color: NAVY, bold: true });
  addClaim(index, "s26-conclusion", "LIBRARY BASELINE  →  MEASURE  →  CUSTOM ONLY WITH EVIDENCE", { left: 110, top: 545, width: 1060, height: 58 }, { fontSize: 27 });
  addSource(index, 26, "NVIDIA CUDA libraries and CCCL documentation; decision flow summarizes slides 16–25.");
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const pptx = await PresentationFile.exportPptx(presentation);
await pptx.save(outputPath);

const previewDir = path.join(here, "cuda-language-preview");
await fs.mkdir(previewDir, { recursive: true });
for (const index of [1, ...Array.from({ length: 11 }, (_, i) => i + 15)]) {
  const blob = await presentation.export({ slide: getSlide(index), format: "png", scale: 1 });
  await fs.writeFile(path.join(previewDir, `slide-${String(index + 1).padStart(2, "0")}.png`), new Uint8Array(await blob.arrayBuffer()));
}
const montage = await presentation.export({ format: "webp", montage: true, scale: 1 });
await fs.writeFile(path.join(previewDir, "continuation-montage.webp"), new Uint8Array(await montage.arrayBuffer()));

console.log(JSON.stringify({ inputPath, outputPath, slides: presentation.slides.count }, null, 2));
