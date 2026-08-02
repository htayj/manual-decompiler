# Implementation status

Date: 2026-08-02

This records implementation against `plans/implementation-plan.md`. Passing a
foundation milestone is not a claim that the full decompiler exists.

## Implemented

### Phase 0 foundation

- Standalone Python project with locked dependencies and strict test/lint/type
  configuration.
- LMDOC v1 immutable model records.
- Integer micropoint geometry, half-open boxes, and exact rational affine
  transforms.
- Stable content-derived IDs and canonical JSON serialization.
- Offline JSON Schemas for manifest, document records, and guarded overrides.
- Deterministic `.lmdoc` ZIP packaging.
- Format and pipeline documentation.

### Phase 1 inventory and evidence

- Recursive PDF discovery with alias-preserving source grouping.
- Read-only SHA-256 fingerprinting and post-operation source verification.
- PDF inventory through capability-probed backends.
- Page-level classification for born-digital, hybrid, scan, schematic, and
  ambiguous content.
- Deterministic page rendering with recorded backend, DPI, dimensions, hashes,
  and transforms.
- Safe generated-root containment and render cache validation.
- Explicit no-op records for normalization that has not yet been implemented.

### OCR quality foundation

- Engine-neutral OCR page/region/line/span/token evidence.
- Literal PDF-text and Tesseract adapters.
- Honest capability placeholders for optional Surya and YomiToku engines.
- Ground-truth CER, WER, punctuation/case, exact code-token, and
  omission/extra-region metrics.
- Benchmark format documentation.
- Versioned JSON/YAML benchmark manifests that require manual transcription and
  bind every page to exact source bytes.
- Deterministic page selection stratified by page class and human-assigned
  difficulty tags.
- Ground-truth-digest-bound aggregation with provisional gates that cannot pass
  missing, undersized, duplicate, or ungrounded samples; reports also bind each
  page to an engine version and derive a digest from supplied raw-output bytes.
- Content-addressed retention for raw adapter outputs, normalized OCR records,
  render evidence, and optional rendered-page bytes. Phase 1 packages retain
  digest-bound evidence records; their compact form deliberately leaves raw
  bytes in the ignored work-root evidence store unless explicitly embedded.
- No generative rewriting in the authoritative text path.
- Strict Wave 1 queue and transcription-package JSON I/O, deterministic blank
  transcription workspaces, truth-free per-region coverage templates, and CLI
  review-state checks. Existing workspaces fail closed instead of being
  overwritten.
- A separate recovered-typesetter-source truth contract binds the source
  archive, primary and supporting source files, converter manifest, derived
  text, exact source spans, PDF/render page identity, mapping anchors, and
  independently reviewed layout. It cannot accept OCR/generated truth or
  bypass mapping/layout review.
- A deterministic MIT Bolio extractor resolves recovered `manual.vars`
  cross-references, emits semantic reference blocks, and reports unsupported
  constructs rather than silently losing them.
- A loopback-only Vite review application displays scan and generated views
  side by side, highlights regions, and atomically persists digest-bound page
  and region annotations as JSON. Assets are allow-listed and hash-verified.
- A fail-closed review consumer binds those exact project/annotation bytes to
  the truth package; only accepted page mapping plus acceptance of every region
  can close the authoritative gate. Declared source files are also verified as
  exact members of the checksummed tar archive.

### Phase 1 orchestration and validation

- Content-addressed, resumable orchestration.
- Routing of usable born-digital/hybrid text to extraction and scan pages to an
  available OCR adapter.
- Canonical manifest/page/structure/style records and retained native evidence.
- Durable page IDs derived from immutable PDF bytes plus source-page index;
  render and evidence digests are separate mutable-stage evidence.
- Source re-verification before and after work.
- Offline tree/package validation for schemas, page order, references, geometry,
  assets, raster policy, and conformance claims.
- Phase 1 output is forcibly `review-required`.
- Headless CLI for discovery, inspection, rendering, OCR capabilities,
  benchmarking, decompilation, packaging, and validation.

### Derived views and review controls

- Deterministic, accessible HTML and per-page SVG generated only from the
  canonical IR.
- SVG text, rules, rectangles, ellipses, and paths with canonical object IDs;
  unsupported and raster content is explicit rather than silently replaced by
  a full-page scan.
- Derived-view format version included in final package build identity.
- Re-rendering an existing authoring tree prunes obsolete generated SVG pages
  and rejects foreign entries in the owned view directory.
- Guarded correction records and in-memory application for literal text,
  geometry, reading order, and styles, with source, object fingerprint,
  original-text, and old-value checks.
- Semantic relabel patches remain explicitly unsupported until their semantics
  are versioned.
