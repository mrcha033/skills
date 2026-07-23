# Adaptation from slides-grab

Reference: `NomaDamas/slides-grab`, main commit `745c931c8f5556d8b9fdfe6718c8a507f6223935`, reviewed 2026-07-15.

## Patterns retained

- A staged Plan → Design → Export workflow.
- A declared style/template choice before authoring.
- An approved outline and content plan before layout work.
- Technical validation before a design-quality gate.
- Fresh rendered evidence for every review loop.
- Two independent, read-only review roles: system-contract integrity and audience impact.
- Severity-classified findings; unresolved Critical findings block export.
- Review reports tied to the current source/render fingerprints.
- A design-debt log for deferred noncritical findings.
- Export refusal when the design gate is stale or unresolved.

## PowerPoint-native default

The reference repository is HTML-first and treats PPTX export as experimental. This skill is template-native because lab-meeting decks require:

- exact source-slide cloning and retained master/layout relationships;
- native editable text, code, arrays, arrows, charts, and technical shapes;
- stable inherited logo, rail, top rule, lockup, and typography;
- reliable speaker notes;
- changed-slide citations and fidelity checks against the final `.pptx`.

The generated `template-frame-map.json` routes every output slide to a proven source slide, and `prepare-template` duplicates those frames into `template-starter.pptx`. Artifact Tool then edits that starter. For the retained S3/Yonsei template, source slide 1 is the cover and source slide 2 is the body shell.

## HTML-assisted native branch

HTML is useful as a disposable composition sandbox when browser iteration materially improves layout decisions. Apply the slides-grab ideas there:

1. constrain the prototype to the inspected slide size, bounded body zone, inherited Arial/type scale, palette, and density budget;
2. validate the HTML and render fresh PNG evidence;
3. run the same system-contract and audience-impact reviews;
4. rebuild the accepted composition with native editable objects in `template-starter.pptx`;
5. run PPTX template-fidelity, editability, notes, package, and full-render QA.

Do not treat PPTX → HTML → PPTX as the default repair route. A direct roundtrip can lose masters, layouts, placeholders, DrawingML geometry, charts, notes, font metrics, and editability.

## Explicit partial fallbacks

Direct HTML-to-PPTX text export is best-effort and does not preserve the original master/layout contract. Slide-sized raster export may preserve appearance but loses editability and semantic structure. Either route requires explicit user acceptance and must be labeled experimental/partial in the handoff. LibreOffice remains a disposable compatibility probe, not a conversion bridge or automatic final source.

## Attribution boundary

This document paraphrases workflow ideas from the MIT-licensed reference. No repository prompt, HTML template, CLI implementation, or presentation content is bundled here. The retained PPTX, preview, frame map, builder scaffold, and visual assets belong to this user's lab-deck workflow.
