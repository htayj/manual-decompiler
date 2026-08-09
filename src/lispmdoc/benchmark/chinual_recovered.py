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
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .bolio import BolioError, apply_section_numbers, extract_bolio, render_bolio_interval
from .bolio_counters import (
    BolioCounterError,
    SectionNumberProof,
    derive_ti4ed_section_numbers_from_buffers,
    verify_ti4ed_counter_receipt,
)
from .source_selectors import (
    SourceSelector,
    SourceSelectorError,
    load_source_selector_overlay,
    select_source_text,
    selector_overlay_sha256,
)
from .wave1 import QueuePage
from .whitespace_projection import (
    ProjectionReceipt,
    ProjectionSubject,
    WhitespaceProjectionError,
    read_contained_overlay,
    sha256_bytes,
    validate_overlay,
)


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
    return _sha256_bytes(_read_evidence_bytes(path))


def _read_evidence_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ChinualImportError(f"required evidence file is missing: {path}")
    try:
        return path.read_bytes()
    except OSError as error:
        raise ChinualImportError(f"cannot read required evidence file: {path}") from error


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
class ChinualR33ReviewRegionEvidence:
    """Final r33 canonical text plus explicitly unbound review-project witnesses."""

    page_number: int
    region_id: str
    canonical_text: str
    source_text: str
    ocr_text: str


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
    final_review_sha256: str
    final_review_regions: tuple[ChinualR33ReviewRegionEvidence, ...]
    section_counter_order_sha256: str
    section_counter_ti_script_sha256: str
    section_counter_manual_vars_sha256: str
    section_counter_receipt_sha256: str
    applied_section_proofs: tuple[SectionNumberProof, ...]
    source_selector_overlay_sha256: str
    applied_source_selectors: tuple[SourceSelector, ...]
    whitespace_overlay_sha256: str
    applied_whitespace_receipts: tuple[ProjectionReceipt, ...]

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
            "final_review_sha256": self.final_review_sha256,
            "section_counter": {
                "manual_vars_sha256": self.section_counter_manual_vars_sha256,
                "order_sha256": self.section_counter_order_sha256,
                "receipt_sha256": self.section_counter_receipt_sha256,
                "ti_script_sha256": self.section_counter_ti_script_sha256,
                "applied_proofs": [proof.to_dict() for proof in self.applied_section_proofs],
            },
            "source_selector_overlay": {
                "applied_selectors": [
                    selector.identity() for selector in self.applied_source_selectors
                ],
                "sha256": self.source_selector_overlay_sha256,
            },
            "whitespace_projection_overlay": {
                "applied_receipts": [
                    receipt.to_dict() for receipt in self.applied_whitespace_receipts
                ],
                "sha256": self.whitespace_overlay_sha256,
            },
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


def _final_review_regions(
    review: Mapping[str, Any], final_pages: Mapping[int, Mapping[str, Any]]
) -> tuple[ChinualR33ReviewRegionEvidence, ...]:
    """Bind every manifest string claim to the final r33 review text.

    The manifest contains only a text digest.  The review project is the
    recoverable byte source for that claim, so accepting either artifact alone
    would leave a gap between the final canonical text and source-derived truth.
    """

    pages: dict[int, Mapping[str, Any]] = {}
    for raw_page in _array(review.get("pages"), "r33 final review.pages"):
        page = _object(raw_page, "r33 final review page")
        number = _page_number(page.get("id"))
        if number in pages:
            raise ChinualImportError("r33 final review has duplicate page IDs")
        pages[number] = page
    if set(pages) != set(final_pages):
        raise ChinualImportError("r33 final review page set differs from final manifest")

    evidence: list[ChinualR33ReviewRegionEvidence] = []
    for number, manifest_page in sorted(final_pages.items()):
        manifest_regions = {
            region.get("region_id"): region
            for region in _array(manifest_page.get("regions"), f"r33 page {number} regions")
            if isinstance(region, dict) and isinstance(region.get("region_id"), str)
        }
        if len(manifest_regions) != len(
            _array(manifest_page.get("regions"), f"r33 page {number} regions")
        ):
            raise ChinualImportError(f"r33 page {number} has malformed or duplicate regions")
        reviewed_regions: dict[str, Mapping[str, Any]] = {}
        for raw_region in _array(pages[number].get("regions"), f"r33 final review page {number}"):
            region = _object(raw_region, "r33 final review region")
            region_id = region.get("id")
            if not isinstance(region_id, str) or not region_id or region_id in reviewed_regions:
                raise ChinualImportError(f"r33 final review page {number} has malformed regions")
            reviewed_regions[region_id] = region
        if set(reviewed_regions) != set(manifest_regions):
            raise ChinualImportError(
                f"r33 final review regions differ from final manifest on page {number}"
            )
        for region_id, reviewed in sorted(reviewed_regions.items()):
            canonical, source, ocr = (
                reviewed.get("canonical_text"),
                reviewed.get("source_text"),
                reviewed.get("ocr_text"),
            )
            if (
                not isinstance(canonical, str)
                or not isinstance(source, str)
                or not isinstance(ocr, str)
            ):
                raise ChinualImportError(
                    f"r33 final review {number}/{region_id} lacks recoverable text evidence"
                )
            manifest_digest = _digest(
                manifest_regions[region_id].get("text_sha256"), "r33 region text_sha256"
            )
            if _sha256_bytes(canonical.encode("utf-8")) != manifest_digest:
                raise ChinualImportError(
                    f"r33 final review text does not match manifest digest for {number}/{region_id}"
                )
            evidence.append(
                ChinualR33ReviewRegionEvidence(number, region_id, canonical, source, ocr)
            )
    return tuple(evidence)


