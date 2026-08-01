# Lisp Manual Decompiler: implementation plan

Status: active; Phase 0 and Phase 1 foundation implemented
Date: 2026-07-29

## 1. Objective

Build a general conversion program for English-language historical manuals.
The operator places PDFs below `source-material/`, runs one command, and gets a
compact, machine-readable document package reconstructed from text, semantic
structure, typography, vector graphics, and only those raster regions that
cannot be represented realistically as text or vectors.

The program must:

- never modify or delete a source PDF;
- handle scanned, born-digital, and hybrid PDFs;
- retain page geometry and a useful semantic reading order;
- preserve literal text rather than silently "improving" it;
- retain uncertainty, provenance, and manual corrections;
- produce deterministic, resumable builds;
- render a faithful paged view without retaining full-page scan backgrounds;
- provide a reflowable semantic view from the same canonical data;
- prove completeness, fidelity, machine readability, and size reduction before
  calling an output a replacement.

Translation is explicitly out of scope.

## 2. Lessons to carry forward from `nethack-guide-jp`

The reference project has several practices worth retaining:

- immutable source material and separate generated work trees;
- canonical page order and explicit source-to-page mappings;
- per-page metadata, hashes, metrics, and flags;
- stable block identifiers and top-left pixel bounding boxes;
- page-subset pilot runs before full-book processing;
- engine bakeoffs on deliberately varied pages;
- manual overrides guarded by source hashes or stable object IDs;
- debug overlays, contact sheets, review PDFs/pages, and structural validators;
- stage caching and reproducible rebuilds from committed structured OCR data;
- destructive-output safeguards.

Its final output model should not be copied. It rebuilds searchable or hybrid
PDFs whose visible layer is still predominantly page imagery. This project
instead needs a canonical document representation from which multiple views
can be rendered.

The reference OCR evidence also gives two cautions:

1. Surya OCR 2 beat or matched YomiToku on many difficult Japanese pages, but
   YomiToku remained easier and faster to reproduce. No engine should be chosen
   for English manuals without a new representative bakeoff.
2. The YomiToku text-LLM extraction pass made literal OCR worse: it collapsed
   structure, substituted text, introduced noise, and sometimes failed to
   produce valid structured output. A language model must not silently rewrite
   the authoritative transcription.

Evidence inspected:

- `../nethack-guide-jp/README.org`
- `../nethack-guide-jp/dist/README.org`
- `../nethack-guide-jp/dist/metadata/ocr-bakeoff/README.md`
- `../nethack-guide-jp/dist/metadata/ocr-bakeoff/surya-vs-yomitoku.md`
- `../nethack-guide-jp/dist/metadata/ocr-bakeoff/yomitoku-full/README.md`
- `../nethack-guide-jp/dist/metadata/ocr-bakeoff/yomitoku-llm-vs-yomitoku-surya.md`
- `../nethack-guide-jp/scripts/process_scans.py`
- `../nethack-guide-jp/scripts/scan_pipeline_common.py`
- `../nethack-guide-jp/scripts/export_yomitoku_layout_source.py`
- `../nethack-guide-jp/scripts/prepare_translation_source.py`
- `../nethack-guide-jp/scripts/make_yomitoku_searchable_pdf.py`
- `../nethack-guide-jp/tests/test_scan_pipeline.py`

## 3. Corpus evidence and input classes

The Sol/xhigh planning pass sampled the local corpus and found materially
different classes that the architecture must accommodate:

- `chinual_4thEd_Jul81.pdf`: 544 scan-only pages, sampled as 400-DPI bilevel
  CCITT images with essentially no text;
- `CADR_schematic.pdf`: 97 landscape, scan-only schematic pages;
- `999017_Users_Guide_To_Symbolics_Computers_Jul86.pdf`: hybrid page images plus
  an existing Acrobat OCR layer;
- `Interlisp-Oct_1978.pdf`: hybrid 300/600-DPI JPEG/JBIG2 pages with sparse
  existing OCR;
- `K_Machine.pdf`: a compact, genuinely born-digital LaTeX PDF with embedded
  Type 1 fonts and no sampled page images;
- `Common_Lisp_Interface_Manager__CLIM__Release_2.0.pdf`: searchable,
  vector-oriented pages with Type 3/custom fonts.

Classification must therefore be page-level, not merely document-level.
Producer metadata is evidence, not classification truth. The initial classes
are:

- `born-digital`;
- `hybrid`;
- `scan-bilevel`;
- `scan-gray`;
- `scan-color`;
- `schematic`;
- `photo-or-illustration-dominant`;
- `ambiguous`.

