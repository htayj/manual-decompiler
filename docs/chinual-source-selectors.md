# Chinual exact-source selector overlay

`config/benchmarks/chinual-source-selector-overlay.json` is a tracked,
provenance-bound overlay for an r33 region whose readable line span contains
more source than the accepted canonical region. It never edits the ignored r33
artifacts.

The overlay binds the exact r33 manifest bytes, page/region identity and
`region_kind`, source path, source digest, readable inclusive line span,
selector, and digest of the selected result. The importer also requires the
selected digest to equal the region's r33 `text_sha256`. Any stale
manifest/source/kind, duplicate or unused target, malformed selector,
unsupported directive role, invalid token range, or digest mismatch is fatal.

Two selector kinds are intentionally small:

- `rendered-character-range` selects `[start, end)` Unicode characters of the
  freshly rendered Bolio interval. Its only optional projection is the
  deterministic `line-breaks-to-spaces` mapping. That projection is permitted
  only for `body` prose regions that have no table/list-item semantics; code,
  function, table-like, section, and other kinds are rejected rather than
  flattened.
- `directive-component` parses one exact directive line. `role` derives the
  display role from the directive class (for example `.defspec`), and
  `token-range` selects a half-open range of balanced-parenthesis directive
  argument tokens. `.kitem` `label-and-following-prose-character-range` is a
  structural two-component entry selector; it preserves the directive label
  and selects a bounded character range of its one following rendered prose
  component, without applying a line-break projection. No title, page number,
  or literal output is encoded in the selector implementation.

The evaluator reports the overlay digest and exact applied selector identities.
Only a successfully selected output is eligible to promote its region and page
from provisional to authoritative; all other disagreements remain evidence
gaps.