def import_chinual_recovered_slice(project_root: Path) -> ChinualRecoveredImport:
    """Import the accepted 20-page slice, rejecting any stale or incomplete chain.

    Fresh Bolio text is the semantic channel.  A stored r33 text disagreement
    is authoritative only when the digest-bound whitespace overlay validates
    that stored text as a permitted physical projection of that fresh text.
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
    try:
        selector_overlay_value, selector_overlay_bytes = _load_bytes(
            _contained(
                root,
                "config/benchmarks/chinual-source-selector-overlay.json",
                "source selector overlay",
            )
        )
        selector_overlay = load_source_selector_overlay(
            selector_overlay_value, manifest_sha256=final_manifest_sha256
        )
    except SourceSelectorError as error:
        raise ChinualImportError("cannot load digest-bound source selector overlay") from error
    source_selector_overlay_digest = selector_overlay_sha256(selector_overlay_bytes)
    final_review_path = slice_root / "replica-review-r33/review-project.json"
    final_review, final_review_bytes = _load_bytes(final_review_path)
    final_review_sha256 = _sha256_bytes(final_review_bytes)
    _verify_assets(final_review_path.parent, final_review, final_pages, "r33 final review")
    final_review_regions = _final_review_regions(final_review, final_pages)
    source_root = root / "source-material/reference-transcriptions/unlambda/extracted/lmman/orig4ed"
    input_paths = {
        "source_pdf_sha256": root
        / "source-material/bitsavers/pdf/mit/cadr/chinual_4thEd_Jul81.pdf",
        "manual_vars_sha256": source_root / "manual.vars",
        "proposals_sha256": slice_root / "source-alignment-proposals-v7.json",
        "layout_results_sha256": slice_root / "surya/pages/results.json",
    }
    input_buffers: dict[str, bytes] = {}
    for field, label in _DIGEST_FIELDS:
        expected = _digest(final_manifest.get(field), f"final {field}")
        content = _read_evidence_bytes(input_paths[field])
        if _sha256_bytes(content) != expected:
            raise ChinualImportError(f"final {label} bytes do not match r33 manifest")
        input_buffers[field] = content
    source_files = _object(final_manifest.get("source_files"), "final source_files")
    source_buffers: dict[str, bytes] = {}
    for relative, expected in sorted(source_files.items()):
        if not isinstance(relative, str):
            raise ChinualImportError("source file bytes do not match final r33 manifest")
        content = _read_evidence_bytes(_contained(source_root, relative, "source file"))
        if _sha256_bytes(content) != _digest(expected, "source file digest"):
            raise ChinualImportError(
                f"source file bytes do not match final r33 manifest: {relative}"
            )
        source_buffers[relative] = content

    try:
        counters = derive_ti4ed_section_numbers_from_buffers(
            source_root.parent,
            "fd_num.77",
            manual_vars=input_buffers["manual_vars_sha256"],
            source_buffers=source_buffers,
            allow_unbuffered_sources=True,
        )
        counter_receipt, counter_receipt_bytes = _load_bytes(
            _contained(
                root,
                "config/benchmarks/chinual-ti4ed-counter-receipt.json",
                "ti-4ed counter receipt",
            )
        )
        verify_ti4ed_counter_receipt(counters, counter_receipt)
    except BolioCounterError as error:
        raise ChinualImportError("cannot derive digest-bound ti-4ed section counters") from error
    counter_receipt_sha256 = _sha256_bytes(counter_receipt_bytes)
    for counter_source in counters.sources:
        buffered = source_buffers.get(counter_source.path)
        manifest_expected = source_files.get(counter_source.path)
        if buffered is None and manifest_expected is None:
            continue
        if (
            buffered is None
            or manifest_expected is None
            or _sha256_bytes(buffered)
            != _digest(manifest_expected, "counter source manifest digest")
            or _sha256_bytes(buffered) != counter_source.sha256
        ):
            raise ChinualImportError(
                f"counter source is not identically bound to final manifest: {counter_source.path}"
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

    try:
        variables = input_buffers["manual_vars_sha256"].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ChinualImportError("cannot decode digest-bound manual.vars") from error
    source_cache: dict[str, tuple[str, Any, str]] = {}
    records: list[ChinualPageRecord] = []
    projection_subjects: list[ProjectionSubject] = []
    review_by_key = {
        (item.page_number, item.region_id): item for item in final_review_regions
    }
    applied_section_proofs: list[SectionNumberProof] = []
    applied_source_selectors: list[SourceSelector] = []
    unused_source_selector_keys = {selector.key for selector in selector_overlay.selectors}
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
                source_bytes = source_buffers[source_path]
                try:
                    source_text = source_bytes.decode("utf-8")
                    source_cache[source_path] = (
                        source_text,
                        extract_bolio(source_text, variables),
                        _sha256_bytes(source_bytes),
                    )
                except (UnicodeDecodeError, BolioError) as error:
                    raise ChinualImportError(
                        f"cannot extract recovered Bolio source: {source_path}"
                    ) from error
            source_text, extraction, source_sha256 = source_cache[source_path]
            proof = counters.proof_for(source_path, span[0])
            rendered_extraction = extraction
            if proof is not None:
                if not proof.matches(
                    source_path=source_path, source_sha256=source_sha256, line=span[0]
                ):
                    raise ChinualImportError(
                        f"section-counter proof does not bind final source for {number}/{region_id}"
                    )
                if kind != "section" or span[0] != span[1]:
                    raise ChinualImportError(
                        "section-counter proof is not an exact manifest heading for "
                        f"{number}/{region_id}"
                    )
                existing = [
                    block
                    for block in extraction.blocks
                    if block.kind == "section" and block.span.start_line == proof.line
                ]
                if len(existing) != 1:
                    raise ChinualImportError(
                        f"section-counter proof has no exact Bolio heading for {number}/{region_id}"
                    )
                if existing[0].section_number is None:
                    try:
                        rendered_extraction = apply_section_numbers(
                            extraction, {proof.line: proof.number}
                        )
                    except BolioError as error:
                        raise ChinualImportError(
                            f"cannot apply section-counter proof for {number}/{region_id}"
                        ) from error
                    applied_section_proofs.append(proof)
                elif existing[0].section_number != proof.number:
                    raise ChinualImportError(
                        "section-counter proof conflicts with Bolio heading for "
                        f"{number}/{region_id}"
                    )
            try:
                literal = render_bolio_interval(
                    rendered_extraction, source_text, start_line=span[0], end_line=span[1]
                )
            except BolioError as error:
                raise ChinualImportError(
                    f"cannot render cited Bolio span for page {number}/{region_id}"
                ) from error
            if not literal:
                raise ChinualImportError(f"cited Bolio span is empty for page {number}/{region_id}")
            block_kinds = {
                block.kind
                for block in extraction.blocks
                if block.span.start_line <= span[1] and span[0] <= block.span.end_line
            }
            stored = _digest(raw_region.get("text_sha256"), "r33 region text_sha256")
            selector = selector_overlay.selector_for(number, region_id)
            if selector is not None:
                if (
                    selector.source_path != source_path
                    or selector.start_line != span[0]
                    or selector.end_line != span[1]
                    or selector.region_kind != kind
                ):
                    raise ChinualImportError(
                        f"source selector does not bind manifest span for {number}/{region_id}"
                    )
                try:
                    literal = select_source_text(
                        selector,
                        source_sha256=source_sha256,
                        source_text=source_text,
                        rendered_interval=literal,
                        region_kind=kind,
                        has_table_semantics="list-item" in block_kinds,
                    )
                except SourceSelectorError as error:
                    raise ChinualImportError(
                        f"cannot select exact source component for {number}/{region_id}"
                    ) from error
                if _sha256_bytes(literal.encode("utf-8")) != stored:
                    raise ChinualImportError(
                        "source selector output does not match final manifest for "
                        f"{number}/{region_id}"
                    )
                unused_source_selector_keys.remove(selector.key)
                applied_source_selectors.append(selector)
            extracted = _sha256_bytes(literal.encode("utf-8"))
            matched = stored == extracted
            if not matched:
                review_region = review_by_key.get((number, region_id))
                if review_region is None:
                    raise ChinualImportError(
                        f"r33 review omits text evidence for {number}/{region_id}"
                    )
                projection_subjects.append(
                    ProjectionSubject(
                        number, region_id, kind, literal, review_region.canonical_text
                    )
                )
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
    if unused_source_selector_keys:
        raise ChinualImportError(
            "source selector overlay has targets absent from final manifest: "
            f"{sorted(unused_source_selector_keys)!r}"
        )
    try:
        whitespace_overlay, whitespace_overlay_bytes = read_contained_overlay(
            root, Path("config/benchmarks/chinual-r33-whitespace-overlay.json")
        )
        whitespace_overlay_digest = sha256_bytes(whitespace_overlay_bytes)
        applied_whitespace_receipts = validate_overlay(
            whitespace_overlay,
            projection_subjects,
            r33_manifest_sha256=final_manifest_sha256,
            r33_review_sha256=final_review_sha256,
        )
    except WhitespaceProjectionError as error:
        raise ChinualImportError(
            "cannot validate digest-bound whitespace projection overlay"
        ) from error
    receipt_keys = {
        (receipt.page_number, receipt.region_id) for receipt in applied_whitespace_receipts
    }
    resolved_records: list[ChinualPageRecord] = []
    gaps = []
    for record in records:
        resolved_regions = tuple(
            ChinualRegionRecord(
                region.region_id,
                region.kind,
                region.literal_text,
                region.source_path,
                region.start_line,
                region.end_line,
                region.stored_text_sha256,
                region.extracted_text_sha256,
                (
                    "authoritative"
                    if region.stored_text_sha256 == region.extracted_text_sha256
                    or (record.page_number, region.region_id) in receipt_keys
                    else "provisional"
                ),
                region.has_table_semantics,
            )
            for region in record.regions
        )
        resolved_page_gaps = tuple(
            f"{region.region_id}: final manifest text digest disagrees with fresh Bolio extraction"
            for region in resolved_regions
            if region.disposition != "authoritative"
        )
        disposition = "authoritative" if not resolved_page_gaps else "provisional"
        resolved = ChinualPageRecord(
            record.page_number,
            record.queue_page,
            disposition,
            resolved_regions,
            resolved_page_gaps,
        )
        resolved_records.append(resolved)
        gaps.extend(f"page {record.page_number}: {gap}" for gap in resolved_page_gaps)
    return ChinualRecoveredImport(
        tuple(resolved_records),
        tuple(review_digests),
        tuple(gaps),
        final_manifest_sha256,
        final_manifest_bytes,
        final_review_sha256,
        final_review_regions,
        counters.order_sha256,
        counters.ti_script_sha256,
        counters.manual_vars_sha256,
        counter_receipt_sha256,
        tuple(applied_section_proofs),
        source_selector_overlay_digest,
        tuple(applied_source_selectors),
        whitespace_overlay_digest,
        applied_whitespace_receipts,
    )


_DERIVATION_CLASSIFICATION: Mapping[tuple[int, str], tuple[str, str]] = {
    # These are observations, not causal explanations.  Each key is usable
    # only if the evidence predicates below reproduce that observation.
    (93, "block-015"): ("source-span-not-exact", "canonical is a proper interval prefix"),
    (94, "block-001"): ("source-span-not-exact", "canonical is a proper interval suffix"),
    (94, "block-006"): ("source-span-not-exact", "directive role fragment differs from interval"),
    (94, "block-012"): ("source-span-not-exact", "canonical is a proper interval prefix"),
    (95, "block-001"): ("source-span-not-exact", "canonical is a proper interval suffix"),
    (96, "block-018"): ("source-span-not-exact", "canonical is a proper interval prefix"),
    (96, "block-019"): ("source-span-not-exact", "canonical is a proper interval suffix"),
    (96, "block-020"): ("source-span-not-exact", "directive role fragment differs from interval"),
    (106, "block-015"): ("source-span-not-exact", "directive role fragment differs from interval"),
    (91, "block-004"): (
        "section-number-absent-from-interval",
        "numbered canonical title differs from exact section title",
    ),
    (93, "block-013"): (
        "section-number-absent-from-interval",
        "numbered canonical title differs from exact section title",
    ),
    (98, "block-001"): (
        "section-number-absent-from-interval",
        "numbered canonical title differs from exact chapter title",
    ),
    (98, "block-002"): (
        "section-number-absent-from-interval",
        "numbered canonical title differs from exact section title",
    ),
    (99, "block-007"): (
        "section-number-absent-from-interval",
        "numbered canonical title differs from exact section title",
    ),
    (101, "block-001"): (
        "section-number-absent-from-interval",
        "numbered canonical title differs from exact section title",
    ),
    (101, "block-014"): (
        "section-number-absent-from-interval",
        "numbered canonical title differs from exact section title",
    ),
    (102, "block-001"): (
        "section-number-absent-from-interval",
        "numbered canonical title differs from exact section title",
    ),
    (104, "block-001"): (
        "section-number-absent-from-interval",
        "numbered canonical title differs from exact chapter title",
    ),
    (106, "block-003"): (
        "section-number-absent-from-interval",
        "numbered canonical title differs from exact section title",
    ),
    (107, "block-003"): (
        "section-number-absent-from-interval",
        "numbered canonical title differs from exact section title",
    ),
    (108, "block-002"): (
        "section-number-absent-from-interval",
        "numbered canonical title differs from exact section title",
    ),
    (92, "block-006"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (95, "block-002"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (95, "block-004"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (95, "block-005"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (95, "block-006"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (96, "block-002"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (96, "block-005"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (96, "block-006"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (96, "block-007"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (96, "block-010"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (96, "block-011"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (96, "block-012"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (104, "block-004"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (106, "block-017"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (107, "block-001"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (108, "block-001"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (108, "block-004"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (108, "block-005"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (108, "block-006"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
    (108, "block-007"): (
        "layout-whitespace-normalization",
        "canonical and interval agree only after layout-whitespace normalization",
    ),
}


def _mapping_evidence(
    root: Path, imported: ChinualRecoveredImport, page_number: int, region_id: str
) -> dict[str, dict[str, object]]:
    """Report only exact mapping identities, bound to each review/annotation pair.

    Mapping-review regions use independent ``mapping-*`` IDs.  In particular,
    a substring in a page-wide mapping region is *not* evidence that an r33
    ``block-*`` region was reviewed.  The report makes that absence explicit.
    """

    expected = {
        revision: (project, annotations)
        for revision, project, annotations in imported.review_digests
    }
    output: dict[str, dict[str, object]] = {}
    page_id = f"page-{page_number:06d}"
    for revision, directory in _MAPPING_REVISIONS:
        directory_root = root / "work/chinual-slice" / directory
        review_path = directory_root / "review-project.json"
        annotations_path = directory_root / "review-project.annotations.json"
        review, review_bytes = _load_bytes(review_path)
        annotations, annotation_bytes = _load_bytes(annotations_path)
        expected_digests = expected.get(revision)
        if (
            expected_digests is None
            or (
                _sha256_bytes(review_bytes),
                _sha256_bytes(annotation_bytes),
            )
            != expected_digests
        ):
            raise ChinualImportError(f"{revision} changed after recovered import verification")
        if annotations.get("project_sha256") != _sha256_bytes(review_bytes):
            raise ChinualImportError(f"{revision} annotations do not bind their review project")
        accepted_pages = _review_status(review=review, annotations=annotations, revision=revision)
        review_pages = {
            _page_number(page.get("id")): page
            for raw_page in _array(review.get("pages"), f"{revision}.pages")
            for page in [_object(raw_page, f"{revision} page")]
        }
        annotation_pages = _annotation_pages(annotations, revision)
        review_page = review_pages.get(page_number)
        annotation = annotation_pages.get(page_id)
        mapping_regions: dict[str, Mapping[str, Any]] = {}
        if review_page is not None:
            for raw_region in _array(review_page.get("regions"), f"{revision} regions"):
                mapping_region = _object(raw_region, f"{revision} mapping region")
                mapping_id = mapping_region.get("id")
                if (
                    not isinstance(mapping_id, str)
                    or not mapping_id
                    or mapping_id in mapping_regions
                ):
                    raise ChinualImportError(f"{revision} has malformed mapping region IDs")
                mapping_regions[mapping_id] = mapping_region
        region_annotation: Mapping[str, Any] | None = None
        if annotation is not None:
            annotation_regions = _object(annotation.get("regions", {}), f"{revision} regions")
            raw_region_annotation = annotation_regions.get(region_id)
            if raw_region_annotation is not None:
                region_annotation = _object(raw_region_annotation, f"{revision} region annotation")
        exact_region = mapping_regions.get(region_id)
        page_disposition = annotation.get("disposition") if annotation is not None else "absent"
        region_disposition = (
            region_annotation.get("disposition", "not-annotated")
            if exact_region is not None and region_annotation is not None
            else "absent"
        )
        output[revision] = {
            "annotation_sha256": _sha256_bytes(annotation_bytes),
            "annotation_sha256_matches_imported_chain": True,
            "annotation_project_sha256_matches_review": True,
            "exact_region": {
                "accepted": bool(
                    exact_region is not None
                    and region_annotation is not None
                    and region_disposition == "accept"
                    and accepted_pages.get(page_id, False)
                ),
                "disposition": region_disposition,
                "present": exact_region is not None,
            },
            "page": {
                "accepted": bool(accepted_pages.get(page_id, False)),
                "annotation_present": annotation is not None,
                "disposition": page_disposition,
                "review_present": review_page is not None,
            },
            "review_sha256": _sha256_bytes(review_bytes),
        }
    return output


def _layout_normalize(value: str) -> str:
    """Explicitly erase only layout whitespace for a semantic-text comparison."""

    return re.sub(r"\s+", " ", value).strip()


def _token_similarity(left: str, right: str) -> float:
    """OCR support witness: ordered non-whitespace token similarity."""

    return SequenceMatcher(None, _layout_normalize(left), _layout_normalize(right)).ratio()


def _has_structural_layout_signal(lines: tuple[str, ...], kind: str) -> bool:
    """Require source structure, not merely arbitrary whitespace, for this label."""

    if kind == "code":
        return True
    structural = {
        ".defspec",
        ".defun",
        ".defun1",
        ".exdent",
        ".item",
        ".kitem",
        ".lisp",
        ".table",
    }
    return any(
        any(line.lstrip().lower().startswith(prefix) for prefix in structural) for line in lines
    )


def _source_span_lines(
    root: Path, region: ChinualRegionRecord, source_files: Mapping[str, Any]
) -> tuple[str, ...]:
    source_root = root / "source-material/reference-transcriptions/unlambda/extracted/lmman/orig4ed"
    path = _contained(source_root, region.source_path, "diagnostic source")
    expected = _digest(source_files.get(region.source_path), "diagnostic source digest")
    content = path.read_bytes()
    if _sha256_bytes(content) != expected:
        raise ChinualImportError(
            f"diagnostic source changed after recovered import: {region.source_path}"
        )
    # SAIL glyph controls such as 0x1c/0x1d are visible source characters to
    # Bolio, but ``str.splitlines`` misclassifies them as Unicode line breaks.
    lines = content.decode("utf-8").split("\n")
    return tuple(lines[region.start_line - 1 : region.end_line])


def _section_directive_title(lines: tuple[str, ...]) -> str | None:
    if len(lines) != 1:
        return None
    match = re.fullmatch(r"\.(?:chapter|section)\s+(.+)", lines[0].strip(), flags=re.IGNORECASE)
    if match is None:
        return None
    title = match.group(1).strip()
    return title[1:-1] if len(title) >= 2 and title[0] == title[-1] == '"' else title


def _classify_disagreement(
    *,
    key: tuple[int, str],
    region: ChinualRegionRecord,
    stored: str,
    scan_ocr_witness: str,
    source_lines: tuple[str, ...],
    same_span_count: int,
) -> tuple[str, str, dict[str, object]]:
    """Apply the ledger's category only when its independent evidence holds."""

    entry = _DERIVATION_CLASSIFICATION.get(key)
    if entry is None:
        return (
            "unresolved",
            "new disagreement is not in the reviewed ledger",
            {"ledger_entry": False},
        )
    intended, reason = entry
    if intended == "section-number-absent-from-interval":
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)*\.?)\s+(.+)", stored, flags=re.DOTALL)
        title = _section_directive_title(source_lines)
        predicates: dict[str, object] = {
            "canonical_has_numeric_prefix": match is not None,
            "fresh_is_numeric_suffix": match is not None and match.group(2) == region.literal_text,
            "region_kind_is_section": region.kind == "section",
            "source_heading_directive_matches_fresh": title == region.literal_text,
        }
    elif intended == "layout-whitespace-normalization":
        similarity = _token_similarity(scan_ocr_witness, stored)
        predicates = {
            "canonical_fresh_layout_normalized_equal": (
                _layout_normalize(stored) == _layout_normalize(region.literal_text)
            ),
            "normalization": "collapse-all-unicode-whitespace",
            "scan_canonical_token_similarity": similarity,
            "scan_canonical_token_similarity_at_least_0_90": similarity >= 0.90,
            "source_has_structural_layout_signal": _has_structural_layout_signal(
                source_lines, region.kind
            ),
        }
    elif intended == "source-span-not-exact":
        normalized_stored, normalized_fresh = (
            _layout_normalize(stored),
            _layout_normalize(region.literal_text),
        )
        proper_fragment = normalized_stored != normalized_fresh and (
            normalized_fresh.startswith(normalized_stored)
            or normalized_fresh.endswith(normalized_stored)
        )
        defspec = len(source_lines) == 1 and source_lines[0].lstrip().lower().startswith(
            ".defspec "
        )
        role_fragment = defspec and stored == "Special Form"
        split_fragment = defspec and same_span_count > 1 and proper_fragment
        predicates = {
            "directive_role_fragment": role_fragment,
            "proper_prefix_or_suffix": proper_fragment,
            "same_span_region_count": same_span_count,
            "split_directive_fragment": split_fragment,
        }
    else:
        raise AssertionError(f"unknown diagnosis category {intended!r}")
    passed = any(
        value is True
        for name, value in predicates.items()
        if name
        in {
            "source_heading_directive_matches_fresh",
            "scan_canonical_token_similarity_at_least_0_90",
            "directive_role_fragment",
            "proper_prefix_or_suffix",
            "split_directive_fragment",
        }
    )
    if intended == "section-number-absent-from-interval":
        passed = all(bool(value) for value in predicates.values())
    elif intended == "layout-whitespace-normalization":
        passed = (
            bool(predicates["canonical_fresh_layout_normalized_equal"])
            and bool(predicates["scan_canonical_token_similarity_at_least_0_90"])
            and bool(predicates["source_has_structural_layout_signal"])
        )
    if not passed:
        return "unresolved", f"{intended} ledger entry lacks required evidence", predicates
    return intended, reason, predicates