## 4. Repository and runtime layout

Tracked:

```text
lispmdoc/
  .gitignore
  README.md
  pyproject.toml
  plans/
  schemas/
    manifest.schema.json
    document.schema.json
    overrides.schema.json
  src/lispmdoc/
    cli.py
    config.py
    model/
    ingest/
    preprocess/
    engines/
    reconcile/
    semantics/
    typography/
    graphics/
    package/
    review/
    validate/
  tests/
    fixtures/                 # small, redistributable synthetic/test inputs
    golden/
```

Ignored:

```text
source-material/              # operator-supplied PDFs
work/                         # stage cache, renders, engine-native output, QA
decompiled/                   # final document packages and reports
```

The existing corpus lives at:

```text
source-material/bitsavers/pdf/
```

Input discovery is recursive. The relative PDF path becomes its default
collection path, but the durable document identity is derived from the source
SHA-256 rather than the filename.

## 5. Canonical decompiled format: LMDOC v1

### 5.1 Package

Use a directory during development and a deterministic ZIP container with a
distinct extension such as `.lmdoc` for distribution:

```text
manual.lmdoc/
  mimetype
  manifest.json
  structure.json
  styles.json
  pages/
    p000001.json
    p000002.json
  text/
    document.html
  render/
    pages/
      0001.svg
      0002.svg
  styles/
    document.css
  fonts/
    *.woff2
  assets/
    <content-sha256>.<ext>
  provenance/
    sources.json
    build.json
  reports/
    validation.json
```

The ZIP is a packaging layer, not the data model. Its entries, timestamps,
permissions, JSON key ordering, number formatting, and compression settings
must be deterministic.

### 5.2 Source of truth

The versioned JSON files collectively form the canonical intermediate
representation (IR). HTML, CSS, SVG pages, plain text, and any compatibility
export are deterministic renderings of that IR. Generated views must never be
edited directly.

`manifest.json` contains document identity, source digest and byte size, page
order, selected profile, exact tool/model identities, configuration digest,
rights notes, conformance level, and known limitations.

`structure.json` contains the logical hierarchy and reading model.
`styles.json` contains reusable typography and drawing tokens. Splitting
physical pages into `pages/pNNNNNN.json` keeps very large manuals streamable,
diffable, and incrementally rebuildable.

Together the IR contains:

- document metadata and source hashes;
- ordered physical pages with source-to-canonical transforms;
- logical document hierarchy: parts, chapters, sections, paragraphs, lists,
  tables, figures, captions, notes, code, math, indexes, and front matter;
- a per-page scene graph containing text, rules, shapes, paths, and raster
  assets;
- reading-order and containment graphs;
- reusable style tokens and inferred typography;
- text down to line and span level, with optional word/glyph geometry;
- OCR confidence, alternatives, engine evidence, and correction provenance;
- links between logical nodes and their physical page regions;
- explicit unresolved/needs-review records.

Coordinates use integer micropoints (`1/1000 pt`) in a top-left-origin page
coordinate system; boxes are half-open `[x0, y0, x1, y1]`. Every ingester
records the exact affine transform from source PDF coordinates and render pixels
into canonical coordinates. Polygons are retained when a rectangle would lose
rotation or shape. PDF points, render pixels, and OCR coordinates must never be
mixed implicitly.

Stable IDs are content-derived where possible. A manual correction targets an
ID plus a source/stage hash guard so it cannot drift silently onto different
content.

### 5.3 Web-native views

`text/document.html` provides semantic, reflowable reading order and
accessibility. It uses real headings, lists, tables, figures, captions, code,
MathML, links, and page anchors.

`pages/*.svg` provides faithful paged views:

- real SVG `<text>` for text;
- SVG primitives and paths for rules, boxes, line art, and diagrams;
- `<image>` only for approved raster assets;
- IDs linking every visible object back to its canonical page record;
- no full-page scan image in a replacement-profile package.

The two views are complementary. HTML alone cannot preserve every historical
page layout, and SVG drawing order alone is not a sufficient semantic reading
model.

### 5.4 Standards interoperability

Provide import/export adapters for ALTO XML and PAGE XML where practical, but
do not make either the canonical format. They are useful OCR/layout exchange
formats but do not fully express the combined semantic document, style system,
vector scene graph, raster decisions, and build provenance required here.

## 6. Pipeline

Each stage reads immutable inputs and writes a content-addressed result plus a
small manifest. A stage key includes source hashes, normalized configuration,
tool/model versions, and upstream stage hashes.

### Stage 1: discover and inspect

