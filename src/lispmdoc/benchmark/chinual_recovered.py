"""Fail-closed importer for the reviewed Chinual recovered-source slice.

The replica manifests are useful review evidence, but their ``text_sha256``
values are *claims*.  This importer re-renders every cited Bolio interval from
the digest-bound recovered files before it exposes a record to benchmark code.
An interval disagreement is retained as a provisional record; it can never be
quietly promoted to authoritative truth.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bolio import BolioError, extract_bolio, render_bolio_interval
from .wave1 import QueuePage


class ChinualImportError(ValueError):
    """The claimed recovery, review, or final-source evidence is not usable."""


_LAYOUT_REVISIONS = (
    ("r25", "replica-review-r25", "review-project.json", "review-project.annotations.json"),
    ("r28", "replica-review-r28", "correction-review.json", "correction-review.annotations.json"),
    ("r30", "replica-review-r30", "final-correction.json", "final-correction.annotations.json"),
    (
        "r33",
        "replica-review-r33",
        "final-alignment-review.json",
        "final-alignment-review.annotations.json",
    ),
    (
        "r33-final",
        "replica-review-r33",
        "final-byte-review.json",
        "final-byte-review.annotations.json",
    ),
)
_MAPPING_REVISIONS = (
    ("r1", "mapping-review-r1"),
    ("r2", "mapping-review-r2"),
)
_DIGEST_FIELDS = (
    ("source_pdf_sha256", "source PDF"),
    ("manual_vars_sha256", "manual.vars"),
    ("proposals_sha256", "source-alignment proposals"),
    ("layout_results_sha256", "layout results"),
)
_UNUSUAL_GLYPHS = frozenset("≠≤≥")
_REGION_KINDS = frozenset({"body", "code", "function", "section", "list-item"})


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ChinualImportError(f"required evidence file is missing: {path}")
    return _sha256_bytes(path.read_bytes())


def _digest(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ChinualImportError(f"{label} must be a lower-case SHA-256 digest")
    return value


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ChinualImportError(f"{label} must be an object")
    return value


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ChinualImportError(f"{label} must be an array")
    return value


def _load(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ChinualImportError(f"required evidence file is missing: {path}")
    try:
        return _object(json.loads(path.read_text(encoding="utf-8")), str(path))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ChinualImportError(f"cannot read JSON evidence: {path}") from error


def _load_bytes(path: Path) -> tuple[Mapping[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise ChinualImportError(f"required evidence file is missing: {path}")
    try:
        content = path.read_bytes()
        value = _object(json.loads(content), str(path))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ChinualImportError(f"cannot read JSON evidence: {path}") from error
    return value, content


def _contained(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str):
        raise ChinualImportError(f"{label} path must be a relative string")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ChinualImportError(f"{label} path escapes its evidence root")
    if root.is_symlink() or not root.is_dir():
        raise ChinualImportError(f"{label} root must be a non-symlink directory")
    current = root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise ChinualImportError(f"{label} path must not traverse a symlink")
    resolved_root = root.resolve()
    resolved = current.resolve()
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise ChinualImportError(f"{label} path must name a file within its evidence root")
    return resolved


def _page_number(page_id: object) -> int:
    if not isinstance(page_id, str) or not page_id.startswith("page-"):
        raise ChinualImportError("review page ID is malformed")
    try:
        return int(page_id.rsplit("-", 1)[1])
    except ValueError as error:
        raise ChinualImportError("review page ID is malformed") from error


def _pages(manifest: Mapping[str, Any], label: str) -> dict[int, Mapping[str, Any]]:
    result: dict[int, Mapping[str, Any]] = {}
    for page in _array(manifest.get("pages"), f"{label}.pages"):
        page = _object(page, f"{label} page")
        number = page.get("page_number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ChinualImportError(f"{label} page needs a positive integer page_number")
        if number in result:
            raise ChinualImportError(f"{label} has duplicate page numbers")
        result[number] = page
    if not result:
        raise ChinualImportError(f"{label} has no pages")
    return result


def _annotation_pages(value: Mapping[str, Any], revision: str) -> dict[str, Mapping[str, Any]]:
    """Normalize both saved review annotation encodings without guessing fields."""
    annotations = _object(value.get("annotations"), f"{revision}.annotations")
    pages = annotations.get("pages")
    if isinstance(pages, dict):
        entries: list[tuple[object, object]] = list(pages.items())
    elif isinstance(pages, list):
        entries = []
        for item in pages:
            item = _object(item, f"{revision} annotation page")
            entries.append((item.get("key"), item.get("value")))
    else:
        raise ChinualImportError(f"{revision} annotations.pages must be an object or array")
    result: dict[str, Mapping[str, Any]] = {}
    for page_id, page in entries:
        if not isinstance(page_id, str) or page_id in result:
            raise ChinualImportError(f"{revision} annotations have malformed or duplicate page IDs")
        result[page_id] = _object(page, f"{revision} annotation {page_id}")
    return result


def _bad(disposition: object) -> bool:
    return disposition in {"needs-fix", "reject"}


def _review_status(
    *, review: Mapping[str, Any], annotations: Mapping[str, Any], revision: str
) -> dict[str, bool]:
    project = annotations.get("project_sha256")
    _digest(project, f"{revision} annotations.project_sha256")
    review_pages: dict[str, Mapping[str, Any]] = {}
    for page in _array(review.get("pages"), f"{revision} review.pages"):
        page = _object(page, f"{revision} review page")
        page_id = page.get("id")
        if not isinstance(page_id, str) or page_id in review_pages:
            raise ChinualImportError(f"{revision} review has malformed or duplicate page IDs")
        review_pages[page_id] = page
    result: dict[str, bool] = {}
    for page_id, annotation in _annotation_pages(annotations, revision).items():
        page = review_pages.get(page_id)
        if page is None:
            raise ChinualImportError(f"{revision} annotations name a page absent from its review")
        disposition = annotation.get("disposition")
        if disposition not in {"accept", "needs-fix", "reject"}:
            raise ChinualImportError(f"{revision} page {page_id} has invalid disposition")
        region_ids = {
            region.get("id")
            for region in _array(page.get("regions"), f"{revision} review regions")
            if isinstance(region, dict) and isinstance(region.get("id"), str)
        }
        if len(region_ids) != len(_array(page.get("regions"), f"{revision} review regions")):
            raise ChinualImportError(
                f"{revision} review {page_id} has malformed or duplicate regions"
            )
        regions = _object(annotation.get("regions", {}), f"{revision} annotation regions")
        if set(regions) - region_ids:
            raise ChinualImportError(f"{revision} annotations name regions absent from its review")
        region_bad = False
        for region_id, region_annotation in regions.items():
            region_annotation = _object(region_annotation, f"{revision} annotation {region_id}")
            state = region_annotation.get("disposition")
            if state is not None and state not in {"accept", "needs-fix", "reject"}:
                raise ChinualImportError(f"{revision} region {region_id} has invalid disposition")
            region_bad |= _bad(state)
        result[page_id] = disposition == "accept" and not _bad(disposition) and not region_bad
    return result


def _verify_assets(
    root: Path, review: Mapping[str, Any], pages: Mapping[int, Mapping[str, Any]], revision: str
) -> None:
    assets = _object(review.get("assets"), f"{revision}.assets")
    for review_page in _array(review.get("pages"), f"{revision}.pages"):
        review_page = _object(review_page, f"{revision} review page")
        number = _page_number(review_page.get("id"))
        manifest_page = pages.get(number)
        if manifest_page is None:
            raise ChinualImportError(f"{revision} review page is absent from its manifest")
        for asset_field, manifest_field in (
            ("reference_asset_id", "scan_sha256"),
            ("generated_asset_id", "replica_sha256"),
        ):
            asset_id = review_page.get(asset_field)
            if not isinstance(asset_id, str):
                raise ChinualImportError(f"{revision} review has invalid {asset_field}")
            asset = _object(assets.get(asset_id), f"{revision} asset {asset_id!r}")
            digest = _digest(asset.get("sha256"), f"{revision} asset digest")
            if digest != _digest(
                manifest_page.get(manifest_field), f"{revision} manifest {manifest_field}"
            ):
                raise ChinualImportError(
                    f"{revision} review asset does not bind its manifest bytes"
                )
            if _sha256_path(_contained(root, asset.get("path"), f"{revision} asset")) != digest:
                raise ChinualImportError(f"{revision} review asset bytes do not match their digest")


def _verify_mapping_assets(
    root: Path,
    review: Mapping[str, Any],
    final_pages: Mapping[int, Mapping[str, Any]],
    revision: str,
) -> None:
    assets = _object(review.get("assets"), f"{revision}.assets")
    for page in _array(review.get("pages"), f"{revision}.pages"):
        page = _object(page, f"{revision} page")
        number = _page_number(page.get("id"))
        final = final_pages.get(number)
        if final is None:
            raise ChinualImportError(f"{revision} maps a page absent from final r33")
        for field in ("reference_asset_id", "generated_asset_id"):
            asset_id = page.get(field)
            if not isinstance(asset_id, str):
                raise ChinualImportError(f"{revision} mapping has invalid {field}")
            asset = _object(assets.get(asset_id), f"{revision} asset {asset_id!r}")
            digest = _digest(asset.get("sha256"), f"{revision} asset digest")
            if digest != _digest(final.get("scan_sha256"), "final scan digest"):
                raise ChinualImportError(f"{revision} mapping scan does not match final r33")
            if _sha256_path(_contained(root, asset.get("path"), f"{revision} asset")) != digest:
                raise ChinualImportError(
                    f"{revision} mapping asset bytes do not match their digest"
                )


def _tags(regions: Iterable[ChinualRegionRecord]) -> tuple[str, ...]:
    values = list(regions)
    tags = {"clean-scanned-prose"}
    if any(item.kind == "code" for item in values):
        tags.add("code-terminal")
    if any(item.has_table_semantics for item in values):
        tags.add("table")
    if any(any(character in _UNUSUAL_GLYPHS for character in item.literal_text) for item in values):
        tags.add("math-unusual-glyph")
    return tuple(sorted(tags))


@dataclass(frozen=True, slots=True)
class ChinualRegionRecord:
    region_id: str
    kind: str
    literal_text: str
    source_path: str
    start_line: int
    end_line: int
    stored_text_sha256: str
    extracted_text_sha256: str
    disposition: str
    has_table_semantics: bool


@dataclass(frozen=True, slots=True)
class ChinualPageRecord:
    page_number: int
    queue_page: QueuePage
    disposition: str
    regions: tuple[ChinualRegionRecord, ...]
    evidence_gaps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChinualRecoveredImport:
    """Deterministic source records plus the Wave-1-compatible selection queue."""

    records: tuple[ChinualPageRecord, ...]
    review_digests: tuple[tuple[str, str, str], ...]
    evidence_gaps: tuple[str, ...]
    final_manifest_sha256: str
    final_manifest_bytes: bytes

    @property
    def queue_pages(self) -> tuple[QueuePage, ...]:
        return tuple(record.queue_page for record in self.records)

    @property
    def authoritative_pages(self) -> tuple[ChinualPageRecord, ...]:
        return tuple(record for record in self.records if record.disposition == "authoritative")

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_gaps": list(self.evidence_gaps),
            "final_manifest_sha256": self.final_manifest_sha256,
            "pages": [
                {
                    "disposition": item.disposition,
                    "evidence_gaps": list(item.evidence_gaps),
                    "page_number": item.page_number,
                    "queue_page": item.queue_page.to_dict(),
                    "regions": [
                        {
                            "disposition": region.disposition,
                            "end_line": region.end_line,
                            "extracted_text_sha256": region.extracted_text_sha256,
                            "has_table_semantics": region.has_table_semantics,
                            "kind": region.kind,
                            "literal_text": region.literal_text,
                            "region_id": region.region_id,
                            "source_path": region.source_path,
                            "start_line": region.start_line,
                            "stored_text_sha256": region.stored_text_sha256,
                        }
                        for region in item.regions
                    ],
                }
                for item in self.records
            ],
            "review_digests": [
                {"annotations_sha256": annotations, "project_sha256": project, "revision": revision}
                for revision, project, annotations in self.review_digests
            ],
        }


def import_chinual_recovered_slice(project_root: Path) -> ChinualRecoveredImport:
    """Import the accepted 20-page slice, rejecting any stale or incomplete chain.

    ``authoritative`` means final review evidence and fresh Bolio text agree.
    ``provisional`` means all byte/review gates passed but at least one stored
    region digest disagreed with that fresh extraction.
    """
    root = project_root.resolve()
    if project_root.is_symlink() or not root.is_dir():
        raise ChinualImportError("project root must be a non-symlink directory")
    slice_root = root / "work/chinual-slice"
    final_manifest_path = _contained(
        root,
        "work/chinual-slice/replica-review-r33/replica-manifest.json",
        "final r33 manifest",
    )
    final_manifest, final_manifest_bytes = _load_bytes(final_manifest_path)
    final_manifest_sha256 = _sha256_bytes(final_manifest_bytes)
    final_pages = _pages(final_manifest, "r33 manifest")
    source_root = root / "source-material/reference-transcriptions/unlambda/extracted/lmman/orig4ed"
    input_paths = {
        "source_pdf_sha256": root
        / "source-material/bitsavers/pdf/mit/cadr/chinual_4thEd_Jul81.pdf",
        "manual_vars_sha256": source_root / "manual.vars",
        "proposals_sha256": slice_root / "source-alignment-proposals-v7.json",
        "layout_results_sha256": slice_root / "surya/pages/results.json",
    }
    for field, label in _DIGEST_FIELDS:
        expected = _digest(final_manifest.get(field), f"final {field}")
        if _sha256_path(input_paths[field]) != expected:
            raise ChinualImportError(f"final {label} bytes do not match r33 manifest")
    source_files = _object(final_manifest.get("source_files"), "final source_files")
    for relative, expected in sorted(source_files.items()):
        if not isinstance(relative, str) or _sha256_path(
            _contained(source_root, relative, "source file")
        ) != _digest(expected, "source file digest"):
            raise ChinualImportError(
                f"source file bytes do not match final r33 manifest: {relative}"
            )

    review_digests: list[tuple[str, str, str]] = []
    mapping_status: dict[str, bool] = {}
    for revision, directory in _MAPPING_REVISIONS:
        review_path = slice_root / directory / "review-project.json"
        annotations_path = slice_root / directory / "review-project.annotations.json"
        review, annotations = _load(review_path), _load(annotations_path)
        if annotations.get("project_sha256") != _sha256_path(review_path):
            raise ChinualImportError(f"{revision} annotations do not bind their review project")
        _verify_mapping_assets(review_path.parent, review, final_pages, revision)
        mapping_status.update(
            _review_status(review=review, annotations=annotations, revision=revision)
        )
        review_digests.append((revision, _sha256_path(review_path), _sha256_path(annotations_path)))
    expected_ids = {f"page-{number:06d}" for number in final_pages}
    if {page for page, accepted in mapping_status.items() if accepted} != expected_ids:
        raise ChinualImportError("mapping review chain leaves pages without accepted mapping")

    layout_status: dict[str, str] = {}
    for revision, directory, review_name, annotations_name in _LAYOUT_REVISIONS:
        directory_root = slice_root / directory
        manifest = _load(directory_root / "replica-manifest.json")
        revision_pages = _pages(manifest, f"{revision} manifest")
        if set(revision_pages) != set(final_pages):
            raise ChinualImportError(f"{revision} manifest page set differs from final r33")
        if manifest.get("source_pdf_sha256") != final_manifest.get("source_pdf_sha256"):
            raise ChinualImportError(f"{revision} manifest source PDF differs from final r33")
        for number, page in revision_pages.items():
            if page.get("scan_sha256") != final_pages[number].get("scan_sha256"):
                raise ChinualImportError(
                    f"{revision} scan differs from final r33 for page {number}"
                )
        review_path, annotations_path = (
            directory_root / review_name,
            directory_root / annotations_name,
        )
        review, annotations = _load(review_path), _load(annotations_path)
        if annotations.get("project_sha256") != _sha256_path(review_path):
            raise ChinualImportError(f"{revision} annotations do not bind their review project")
        _verify_assets(directory_root, review, revision_pages, revision)
        for page_id, accepted in _review_status(
            review=review, annotations=annotations, revision=revision
        ).items():
            layout_status[page_id] = revision if accepted else ""
        review_digests.append((revision, _sha256_path(review_path), _sha256_path(annotations_path)))
    if set(page for page, revision in layout_status.items() if revision) != expected_ids:
        raise ChinualImportError("layout review chain leaves pages without acceptance")
    for page_id, revision in layout_status.items():
        if revision and _pages(
            _load(
                slice_root
                / dict((r, d) for r, d, _, _ in _LAYOUT_REVISIONS)[revision]
                / "replica-manifest.json"
            ),
            revision,
        )[_page_number(page_id)].get("replica_sha256") != final_pages[_page_number(page_id)].get(
            "replica_sha256"
        ):
            raise ChinualImportError(f"{page_id} accepted replica differs from final r33 bytes")

    variables = (source_root / "manual.vars").read_text(encoding="utf-8")
    source_cache: dict[str, tuple[str, Any]] = {}
    records: list[ChinualPageRecord] = []
    gaps: list[str] = []
    source_pdf_digest = _digest(final_manifest.get("source_pdf_sha256"), "source PDF digest")
    for number, page in sorted(final_pages.items()):
        regions: list[ChinualRegionRecord] = []
        ids: list[str] = []
        page_gaps: list[str] = []
        for raw_region in _array(page.get("regions"), f"r33 page {number} regions"):
            raw_region = _object(raw_region, "r33 region")
            region_id, source_path = raw_region.get("region_id"), raw_region.get("source_path")
            kind = raw_region.get("kind")
            span = _array(raw_region.get("source_span"), "r33 region source_span")
            bbox = _array(raw_region.get("bbox"), "r33 region bbox")
            if (
                not isinstance(region_id, str)
                or not region_id
                or not isinstance(source_path, str)
                or kind not in _REGION_KINDS
                or len(span) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) for value in span)
                or len(bbox) != 4
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float)) for value in bbox
                )
            ):
                raise ChinualImportError("r33 region has malformed identity or source span")
            if region_id in ids:
                raise ChinualImportError(f"r33 page {number} has duplicate region IDs")
            ids.append(region_id)
            if source_path not in source_files:
                raise ChinualImportError("r33 region cites a source file absent from source_files")
            if source_path not in source_cache:
                source_text = _contained(source_root, source_path, "region source").read_text(
                    encoding="utf-8"
                )
                try:
                    source_cache[source_path] = (source_text, extract_bolio(source_text, variables))
                except (UnicodeDecodeError, BolioError) as error:
                    raise ChinualImportError(
                        f"cannot extract recovered Bolio source: {source_path}"
                    ) from error
            source_text, extraction = source_cache[source_path]
            try:
                literal = render_bolio_interval(
                    extraction, source_text, start_line=span[0], end_line=span[1]
                )
            except BolioError as error:
                raise ChinualImportError(
                    f"cannot render cited Bolio span for page {number}/{region_id}"
                ) from error
            if not literal:
                raise ChinualImportError(f"cited Bolio span is empty for page {number}/{region_id}")
            stored = _digest(raw_region.get("text_sha256"), "r33 region text_sha256")
            extracted = _sha256_bytes(literal.encode("utf-8"))
            matched = stored == extracted
            if not matched:
                page_gaps.append(
                    f"{region_id}: final manifest text digest disagrees with fresh Bolio extraction"
                )
            block_kinds = {
                block.kind
                for block in extraction.blocks
                if block.span.start_line <= span[1] and span[0] <= block.span.end_line
            }
            regions.append(
                ChinualRegionRecord(
                    region_id,
                    kind,
                    literal,
                    source_path,
                    span[0],
                    span[1],
                    stored,
                    extracted,
                    "authoritative" if matched else "provisional",
                    "list-item" in block_kinds,
                )
            )
        queue = QueuePage(
            source_pdf_digest,
            number - 1,
            _digest(page.get("scan_sha256"), "final scan_sha256"),
            "recovered-typesetter-source",
            _tags(regions),
            tuple(ids),
        )
        disposition = "authoritative" if not page_gaps else "provisional"
        records.append(
            ChinualPageRecord(number, queue, disposition, tuple(regions), tuple(page_gaps))
        )
        gaps.extend(f"page {number}: {gap}" for gap in page_gaps)
    return ChinualRecoveredImport(
        tuple(records),
        tuple(review_digests),
        tuple(gaps),
        final_manifest_sha256,
        final_manifest_bytes,
    )
