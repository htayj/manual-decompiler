# LMDOC local review app

This is a Vite interface for the small amount of human adjudication that
remains after a source-backed OCR/decompilation run. It is deliberately a
separate local application: it reads a review-project JSON manifest, serves
only assets named by that manifest, and writes annotations beside the manifest.
It never writes the scan, the source transcription, OCR evidence, or generated
replica.

## Run

Install the pinned dependency set once:

```sh
cd web/review
npm install
```

Start it with an explicit project path. Vite is hard-bound to `127.0.0.1` and
uses a strict filesystem allow-list; it is not reachable on the LAN.

```sh
LISPMDOC_REVIEW_PROJECT=/absolute/path/to/review-project.json npm run dev
```

Open the URL printed by Vite, normally `http://127.0.0.1:5173`. Use the page
and region queue at left, inspect the synchronized highlighted regions, choose
a disposition, optionally correct canonical text, add a note, and save. The
app writes `review-project.annotations.json` next to the manifest atomically.
Every write includes the SHA-256 of the exact manifest bytes. Saving fails if
the manifest or annotations changed after the page was loaded.

Consume the saved file through the fail-closed benchmark contract:

```sh
lispmdoc benchmark-authoritative-apply-review \
  /path/to/truth-package.json /path/to/review-project.json \
  /path/to/review-project.annotations.json /path/to/reviewed-truth-package.json
```

This validates the displayed page/render identity and exact source text,
rejects unknown regions or stale project digests, and derives mapping/layout
states from the saved dispositions. A page and every region must all be
accepted before the resulting package is authoritative-ready.

## Manifest contract

See [`project.example.json`](project.example.json). The essential form is:

```json
{
  "format_version": "1.0",
  "document_id": "stable-document-id",
  "assets": {
    "scan-001": {"path": "assets/scan-001.png", "media_type": "image/png"}
  },
  "pages": [{
    "id": "page-001",
    "reference_asset_id": "scan-001",
    "generated_asset_id": "replica-001",
    "regions": [{
      "id": "region-1",
      "reference_box": [0.1, 0.2, 0.3, 0.1],
      "generated_box": [0.1, 0.2, 0.3, 0.1],
      "source_text": "authoritative source",
      "ocr_text": "OCR witness",
      "canonical_text": "current canonical text"
    }]
  }]
}
```

Every asset declaration needs a lower-case SHA-256 digest of its exact bytes.
The app checks it while loading the project and immediately before serving the
asset; a changed scan or replica is rejected rather than silently reviewed.
Asset paths must be non-empty relative paths beneath the manifest directory.
Absolute paths, `..` escapes, and symlinks resolving outside that directory are
rejected. Pages may only reference declared asset IDs. Asset files are available
only through opaque `/api/assets/<asset-id>` routes; no arbitrary local-file
route exists. The server permits only scan/replica media types (SVG, PNG, JPEG,
WebP, GIF, or PDF) and sends a restrictive CSP with every asset response.

Bounding boxes are normalized `[x, y, width, height]` fractions of their image,
using the top-left origin. Boxes are optional, but supplying both reference and
generated boxes makes comparison much faster.

The optional `review_instructions.page` and `review_instructions.region`
strings customize the reviewer-facing meaning of acceptance for a particular
task. Without them, page acceptance means confirming the page/source mapping;
region acceptance means confirming the source text, scan evidence, and
generated layout agree.

## Checks

```sh
npm test
npm run build
```

The tests cover exact-manifest digest binding, required asset digests,
annotation page/region allow-lists, and asset path traversal, absolute-path,
and symlink escape rejection.
