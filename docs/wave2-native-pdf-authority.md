# Native-PDF authority receipt

`config/benchmarks/native-pdf-k-machine-p3-receipt.json` is deliberately
pending. A native PDF text layer is evidence, not truth. The only current
candidate is K Machine PDF source page index 2 (printed page 3).

Prepare a fresh local project from a complete deterministic proposal workspace:

```sh
uv run scripts/prepare-native-pdf-authority-review \
  work/native-seal-determinism-a/work/proposals/proposal-4dd246af8f379a4622ce \
  work/k-machine-p3-native-review
cd web/review && LISPMDOC_REVIEW_PROJECT=../../work/k-machine-p3-native-review/native-pdf-authority-review.json npm run dev -- --port 5173
```

The reviewer must save annotations on localhost. The suggested grouping is a
vision-first prefill only: it is not an acceptance. Reviewers accept/reject
the fixed Poppler grouping, reading order, and running-matter exclusion. A
`needs-fix` note asks us to regenerate evidence; reviewers do not edit IDs or
text. They separately dispose of every mechanical finding. Before a receipt
can be changed to `accepted`, a receipt writer
must bind the saved annotation bytes plus exact proposal, plan, raw inventory,
tracked inventory, source-snapshot, rendered-PNG, and Poppler raw XML hashes.
The verifier requires an exhaustive selected/excluded word partition and four
separate layout/order/semantics/object-extraction acceptance decisions.
