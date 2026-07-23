# Template-native authoring

Use this mode whenever a user supplies a PPTX as a template or asks to preserve an existing deck.

## Why this mode exists

A palette, font list, or screenshot is not a PowerPoint template. The source also carries master/layout relationships, placeholder geometry, text metrics, crop state, logos, page furniture, and editable objects. Exact clone/edit preserves those facts; rebuilding from visual resemblance does not.

## Required artifacts

`lab_slides.py inspect-template` creates:

- `work/template/template-inspect/`: complete source renders, layout JSON, object inventory, fonts, theme, and media;
- `content/template-profile.json`: hash-pinned frame roles, inherited targets, safe zones, and locked elements;
- `content/design-system.json`: template-derived visual thesis and system declaration;
- `reports/template-audit.txt`: compact human-readable audit.

`lab_slides.py prepare-template` creates:

- `content/template-frame-map.json`;
- `work/template/template-starter.pptx`;
- starter slide renders, layouts, contact sheet, and manifest;
- `reports/deviation-log.txt`.

## Frame mapping

Every output slide maps to a real source slide with `reuseMode: duplicate-slide`. A source slide can be reused many times. The frame map is derived from `content/slide-plan.json`; rerun `prepare-template` after routing changes.

An inherited edit target uses `rewrite`, `rewrite-and-reposition`, `replace`, `delete`, or `fill-placeholder` and must resolve to a source element ID. An addition uses:

```json
{
  "action": "add",
  "newPrimitiveAllowed": true,
  "zone": {"x": 71.36, "y": 109.42, "w": 1137.22, "h": 545.01},
  "reason": "The inherited layout defines this exact body region as the content canvas.",
  "mustNotOverlapInherited": true
}
```

An addition without a bounded zone and reason fails validation. A content slide cannot be add-only: at least its inherited title or another inherited content target must be edited.

## Default S3/Yonsei frame contract

The retained template is 1280×720 px / 16:9 and has two proven source slides.

- Cover: clone source slide 1. Rewrite the inherited title, subtitle/date-presenter line, and “Meeting Subject” eyebrow. Preserve the Yonsei and S3 lab lockup.
- Body: clone source slide 2. Rewrite the inherited title. Add editable content only inside the body zone recorded above. Preserve the top rule, left double rail, upper-right S3 mark, title geometry, master, and layout.

Unused source layouts are not authoring frames until a real source slide proves that they render correctly. Do not create a new slide from a layout name.

## Builder contract

Start from `assets/template-builder-scaffold.mjs`.

- Import `INPUT_PPTX`, which must be the prepared starter.
- Keep the existing slide count and order.
- Resolve inherited edits by stable source name/role from the frame map and profile.
- Add advanced native mechanisms only through deck-specific builder functions and keep their bounding boxes inside the allowed zone.
- Create connectors before nodes.
- Export with `PresentationFile.exportPptx`.
- Do not call `Presentation.create()` or `presentation.slides.add()`.

## Fidelity gate

Final QA re-inspects the output and runs the Presentations template-fidelity checker with paired starter/final layout evidence. It fails on fresh-slide rebuilds, direct OOXML or python-pptx mutation, LibreOffice mutation, unresolved placeholders, unplanned overlays, missing starter import/export evidence, or other forbidden bypasses.

The source template, frame map, design declaration, builder, content ledgers, final renders, and final PPTX are fingerprinted. Any material change invalidates the visual reviews.