- SVG now preserves typed text/glyph placement, z-order, transforms, opacity,
  rectangular clipping, bounded hash-verified raster assets, and diplomatic
  plain text. Replica mode fails closed on placeholders, full-page rasters,
  missing assets/alt text, unpermitted fonts, and unavailable shaping.
- Read-only/proposal-only CLI reports cover renderer capabilities,
  preprocessing proposals, Wave 1 queue validation, replica gate inputs, and
  digest-bound review exports. They do not fabricate metrics, approvals, or
  replacement claims.

### Reproducible native and GPU environment

- Locked Nix development shell with Python 3.12, uv, Tesseract, Poppler,
  MuPDF, qpdf, resvg, HarfBuzz, FontTools, WOFF2, Potrace, Chromium, Node,
  jq, and Podman.
- Rootless Podman GPU execution through the host NVIDIA CDI specification;
  an actual container smoke sees the RTX 4090.
- Narrow Docker-CLI compatibility shim for Surya translates its NVIDIA flags
  to Podman CDI and replaces the mutable vLLM tag with a platform digest.
- Separate locked Surya 0.22.1 environment; PyTorch 2.13.0+cu130 sees the
  RTX 4090.
- Surya OCR 2 model is bound to Hugging Face revision
  `3b3d4cdf88d6928b0acdc75181b13206ea67c4a3`; its primary 1,372,368,672-byte
  weight was downloaded and verified as
  `5755f82a997dd0b111964fa8b31cc2daef7aeb7a706bbd17d73d6a93ef3f723e`.
- CUDA smoke, Surya vLLM, and Paddle base images are platform-digest locked.
  The vLLM image has now been pulled and completed one-page Surya inference on
  the RTX 4090; the retained raw result SHA-256 is
  `660018f74ca52ef5175dec3745d43f3995f34d1fb19873d3f3a422dc018a8d1b`.
- A derived PaddleOCR 3.7.0 CUDA 12.6 runtime is built from a fully hash-locked
  Python dependency set. Paddle 3.0.0 reports CUDA, `gpu:0`, and one GPU.
- The selected English PP-OCRv5 detection and recognition model trees are
  revision- and file-digest locked. K Machine page 68 completed real inference
  with 38 lines, polygons, and word boxes; retained output SHA-256 is
  `6ed358097c1b7796fc8c5a510731547c16507b797c383354b34d2e4bcfd37425`.
- These runtime smokes do not validate OCR accuracy. Neither engine may be
  selected until its output is scored against reviewed ground truth.
- Runtime evidence is emitted into ignored work storage by
  `scripts/ocr-env-doctor`; model weights and container storage remain outside
  Git.

### Roadmap foundations not yet wired into automatic conversion

- Typed IR, reading-graph, evidence-store, stage-DAG, reconciliation, layout,
  semantics, typography/licensing, born-digital graphics, scanned-graphics,
  review, renderer, and replica-validator foundations now have synthetic tests.
- The Phase 1 CLI intentionally does **not** automatically promote those
  proposals into semantic, typography, vector, or replacement-ready output.
- Package validation verifies content-addressed assets, bound evidence-record
  structure, scene evidence references, and any explicitly embedded evidence
  bytes. It reports external evidence artifacts without claiming to fetch them.

## Real-corpus evidence

Read-only inspection was exercised against:

- `chinual_4thEd_Jul81.pdf`: 544 `scan-bilevel` pages;
- `CADR_schematic.pdf`: 97 `schematic` pages;
- `999017_Users_Guide_To_Symbolics_Computers_Jul86.pdf`: predominantly hybrid;
- `K_Machine.pdf`: 123 born-digital plus 3 ambiguous pages, summarized as
  born-digital with medium confidence;
- `Common_Lisp_Interface_Manager__CLIM__Release_2.0.pdf`: 564 born-digital pages.

All inspection source hashes were unchanged afterward.

`K_Machine.pdf` was exercised end to end at 72 DPI after evidence-store,
cross-wave, and adversarial-hardening integration. It was exercised again after
the final stage-v6 implementation-closure hardening. The second v6 invocation
reused the content-addressed stage and verified its retained evidence artifacts
before reuse.

- 126 source pages;
- structurally valid 385-entry LMDOC package;
- 126 deterministic SVG pages, 126 packaged evidence records, accessible
  HTML/CSS, and diplomatic plain text;
- the title text `K Technical Manual` occurs once in both semantic HTML and
  diplomatic plain text;
- no unsupported-object warnings from the derived-view renderer (this is not a
  visual-fidelity result);
- claimed and effective conformance: `review-required`;
- second run reused the content-addressed stage;
- current v6 stage ID:
  `e17f753e781fc2acadb062ab522ea2373737eefcb31525fd09047608664e28af`;
