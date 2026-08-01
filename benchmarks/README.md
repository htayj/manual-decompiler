# OCR benchmark corpus

Engine selection must be based on a manually transcribed English/manual
benchmark, not on confidence scores or the Japanese NetHack guide results.

The versioned corpus manifest is JSON or YAML. Every selected page is bound to
the exact PDF SHA-256 and zero-based source page index. Every truth record must
declare `method: manual` and a human `recorded_by`; generated text is rejected.

```yaml
version: lispmdoc-benchmark-1
name: English historical manuals
pages:
  - source_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    source_page_index: 0
    page_class: scan-gray
    difficulty_tags: [degraded, prose]
    ground_truth:
      - region_id: page-001-region-001
        text: Literal transcription, including punctuation.
        kind: prose
        method: manual
        recorded_by: reviewer-name
        required: true
```

`lispmdoc benchmark-check` validates this provenance-bearing manifest.
`lispmdoc benchmark-select` creates a deterministic review queue stratified by
page class and operator-supplied difficulty tags; it does not invent truth.

The lower-level `benchmark-ocr` evaluator accepts a JSON ground-truth array:

```json
[
  {
    "id": "page-001-region-001",
    "text": "Literal transcription, including punctuation.",
    "kind": "prose",
    "required": true
  },
  {
    "id": "page-002-code-001",
    "text": "(DEFUN EXAMPLE (X)\\n  (CAR X))",
    "kind": "code",
    "required": true
  }
]
```

Predictions are a JSON object mapping the same stable region IDs to literal
engine output. Reports include CER, WER, punctuation/case accuracy, exact code
tokens, missing required regions, and unexpected regions.

The benchmark aggregation API binds each engine result to the digest of its
exact truth page and records the engine version and SHA-256 of the retained raw
engine-output bytes supplied to the aggregator. The caller remains responsible
for persistently storing those raw artifacts by digest. Its provisional gates
currently require:

- clean-page CER at or below 0.5%;
- degraded-page CER at or below 2%;
- exact code-token accuracy at or above 99.5%;
- zero silently omitted required regions;
- at least 40 evaluated pages, 1,000 clean characters, 1,000 degraded
  characters, and 100 code tokens.

A missing page, undersized stratum, duplicate evaluation, or wrong truth digest
cannot receive a passing disposition. These thresholds are provisional until
the first corpus is transcribed and reviewed.

Before selecting default OCR routing, build a redistribution-safe 40–60 page
corpus spanning:

- clean and degraded prose;
- single- and multi-column pages;
- contents, indexes, and tables;
- Lisp/code listings and command syntax;
- unusual fonts and symbols;
- schematics and labels;
- mathematics;
- hybrid and born-digital pages.

Raw engine output and tool/model identities must be stored as evidence; the
aggregator derives and reports the raw-output digest but is not itself an
artifact store. An LLM-generated correction is never ground truth.
