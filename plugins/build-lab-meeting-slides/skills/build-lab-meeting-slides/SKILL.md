---
name: build-lab-meeting-slides
description: Build or revise polished, editable PowerPoint lab-meeting decks from a supplied PPTX template, research sources, code, transcripts, and optional GPU evidence. Use whenever the user asks for a lab meeting, research talk, CUDA/performance deck, or says to preserve a lab/corporate PPTX template. The skill defaults to exact source-slide cloning, native editable objects, detailed template fidelity QA, audience-first narrative, and role-aware speaker notes; it can use HTML only as an optional visual prototype, never as an unmarked fidelity shortcut.
---

# Build Lab Meeting Slides

Build a strong research story inside the user's actual PowerPoint system. A deck is not good because it contains all facts; it is good when the audience can follow one cumulative argument, recognize the template immediately, and understand each slide's mechanism or evidence in seconds.

## Required companion skills

Before inspecting or editing a deck, read the complete `Presentations:Presentations` skill, its content rules, and its template-following reference. Its Artifact Tool runtime and exact clone/edit helpers are the implementation source of truth. Read the PDF skill when PDFs must be extracted or visually inspected.

Use these ideas from the named companion skills:

- `slides-grab`: Plan → Design → Export order, style approval, fresh render evidence, and two independent design reviews.
- `frontend-design`: a subject-specific visual thesis, declared system, one justified signature, and self-critique against generic defaults.
- `template-creator`: retain the original Office file and a verified preview as hash-pinned visual provenance.
- Canva branded presentations: separate the approved visual direction from the final editable candidate. A supplied template already establishes the direction; do not invent competing candidates.

## Route before authoring

Choose one authoring mode. The first match wins.

1. `template-native` — default whenever the user supplies a PPTX, asks to preserve a template, or edits an existing deck. Duplicate mapped source slides and edit inherited objects.
2. `native-original` — only when no user visual source exists. Declare and approve a design system before authoring.
3. `html-assisted-native` — optional when the user requests browser-based iteration or it materially improves composition review. HTML is disposable; the approved composition is rebuilt in the native starter PPTX.
4. `raster-fallback` — only after native editing is proven blocked and the user explicitly accepts reduced editability.

Precedence is: user template and explicit brief → inherited source objects → approved content/evidence → domain profile → generic design defaults. Never mix a user template with the bundled CUDA visual reference unless the user explicitly approves the mixture.

## Default retained template

`assets/lab-meeting-template.pptx` is the default S3/Yonsei template retained from the user's source file; `assets/lab-meeting-template-preview.png` is its inspected cover preview. Its expected SHA-256 is recorded in `assets/style-manifest.json`.

The template has two proven source frames:

- source slide 1: cover;
- source slide 2: content shell with inherited title and a bounded body zone.

Clone slide 1 once for the opening. Clone slide 2 for body slides. Do not create slides from the template's unused layout names: an available layout is not a proven rendered frame.

## Start here

Run the environment check:

```bash
python3 scripts/lab_slides.py doctor
```

Initialize one project per deck. `--template-pptx` may point to a newer user template; otherwise the retained S3/Yonsei template is used.

```bash
python3 scripts/lab_slides.py init \
  --project-dir /absolute/path/to/project \
  --source-pptx /absolute/path/to/content-or-current-deck.pptx \
  --template-pptx /absolute/path/to/template.pptx \
  --output-name lab-meeting-final.pptx
```

`init` inspects every template slide, writes a hash-pinned profile and design declaration, and copies a native builder scaffold. Then:

1. Fill `deck.communication_job` in `labdeck.json` using: “By the end, [audience] should [outcome] because [central takeaway].”
2. Fill `content/outline.md`, `content/slide-plan.json`, `content/claims.json`, and `content/notes.json`.
3. Review `content/design-system.json` and keep its template hash, palette, type, locked chrome, signature, and anti-patterns aligned with the approved source.
4. Run `prepare-template`, then `audit`.
5. Implement the deck-specific native mechanisms in `build/builder.mjs` from the generated scaffold.
6. Run `finalize`, mechanical QA, both visual reviews, and final QA.

For every new project, `audit` also enforces a visual contract. Each body slide must
declare a content-specific anchor, a composition family, and the claim/source basis
that justifies it. Generic `card-grid`, dashboard, icon-grid, pill-grid, and tile-grid
families are rejected. The default contract allows at most two consecutive slides in
one family/signature and requires the native visual to occupy at least 16% of its
declared body zone; intentionally sparse slides must carry an explicit,
template-audit-tied `underfill_rationale`. These are recurrence guards, not a claim
that a unit test can prove taste or audience comprehension.

Normal `qa` also has a separate user-acceptance gate. A mechanically valid deck is
not handoff-ready while `qa.user_acceptance.status` is `pending`; after reviewing
fresh full-size renders, record `reviewer`, `accepted_at`, and the exact final
`reviewed_deck_sha256`. Structural QA, visual review, and user acceptance are
reported as separate statuses.