- recursively discover PDFs;
- hash the exact source bytes;
- reject duplicate work while retaining every source alias;
- inspect page count, boxes, rotation, encryption, embedded text, fonts,
  images, vector operators, annotations, and metadata;
- classify the document and each page as scanned, born-digital, or hybrid;
- detect corrupted PDFs and record a disposition rather than guessing.

Born-digital text and vector operators are extracted before OCR. OCR is used
only for regions lacking trustworthy embedded content.

### Stage 2: render and normalize

- render pages at a recorded DPI and color profile;
- correct page rotation, modest skew, uneven illumination, bleed-through, and
  scanner borders non-destructively;
- preserve both the source render and OCR helper render in the cache;
- detect spreads and foldouts without splitting them automatically when the
  decision is uncertain;
- emit per-page transforms, metrics, flags, and debug overlays.

Unlike the NetHack pipeline, fixed crop sizes and book-specific page-side
assumptions must be optional profiles, not global behavior.

### Stage 3: region and layout analysis

Detect:

- text columns and reading order;
- headings, body text, lists, headers, footers, marginalia, and page numbers;
- tables and cells;
- figures, captions, callouts, and image regions;
- code/listings and terminal transcripts;
- equations;
- rules, boxes, arrows, connectors, and other graphics.

Represent reading order as a graph with a deterministic linearization, not just
an array index. Preserve uncertainty for ambiguous multi-column or inset
layouts.

### Stage 4: OCR adapters and benchmark

Define an engine-neutral adapter contract. An engine returns its native evidence
plus normalized regions, lines, spans, text, geometry, confidence, language,
orientation, and alternatives.

The first bakeoff should include:

- Surya OCR 2, because it was the strongest reference-project result;
- Tesseract as a small, reproducible baseline and for specialized English/font
  configurations;
- at least one current document-layout/OCR engine selected after a local
  feasibility check;
- direct PDF text extraction as a separate "engine" for born-digital pages.

Do not assume the Japanese-focused YomiToku result transfers to English.
YomiToku can remain an optional adapter if its English models prove useful.

Build a manually transcribed evaluation set spanning:

- clean and degraded body prose;
- one- and multi-column layouts;
- tables and indexes;
- monospaced code and command syntax;
- unusual glyphs and historical fonts;
- schematics and labeled diagrams;
- math;
- photographs and halftones;
- born-digital and hybrid PDFs.

Measure character and word error rates, punctuation, whitespace-sensitive code,
reading order, layout-region accuracy, table structure, and runtime/resource
cost. Choose defaults by page class rather than requiring one universal engine.

### Stage 5: evidence reconciliation

- align embedded text and multiple OCR results geometrically;
- select text using measured engine/profile performance and confidence;
- retain all alternatives and their provenance;
- flag disagreements involving identifiers, numbers, punctuation, symbols, or
  low-confidence words;
- dehyphenate only in the semantic view while retaining diplomatic line text;
- normalize Unicode only in an explicit derived field.

No generative model may overwrite literal text. A model may propose a
correction or semantic label only if the proposal is stored separately, linked
to the source evidence, and accepted by a deterministic rule or human review.

The patch guard contains the source page hash, region ID, expected region
fingerprint, original text hash, operation, reason, and reviewer. Stale patches
must fail rather than float onto newly segmented content.

### Stage 6: semantics and typography

- infer repeated headers/footers and exclude them from normal reading flow
  without deleting them from the page model;
- infer document hierarchy from numbering, typography, whitespace, and running
  structure;
- reconstruct paragraphs, lists, tables, captions, cross-references, indexes,
  code, and math;
- cluster fonts into reusable style tokens;
- preserve font family class, size, weight, slant, color, tracking, leading,
  alignment, indentation, and superscript/subscript behavior;
- extract and subset embedded fonts only when redistribution is permitted;
- otherwise use documented metric-compatible/free substitutes and record the
  substitution.

Keep diplomatic and normalized text separate. Code, keyboard notation, Lisp
symbols, part numbers, and equations default to diplomatic preservation.

### Stage 7: graphics decomposition

For born-digital PDFs, preserve source vector paths, fills, strokes, clipping,
and transforms where they can be represented safely in SVG. When arbitrary
patterns, transparency, clipping, or Type 3 behavior cannot yet be lowered
losslessly, preserve a bounded vector fragment with an explicit disposition;
do not rasterize the entire page merely to make the pipeline uniform.

For scans:

- segment text from non-text graphics;
- reconstruct rules, boxes, arrows, circles, leaders, and simple diagrams as
  SVG primitives;
