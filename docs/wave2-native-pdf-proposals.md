# Wave-2 native-PDF object evidence proposals

`scripts/build-wave2-native-pdf-proposals` is a no-OCR, no-transcription
preparation step for only Wave-2 inventory entries whose truth origin is
`native-pdf-objects`. At present that is K Machine pages 2, 72, 90, and 95
(zero-based), plus CLIM 2.0 page 562. It cannot make a page benchmark truth.

```sh
uv run scripts/build-wave2-native-pdf-proposals --root .
```

The builder descriptor-reads the tracked inventory and each selected source,
checks the declared source bytes, SHA-256, and page count, then writes one new
ignored workspace under `work/wave2-native-pdf-proposals/`. A content-derived
workspace name is deliberately single-use: a pre-existing workspace aborts
rather than being changed. It snapshots the verified input PDF bytes and uses
those snapshots for all subprocess and rendering work.

Before probing or invoking them, the builder snapshots the selected Poppler
`pdftotext` and PNG renderer into `tools/` as regular, non-symlink files with
controlled executable mode. It descriptor-reads and hashes those snapshots,
then executes a fully write-sealed Linux memfd made from those exact bytes;
the descriptor is explicitly inherited by each subprocess and is closed on
every success, cache, and error path. The native builder uses the strict
five-field pathname/digest identity form; the public renderer also preserves
its legacy exact three-field form, and neither form accepts a caller-supplied
execution descriptor. Changing a snapshot or ambient binary after preparation
therefore cannot alter a version probe, extraction, or page render.
Canonical proposal records name those snapshots with stable workspace-relative
paths (for example, `tools/pdftoppm`) rather than host paths. The executable
digest, controlled snapshot bytes, and logical command path bind the actual
tool without making equivalent workspaces machine- or location-dependent. The
renderer cache/manifest includes that verified executable SHA-256, so two
different binaries cannot collide merely by claiming the same logical name and
version.

Each proposal retains, without text or coordinate cleanup:

- pypdf `visitor_text` callback trace, extracted text result, and decoded page
  content-stream bytes;
- Poppler `pdftotext -bbox-layout` XML plus captured stdout/stderr and the
  exact command record; and
- a 300-DPI raster produced through the existing deterministic renderer,
  including its manifest, renderer version, executable path, resolved binary
  digest, page transforms, and rendered PNG hashes.

`schemas/native-pdf-evidence-proposal.schema.json` defines the strict proposal
root/page contract, which the builder also checks before binding the evidence.
`proposal.json`, `plan.json`, and the sorted `raw-inventory.json` bind the
workspace artifacts. The proposal records literal sequence disagreement rather
than silently reconciling the two text witnesses. Its mechanical checks flag
missing text, malformed/out-of-page/overlapping Poppler boxes, pypdf baselines
outside the page, and raw extraction-order disagreement. Callback coordinates
are explicitly labelled baseline approximations; neither backend proves visual
reading order, glyph geometry, table structure, diagram meaning, or semantic
text.

The pypdf visitor trace stores a recursively canonicalized font dictionary:
indirect PDF references are represented by object and generation numbers, and
unsupported pypdf values abort the proposal. It never serializes Python
`repr()` values carrying reader-instance addresses. Two separately rooted
equivalent runs must therefore produce byte-identical complete workspaces.

The generated `review/index.html` compares the same page render with numbered
Poppler boxes (blue) and pypdf visitor-baseline approximations (red dashed),
side by side. It copies only the five rendered PNGs, overlays, and review HTML
under the self-contained `review/` tree; source snapshots, raw extraction, and
tool copies are excluded. The localhost server recursively rejects symlinks or
nonregular review assets and serves that tree—not the workspace—with no
directory listings. It is an evidence-only project with no acceptance state.
Open it only locally:

```sh
uv run scripts/serve-wave2-native-pdf-proposals work/wave2-native-pdf-proposals/<proposal-id>
```

The server binds `127.0.0.1`; it is not a review decision or a public service.

Symbolics Users Guide page 49 is intentionally **not** proposed here. Its
Acrobat text layer has not been demonstrated to be original typesetter text, so
the inventory records it as an `ocr-derived` witness and it remains ineligible
for native-object evidence or engine selection.
