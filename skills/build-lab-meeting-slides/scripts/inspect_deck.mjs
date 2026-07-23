#!/usr/bin/env node

import crypto from "node:crypto";
import { FileBlob, PresentationFile } from "@oai/artifact-tool";

const [inputPath] = process.argv.slice(2);
if (!inputPath) throw new Error("usage: node inspect_deck.mjs <deck.pptx>");

const presentation = await PresentationFile.importPptx(await FileBlob.load(inputPath));
const notes = presentation.slides.items.map((slide, index) => {
  const text = slide.speakerNotes.text.trim();
  return {
    slide: index + 1,
    visible: slide.speakerNotes.isVisible(),
    words: text ? text.split(/\s+/u).filter(Boolean).length : 0,
    sha256: crypto.createHash("sha256").update(text).digest("hex"),
  };
});
const combinedText = presentation.slides.items.map((slide) => slide.speakerNotes.text.trim());
const result = {
  slide_count: presentation.slides.items.length,
  notes,
  notes_sha256: crypto.createHash("sha256").update(JSON.stringify(combinedText)).digest("hex"),
};
process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);

