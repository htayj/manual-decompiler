# Pipeline contract

The source PDF is immutable. A run records and later re-verifies its SHA-256.

Stages are content-addressed by source/page digest, normalized configuration,
implementation version, tool/model identity, and upstream artifact digests:

1. Discover and inventory PDFs.
2. Classify each page.
3. Render and normalize scan-derived pages.
4. Extract existing PDF text and run selected OCR adapters.
5. Reconcile evidence without generative rewriting.
6. Infer layout, semantics, and typography.
7. Decompose visible content into text, vectors, and bounded rasters.
8. Validate and render derived views.
9. Package deterministic LMDOC output.

The current executable integration stops after Phase 1 projection: it retains
raw/normalized OCR and render evidence in a content-addressed work store,
writes digest-bound evidence records into the authoring tree, then derives
HTML/SVG/plain text and validates the package. Reconciliation, semantic,
typographic, vector, raster-decomposition, visual-comparison, and human-review
promotion stages are foundations or proposal libraries, not automatic stages.

Failures are explicit records and page-local where possible. Generated output
may only be replaced beneath configured `work/` and `decompiled/` roots.