- trace suitable line art, then simplify paths under an explicit visual-error
  bound;
- OCR diagram labels as text linked to their geometry;
- preserve connector topology for schematics only when confidence is adequate;
- retain uncertain topology as visible vector paths without claiming semantic
  connectivity.

A traced tangle of outlines is not a reconstructed schematic. Semantic
schematic conformance requires reviewed components, pins, wires, junctions,
labels, net names, and connectivity. Until then, a tightly bounded high-quality
bilevel fallback plus searchable labels is more honest—and can be smaller than
naive SVG tracing.

Raster fallback requires a reason code such as:

- `continuous-tone-photo`;
- `halftone-or-texture`;
- `complexity-exceeds-vector-budget`;
- `vectorization-error-exceeds-threshold`;
- `manual-raster-override`.

Choose raster encoding by content and measured size/fidelity. Never rasterize a
whole page merely because one region needs a raster fallback.

### Stage 8: package and render

- validate the IR against versioned JSON Schemas;
- generate semantic HTML/CSS and paged SVG;
- subset fonts and deduplicate assets by content hash;
- generate search indexes and plain-text exports as optional derived files;
- write deterministic provenance and validation reports;
- pack a deterministic `.lmdoc`.

PDF and EPUB exporters may be added later as compatibility outputs. They are not
the canonical result. A future PDF renderer should use HarfBuzz-backed shaping,
embedded permitted font subsets, and correct ToUnicode mappings; simple
ReportLab-style string placement is not sufficient for final typography.

### Stage 9: review and corrections

Generate a local review application with:

- synchronized source render, reconstructed page, and semantic view;
- overlays for regions, reading order, confidence, and raster/vector decisions;
- blink/difference views;
- filters for severe flags and engine disagreements;
- editing of text, region geometry, semantics, style, and reading order;
- guarded YAML or JSON patches that remain separate from generated output.

Every accepted correction records author, timestamp, reason, old value, new
value, target ID, and guard hash. Applying the same correction set twice must
produce byte-identical output.

## 7. CLI contract

Initial command shape:

```text
lispmdoc discover [PATH]
lispmdoc benchmark --corpus tests/evaluation-corpus.yaml
lispmdoc decompile SOURCE.pdf [--profile PROFILE] [--pages SPEC]
lispmdoc validate PACKAGE
lispmdoc review PACKAGE
lispmdoc inspect SOURCE.pdf
lispmdoc clean-cache [--document ID] [--dry-run]
```

`decompile` defaults to `source-material/` discovery and writes only below
`work/` and `decompiled/`. It supports resumable execution, bounded parallelism,
CPU-only operation where possible, optional GPU engines, structured logs, and a
machine-readable run summary.

`--force` may replace only a resolved path below the configured generated roots.
It must refuse source paths, repository roots, empty paths, and symlink escapes.

## 8. Validation and conformance

Validation is layered:

### Structural

- schema-valid manifest and IR;
- complete page sequence and stable source mapping;
- unique IDs and valid references;
- finite, in-bounds geometry and valid transforms;
- acyclic containment and usable reading-order graph;
- referenced assets exist and match hashes;
- source files remain byte-identical.

### Text and semantics

- benchmark CER/WER and confidence calibration;
- reading-order accuracy;
- exact preservation tests for code, symbols, part numbers, and math;
- table cell/row/column structure tests;
- heading/list/caption and cross-reference consistency;
- round-trip plain-text queries over known golden passages.

Provisional benchmark targets are no worse than 0.5% CER on clean prose, 2% CER
on degraded prose, 99.5% exact code-token accuracy, and zero silently omitted
benchmark regions. These are hypotheses to confirm or revise from the English
bakeoff, not claims inherited from the Japanese guide.

### Visual

- render every SVG page in a pinned renderer;
- compare against the normalized source at page and region levels;
- use perceptual metrics plus edge/text masks, not one whole-page hash;
- fail on missing regions, clipped content, overflow, or unexpected overlaps;
- produce difference images and contact sheets for review.

### Replacement profile

A package may be labeled `replacement-ready` only when:

- all pages and content regions are accounted for;
- no full-page raster background exists;
- every raster asset has an allowed reason;
- severe OCR/layout/overflow flags are resolved;
- the package is smaller than its source PDF;
- semantic HTML passes accessibility and reading-order checks;
- the paged SVG render passes the reviewed fidelity thresholds;
- the deterministic rebuild check produces identical package bytes.

Documents that cannot meet these gates remain useful decompilations but are
reported as `review-required` or `facsimile-required`; the program must not hide
that distinction.

