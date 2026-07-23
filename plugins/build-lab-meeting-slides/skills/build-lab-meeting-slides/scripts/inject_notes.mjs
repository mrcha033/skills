#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, PresentationFile } from "@oai/artifact-tool";

const [inputPath, outputPath, notesPath, configPath] = process.argv.slice(2);
if (!inputPath || !outputPath || !notesPath || !configPath) {
  throw new Error("usage: node inject_notes.mjs <input.pptx> <output.pptx> <notes.json> <labdeck.json>");
}

const notesData = JSON.parse(await fs.readFile(notesPath, "utf8"));
const config = JSON.parse(await fs.readFile(configPath, "utf8"));
const rawEntries = Array.isArray(notesData) ? notesData : notesData.slides;
if (!Array.isArray(rawEntries)) throw new Error("notes JSON must be an array or contain a slides array");

const entries = rawEntries.map((entry, index) => {
  if (typeof entry === "string") return { slide: index + 1, text: entry, mode: "replace" };
  return {
    slide: Number(entry.slide ?? index + 1),
    text: String(entry.text ?? ""),
    mode: String(entry.mode ?? "replace"),
    preserveRationale: String(entry.preserve_rationale ?? "").trim(),
  };
}).sort((a, b) => a.slide - b.slide);

const planData = JSON.parse(
  await fs.readFile(path.join(path.dirname(path.resolve(configPath)), "content", "slide-plan.json"), "utf8"),
);

function noteRange(planEntry) {
  if (Array.isArray(planEntry?.notes_word_range) && planEntry.notes_word_range.length === 2) {
    return planEntry.notes_word_range.map(Number);
  }
  const frameRole = typeof planEntry?.template_frame === "object" ? planEntry.template_frame.role : "";
  const roleText = `${frameRole || ""} ${planEntry?.narrative_job || ""} ${planEntry?.layout_family || ""}`.toLowerCase();
  const ranges = config.notes?.role_ranges || {};
  for (const role of ["cover", "divider", "closing", "appendix", "mechanism", "evidence"]) {
    if (roleText.includes(role) && Array.isArray(ranges[role]) && ranges[role].length === 2) {
      return ranges[role].map(Number);
    }
  }
  if (Array.isArray(ranges.default) && ranges.default.length === 2) return ranges.default.map(Number);
  return [Number(config.notes?.minimum_words ?? 30), Number(config.notes?.maximum_words ?? 190)];
}

const presentation = await PresentationFile.importPptx(await FileBlob.load(inputPath));
const slides = presentation.slides.items;
if (entries.length !== slides.length) {
  throw new Error(`notes cover ${entries.length} slides, but deck has ${slides.length}`);
}

for (let i = 0; i < slides.length; i += 1) {
  const entry = entries[i];
  if (entry.slide !== i + 1) throw new Error(`notes are missing or duplicate slide ${i + 1}`);
  if (entry.mode === "preserve") {
    if (!entry.preserveRationale) {
      throw new Error(`slide ${i + 1} preserve-mode notes require preserve_rationale`);
    }
    const inherited = slides[i].speakerNotes.text.trim();
    if (!inherited) {
      throw new Error(`slide ${i + 1} requests preserve-mode notes, but the inherited note is empty`);
    }
    if (/긴장하지\s*말자/u.test(inherited) || /^(?:todo|tbd|sample note|speaker notes?)\b/iu.test(inherited)) {
      throw new Error(`slide ${i + 1} inherited note is a sample/authoring note and cannot be preserved`);
    }
    slides[i].speakerNotes.setVisible(true);
    continue;
  }
  if (entry.mode !== "replace") throw new Error(`slide ${i + 1} notes mode must be replace or preserve`);
  const text = entry.text.trim();
  const words = text ? text.split(/\s+/u).length : 0;
  const [minimum, maximum] = noteRange(planData.slides?.[i] || {});
  if (words < minimum || words > maximum) {
    throw new Error(`slide ${i + 1} notes have ${words} words; expected ${minimum}-${maximum}`);
  }
  slides[i].speakerNotes.clear();
  slides[i].speakerNotes.textFrame.setText(text);
  slides[i].speakerNotes.setVisible(true);
}

await fs.mkdir(path.dirname(path.resolve(outputPath)), { recursive: true });
const exported = await PresentationFile.exportPptx(presentation);
await exported.save(outputPath);
process.stdout.write(`attached ${entries.length} visible speaker notes to ${outputPath}\n`);
