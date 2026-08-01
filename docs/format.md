# LMDOC v1 format

LMDOC is a structured decompilation format for historical manuals. Its
authoritative representation is versioned canonical JSON; HTML, SVG, PDF, and
plain-text views are derived.

The normative schemas live in `schemas/`. An authoring directory contains:

```text
manifest.json
structure.json
styles.json
pages/pNNNNNN.json
evidence/records/<page-id>.json
assets/
```

A distribution package is a deterministic ZIP with the extension `.lmdoc`.
Its first entry is an uncompressed `mimetype` containing:

```text
application/vnd.lispmdoc+zip
```

## Geometry

- Distances are integer micropoints (`1/1000 pt`).
- The origin is the physical page's top-left.
- Boxes are half-open: `[x0, y0, x1, y1]`.
- Every page records the affine transformation from its source PDF coordinates.
- Render pixels, PDF points, and canonical micropoints are never interchangeable.

## Authority

Physical page records describe visible objects and geometry. `structure.json`
describes semantic reading structure. These may disagree explicitly: drawing
order is not necessarily reading order.

Every visible region has one authoritative treatment: text, vector, raster, or
intentional background. Raster regions require a reason and source crop hash.
Full-page raster backgrounds cannot reach the replacement-ready conformance
level.

## Evidence retention

Phase 1 page records can bind a `page_evidence_sha256` to a canonical
`evidence/records/<page-id>.json` record. Scene objects may name the retained
artifact digests that support them. Exact artifact bytes live in the
content-addressed work-root evidence store by default; this keeps a compact
distribution package from silently becoming a scan archive. If a package embeds
an artifact at `evidence/sha256/<first-two-hex>/<remaining-hex>`, offline
validation verifies both its digest and declared byte size. Otherwise it reports
that the bytes are external and makes no availability claim.

## Reproducibility

Canonical JSON uses UTF-8, sorted keys, stable separators, normalized number
forms, and a trailing newline. Package entry names, timestamps, modes, ordering,
and compression parameters are fixed. Runtime telemetry belongs in noncanonical
run reports.
