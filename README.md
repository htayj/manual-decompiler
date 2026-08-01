# lispmdoc

`lispmdoc` will decompile historical manual PDFs into compact, machine-readable
text, semantic structure, typography, vectors, and tightly scoped raster assets.

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
and SVG views, evaluate literal OCR against manually entered ground truth, and
validate deterministic package envelopes:

```sh
uv run lispmdoc discover source-material --no-fingerprint
uv run lispmdoc inspect source-material/path/to/manual.pdf
uv run lispmdoc render source-material/path/to/manual.pdf --pages 1-5
uv run lispmdoc ocr-capabilities
uv run lispmdoc benchmark-ocr benchmark.json predictions.json
uv run lispmdoc benchmark-check benchmarks/corpus.yaml
uv run lispmdoc benchmark-select inspections.json page-tags.json --per-stratum 2
uv run lispmdoc decompile source-material/path/to/manual.pdf --dpi 300
uv run lispmdoc render-views work/manual/lmdoc
uv run lispmdoc patch-check corrections/example.json
uv run lispmdoc validate decompiled/manual.lmdoc
uv run lispmdoc validate-schema schemas/manifest.schema.json
uv run lispmdoc pack work/manual/authoring decompiled/manual.lmdoc
```

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

OCR quality is verified against a versioned, manually transcribed corpus.
Metrics include character and word error rates, punctuation and case accuracy,
exact code-token accuracy, and explicit omitted/extra-region accounting.
Provisional gates refuse to pass missing, undersized, or incorrectly grounded
samples. No representative corpus has been transcribed yet, so no OCR engine is
currently endorsed as the default.