- repeated v6 packaging SHA-256:
  `8ec292fe1338a1d4c2fb4cff1c36af69ea56da4fcdc4c9d2cbf8aade7ef7a015`;
- source SHA-256 remained:
  `d014d9f5342a509d4ca329e308fcd842d55f3074089ae986242c4bccae1748dd`.

Exact native/render evidence bytes remain in the ignored external evidence
store and are referenced by digest rather than duplicated into the compact
package. These are local smoke results, not committed output artifacts, and do
not establish replica fidelity.

Chinual 4th Edition page index 99 is now the first ignored authoritative-source
benchmark pilot. Its exact source and 300-DPI render are digest-bound, and
PaddleOCR and Surya raw outputs are retained separately. Recovered `FD.SYM 70`
and `manual.vars` produce 15 semantic source regions with zero extraction
findings. Human review accepted the page/source mapping and every region in
revision r11. Material verification binds the reviewed package to the source
archive, source member, variables, converter, canonical text, project, and
annotations. No model output was promoted to ground truth.

The semantic reflow correction records line-break policy in the converter
artifact. Source font controls and definition directives now produce reusable,
source-scoped semantic spans; no word-spelling rule decides bold, italic, or
inline-code formatting. A separate reusable native Pango layer maps those
spans to physical font styles and emits vector SVG paths from one continuous
layout per region. Refactoring the pilot onto those libraries reproduced the
accepted r11 SVG byte-for-byte, SHA-256
`850b3ffe02bb7a18e34e2b8d09b635a5fa97d6658b17e6233fbd34581c25e893`.

A reproducible provisional text-only spatial report finds zero Surya text
errors on the 15 content regions and PaddleOCR CER 0.5845% / WER 3.5135%, with
no content omissions for either engine. It remains a single-clean-page result
and therefore cannot select an OCR engine for the manual or a page class.

A next-slice candidate comprising PDF pages 91 through 110 is rendered at 300
DPI. It covers four recovered source files and includes prose, definition
lists, Lisp displays, mathematical operators, chapter/section transitions, and
dense mixed layouts. A reusable locator aligns baseline Tesseract helper text
to recovered source with 64.7% to 95.5% exact-token coverage and a large
winner/runner-up margin on every page. These are explicitly approximate,
review-required mapping proposals and not ground truth. OCR artifacts, renders,
source files, and proposals are separately digest-bound in ignored work
storage.

The four recovered source files now extract end to end with zero findings. The
reader covers the slice's special-form definitions, alias definitions, tables
and items, indented prose, exdented code labels, and SAIL `<=`/`>=` glyphs. A
digest-pinned Surya run retained rich layout for all 20 pages (SHA-256
`6e55ce5261c6acf37999923abb97a5f44f7f3a2b9053b9c6c1232d7b964af79e`);
one page used Surya's explicit block-mode fallback. Region reconciliation
explains every substantial block and detects the page-93 transition from
`fd_con.141` to `resour.15`, despite the old source footer remaining on that
page. The resulting 20-page, 21-segment mapping review is served only on
loopback and asks for source-mapping approval, not transcription or layout
approval.

## Current automated verification

```text
uv run pytest -q
232 passed

uv run ruff check .
All checks passed

uv run mypy src/lispmdoc
Success: no issues found in 83 source files

git status --short
tracked working tree clean after the recorded commit
```

## Not yet implemented

- Representative English benchmark corpus. One recovered-source scan page is
  authoritative-ready and a 20-page expansion has source-mapping proposals;
  the expansion still needs exact boundary derivation and independent review.
- Evidence-backed OCR engine selection by page class.
- Automatic integration of preprocessing proposals into conversion. The
  foundation supports conservative deskew/border operations and explicit
  review/no-op dispositions; dewarp and uncertain transformations remain
  review-gated.
- Pilot integration of multi-engine reconciliation and review queues.
- Pilot integration of paragraph/list/table/code/math reconstruction.
- Pilot typography/font decisions and legal distribution clearance.
- Pilot PDF-vector and scanned-line-art extraction against representative PDFs.
- Schematic connectivity validation and raster byte/error decisions on pilots.
- Evidence-backed visual comparison and accessibility runs with pinned renderers.
- Persisting reviewed corrections into a new content-addressed build; the
  guarded application layer is currently an in-memory library.
- Visual comparison, OCR threshold enforcement, accessibility validation, and
  clean-environment reproducibility checks.
- Replacement-ready conformance.

The next implementation milestone is the English benchmark plus a reviewed
prose-scan slice. A corpus-wide OCR run should not happen before that benchmark
exists.