```bash
python3 scripts/lab_slides.py prepare-template /absolute/path/to/labdeck.json
python3 scripts/lab_slides.py audit /absolute/path/to/labdeck.json
python3 scripts/lab_slides.py finalize /absolute/path/to/labdeck.json
python3 scripts/lab_slides.py qa /absolute/path/to/labdeck.json --skip-review-gate
# write fresh Pass A and Pass B reports using the emitted hashes and renders
python3 scripts/lab_slides.py qa /absolute/path/to/labdeck.json
```

Read [workflow.md](references/workflow.md) for the full artifact sequence and [template-native.md](references/template-native.md) for the frame-map contract.

## Exact template-native contract

When `mode: exact-clone-edit`:

- Inspect every source slide. The source PPTX, preview, template profile, and SHA are visual provenance.
- Map every output slide to an exact `sourceSlide`, narrative role, density budget, visual anchor, and routing rationale.
- Resolve inherited `editTargets`; all unlisted objects default to `keep`.
- Build `template-starter.pptx` by duplicating mapped source slides. The builder imports this starter, never a fresh presentation.
- Edit inherited title/subtitle objects in place. New primitives are allowed only inside an explicit `zone` with a reason and `mustNotOverlapInherited: true`.
- Preserve original font family, size, weight, line/paragraph spacing, insets, alignment, master/layout relationship, logo, rail, top rule, and image crop unless a deviation is approved and logged.
- If copy does not fit, shorten, split, or remap. Do not silently shrink type or cover the template with a parallel custom layout.
- After `prepare-template`, `audit` measures every mapped `title_claim` against the actual slide-level starter title object. It uses that object's bbox, insets, first run size/style, and a matching local font through Pillow; an inherited layout/master duplicate is never used as the measurement target. Clear wrap/height overflow blocks `finalize` and is recorded in `reports/title-fit-preflight.json`.
- If the exact template font cannot be measured locally, the title check is explicitly `skipped` with an audit warning instead of silently substituting another family. Treat that warning as unverified typography and close it through full-size visual review or by installing the exact font.
- Run both the Presentations `check_template_fidelity.mjs` gate and `scripts/check_locked_template.py`. The strict checker compares slide routing, master/layout/theme semantics, locked slide objects, mapped text-only rewrites, and declared bounded additions.

For the retained body frame, bounded insertion is restricted to the inherited body zone recorded in the generated template profile. The builder rejects declared elements outside that zone.

## Plan and design declaration

Before layout work, state the communication job and choose a narrative arc appropriate to the research question. Every slide advances that arc with one narrative job and one audience-facing title claim.

Every schema-v2 slide-plan entry needs:

```json
{
  "slide": 2,
  "narrative_job": "mechanism explanation",
  "title_claim": "Coalesced access removes redundant memory transactions",
  "template_frame": {
    "role": "content",
    "source_slide": 2,
    "layout_family": "single-mechanism",
    "density_budget": "medium",
    "routing_rationale": "The supplied content shell preserves the lab title and chrome while leaving one mechanism canvas."
  },
  "visual_anchor": "fixed-coordinate lanes, addresses, and one highlighted transaction delta",
  "visual_contract": {
    "anchor_type": "mechanism",
    "family": "address-lanes",
    "content_basis": ["k2-time", "k2-sectors"],
    "allow_underfill": false,
    "underfill_rationale": "",
    "repeat_group": ""
  },
  "coverage": ["memory coalescing"],
  "evidence_refs": ["k2-time", "k2-sectors"],
  "notes_ref": "slide-02"
}
```

For incremental kernels, also record the predecessor, exact changed code line, unchanged objects, mechanism visual, and evidence. Keep coordinates stable across consecutive slides and ghost the previous state so the delta is immediately visible.

`content/design-system.json` must declare the visual thesis, inherited system, section rhythm, layout families, visual-anchor strategy, subject-specific signature, and explicit anti-patterns. In template-native mode this declaration explains how the inherited system will be used; it does not authorize new fonts, colors, or decorative components.

## Composition and editability

Use one canvas composition, not a component-library interface. Avoid generic card grids, pills, badges, faux navigation, decorative metric strips, repeated icon blurbs, arbitrary gradients, and shadows added only to fill space. Do not add filler copy, decorative icons, or invented data.

The inherited template may legitimately use a pattern or Arial typography that a generic anti-slop list would reject. The approved template wins. Judge whether a treatment is source-authentic and meaningful, not whether a font appears on a blacklist.

Native editable shapes are appropriate for technical mechanisms: arrays, lanes, arrows, memory paths, code deltas, tables, charts, and simple hardware abstractions. Decorative programmatic illustration, fake screenshots, fake products, fake scientific evidence, and pseudo-official logos are not. Use authentic raster assets for photographs, screenshots, sourced figures, and approved illustration. A slide-sized raster wrapper is Critical in native mode.

Create connectors before their nodes. Keep technical body text and diagram labels at least 16 pt unless the source template explicitly establishes another readable size. Check Korean/CJK wrapping visually and break only at phrase or word boundaries.

