# QA and design gates

## Gate sequence

1. Validate configuration, source/template hashes, and tool paths.
2. Inspect every template source slide and approve the design declaration.
3. Build and audit the exact source-slide frame map.
4. Prepare `template-starter.pptx` from mapped source slides.
5. Preflight every mapped `title_claim` against its actual slide-level starter title bbox and inherited run metrics.
6. Audit topic coverage, slide-plan completeness, and every numeric claim.
7. Audit the visual contract: content-specific anchor, source/claim basis, composition-family diversity, and justified body-zone coverage.
8. Build the native editable PPTX from the starter.
9. Attach role-aware notes and run package/overflow checks.
10. Render every slide full-size and create a montage.
11. Compare starter and final evidence with the template-fidelity gate.
12. Run Pass A and Pass B against the current full-size renders.
13. Fix findings, rerender, and repeat both reviews.
14. Obtain explicit user acceptance for the reviewed final deck, pinning its SHA-256.
15. Run the disposable compatibility probe and final package-integrity check.

No old render or review report survives a material edit.

## Severity

| Severity | Meaning | Disposition |
|---|---|---|
| Critical | Slide fails its job; factual/provenance failure; unreadable/overflowing content; broken frame-map/template/font contract; locked-chrome drift; missing mechanism on a key slide; slide-sized raster; or unexplained overlay/red geometry | Must fix; blocks handoff |
| Major | Significant hierarchy, consistency, clarity, comparison, editability, or source-fidelity defect | Fix before handoff or obtain explicit user acceptance |
| Minor | Local polish issue that does not impede comprehension or template recognition | Fix if cheap or log as design debt |
| Note | Future improvement | Track only |

Never downgrade a Critical finding to make the gate pass.

## Mechanical checks

The automated runner must fail on:

- corrupt ZIP/package;
- wrong slide count;
- stale template hash, profile, frame map, starter hash, or design declaration;
- a measured mapped title that would require more wrapped lines than its actual starter title box permits;
- a template-native builder that creates a fresh presentation or unmapped slides;
- unbounded new primitives or additions overlapping inherited objects;
- template-fidelity failure against starter/final layout evidence;
- empty structural placeholders in template-native mode;
- missing or hidden notes when required;
- notes outside the slide's role-aware word range;
- fonts outside the source-derived allowlist;
- missing required provenance text;
- measured evidence labels with an empty claims ledger, or bare `CODE` instead of `CODE-DERIVED`;
- overflow reported by `slides_test.py`;
- failed LibreOffice probe for notes digest or slide count when enabled;
- stale or failing Pass A/Pass B reports.
- missing `visual_contract` on body slides in a new project;
- forbidden generic composition families, excessive family/signature repetition, or body-zone underfill without an explicit rationale tied to the template audit;
- `qa.user_acceptance.status=pending` or an acceptance record whose deck SHA does not match the current final PPTX.

Also inspect every full-slide render for:

- title wrapping or movement;
- body/teaching text below 16 pt without a source-established exception;
- source/footer, connector/text, or label/shape collisions;
- missing or moved lab mark, rail, top rule, or page rhythm;
- rasterized text, code, charts, or mechanisms;
- slide-sized overlays hiding inherited chrome;
- unexplained red strokes or generic UI-card drift;
- Korean/CJK word breaks;
- one-pixel seams or unexpected movement introduced by conversion;
- body elements outside the approved insertion zone.

The visual-contract gate is intentionally heuristic. Its default 16% union-area
threshold is a recurrence detector, not a mandate to fill the canvas. Sparse
poster-like or divider-like slides may set `allow_underfill: true` only with a
specific `underfill_rationale`; that exception is visible in
`reports/visual-contract.json`. A family may repeat for a deliberate comparison
sequence only when `repeat_group` explains the comparison and Pass B confirms the
sequence remains readable.

`audit` writes `reports/title-fit-preflight.json`. Each mapped title check identifies the actual top-level starter element, font file, inherited run size, usable bbox after insets, measured unwrapped width, required line count, and maximum line count. The preflight intentionally ignores same-named inherited layout/master elements. It fails on clear measured overflow before `finalize`. If Pillow or an exact local font file is unavailable, it records `status: skipped` and emits a warning; this is not evidence that the title fits, so Pass B must close the typography check from the full-size render.

