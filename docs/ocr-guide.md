# OCR guide for agents

This repository treats OCR as evidence extraction, not as a one-command text
cleanup. Preserve the source document, retain raw engine geometry and text,
score against grounded truth, and keep uncertain or unreviewed output labeled
as such.

## Start here

Enter the pinned environment and install the locked Python project:

```sh
scripts/dev-shell
uv sync --extra dev --frozen
```

Inventory a document before deciding that it needs OCR:

```sh
uv run lispmdoc inspect path/to/manual.pdf
uv run lispmdoc render-capabilities
uv run lispmdoc ocr-capabilities
```

Born-digital and hybrid pages may have useful embedded text. Scan pages are
rendered deterministically and routed to an available OCR adapter by the Phase
1 decompiler:

```sh
uv run lispmdoc decompile path/to/manual.pdf --dpi 300
uv run lispmdoc validate work/manual/lmdoc
```

The result is always `review-required`. The current automatic pipeline does
not claim semantic reconstruction, layout fidelity, or replacement readiness.

## GPU engines and exact identities

The reproducible GPU boundary and setup are documented in
[`reproducible-environment.md`](reproducible-environment.md). Run
`scripts/ocr-env-doctor` before inference. Engine and runtime identities are
locked in:

- `config/models/surya-ocr-2.lock.json`;
- `config/models/paddleocr-ppocrv5-en.lock.json`;
- `containers/images.lock.json`;
- `containers/paddleocr/requirements.lock`;
- `tools/surya/` and `tools/paddleocr/` launchers.

Do not compare results across changed model, image, launcher, renderer, or
preprocessing identities without rerunning the benchmark.

## Current bakeoff

The tracked machine-readable report is
[`../benchmarks/results/chinual-ti4ed-surya-paddle-r5.json`](../benchmarks/results/chinual-ti4ed-surya-paddle-r5.json).
It compares Surya 0.22.1 and PaddleOCR 3.7.0/Paddle 3.0.0 over 20 scanned pages
of the Chinual 4th Edition, using recovered typesetter source as semantic text
and reviewed r33 physical projections.

| Engine | Semantic CER | Semantic WER | Exact code-token accuracy | Regions assigned |
| --- | ---: | ---: | ---: | ---: |
| Surya | 1.0088% | 0.5443% | 92.05% | 269/269 |
| PaddleOCR | 1.9796% | 5.7967% | 86.41% | 269/269 |

Both engines also left 100 raw lines outside reviewed content regions. Those
lines are retained in the report; the assignment rate is not a layout-quality
score. On this cohort Surya has lower semantic CER/WER and higher exact
code-token accuracy, but the report deliberately says `not-selected` because
the physical projections are not independent human truth and the cohort lacks
adjudicated layout and selection thresholds.

## Reproduce or adapt the evaluation

With the exact ignored source, review, and r5 run evidence present, regenerate
the tracked report without overwriting it:

```sh
uv run python scripts/evaluate-chinual-r5 \
  --output work/chinual-r5-evaluation-check.json
sha256sum work/chinual-r5-evaluation-check.json \
  benchmarks/results/chinual-ti4ed-surya-paddle-r5.json
```

The evaluator fails closed if the tracked receipt, source identities, review
geometry, engine outputs, or provenance seal drift. The source PDFs, recovered
source, model weights, renders, and full raw-output store are intentionally not
committed. Their digests and the result needed to audit the comparison are.

For a different document, follow [`../benchmarks/README.md`](../benchmarks/README.md):
build representative strata, ground every page in exact source or reviewed
transcription, retain raw output separately, and report CER, WER, punctuation,
case, code-token, omission, and extra-region metrics. Never use an LLM rewrite
as ground truth or silently replace literal OCR with generated prose.
