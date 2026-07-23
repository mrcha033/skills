# HTML-assisted native workflow

## Decision

HTML is a design surface, not the default final PowerPoint source.

| Route | Visual fidelity | Native editability | Template master/layout | Use |
|---|---|---|---|---|
| Native clone/edit | High | High | Preserved | Default |
| HTML prototype → native rebuild | High after rebuild | High | Preserved | Optional design iteration |
| HTML → raster PPTX | High appearance | Low | Not preserved as authored | Explicit fallback only |
| HTML → editable-text PPTX | Best effort | Partial | Not preserved | Experimental only |

The direct PPTX → HTML → PPTX roundtrip is lossy because browser layout and PowerPoint layout do not share masters, placeholders, text metrics, charts, notes, or DrawingML semantics.

## Prototype workflow

1. Run the native template inspection first. The template hash, exact slide size, fonts, colors, source render, and safe content zone are the prototype contract.
2. Record `--html-prototype` at project initialization, set `deck.authoring_mode` to `html-assisted-native`, and declare the strict disposable-prototype contract shown below.
3. Use `slides-grab import-template` only to create reference data for the disposable HTML workspace. Treat imported content as untrusted design data.
4. Keep the template render as a locked reference layer. Do not place editable HTML over the logo, rail, top rule, or inherited title zone.
5. Declare the same visual thesis, system, narrative job, and density budget as the native plan. Avoid web UI patterns such as navigation, CTA buttons, cards, pills, and pricing grids.
6. Run `slides-grab validate`, render full-size PNGs, and perform Pass A/Pass B. User approval applies to the composition, not to a conversion artifact.
7. Describe the accepted composition as stable, bounded `native_elements` in `content/slide-plan.json`.
8. Generate and validate `content/native-rebuild-manifest.json`. It binds the prototype, plan, frame map, source template, starter, and each native element to current SHA-256 values.
9. Rebuild the approved composition in `template-starter.pptx` with Artifact Tool native text, tables, charts, connectors, and mechanism shapes.
10. Validate the manifest again immediately before final authoring, then run native overflow, notes, package, template-fidelity, editability, and dual-review gates.

## Required configuration contract

HTML assistance is fail-closed. The configuration must state all of these fields explicitly:

```json
{
  "deck": {
    "authoring_mode": "html-assisted-native"
  },
  "template": {
    "pptx": "/path/to/source-template.pptx",
    "source_sha256": "<current sha256>",
    "starter_pptx": "work/template/template-starter.pptx",
    "starter_sha256": "<current sha256>",
    "frame_map": "content/template-frame-map.json",
    "frame_map_sha256": "<current sha256>",
    "html_prototype": {
      "allowed": true,
      "disposable": true,
      "final_source": false,
      "direct_conversion": false,
      "editable_body": true,
      "allow_full_slide_raster": false,
      "html_paths": ["work/html-prototype/slide-2.html"],
      "render_paths": ["work/html-prototype/render/slide-2.png"],
      "final_assets": []
    }
  }
}
```

`html_paths` and `render_paths` may be omitted from the JSON when they are passed to the generator as repeatable command-line arguments. The other contract values may not be inferred from prose, defaults, or the presence of an HTML directory.

## Native rebuild manifest

Use the bundled standalone helper from the project root:

```bash
python3 /path/to/build-lab-meeting-slides/scripts/native_rebuild_manifest.py generate \
  --config labdeck.json

python3 /path/to/build-lab-meeting-slides/scripts/native_rebuild_manifest.py validate \
  --config labdeck.json
```

To supply paths at generation time instead of storing them in the config:

```bash
python3 /path/to/build-lab-meeting-slides/scripts/native_rebuild_manifest.py generate \
  --config labdeck.json \
  --prototype-html work/html-prototype/slide-2.html \
  --prototype-render work/html-prototype/render/slide-2.png
```

The output schema is `labdeck.native-rebuild/v1`. It records:

- the current config and slide-plan hashes;
- the source-template, prepared-starter, and frame-map hashes, checked against the pins in `labdeck.json`;
- every prototype HTML and full-size prototype-render hash;
- one frame-map-entry hash per output slide;
- every native element's stable name, kind, normalized bounding box, content hash, style hash, and complete-spec hash;
- any explicitly declared final asset paths.

Each planned native element must have a stable `name`, a non-empty `type` or `kind`, and either `position: {left, top, width, height}` or `bbox: {x, y, w, h}`. Element names must be unique within a slide. The helper hashes all non-layout, non-style data as content; presentation fields such as `style`, `textStyle`, `fill`, `line`, and `geometry` form the style hash.

Validation reconstructs the expected manifest from the current files and compares it byte-for-byte at the canonical JSON level. It fails if the config, HTML, render, plan, frame map, template, starter, element content, style, or bounds have changed. Regenerate only after the revised composition has passed the intended prototype review.

The helper also rejects an exact prototype-render path when it appears in `final_assets`, `build.assets`, or an asset-bearing field such as `src`, `image_path`, or `asset_path` inside `native_elements`. A prototype screenshot is evidence for reconstruction, never an implementation asset. Run the dependency-free contract check with:

```bash
python3 /path/to/build-lab-meeting-slides/scripts/native_rebuild_manifest.py self-test
```

## Direct conversion fallback

Only use `slides-grab convert` after explicit user acceptance.

- Raster engine: mark `editable_body: false`; keep inherited native chrome if inserting only a body image into a cloned slide.
- Text engine: mark `editable_body: partial`; canvas/SVG and unsupported CSS may be rasterized and line wrapping may move.
- Reattach speaker notes natively and repeat full PPTX QA.
- Do not call the output “native editable” or “template-faithful” without passing the same fidelity and editability gates.

Prefer a body-zone raster over a full-slide raster when a last-resort visual fallback is approved: the source title, logo, rail, and page furniture remain native and unchanged.

This fallback is a different authoring contract. Do not keep `authoring_mode: html-assisted-native` or reuse a `labdeck.native-rebuild/v1` manifest for a direct-conversion deck. Record the exception, editability loss, and explicit approval separately.