For scan-dominant manuals, the initial compactness target is a package below 60%
of the source size after all fidelity gates pass. Already compact born-digital
PDFs and efficient bilevel schematic scans may report `size-non-goal` with
evidence. They must never be degraded simply to make the new package smaller.

## 9. Difficult-content policies

- **Schematics:** preserve visible topology first; claim electrical/logical
  connectivity only when validated. Labels stay as searchable text.
- **Tables:** store a logical grid plus physical cell polygons, including spans,
  rules, headers, and footnotes.
- **Math:** retain diplomatic OCR and geometry; add MathML/LaTeX only when
  validated. Never discard the visual expression on a failed parse.
- **Code:** preserve line breaks, indentation, case, punctuation, and monospaced
  styling. Disable prose dehyphenation and spelling correction.
- **Historical fonts:** preserve glyph images as evidence during review, map to
  Unicode cautiously, and allow documented private/unresolved glyph records.
- **Multi-column pages:** store region graph and explicit reading-order edges.
- **Foldouts/spreads:** preserve physical geometry; create logical page splits
  only as a reversible derived view.
- **Photographs/halftones:** crop to the actual figure, retain captions as text,
  and use a content-appropriate raster codec.
- **Born-digital PDFs:** prefer lossless extraction of text, fonts, and paths;
  rasterization is validation evidence, not the primary ingest path.

## 10. Phased delivery

### Phase 0: format and benchmark fixtures

Deliver:

- format ADR and versioned JSON Schemas;
- synthetic fixtures and a small legally redistributable evaluation corpus;
- package reader/rendering spike;
- benchmark rubric and ground-truth conventions.

Exit criteria:

- one hand-authored package renders both semantic HTML and paged SVG;
- schema and deterministic-pack tests pass;
- coordinates and source transforms are proven by tests.

### Phase 1: inspect, render, OCR, and IR

Deliver:

- CLI skeleton and configuration;
- PDF inspection/classification;
- immutable page rendering and content-addressed cache;
- Surya, Tesseract, and embedded-text adapters;
- normalized OCR/layout IR;
- page-subset execution, manifests, logs, and overlays.

Exit criteria:

- representative PDFs run end to end into schema-valid IR;
- reruns reuse valid cache entries;
- source hashes remain unchanged;
- engine evidence is retained without lossy conversion.

### Phase 2: English manual benchmark and reconciliation

Deliver:

- annotated benchmark pages;
- reproducible engine bakeoff report;
- page-class routing;
- evidence reconciliation and guarded corrections.

Exit criteria:

- default engines are selected from measured results;
- literal-text regressions are caught automatically;
- low-confidence and engine-disagreement queues are useful in review.

### Phase 3: semantic and paged text reconstruction

Deliver:

- hierarchy, paragraphs, lists, tables, code, captions, and typography;
- semantic HTML/CSS;
- text-first page SVGs;
- initial review application.

Exit criteria:

- no whole-page raster is needed for prose-only manuals;
- selected multi-column, table, and code fixtures meet text, reading-order, and
  visual gates;
- review patches are deterministic and guard against stale targets.

### Phase 4: vector graphics and raster policy

Deliver:

- PDF vector extraction;
- scan line-art reconstruction and tracing;
- raster segmentation, reason codes, and byte/error policy;
- schematic, photograph, and halftone fixtures.

Exit criteria:

- diagrams retain searchable labels;
- simple figures use vectors;
- raster fallback is region-scoped and auditable;
- vectorization never increases size without a documented fidelity reason.

### Phase 5: package, conformance, and corpus pilots

Deliver:

- deterministic `.lmdoc` packer;
- full validator and reports;
- parallel/resumable corpus runner;
- failure quarantine and summary dashboard;
- optional compatibility exporters.

Exit criteria:

- clean rebuilds are byte-identical;
- pilot manuals representing each difficult class pass or receive explicit
  non-replacement dispositions;
- replacement-ready packages are demonstrably smaller and more machine-readable
  than their source PDFs.

## 11. Decisions to make before implementation

1. Final package name/extension and whether the directory form is also a public
   contract.
2. Python-only first implementation versus a Rust core for packaging/geometry.
   The reference project supports starting in Python; profiling should justify
   any rewrite.
3. The redistributable English benchmark corpus and its ground-truth license.
4. The third OCR/layout engine in the first bakeoff and GPU deployment model.
5. Font redistribution policy and approved fallback font families.
6. Initial fidelity thresholds and size budgets, established from pilot data
   rather than invented in advance.

No large-corpus OCR run should begin until Phase 0 fixes the format contract and
the benchmark/gates exist.
