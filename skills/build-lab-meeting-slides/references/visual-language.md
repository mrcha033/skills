# Visual-language contract

## Source of truth and precedence

The user-designated PPTX is the visual contract. If no newer template is supplied, use the retained S3/Yonsei template in `assets/lab-meeting-template.pptx` and verify its hash against `assets/style-manifest.json`.

Precedence is:

1. user template and explicit brief;
2. inherited source objects and measured layout evidence;
3. approved content and evidence;
4. an explicitly approved domain profile;
5. generic design guidance.

Do not mix the optional CUDA reference with an active user template unless the user approves that mixture. The template is visual provenance, not a hardware-fact source.

## Proven retained frames

The default template has two verified source slides:

- source slide 1: cover;
- source slide 2: content shell with inherited title and bounded body zone.

Clone those exact source slides. Preserve the master/layout relationship, upper-right mark, left rail, top rule, lab lockup, margins, crop, and page rhythm. Unmapped inherited objects are locked chrome and default to `keep`. An unused layout is not permission to invent a new frame.

## Typography

The approved template's Arial typography overrides generic preferences that might otherwise reject it. Preserve the inherited family, size, weight, line/paragraph spacing, insets, alignment, and title geometry. Use Courier New for editable code when the template does not provide a code style.

- Technical body and diagram labels: normally at least 16 pt.
- Titles: inherit the mapped title object; do not replace it with a generic title component.
- Sources and page numbers may be smaller when the source already establishes that treatment.
- Shorten, restructure, split, or remap instead of silently shrinking type.
- Check Korean/CJK wrapping visually. Break at phrase or word boundaries; never split an 어절 mid-word.

Audit both direct object fonts and theme/font references. Font substitution can move inherited text even when the package remains valid.

## Native editable technical grammar

Locked chrome does not mean every new object is forbidden. Native editable shapes are preferred for semantic technical content inside the approved body zone:

- arrays, matrices, lanes, addresses, and memory paths;
- arrows/connectors, synchronization boundaries, and code deltas;
- tables, charts, result bands, and simple conceptual hardware maps.

Create connectors before nodes. Use one meaningful highlight or exception color, not decorative red geometry. Keep microgrids and dense tables clean when rough outlines would add noise.

Do not add decorative programmatic illustrations, fake screenshots, fake products, pseudo-official logos, invented data, generic card grids, pills, navigation, arbitrary gradients, or shadows merely to fill space. Use authentic raster assets for photographs, screenshots, sourced figures, and approved illustration. A slide-sized raster wrapper is a Critical native-mode defect.

## Mechanism-first composition

Each slide has one narrative job, one dominant title claim, and one visual anchor. For technical slides, the anchor is normally a mechanism or evidence relationship, not a paragraph.

Preferred order:

1. input/state;
2. active threads or lanes;
3. arrows/communication;
4. destination/output;
5. exact code delta;
6. measured consequence and bounded interpretation.

The audience should grasp the point in 3–5 seconds. Remove labels, cards, prose, and ornament that do not support that point.

## Incremental kernels

Consecutive kernel slides must share coordinates. Keep unchanged arrays, rows, destinations, metrics, and result bands in the same position. Ghost the previous state and highlight only the changed line, lane mapping, memory address, synchronization, or grid geometry.

Do not repeat an entire kernel when two changed lines are the lesson. Show the exact delta and enough stable context for the audience to simulate the change mentally.

## Hardware abstractions

Use flat editable conceptual diagrams with a visible label such as “conceptual — not to scale.” Distinguish:

- device memory, L2, SM, shared memory, register/lane, and Tensor Core scopes;
- architectural maxima, default allocation limits, and measured device attributes;
- resident warp state from execution pipelines.

Do not imply that 48 resident warps means 48 physical warp pipelines. Do not state a fetch/decode-unit count per scheduler unless a current primary source documents it. Explain the programming/performance contract without pretending to be a die schematic.

## Section consistency

Reduction, prefix sum, warp primitive, Tensor Core, and library sections all use the same inherited deck grammar. Vary the mechanism, not the brand system. Keep the same title/chrome geometry, palette, typography, source-footers, provenance labels, and evidence logic throughout.

## HTML boundary

An HTML prototype must use the inspected canvas, body zone, template tokens, and density budget. Its purpose is to test composition. After approval, reconstruct the composition in the native starter PPTX. Direct conversion or rasterization does not inherit the original master/layout contract and must be labeled as a partial fallback.
