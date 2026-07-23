import crypto from "node:crypto";
import { FileBlob, PresentationFile } from "@oai/artifact-tool";

const inputPath = process.argv[2];
if (!inputPath) throw new Error("usage: node verify-detailed-speaker-notes.mjs <deck.pptx>");

const presentation = await PresentationFile.importPptx(await FileBlob.load(inputPath));
const slides = presentation.slides.items;
if (slides.length !== 26) throw new Error(`expected 26 slides, found ${slides.length}`);

const notes = slides.map((slide, index) => {
  const text = slide.speakerNotes.text.trim();
  const words = text.split(/\s+/).filter(Boolean).length;
  if (!text) throw new Error(`slide ${index + 1} has empty speaker notes`);
  if (!slide.speakerNotes.isVisible()) throw new Error(`slide ${index + 1} speaker notes are hidden`);
  if (words < 90) throw new Error(`slide ${index + 1} notes are too brief: ${words} words`);
  if (words > 190) throw new Error(`slide ${index + 1} notes are too long: ${words} words`);
  return text;
});

const sentinels = [
  [0, "controlled optimization story"],
  [1, "Nsight Systems pass traces"],
  [5, "Nsight Compute separately reports"],
  [11, "64 MiB warm-cache CUDA-event control"],
  [13, "do not prove one unique DRAM-row"],
  [15, "FP32 addition is only approximately associative"],
  [17, "2log₂8−1"],
  [18, "Nsight Compute reports about 65.7% active warps"],
  [19, "avoiding the extra global round trip"],
  [22, "733 dense FP8 TFLOPS"],
  [25, "customize only with evidence"],
];
for (const [index, expected] of sentinels) {
  if (!notes[index].includes(expected)) {
    throw new Error(`slide ${index + 1} notes missing sentinel: ${expected}`);
  }
}

const wordCounts = notes.map((note) => note.split(/\s+/).filter(Boolean).length);
const digest = crypto.createHash("sha256").update(JSON.stringify(notes)).digest("hex");
process.stdout.write(
  `verified 26 detailed, visible transcripts | words=${wordCounts.reduce((a, b) => a + b, 0)} | min=${Math.min(...wordCounts)} | max=${Math.max(...wordCounts)} | sha256=${digest}\n`,
);