def diagnose_chinual_derivation_disagreements(project_root: Path) -> dict[str, object]:
    """Classify every final r33 text disagreement without trusting stored text.

    It starts with the ordinary fail-closed importer, which verifies the whole
    review/source chain and binds manifest text digests to final r33 review
    strings.  The report retains the digest-bound final canonical string,
    unbound ``source_text``/``ocr_text`` witnesses, and fresh Bolio text for
    each mismatch. Unexpected new mismatches are explicitly ``unresolved``.
    """

    imported = import_chinual_recovered_slice(project_root)
    root = project_root.resolve()
    manifest = _object(json.loads(imported.final_manifest_bytes), "buffered final r33 manifest")
    source_files = _object(manifest.get("source_files"), "buffered final source_files")
    review = {(item.page_number, item.region_id): item for item in imported.final_review_regions}
    same_span_counts: dict[tuple[str, int, int], int] = {}
    for page in imported.records:
        for region in page.regions:
            key = (region.source_path, region.start_line, region.end_line)
            same_span_counts[key] = same_span_counts.get(key, 0) + 1
    receipt_by_key = {
        (receipt.page_number, receipt.region_id): receipt
        for receipt in imported.applied_whitespace_receipts
    }
    rows: list[dict[str, object]] = []
    projections: list[dict[str, object]] = []
    for page in imported.records:
        for region in page.regions:
            if region.stored_text_sha256 == region.extracted_text_sha256:
                continue
            evidence = review[(page.page_number, region.region_id)]
            receipt = receipt_by_key.get((page.page_number, region.region_id))
            if receipt is not None:
                projections.append(
                    {
                        "fresh_bolio_interval": region.literal_text,
                        "fresh_bolio_sha256": region.extracted_text_sha256,
                        "page_number": page.page_number,
                        "physical_r33_text": evidence.canonical_text,
                        "physical_r33_text_sha256": region.stored_text_sha256,
                        "receipt": receipt.to_dict(),
                        "region_id": region.region_id,
                    }
                )
                continue
            source_lines = _source_span_lines(root, region, source_files)
            category, reason, predicates = _classify_disagreement(
                key=(page.page_number, region.region_id),
                region=region,
                stored=evidence.canonical_text,
                scan_ocr_witness=evidence.ocr_text,
                source_lines=source_lines,
                same_span_count=same_span_counts[
                    (region.source_path, region.start_line, region.end_line)
                ],
            )
            rows.append(
                {
                    "category": category,
                    "classification_predicates": predicates,
                    "fresh_bolio_interval": region.literal_text,
                    "fresh_bolio_sha256": region.extracted_text_sha256,
                    "mapping_revisions": _mapping_evidence(
                        root, imported, page.page_number, region.region_id
                    ),
                    "page_number": page.page_number,
                    "reason": reason,
                    "region_id": region.region_id,
                    "stored_r33_text": evidence.canonical_text,
                    "stored_r33_text_sha256": region.stored_text_sha256,
                    "stored_text_digest_matches_manifest": (
                        _sha256_bytes(evidence.canonical_text.encode("utf-8"))
                        == region.stored_text_sha256
                    ),
                    "unbound_review_source_text_witness": evidence.source_text,
                    "unbound_scan_ocr_text_witness": evidence.ocr_text,
                    "witness_binding": {
                        "scan_ocr": "unbound: no r33 manifest text digest",
                        "source_text": "unbound: no r33 manifest text digest",
                        "stored_r33_text": "bound: SHA-256 equals r33 manifest text_sha256",
                    },
                }
            )
    categories = (
        "source-span-not-exact",
        "section-number-absent-from-interval",
        "layout-whitespace-normalization",
        "unresolved",
    )
    counts = {category: sum(row["category"] == category for row in rows) for category in categories}
    return {
        "final_manifest_sha256": imported.final_manifest_sha256,
        "final_review_sha256": imported.final_review_sha256,
        "source_selector_overlay": {
            "applied_selectors": [
                selector.identity() for selector in imported.applied_source_selectors
            ],
            "sha256": imported.source_selector_overlay_sha256,
        },
        "whitespace_projection_overlay": {
            "applied_receipts": [
                receipt.to_dict() for receipt in imported.applied_whitespace_receipts
            ],
            "sha256": imported.whitespace_overlay_sha256,
        },
        "format_version": "lispmdoc-chinual-derivation-diagnosis-1",
        "mismatches": rows,
        "resolved_projections": projections,
        "summary": {
            "category_counts": counts,
            "mismatch_count": len(rows),
            "resolved_projection_count": len(projections),
            "stored_digest_bound_count": sum(
                bool(row["stored_text_digest_matches_manifest"]) for row in rows
            ),
        },
    }
