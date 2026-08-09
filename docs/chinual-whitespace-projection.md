# Chinual semantic text and physical whitespace projections

The Chinual r33 review is an ignored, scan-oriented physical artifact.  It is
not modified to make recovered source text fit it.  The tracked
`config/benchmarks/chinual-r33-whitespace-overlay.json` instead binds two
separate channels for explicitly reviewed regions:

- semantic canonical text is the freshly extracted Bolio interval and retains
  its exact UTF-8 bytes, including literal code line endings and indentation;
- physical layout projection is the r33 `review-project.json` canonical text;
  its SHA-256 is bound by the corresponding r33 replica-manifest region
  `text_sha256`. This receipt does not treat a review-project string itself as
  separately accepted or annotation-bound.

`scripts/verify-chinual-whitespace-overlay` re-imports the source/review
chain, rehashes both channels, and applies only the kind-selected policy.  It
fails on source/review digest drift, unknown or duplicate region keys, missing
subjects, policy drift, or an unpermitted physical relationship.

Policies are general rather than page-specific:

| Region kind | Projection policy | Permitted physical difference |
| --- | --- | --- |
| `body`, `function`, `section` | prose layout whitespace | Unicode whitespace runs only; non-whitespace text must remain ordered and identical. |
| `code` | leading indentation projection | Only leading ASCII space/tab indentation on each line may differ. Line endings, line count, internal text, and trailing whitespace remain literal semantic data. |
| `table`, `unknown` | exact | No difference. |

The correspondence proof does not mutate source text or normalize the semantic
channel. The fail-closed importer uses a valid receipt as region-level
source-semantic plus r33-physical evidence after its ordinary mapping/review
gates pass. A page is authoritative only when every text-digest disagreement
has exactly one valid receipt; a missing, extra, changed, or policy-invalid
receipt aborts import rather than silently retaining or promoting a row.
