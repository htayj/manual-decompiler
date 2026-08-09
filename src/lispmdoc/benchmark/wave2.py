"""Digest-bound Wave-2 representative-benchmark inventory and authority ledger.

This module inventories candidate PDFs without rendering or invoking OCR.  It
distinguishes an available page from eligible benchmark truth: only completed
independent adjudication, reviewed recovered typesetter source, or reviewed
native-PDF object evidence can make a page eligible.  OCR-derived text is
never an authority method.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
from collections import Counter
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .wave1 import REQUIRED_COMPOSITION

WAVE2_INVENTORY_VERSION = "lispmdoc-wave2-representative-inventory-1"
_DIGEST = frozenset("0123456789abcdef")
_TRUTH_ORIGINS = frozenset(
    {
        "independent-adjudicated",
        "native-pdf-objects",
        "ocr-derived",
        "recovered-source-reviewed",
    }
)
_TRUTH_STATES = frozenset({"inventory-only", "reviewed"})
_GATE_STATES = frozenset({"not-applicable", "pending", "verified"})
_PAGE_CLASSES = frozenset(
    {
        "born-digital",
        "hybrid",
        "recovered-typesetter-source",
        "scan-bilevel",
        "scan-color",
        "scan-gray",
        "schematic",
    }
)

class Wave2InventoryError(ValueError):
    """The candidate inventory is malformed, stale, or lacks proven authority."""


def _digest(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _DIGEST for character in value)
    ):
        raise Wave2InventoryError(f"{label} must be a lower-case SHA-256")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise Wave2InventoryError(f"{label} must be a non-empty trimmed string")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise Wave2InventoryError(f"{label} must be an integer at least {minimum}")
    return value


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise Wave2InventoryError(f"{label} must be an object")
    return value


def _reject_unknown(record: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(record).difference(allowed))
    if unknown:
        raise Wave2InventoryError(f"{label} contains unknown fields: {unknown}")


def _safe_relative_path(value: object, label: str) -> str:
    path = _text(value, label)
    candidate = Path(path)
    if (
        candidate.is_absolute()
        or candidate.as_posix() != path
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise Wave2InventoryError(f"{label} must be a contained relative path")
    return path


def _tags(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise Wave2InventoryError(f"{label} must be a non-empty array")
    if any(not isinstance(tag, str) or tag not in REQUIRED_COMPOSITION for tag in value):
        raise Wave2InventoryError(f"{label} contains an unknown composition tag")
    result = tuple(value)
    if result != tuple(sorted(set(result))):
        raise Wave2InventoryError(f"{label} must be sorted and duplicate-free")
    return result


@dataclass(frozen=True, slots=True)
class TruthLedger:
    origin: str
    status: str
    mapping_gate: str
    layout_gate: str
    reading_order_gate: str
    semantics_gate: str
    object_extraction_gate: str
    evidence: Mapping[str, Any] | None

    def __post_init__(self) -> None:
        if self.origin not in _TRUTH_ORIGINS:
            raise Wave2InventoryError("truth.origin is unsupported")
        if self.status not in _TRUTH_STATES:
            raise Wave2InventoryError("truth.status is unsupported")
        if any(
            state not in _GATE_STATES
            for state in (
                self.mapping_gate,
                self.layout_gate,
                self.reading_order_gate,
                self.semantics_gate,
                self.object_extraction_gate,
            )
        ):
            raise Wave2InventoryError("truth gates are unsupported")
        if self.status == "reviewed" and self.evidence is None:
            raise Wave2InventoryError("reviewed truth requires evidence")
        if self.status == "reviewed" and self.origin != "recovered-source-reviewed":
            raise Wave2InventoryError(
                "reviewed truth requires a supported contained receipt verifier"
            )

    @property
    def declared_eligible(self) -> bool:
        if self.status != "reviewed" or self.origin == "ocr-derived":
            return False
        if self.origin == "independent-adjudicated":
            return (
                self.mapping_gate == "not-applicable"
                and self.layout_gate == "verified"
                and self.reading_order_gate == "verified"
                and self.semantics_gate == "verified"
                and self.object_extraction_gate == "not-applicable"
            )
        if self.origin == "native-pdf-objects":
            return (
                self.mapping_gate == "verified"
                and self.layout_gate == "verified"
                and self.reading_order_gate == "verified"
                and self.semantics_gate == "verified"
                and self.object_extraction_gate == "verified"
            )
        return (
            self.mapping_gate == "verified"
            and self.layout_gate == "verified"
            and self.reading_order_gate in {"not-applicable", "verified"}
            and self.semantics_gate == "verified"
            and self.object_extraction_gate == "not-applicable"
        )

    @classmethod
    def from_dict(cls, value: object) -> TruthLedger:
        record = _object(value, "truth")
        _reject_unknown(
            record,
            frozenset(
                {
                    "evidence",
                    "layout_gate",
                    "mapping_gate",
                    "object_extraction_gate",
                    "origin",
                    "reading_order_gate",
                    "semantics_gate",
                    "status",
                }
            ),
            "truth",
        )
        evidence = record.get("evidence")
        if evidence is not None:
            evidence = _object(evidence, "truth.evidence")
            _reject_unknown(evidence, frozenset({"kind"}), "truth.evidence")
        return cls(
            _text(record.get("origin"), "truth.origin"),
            _text(record.get("status"), "truth.status"),
            _text(record.get("mapping_gate"), "truth.mapping_gate"),
            _text(record.get("layout_gate"), "truth.layout_gate"),
            _text(record.get("reading_order_gate"), "truth.reading_order_gate"),
            _text(record.get("semantics_gate"), "truth.semantics_gate"),
            _text(record.get("object_extraction_gate"), "truth.object_extraction_gate"),
            evidence,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence": dict(self.evidence) if self.evidence is not None else None,
            "layout_gate": self.layout_gate,
            "mapping_gate": self.mapping_gate,
            "object_extraction_gate": self.object_extraction_gate,
            "origin": self.origin,
            "reading_order_gate": self.reading_order_gate,
            "semantics_gate": self.semantics_gate,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class CandidatePage:
    page_index: int
    page_class: str
    tags: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.page_index < 0:
            raise Wave2InventoryError("candidate page index must be non-negative")
        if self.page_class not in _PAGE_CLASSES:
            raise Wave2InventoryError("candidate page class is unsupported")

    @classmethod
    def from_dict(cls, value: object) -> CandidatePage:
        record = _object(value, "candidate page")
        _reject_unknown(record, frozenset({"page_class", "page_index", "tags"}), "candidate page")
        return cls(
            _integer(record.get("page_index"), "candidate page_index"),
            _text(record.get("page_class"), "candidate page_class"),
            _tags(record.get("tags"), "candidate tags"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "page_class": self.page_class,
            "page_index": self.page_index,
            "tags": list(self.tags),
        }


@dataclass(frozen=True, slots=True)
class CandidateManual:
    manual_id: str
    source_path: str
    source_sha256: str
    source_byte_size: int
    page_count: int
    intended_page_classes: tuple[str, ...]
    truth: TruthLedger
    pages: tuple[CandidatePage, ...]

    def __post_init__(self) -> None:
        _safe_relative_path(self.source_path, "source_path")
        _digest(self.source_sha256, "source_sha256")
        if self.source_byte_size < 1 or self.page_count < 1:
            raise Wave2InventoryError("source size and page_count must be positive")
        if not self.intended_page_classes or any(
            page_class not in _PAGE_CLASSES for page_class in self.intended_page_classes
        ):
            raise Wave2InventoryError("intended_page_classes are unsupported")
        if self.intended_page_classes != tuple(sorted(set(self.intended_page_classes))):
            raise Wave2InventoryError("intended_page_classes must be sorted and duplicate-free")
        indices = tuple(page.page_index for page in self.pages)
        if len(indices) != len(set(indices)):
            raise Wave2InventoryError("candidate page indices must be unique per manual")
        if any(index >= self.page_count for index in indices):
            raise Wave2InventoryError("candidate page index exceeds declared page_count")
        if any(page.page_class not in self.intended_page_classes for page in self.pages):
            raise Wave2InventoryError("candidate page class is outside intended_page_classes")
        if self.truth.status == "reviewed" and not self.pages:
            raise Wave2InventoryError("reviewed truth requires at least one candidate page")

    @classmethod
    def from_dict(cls, value: object) -> CandidateManual:
        record = _object(value, "candidate manual")
        _reject_unknown(
            record,
            frozenset(
                {
                    "candidate_pages",
                    "intended_page_classes",
                    "manual_id",
                    "page_count",
                    "source_byte_size",
                    "source_path",
                    "source_sha256",
                    "truth",
                }
            ),
            "candidate manual",
        )
        intended = record.get("intended_page_classes")
        pages = record.get("candidate_pages")
        if not isinstance(intended, list) or not isinstance(pages, list):
            raise Wave2InventoryError(
                "candidate manual requires intended classes and candidate pages"
            )
        return cls(
            _text(record.get("manual_id"), "manual_id"),
            _safe_relative_path(record.get("source_path"), "source_path"),
            _digest(record.get("source_sha256"), "source_sha256"),
            _integer(record.get("source_byte_size"), "source_byte_size", minimum=1),
            _integer(record.get("page_count"), "page_count", minimum=1),
            tuple(_text(item, "intended_page_class") for item in intended),
            TruthLedger.from_dict(record.get("truth")),
            tuple(CandidatePage.from_dict(item) for item in pages),
        )


@dataclass(frozen=True, slots=True)
class Wave2Inventory:
    manuals: tuple[CandidateManual, ...]

    def __post_init__(self) -> None:
        manual_ids = tuple(manual.manual_id for manual in self.manuals)
        paths = tuple(manual.source_path for manual in self.manuals)
        digests = tuple(manual.source_sha256 for manual in self.manuals)
        if not self.manuals or len(manual_ids) != len(set(manual_ids)):
            raise Wave2InventoryError("candidate manual IDs must be non-empty and unique")
        if len(paths) != len(set(paths)) or len(digests) != len(set(digests)):
            raise Wave2InventoryError("candidate source paths and digests must be unique")
        if manual_ids != tuple(sorted(manual_ids)):
            raise Wave2InventoryError("candidate manuals must be sorted by manual_id")

    @classmethod
    def from_bytes(cls, content: bytes) -> Wave2Inventory:
        try:
            raw = json.loads(content.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise Wave2InventoryError("inventory must be UTF-8 JSON") from error
        record = _object(raw, "inventory")
        _reject_unknown(record, frozenset({"format_version", "manuals"}), "inventory")
        if record.get("format_version") != WAVE2_INVENTORY_VERSION:
            raise Wave2InventoryError("unsupported Wave-2 inventory format")
        manuals = record.get("manuals")
        if not isinstance(manuals, list):
            raise Wave2InventoryError("inventory manuals must be an array")
        return cls(tuple(CandidateManual.from_dict(item) for item in manuals))


def read_contained_regular(root: Path, relative: str, label: str) -> bytes:
    """Read a regular contained file through descriptor-bound, no-follow opens."""

    if root.is_symlink() or not root.is_dir():
        raise Wave2InventoryError("inventory root must be a non-symlink directory")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        directory_fd = os.open(root, directory_flags)
    except OSError as error:
        raise Wave2InventoryError("inventory root must be a non-symlink directory") from error
    try:
        parts = Path(relative).parts
        for part in parts[:-1]:
            try:
                child_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            except OSError as error:
                raise Wave2InventoryError(
                    f"{label} path is missing or contains a symlink: {relative}"
                ) from error
            os.close(directory_fd)
            directory_fd = child_fd
        try:
            file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        except OSError as error:
            raise Wave2InventoryError(
                f"{label} is missing or contains a symlink: {relative}"
            ) from error
    finally:
        os.close(directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise Wave2InventoryError(f"{label} is not a regular file: {relative}")
        with os.fdopen(file_fd, "rb", closefd=True) as source:
            return source.read()
    except BaseException:
        with suppress(OSError):
            os.close(file_fd)
        raise


# Kept as a compatibility alias for the initial inventory tests and callers.
_read_contained_regular = read_contained_regular


def load_inventory(root: Path, relative_path: str) -> tuple[Wave2Inventory, bytes]:
    """Read and parse the tracked inventory from the exact bytes that are hashed."""

    relative = _safe_relative_path(relative_path, "inventory path")
    content = read_contained_regular(root, relative, "inventory")
    return Wave2Inventory.from_bytes(content), content


def _pdf_page_count(content: bytes, path: str) -> int:
    if not content.startswith(b"%PDF-"):
        raise Wave2InventoryError(f"source PDF has an invalid signature: {path}")
    try:
        return len(PdfReader(io.BytesIO(content)).pages)
    except Exception as error:  # pypdf has several format-specific exception types.
        raise Wave2InventoryError(f"cannot count source PDF pages: {path}") from error


def _verify_chinual_evidence(root: Path, manual: CandidateManual) -> Mapping[int, Any]:
    evidence = manual.truth.evidence
    assert evidence is not None
    if evidence.get("kind") != "chinual-recovered-slice":
        raise Wave2InventoryError("reviewed recovered-source truth lacks a supported verifier")
    from .chinual_recovered import ChinualImportError, import_chinual_recovered_slice

    try:
        imported = import_chinual_recovered_slice(root)
    except ChinualImportError as error:
        raise Wave2InventoryError("cannot verify Chinual recovered-source evidence") from error
    if {record.queue_page.source_sha256 for record in imported.records} != {manual.source_sha256}:
        raise Wave2InventoryError("Chinual evidence source PDF digest drifted")
    return {
        record.queue_page.source_page_index: record.queue_page
        for record in imported.authoritative_pages
    }


def verify_inventory(root: Path, inventory: Wave2Inventory) -> dict[str, object]:
    """Verify local PDFs once and derive authority/readiness without OCR inference."""

    verified_pages: list[dict[str, object]] = []
    manual_rows: list[dict[str, object]] = []
    composition: Counter[str] = Counter()
    diagnostic_composition: Counter[str] = Counter()
    for manual in inventory.manuals:
        content = _read_contained_regular(root, manual.source_path, "source PDF")
        actual_digest = hashlib.sha256(content).hexdigest()
        if actual_digest != manual.source_sha256 or len(content) != manual.source_byte_size:
            raise Wave2InventoryError(f"source PDF identity drifted: {manual.manual_id}")
        actual_page_count = _pdf_page_count(content, manual.source_path)
        if actual_page_count != manual.page_count:
            raise Wave2InventoryError(f"source PDF page count drifted: {manual.manual_id}")
        proven_pages: Mapping[int, Any] = {}
        if (
            manual.truth.status == "reviewed"
            and manual.truth.origin == "recovered-source-reviewed"
        ):
            proven_pages = _verify_chinual_evidence(root, manual)
        eligible = manual.truth.declared_eligible
        if manual.truth.origin == "recovered-source-reviewed" and eligible:
            declared = {page.page_index for page in manual.pages}
            if not declared.issubset(proven_pages):
                raise Wave2InventoryError(
                    "recovered-source evidence does not prove every candidate page"
                )
            for page in manual.pages:
                queue_page = proven_pages[page.page_index]
                if queue_page.page_class != page.page_class or queue_page.tags != page.tags:
                    raise Wave2InventoryError(
                        "recovered-source candidate class or tags drifted from its QueuePage"
                    )
        if manual.truth.origin == "ocr-derived" and eligible:
            raise AssertionError("OCR-derived truth must be ineligible")
        manual_rows.append(
            {
                "manual_id": manual.manual_id,
                "page_count": manual.page_count,
                "source_byte_size": manual.source_byte_size,
                "source_sha256": manual.source_sha256,
                "truth": manual.truth.to_dict(),
                "truth_selection_eligible": eligible,
            }
        )
        for page in manual.pages:
            page_eligible = eligible and (
                manual.truth.origin != "recovered-source-reviewed"
                or page.page_index in proven_pages
            )
            if page_eligible:
                composition.update(page.tags)
            if manual.truth.status == "reviewed" and manual.truth.origin != "ocr-derived":
                diagnostic_composition.update(page.tags)
            verified_pages.append(
                {
                    "manual_id": manual.manual_id,
                    "page_class": page.page_class,
                    "page_index": page.page_index,
                    "selection_eligible": page_eligible,
                    "tags": list(page.tags),
                    "truth_origin": manual.truth.origin,
                }
            )
    gaps = {
        tag: target - composition[tag]
        for tag, target in sorted(REQUIRED_COMPOSITION.items())
        if composition[tag] < target
    }
    eligible_pages = sum(bool(page["selection_eligible"]) for page in verified_pages)
    return {
        "composition": dict(sorted(composition.items())),
        "composition_gaps": gaps,
        "diagnostic_composition": dict(sorted(diagnostic_composition.items())),
        "eligible_pages": eligible_pages,
        "format_version": WAVE2_INVENTORY_VERSION,
        "inventory_ready": True,
        "manuals": manual_rows,
        "pages": verified_pages,
        "selection_readiness": (
            "selection-ready" if eligible_pages == 60 and not gaps else "undersized"
        ),
    }