In the generated scaffold, specify native text size as `fontSizePt`. `fontSize` is the Artifact Tool's CSS-pixel unit; the scaffold converts points at 96/72 and enforces `style.minimum_body_pt`. The scaffold supports bounded `text`, `shape`, and editable `line` primitives and creates line primitives before nodes. Attached connectors, charts, tables, or other advanced authoring requires a project-specific builder and fresh strict fidelity plus dual visual review; there is no unchecked custom callback.

## Evidence profile

Measurement is opt-in. Read [measurement-policy.md](references/measurement-policy.md) before running or interpreting a benchmark.

- Use physical Device 3 only on `ssh l40s-yunm` through `lab_slides.py gpu`.
- Keep timing, Nsight Compute attribution, and Nsight Systems decomposition separate.
- Label claims `CUDA EVENTS`, `NSIGHT COMPUTE`, `NSIGHT SYSTEMS`, `DERIVED`, `CODE-DERIVED`, `VENDOR SPEC`, or `INFERENCE`.
- Never use profiler replay duration as headline timing or turn counter correlation into a unique causal claim.
- Mechanism slides show the changed mechanism, measured consequence, and appropriately bounded interpretation together.

## Speaker notes

Replace the retained template's sample/authoring note unless the slide plan explicitly chooses `mode: preserve` with a specific `preserve_rationale`. Known sample notes, including the retained cover's authoring note, are rejected even in preserve mode.

Use role-aware budgets instead of padding every slide to the same length:

- cover, divider, closing: normally 20–100 words;
- mechanism and evidence: normally 90–190 words;
- appendix/reference: normally 40–120 words;
- ordinary content: normally 60–170 words.

Each note explains the slide's purpose and transition. Add mechanism, numerical meaning, provenance, and caveats only when present. Never invent numerical interpretation to satisfy a word-count rule.

## HTML-assisted native mode

Do not use a PPTX → HTML → PPTX roundtrip as the default repair path. It can lose masters, layouts, DrawingML, chart semantics, line metrics, notes, and editability.

HTML can still help:

1. Initialize with `--html-prototype`; this persists `deck.authoring_mode: html-assisted-native` and a fail-closed prototype contract.
2. Run `prepare-template`, render the source frame, and build a disposable 1280×720-equivalent HTML prototype constrained to the approved body zone, fonts, colors, and density budget.
3. Run `slides-grab validate`, render PNGs, and apply the dual design gate.
4. After approval, declare the accepted composition as `native_elements` and generate `content/native-rebuild-manifest.json` with `scripts/native_rebuild_manifest.py generate`. The manifest hash-pins the HTML, prototype render, starter, frame map, plan, and native element projection.
5. Reimplement the composition in `template-starter.pptx` with native objects. The PPTX builder receives the manifest, never the HTML; using the prototype render as a final asset is rejected.
6. Run `audit`, template fidelity, editability, render, notes, and package QA on the PPTX. A stale or missing native-rebuild manifest blocks finalization.

If the user explicitly chooses direct HTML-to-PPTX conversion, label it experimental and partially editable. Raster export preserves appearance but not editability; text export is best-effort and does not preserve the original master/layout contract. Read [html-assisted-native.md](references/html-assisted-native.md).

## Design and handoff gates

Render every slide full-size after material layout, type, color, density, or imagery changes. Run two independent read-only reviews against the current renders:

- Pass A: template fidelity, frame map, locked chrome, palette/type provenance, editability, anti-slop, claim truth, and no unplanned overlays.
- Pass B: audience comprehension, one-job hierarchy, mechanism-first reading, 3–5 second takeaway, pacing, line breaks, Korean/CJK integrity, and overlap.

Critical findings block delivery. Fix Major findings or obtain explicit user acceptance. Track deferred Minor/Note findings in `reports/design-debt.md`. Reviews are stale after any material edit; regenerate renders and both reports. The user-acceptance record is still required for a new project even when Pass A/B are clean; it is the explicit human check against repetition, unjustified whitespace, dense limitations, and title/logo breathing that static gates cannot fully judge.

LibreOffice is probe-only by default. Never promote its roundtrip automatically: it can change paragraph margins, layout relationships, and object positions even when slide count and notes survive.

Deliver one immutable final PPTX, QA report, template-fidelity report, and concise measurement-scope statement. Preserve the source template and raw evidence. Use the presentation citation format required by the Presentations skill.

## Bundled resources

- `assets/lab-meeting-template.pptx` and `assets/lab-meeting-template-preview.png`: retained default template and verified cover preview.
- `assets/style-manifest.json`: template hash, tokens, locked chrome, and bounded body zone.
- `assets/template-builder-scaffold.mjs`: native starter import/edit/export spine with bounded element checks.
- `scripts/check_locked_template.py`: strict starter/final comparison for routing, template core, locked objects, mapped rewrites, and bounded native additions.
- `scripts/native_rebuild_manifest.py`: hash-pinned one-way HTML-prototype-to-native boundary.
- `assets/cuda-visual-language-source.pptx` and `assets/cuda-style-manifest.json`: optional CUDA mechanism reference, disabled when a user template is active unless explicitly approved.
- [qa-gates.md](references/qa-gates.md): exact mechanical and dual-review contract.
- [slides-grab-adaptation.md](references/slides-grab-adaptation.md): retained orchestration ideas and the HTML boundary.