## Template-fidelity contract

The template-native gate compares:

- the source PPTX SHA and inspected profile;
- the exact source slide selected for every output slide;
- the generated starter and final PPTX;
- inherited target identities, keep/edit/add actions, and bounded zones;
- starter and final layout/object evidence;
- builder source for fresh-presentation and overlay shortcuts.

Two gates are required. The bundled Presentations checker verifies the declared import/export route and broad overlay risks. The strict locked-template checker independently compares starter and final slide size/routing, normalized master-layout-theme core, inherited-layer signatures, locked slide objects, rewrite style/geometry, and every declared native addition by name, kind, bounds, text, and safe-zone containment. Either failure is Critical.

For `html-assisted-native`, `content/native-rebuild-manifest.json` must validate against the current config, plan, starter, frame map, prototype HTML, prototype render, and native element projection. The prototype render must never appear as a final/native asset.

For the retained template, output slide 1 derives from source slide 1 and body slides derive from source slide 2. Locked chrome must remain source-authentic. Technical content may be added as native editable shapes only within the approved body zone.

## Pass A: System Contract / Constraint Integrity

Pass A is read-only and reviews the full-size PNGs plus source/config/claims/profile/frame-map evidence:

- exact source-frame selection and inherited chrome;
- template hash, palette, Arial/type allowlist, and locked-object integrity;
- bounded native additions, editability, and no slide-sized overlays;
- consistent system across sections and no generic UI-card drift;
- measurement provenance and claim truth;
- no invented data, fake evidence, or unsupported hardware detail;
- no unapproved HTML/raster fidelity shortcut.

Required report header:

```text
# Pass A: System Contract / Constraint Integrity
VERDICT: PASS
Confidence: High
Evidence: full-size renders and current template/frame-map artifacts
Deck SHA256: <current deck hash>
Render manifest SHA256: <current render-manifest hash>
Source manifest SHA256: <current source-manifest hash>
Unresolved Critical: 0
Blocking findings: None
```

Include a findings table with slide number, finding, severity, fix, and status. A one-line “looks good” is not a report.

## Pass B: Audience Impact / Expressive Readability

Pass B is read-only and opens every full-size render:

- one job and one dominant takeaway per slide;
- mechanism readable before prose;
- 1-to-1 comparability across incremental slides;
- exact code/arrow/address delta visible;
- hierarchy, whitespace, line balance, contrast, and font size;
- Korean/CJK word integrity;
- plateau/regression control legibility;
- main point graspable in 3–5 seconds.

Use the same fingerprint fields as Pass A, with the Pass B title. Include a located findings table.

## Review fingerprint contract

`lab_slides.py qa --skip-review-gate` creates:

- `reports/source-fingerprints.json` with the current declarative sources, builder, template, frame map, claims, and notes hashes;
- `reports/render-manifest.json` with the deck hash, source-manifest hash, and every current render hash;
- a template-fidelity result comparing the prepared starter and final deck;
- the review montage and full-slide PNG directory.

Copy the deck, render-manifest, and source-manifest SHA-256 values into both reports. Normal `qa` rejects stale reports. After any layout, content, color, type, density, imagery, builder, plan, note, or frame-map change, rerun mechanical QA and both reviews.

`qa-report.json` separates `template_fidelity`, `visual_contract`, and
`user_acceptance`. `qa --skip-review-gate` is a diagnostic/mechanical pass only;
it must not be described as handoff-ready because it intentionally skips both
visual review reports and user acceptance.

## Role-aware notes

Notes must cover every slide exactly once unless the template source note is deliberately preserved. The configured role determines the normal range: short for cover/divider/closing, full for mechanism/evidence, compact for appendix/reference, and moderate for ordinary content. Never add filler or invent numerical meaning to reach a word count.

## LibreOffice compatibility probe

LibreOffice is probe-only by default:

1. keep the Artifact Tool/native final as the source candidate;
2. create a disposable roundtrip copy;
3. compare slide count and notes digest;
4. visually inspect if the probe is used for a compatibility decision;
5. discard the probe after recording the result.

Do not promote the roundtrip automatically. Package validity and intact notes do not prove unchanged paragraph margins, layout relationships, or object positions.
