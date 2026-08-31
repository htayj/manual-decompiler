# OCR benchmark corpus

Engine selection must be based on independently grounded English/manual truth,
not confidence scores or the Japanese NetHack guide results. Two truth routes
are valid:

- recovered original typesetter/author source, with every archive, source,
  supporting file, converter, derived-text span, scan mapping, and layout
  decision hash-bound and verified;
- independent manual double transcription plus adjudication when authoritative
  source cannot be found.

OCR output, search-engine text, and model-generated corrections are witnesses,
never truth.

The legacy corpus manifest is JSON or YAML. Every selected page is bound to the
exact PDF SHA-256 and zero-based source page index. Its inline truth records
remain manual-only; generated text is rejected. Recovered source uses the
stronger `lispmdoc-authoritative-typesetter-truth-1` package instead of
weakening that legacy contract.

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

Wave 1 queues use an object with `version: lispmdoc-benchmark-wave1` and a
`pages` array. Once each selected page has an exact render, reviewed region
inventory, and expected engine/model/tool identity, initialize blank human
transcription packages with:

```sh
lispmdoc benchmark-transcription-init queue.json work/transcription-wave1
lispmdoc benchmark-transcription-check \
  work/transcription-wave1/pages/SOURCE-p000001.json
```

Initialization copies no OCR text into the packages. Every inventory region is
created with `needs-review`, no transcriber, and no adjudicator. The check exits
nonzero until coverage is complete, at least two independent human
transcriptions have been submitted, and an adjudicated transcription exists.
It binds the package to the source PDF digest, zero-based page index, render
digest, region inventory, composition tags, and exact expected run identity.
Existing workspaces are never overwritten.

For recovered MIT Bolio material, derive a comparison artifact without OCR:

```sh
lispmdoc benchmark-bolio-extract SOURCE MANUAL.VARS \
  --start-line 126 --end-line 180 \
  --text-output work/reference.txt
```

The extractor resolves typesetter variables from `manual.vars`, retains source
spans, and reports unsupported directives or control bytes. An authoritative
truth package is then checked against the exact local material bytes:

```sh
lispmdoc benchmark-authoritative-check work/truth-package.json \
  --source-archive source-material/reference.tar.gz \
  --source-file source-material/extracted/manual.bolio \
  --converter work/converter-manifest.json \
  --converted-text work/reference.txt \
  --supporting archive/member.vars=source-material/extracted/manual.vars \
  --review-project work/review-project.json \
  --review-annotations work/review-project.annotations.json
```

The declared primary/supporting paths must be exact regular-file members of the
checksummed tar archive, with identical bytes. After saving the local UI, first
derive review state without hand-editing JSON:

```sh
lispmdoc benchmark-authoritative-apply-review \
  work/truth-package.json work/review-project.json \
  work/review-project.annotations.json work/reviewed-truth-package.json
```

The check command exits successfully only when the material verifies and mapping and
layout review are complete. It distinguishes `human-mapping-review-required`,
`human-layout-review-required`, and `source-scan-discrepancy`. Verified literal
truth can be exported for `benchmark-ocr` with `--ground-truth-output`; pending
or discrepant packages never create that output.

The workspace, recovered source, and copyrighted truth belong under ignored
`source-material/` and `work/` unless redistribution rights are established.
Tracked fixtures must remain synthetic and contain no copied manual text.

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

## Tracked results

The final 20-page Chinual 4th Edition Surya/PaddleOCR comparison is committed
at
[`results/chinual-ti4ed-surya-paddle-r5.json`](results/chinual-ti4ed-surya-paddle-r5.json).
It is generated by `scripts/evaluate-chinual-r5` from the exact run anchored by
`config/benchmarks/chinual-r5-receipt.json`. The report includes per-engine
metrics, raw-output digests, applied source/counter/whitespace receipts,
unassigned OCR lines, limitations, and the explicit `not-selected` engine
disposition.

The report is distributable benchmark evidence, not a self-contained copy of
the source corpus or raw engine artifact store. Reproducing it byte-for-byte
requires the separately retained, digest-matching source/review/run evidence.
Using the method on another manual requires new ground truth and a new report;
the Chinual scores are not inherited as an engine choice.

## Current pilot

The first local, ignored review pilot is Chinual 4th Edition source page index
99 (printed page 88), a clean bilevel scan containing prose and Lisp symbols.
It is bound to source SHA-256
`123ceb361b0c84864425fc7eee319afc9891a7a68e417f93386f8172334a6e85`
and 300-DPI render SHA-256
`54cb0a866ecb74d6594ab834b83865221df1feb1cc51fbcfb2c4b5f568f7f502`.
Recovered Bolio `FD.SYM 70` plus `manual.vars` now produces 15 exact semantic
text regions with zero extraction findings. The scan footer, printed page, and
heading provide mapping anchors. Human review accepted the page/source mapping
and flagged all 15 initial replica regions for typography/layout repair: source
editor newlines were incorrectly treated as printed breaks, text was too small,
paragraph indentation was missing, and function/code/argument font intent was
lost. The saved annotation bytes remain attached to that exact first revision.

The corrected Bolio converter now explicitly reflows ordinary prose while
preserving code and structural breaks. A provisional text-only spatial
evaluation of the reviewed first revision reports:

- Surya: 0 CER, 0 WER, no content-region omissions;
- PaddleOCR: 0.5845% CER, 3.5135% WER, no content-region omissions.

Both engines left the same five page header/footer lines outside the 15 content
regions; those lines remain explicit unassigned evidence. This is one clean
page, and its replica layout is not accepted, so the report is not an engine
selection or replacement-readiness claim.

A second digest-bound review accepted the r5 page mapping and two regions,
flagged eleven for smaller typography refinements, and left two undecided. The
r6 revision preserves that feedback separately and corrects recovered `^F3`
as bold roman, bold-monospace function headings, font scale/leading, optical
left inset, and paragraph indent. It remains review-required until r6 is
visually accepted; r5 annotations cannot be replayed against it.

Reproduce the ignored report from the retained raw outputs and exact saved
review bytes with:

```sh
uv run python scripts/evaluate-chinual-authoritative-pilot
```

The script refuses to overwrite an existing report and labels its result
`provisional-text-only` while reviewed replica discrepancies remain.

Raw candidate outputs are retained separately from truth:

- PaddleOCR output SHA-256
  `ba69639aadadaf819b1208edc8257d9632db98924e19776508e4f5be33e19baa`;
- Surya output SHA-256
  `17e369cd12e97942387df9713dc94a41fccf714fbd562d97fa05f3e92bfdf595`.

Neither candidate OCR output is authoritative. With the recovered source, the
single human confirms mapping/layout and adjudicates source-versus-scan
differences rather than retranscribing the page.
