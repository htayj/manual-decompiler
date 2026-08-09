# Wave-2 representative benchmark inventory

`config/benchmarks/wave2-representative-candidates.json` is a tracked,
digest-bound inventory of local English-manual candidates. It is not a truth
corpus, OCR run request, or engine-selection result.

Run the no-inference checker from the repository root:

```sh
uv run scripts/check-wave2-inventory --root .
```

The checker reads each configured PDF once through a contained, regular-file
path, verifies byte size, SHA-256, and page count from those bytes, and emits a
deterministic candidate/authority ledger. It rejects path escapes, symlinks,
duplicate manual/path/digest/page identities, malformed composition tags, and
identity drift. It never renders pages or invokes an OCR engine.

Each candidate declares page classes and page indices only where they are
currently known. Its `truth` record names an authority origin and explicit
mapping, layout, reading-order, semantic, and native-object-extraction gates.
Selection eligibility is derived by the checker:

- `recovered-source-reviewed` is eligible only after exact source verification,
  mapping, scan-bound layout, and semantic gates are complete; its
  reading-order gate must be reviewed or explicitly not applicable to the
  truth package. Engine selection still requires the later reading-order metric;
- `independent-adjudicated` is eligible only after the independent
  adjudication, layout, reading-order, and semantic gates are complete;
- `native-pdf-objects` is eligible only after exact object extraction and
  reviewed layout, reading order, and semantics. It cannot assert diagram
  meaning from PDF objects alone;
- `ocr-derived` is always ineligible.

The current Chinual entry uses the existing recovered-source importer as its
only evidence verifier. Its mapping/layout/source evidence is checked live and
the proven 20 pages are truth-eligible; they remain far short of the full
composition and later engine reading-order gates. K Machine, CLIM 2.0,
Symbolics' July 1986 Users Guide, Interlisp October 1978, and the CADR
schematic are inventory-only. No manual transcription is requested by this
milestone.

The visually reviewed, pending candidates are K Machine zero-based pages 2
(prose), 72 (code), 90 (three-column table), and 95 (box/arrow storage layout
with prose); CLIM page 562 (two-column glossary); CADR schematic page 1;
Interlisp page 99; and Symbolics Users Guide page 49. The K page 95 tag is only
`born-digital`: its visible boxes and arrows are not asserted to be a semantic
schematic before a semantics review.

The report counts only selection-eligible pages toward the 60-page Wave-1
composition. Consequently its current `undersized` state is an expected
boundary, not a quality result or a reason to run more OCR.
