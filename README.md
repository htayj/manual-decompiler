# Manual Decompiler

Manual Decompiler turns historical technical-manual PDFs into compact,
machine-readable text, semantic structure, typography, vectors, and tightly
scoped raster assets. The current Python package, CLI, and output format retain
the `lispmdoc`/LMDOC names.

The conversion program and its schemas/tests belong in Git. Local inputs,
intermediate work, and generated conversions do not.

## Local layout

- `source-material/` — ignored input corpus
- `work/` — ignored stage cache and review data
- `decompiled/` — ignored generated document packages
- `plans/implementation-plan.md` — implementation-ready architecture plan

The preserved Bitsavers PDFs are currently under:

```text
source-material/bitsavers/pdf/
```

Source PDFs are immutable inputs. The future converter must never overwrite or
delete them.

## Development

```sh
scripts/dev-shell
uv sync --extra dev --frozen
uv run pytest
uv run ruff check .
uv run mypy src/lispmdoc
```

The reproducible environment and rootless Podman GPU boundary are documented
in [`docs/reproducible-environment.md`](docs/reproducible-environment.md).

The current headless foundation can inventory and classify inputs, render
immutable source pages, produce review-required Phase 1 packages, derive HTML
and SVG views, evaluate literal OCR against source- or manually grounded truth, and
validate deterministic package envelopes:

```sh
uv run lispmdoc discover source-material --no-fingerprint
uv run lispmdoc inspect source-material/path/to/manual.pdf
uv run lispmdoc render source-material/path/to/manual.pdf --pages 1-5
uv run lispmdoc ocr-capabilities
uv run lispmdoc benchmark-ocr benchmark.json predictions.json
uv run lispmdoc benchmark-check benchmarks/corpus.yaml
uv run lispmdoc benchmark-select inspections.json page-tags.json --per-stratum 2
uv run lispmdoc benchmark-bolio-extract source.bolio manual.vars \
  --text-output work/reference.txt
uv run lispmdoc benchmark-authoritative-apply-review \
  work/truth-package.json work/review-project.json \
  work/review-project.annotations.json work/reviewed-truth-package.json
uv run lispmdoc benchmark-authoritative-check work/reviewed-truth-package.json \
  --source-archive source-material/source.tar.gz \
  --source-file source-material/extracted/source.bolio \
  --review-project work/review-project.json \
  --review-annotations work/review-project.annotations.json
uv run lispmdoc decompile source-material/path/to/manual.pdf --dpi 300
uv run lispmdoc render-views work/manual/lmdoc
uv run lispmdoc patch-check corrections/example.json
uv run lispmdoc validate decompiled/manual.lmdoc
uv run lispmdoc validate-schema schemas/manifest.schema.json
uv run lispmdoc pack work/manual/authoring decompiled/manual.lmdoc
```

If you are choosing or debugging OCR for a new manual, start with
[`docs/ocr-guide.md`](docs/ocr-guide.md). It connects the runnable commands,
GPU/runtime locks, benchmark method, and the tracked Surya/PaddleOCR bakeoff.

`inspect` records a source hash before analysis and verifies it again afterward.
Its classification is page-level evidence, not a claim that existing text or
OCR is correct.

`decompile` currently produces a **Phase 1, `review-required`** LMDOC package:
literal embedded-PDF text or OCR evidence, physical page geometry, provenance,
digest-bound evidence records, and deterministic package structure. Exact raw
adapter/render evidence is retained in the ignored work-root content-addressed
store; the compact package records those digests and does not claim to embed
every source render. It does not yet claim semantic reconstruction, vector
replacement, visual fidelity, or replacement readiness.

Useful read-only or proposal-only operational checks are:

```sh
uv run lispmdoc render-capabilities
uv run lispmdoc preprocess-proposal work/render/.../page.png
uv run lispmdoc benchmark-queue-check benchmarks/wave1-queue.json
uv run lispmdoc replica-check reports/replica-evidence.json --attest
uv run lispmdoc review-export work/decompile/<stage>/lmdoc work/review-input.json
```

`preprocess-proposal` does not alter pixels. `replica-check` accepts only
explicit measurements and refuses an attestation unless every gate passes.
`review-export` is a digest-bound read-only review input; it creates neither
approvals nor a promotion claim. `render-capabilities` reports unavailable
dependencies as non-claims, rather than silently falling back to a different
renderer.

OCR quality is verified against versioned ground truth. Recovered original
typesetter/author source is preferred and must pass exact material, page-mapping,
and layout gates; independent double transcription is the fallback.
Metrics include character and word error rates, punctuation and case accuracy,
exact code-token accuracy, and explicit omitted/extra-region accounting.
Provisional gates refuse to pass missing, undersized, or incorrectly grounded
samples. No representative corpus has passed all review gates yet, so no OCR
engine is currently endorsed as the default.

When judgment is required, `web/review/` provides a loopback-only Vite UI with
side-by-side scan/generated views, synchronized region highlights, source/OCR
text comparison, corrections, and machine-readable annotations. See
[`web/review/README.md`](web/review/README.md). Review output remains under
ignored `work/` and is bound to the exact project and asset hashes.
`benchmark-authoritative-apply-review` is the only promotion path from those
saved decisions: page acceptance verifies mapping, every region must be
accepted for layout readiness, and corrections/rejections remain discrepancies.
The material gate reloads both exact files and refuses digest-shaped substitutes.
The current Chinual pilot also has a reproducible, explicitly provisional
spatial text report via `scripts/evaluate-chinual-authoritative-pilot`; it does
not select an OCR engine from a single page.

The later 20-page Chinual bakeoff is tracked as machine-readable evidence at
[`benchmarks/results/chinual-ti4ed-surya-paddle-r5.json`](benchmarks/results/chinual-ti4ed-surya-paddle-r5.json).
Its source-derived truth is metric-eligible but not independent human truth, so
the report deliberately leaves the default engine unselected.
