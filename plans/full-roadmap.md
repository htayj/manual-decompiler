# LMDOC full roadmap to replacement-ready replicas

Status: active
Date: 2026-07-29
Planning pass: Sol, `xhigh`

## Target

Produce compact, machine-readable replicas of historical manuals. A replica is
a content-faithful paged reconstruction, not a pixel-identical copy of scanner
noise:

- exact page count, order, dimensions, orientation, and meaningful printed
  content;
- diplomatic text, page breaks, line breaks, typography, rules, figures, and
  spatial relationships reproduced within measured tolerances;
- semantic HTML with the same authoritative text and a usable reading order;
- paper texture, scanner borders, skew, bleed-through, and dust omitted only
  through explicit non-content dispositions;
- bounded raster assets only where text or vectors are not realistic;
- immutable source PDFs retained as archival evidence.

The current Phase 1 packages are experimental and have no compatibility
guarantee. Freeze LMDOC 1.0 only after the first representative pilot.

## Foundation contract changes

Before deeper extraction:

1. Replace loose scene payload conventions with typed text blocks, lines,
   spans, tokens, glyphs, groups, clipping paths, rules, shapes, paths,
   rasters, links, and annotations. Record z-order, transforms, opacity,
   stroke/fill, masks, and evidence references.
2. Add nested regions, polygons, and baselines.
3. Replace tuple-only reading order with a directed graph and deterministic
   linearization. Represent containment, adjacency, caption/figure,
   table-cell, and logical-to-physical edges separately.
4. Replace the misleading `source_page_sha256` contract with:
   source-PDF hash plus page index for durable identity, concrete source-render
   digest, and page-evidence digest.
5. Add canonical evidence records and a content-addressed artifact store for
   native adapter results, normalized evidence, engine/model/container/config
   identities, selection decisions, alternatives, and unresolved findings.
6. Expand typography with font resources, exact/substitute status, licensing,
   variation axes, OpenType features, baselines, alignment, decoration,
   writing mode, spacing, and fallback chains.
7. Split conformance into fidelity, text, structure, accessibility,
   reproducibility, raster-policy, size, and distribution-rights facets.
8. Expand guarded corrections to cover split/merge, baselines/polygons,
   reading graph, semantics, table grids/spans, fonts, raster/vector decisions,
   alt text, unresolved glyphs, and page approval.
9. Add migrations and exhaustive schema fixtures.

## Wave 1: three parallel foundations

### Lane A: IR, evidence, stages, and conformance

Ownership:

- `src/lispmdoc/model/`
- `src/lispmdoc/evidence/`
- `src/lispmdoc/stages/`
- `schemas/`

Deliver:

- typed IR and matching offline schemas;
- canonical evidence records;
- atomic, hash-verifying content-addressed artifact storage;
- page-local keys containing source, configuration, implementation,
  tool/model, and upstream digests;
- dependency DAG, bounded parallelism, deterministic manifests, failure
  records, and resumability;
- conformance facets and Phase 1 migration.

Gate:

- synthetic packages exercise every object/evidence type;
- graph, geometry, and reference validators pass;
- corrupt or mismatched cache artifacts are rejected;
- one-job and multi-job builds from different roots are byte-identical;
- source mutation is detected before publication;
- native OCR evidence is recoverable by digest.

### Lane B: benchmark construction and evaluation

Ownership:

- `src/lispmdoc/benchmark/`
- benchmark schemas and reports;
- transcription and adjudication tooling;
- `benchmarks/`.

Deliver:

- deterministic 60-page queue bound to exact source and render hashes;
- transcription export covering regions, literal text, line breaks, baselines,
  polygons, reading order, semantics, tables, and coverage dispositions;
- authoritative recovered-source truth when available, otherwise independent
  double transcription and adjudication;
- exact engine-output artifact retention;
- metrics for text, omissions, regions, reading order, semantics, tables,
  calibration, runtime, memory, VRAM, and size;
- reports stratified by page class and difficulty.

Required composition, with overlap allowed:

- 12 clean scanned prose pages;
- 12 degraded scanned prose pages;
- 8 hybrid pages;
- 6 born-digital pages;
- 6 multi-column, TOC, index, or list pages;
- 6 code or terminal pages;
- 4 tables;
- 3 math or unusual-glyph pages;
- 3 diagram-label or schematic pages;
- at least 100 exact code tokens and 2,000 characters in each scanned-prose
  stratum.

Gate:

- complete page-region inventory;
- every truth package is either verified recovered typesetter source with
  confirmed page mapping and reviewed scan-bound layout, or two manual
  transcriptions adjudicated;
- generated text cannot become truth;
- duplicate, missing, single-review, stale-source, undersized, or
  absent-raw-output records cannot pass;
- reports reproduce byte-for-byte.

Human dependency: source/scan mapping and layout adjudication; transcription is
the fallback only where no authoritative source exists. Copyrighted truth
remains ignored unless redistribution is cleared.

### Lane C: extraction and reversible preprocessing

Ownership:

- `src/lispmdoc/preprocess/`;
- low-level PDF extraction helpers;
- transform and overlay fixtures.

Deliver:

- lossless page-image extraction when a scan is a simple PDF image object;
- separate source and OCR-helper renders;
- spread/foldout, border, and scanner-bed detection;
- orientation, deskew, crop, illumination, bleed-through, dewarp, and
  binarization;
- exact composed transforms back to canonical coordinates;
- confidence, before/after metrics, reversible settings, and debug overlays;
- explicit no-op/review dispositions when confidence is insufficient.

Gate:

- synthetic geometry recovered within 0.25 degrees and 0.5 source pixel;
- no detected content is cropped without disposition;
- estimator disagreement cannot silently alter an image;
- reruns are deterministic and source bytes stay unchanged.

## Wave 2: OCR and layout adapter bakeoff

Implement pinned adapters for embedded PDF content, Tesseract, Surya OCR 2, and
one English document-layout candidate. PaddleOCR/PP-Structure is the initial
feasibility candidate; retain it only after license, output, CPU/GPU, and local
installation checks. Kraken and YomiToku are optional evidence, not required
dependencies. Add ALTO and PAGE XML interchange.

Every adapter retains complete native output, normalizes without rewriting,
records exact model/container/config identities, and supports page subsets.

Per-stratum selection gates:

- clean prose CER at most 0.5%;
- degraded prose CER at most 2%;
- exact code-token accuracy at least 99.5%;
- zero silently omitted required regions;
- text-region recall at least 99% and precision at least 98% at IoU 0.5;
- reading-order pair accuracy at least 99.5%;
- expected calibration error at most 0.05 where confidence exists.

Runtime and memory are reported but cannot override fidelity failures.

## Wave 3: reconciliation and review queues

Add geometric alignment, calibrated selection, evidence-conditioned routing,
alternative retention, and explicit disagreement findings.

Always escalate identifiers, numbers, punctuation, symbols, Lisp tokens, key
names, equations, low confidence, engine disagreement, omissions, duplicated
lines, hyphenation ambiguity, unexpected Unicode, and unresolved glyphs.
Diplomatic text remains authoritative; normalization and spelling suggestions
are derived only.

Gate:

- never worsen benchmark CER or omissions relative to the routed best engine;
- every selected token links to exact evidence;
- every high-risk disagreement enters the review queue;
- accepted patches create a new deterministic build identity.

## Wave 4: physical layout reconstruction

Reconstruct nested regions, columns, baselines, running matter, marginalia,
lists, tables/cells/spans, code, equations, figures/captions, callouts, and page
numbers. Prefer PDF objects for born-digital pages and measured image/layout
evidence for scans.

Gate:

- text-region recall at least 99% and precision at least 98%;
- reading-order pair accuracy at least 99.5%;
- table cell/span F1 at least 98%;
- every content region has exactly one treatment or intentional-exclusion
  disposition;
- ambiguity remains reviewable evidence rather than guessed order.

## Wave 5: semantic reconstruction

Infer parts, chapters, sections, paragraphs, lists, tables, captions, notes,
code, terminal transcripts, indexes, cross-references, math, and repeated
running matter. ML or language models may propose structure but cannot rewrite
diplomatic text.

Gate:

- semantic macro-F1 at least 98% on reviewed fixtures;
- every logical node links to physical evidence;
- repeated headers/footers remain visible but leave normal reading order;
- benchmark code indentation, case, punctuation, and breaks are exact;
- math keeps a visual/diplomatic fallback until MathML is reviewed.

Human dependency: ambiguous hierarchy, tables, math, captions, and references.

## Wave 6: typography, shaping, and fonts

For born-digital pages extract font programs, encodings, ToUnicode maps,
widths, kerning, and Type 3 procedures. Preserve original programs only when
distribution is explicitly allowed; otherwise use measured substitutes or
vector glyphs.

For scans infer size, weight, slant, leading, tracking, alignment, and family,
then select documented free substitutes by measured line fit. Use FontTools,
WOFF2 subsetting, and HarfBuzz-compatible shaping. Record unresolved glyphs and
bounded fallbacks.

Gate:

- line-break agreement at least 99.5%;
- p95 baseline displacement at most 0.5 pt;
- no clipping or overflow;
- code columns align within 0.5 pt;
- every font has hash, provenance, and rights disposition;
- unknown or prohibited fonts cannot enter distributable output.

Human/legal dependency: font redistribution decisions.

## Wave 7: born-digital vector extraction

Preserve paths, transforms, clipping, stroke/fill, dash, joins, caps, reusable
XObjects, annotations, Type 3 glyph procedures, and supported transparency.
Unsupported patterns, shadings, blend modes, or malformed streams become
bounded findings rather than whole-page rasterization.

Gate:

- synthetic operator geometry round-trips exactly;
- every drawing operator is consumed or disposed;
- K Machine uses no raster page backgrounds;
- CLIM Type 3 content is reproduced or gets bounded reviewed fallbacks;
- born-digital rendering reaches SSIM at least 0.995 with zero missing-content
  components.

## Wave 8: scanned graphics and raster decomposition

Separate text, line art, continuous tone, halftone, and texture. Remove and
reinsert labels as text; recover rules, boxes, circles, arrows, leaders, and
junction candidates; trace remaining line art under a recorded error bound;
choose raster codecs per bounded region using byte/error curves.

Raster reason codes are mandatory. A raster above 80% page area requires
manual approval and cannot be replica-ready unless explicitly photo-dominant
with no embedded meaningful text/vector content. Visible schematic fidelity
does not imply semantic connectivity.

Gate:

- labels remain searchable and spatially linked;
- foreground edge recall at least 99%;
- p95 symmetric edge distance at most 1.5 pixels at 300 DPI;
- no undisposed missing component above 0.25 square millimetres;
- vectorization beats raster size or has documented semantic/fidelity value;
- raster crops are tight, hashed, deduplicated, and reasoned.

## Wave 9: final renderers and compatibility outputs

Generate semantic HTML, deterministic SVG, diplomatic plain text, and later an
optional vector/text-first PDF. SVG uses shaped text, paths, clipping, font
subsets, real bounded raster assets, canonical IDs, and deterministic z-order.
The optional PDF is derived, has correct ToUnicode maps, and must pass
`pdftotext` equivalence.

Gate:

- all views derive exclusively from the IR;
- pinned Chromium/resvg rendering is deterministic;
- no placeholder remains in a replica candidate;
- plain text round-trips all diplomatic text;
- optional PDF contains no unexpected full-page image and matches SVG gates.

## Wave 10: persistent human review

Provide synchronized source/reconstruction/semantic views, blinking and
differences, overlays, reading graph, engine alternatives, raster/vector
decisions, and searchable severe queues. Runtime state may use SQLite; build
input is sorted canonical JSON. Review exports guarded patches and never edits
source or generated views.

Gate:

- stale guards and partial patch sets are rejected;
- identical IR plus patches yields identical package bytes;
- page approval binds final evidence and render digests;
- severe findings block promotion.

Human dependency: every replica-ready page receives approval; a stratified 10%
receives independent second review, as does every severe page.

## Wave 11: validation and attestation

Add text, layout, visual, accessibility, size, reproducibility, and attestation
validators. Use pinned rendering and masks for text, line art, bounded raster
figures, and intentional background; whole-page SSIM cannot hide omissions.

Initial thresholds:

- all source content accounted for;
- born-digital SSIM at least 0.995;
- scan-content SSIM at least 0.985 after reviewed background exclusion;
- foreground edge recall at least 99%;
- p95 edge displacement at most 1.5 pixels at 300 DPI;
- continuous-tone crop SSIM at least 0.99;
- no unexpected overlap, overflow, missing region, placeholder, or severe
  finding;
- zero critical or serious accessibility violations.

Changing thresholds requires a versioned ADR and benchmark rerun.

## Exact `replica-ready` definition

A document is replica-ready only when:

1. Source hash, bytes, page order, boxes, rotation, and transforms are exact.
2. Every page and detected content region has one authoritative treatment:
   text, vector, approved bounded raster, intentional background, or explicit
   unresolved failure.
3. No full-page raster, placeholder, or unsupported object remains.
4. OCR/layout benchmarks pass for every page class used.
5. All high-risk and omission findings are resolved.
6. Every page has human approval bound to final evidence/render digests.
7. A stratified manual audit covers at least 10,000 characters or 5% of text,
   whichever is larger; prose CER is at most 0.25%, sampled
   code/identifiers/math are exact, and the 95% upper bound is reported.
8. No severe reading-order or structural finding remains.
9. Every page passes region-aware visual gates with no undisposed component.
10. Semantic HTML passes accessibility gates with identical authoritative
    text.
11. Offline validation passes and every asset/font hash and rights disposition
    resolves.
12. Two clean builds from different roots and allowed job counts are
    byte-identical.
13. A scan-dominant package is smaller than its PDF and targets at most 60% of
    source size; hybrid output is smaller. Compact born-digital sources may
    mark size not applicable without satisfying the scan-size milestone.
14. An attestation binds package, source, benchmark, renderer, and review-set
    hashes.

Failure leaves the document `review-required` or `facsimile-required`.
Distribution rights are an independent status.

## Representative pilots

1. `K_Machine.pdf` (126 pages): born-digital text, fonts, paths, code, SVG, and
   reproducibility without page rasters.
2. `chinual_4thEd_Jul81.pdf` (544 pages): reviewed 20-page scan slice, then
   prose/code/list/table/glyph coverage and the 60% size target.
3. Symbolics Users Guide (353 pages): reconcile hybrid image and Acrobat text.
4. CLIM Release 2.0 (564 pages): Type 3/custom fonts, code, tables, references.
5. Interlisp October 1978 (773 pages): mixed 300/600-DPI JPEG/JBIG2 and
   degradation.
6. CADR schematic (97 pages): visible fidelity on a 10-page slice, with
   connectivity as a separate reviewed milestone.

Every pilot emits a failure report rather than being forced into a passing
status.

## Full-corpus rollout

After the pilots:

1. Inventory all 514 PDFs by hashes, aliases, classes, encodings, fonts,
   producers, rotations, corruption, and difficult-feature signatures.
2. Select a stratified 20-manual canary set.
3. Preflight inspect/preprocess/layout and estimate storage/compute before
   high-DPI rendering.
4. Route pages by measured class using page-local bounded CPU/GPU queues.
5. Quarantine failures per page/document.
6. Report stage status, review burden, size projection, and severe findings.
7. Promote manuals individually; never assign blanket corpus conformance.
8. Deduplicate sources, page images, fonts, and raster assets by hash while
   preserving aliases.
9. Garbage-collect only unreferenced generated artifacts after dry-run and
   containment validation. Never collect sources or accepted review patches.

The 2.87 GB source set may expand by one or two orders of magnitude at
400–600 DPI, so full-corpus high-resolution rendering is gated by preflight.

## Human, legal, and compute boundaries

Automation can inspect, normalize, OCR, align, infer, trace, render, measure,
cache, validate, and package. It cannot independently supply:

- benchmark transcription and adjudication;
- final page approval and severe-content review;
- reliable ambiguous semantics, math, or schematic connectivity;
- font/source/model distribution-rights decisions;
- meaningful figure alt text;
- unlimited GPU time, storage, or externally unavailable model weights.

Automation may propose and measure, but it must never conceal uncertainty or
promote a document across a gate requiring human or legal evidence.
