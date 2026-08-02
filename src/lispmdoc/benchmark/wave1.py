"""Wave 1 benchmark contracts: selection, grounded truth, evidence, and gates.

No OCR engine is invoked here and no text is generated.  A 60-page queue is a
selection contract only. It cannot pass until each page has either verified
typesetter-source truth with reviewed mapping/layout, or independent human
double transcription and adjudication, plus coverage and raw engine evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .authoritative import AuthoritativeMaterial, AuthoritativeTruthPackage

WAVE1_VERSION = "lispmdoc-benchmark-wave1"
REQUIRED_COMPOSITION: dict[str, int] = {
    "clean-scanned-prose": 12,
    "degraded-scanned-prose": 12,
    "hybrid": 8,
    "born-digital": 6,
    "multi-column-toc-index-list": 6,
    "code-terminal": 6,
    "table": 4,
    "math-unusual-glyph": 3,
    "diagram-label-schematic": 3,
}
_COVERAGE = frozenset({"transcribed", "non-content", "unreadable", "needs-review"})
_REVIEW_STATES = frozenset({"draft", "submitted", "adjudicated", "rejected"})


class Wave1ContractError(ValueError):
    """A benchmark record is structurally invalid or attempts to bypass review."""


def _sha(value: str, name: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise Wave1ContractError(f"{name} must be a lower-case SHA-256")
    return value


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ExpectedRunIdentity:
    """Exact engine, model, and harness identity authorized for a benchmark run."""

    engine: str
    engine_version: str
    model: str
    model_version: str
    tool: str
    tool_version: str

    def __post_init__(self) -> None:
        values = (
            self.engine,
            self.engine_version,
            self.model,
            self.model_version,
            self.tool,
            self.tool_version,
        )
        if any(not value.strip() or value != value.strip() for value in values):
            raise Wave1ContractError(
                "expected run identity requires exact engine, model, and tool versions"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "engine": self.engine,
            "engine_version": self.engine_version,
            "model": self.model,
            "model_version": self.model_version,
            "tool": self.tool,
            "tool_version": self.tool_version,
        }


@dataclass(frozen=True, slots=True)
class QueuePage:
    source_sha256: str
    source_page_index: int
    render_sha256: str
    page_class: str
    tags: tuple[str, ...]
    inventory_region_ids: tuple[str, ...] = ()
    expected_run: ExpectedRunIdentity | None = None

    def __post_init__(self) -> None:
        _sha(self.source_sha256, "source_sha256")
        _sha(self.render_sha256, "render_sha256")
        if self.source_page_index < 0 or not self.page_class:
            raise Wave1ContractError("queue page needs page index and page class")
        tags = tuple(sorted(set(self.tags)))
        if not tags:
            raise Wave1ContractError("queue page needs at least one composition tag")
        inventory = tuple(sorted(self.inventory_region_ids))
        if any(not region_id for region_id in inventory) or len(inventory) != len(set(inventory)):
            raise Wave1ContractError("queue page region inventory IDs must be unique and non-empty")
        object.__setattr__(self, "tags", tags)
        object.__setattr__(self, "inventory_region_ids", inventory)

    @property
    def id(self) -> str:
        return f"{self.source_sha256}:{self.source_page_index}"

    def to_dict(self) -> dict[str, object]:
        return {
            "page_class": self.page_class,
            "render_sha256": self.render_sha256,
            "source_page_index": self.source_page_index,
            "source_sha256": self.source_sha256,
            "tags": list(self.tags),
            "inventory_region_ids": list(self.inventory_region_ids),
            "expected_run": self.expected_run.to_dict() if self.expected_run else None,
        }


@dataclass(frozen=True, slots=True)
class QueueValidation:
    disposition: str
    selected_pages: int
    composition: Mapping[str, int]
    missing_tags: tuple[str, ...]
    duplicate_page_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "composition": dict(sorted(self.composition.items())),
            "disposition": self.disposition,
            "duplicate_page_ids": list(self.duplicate_page_ids),
            "missing_tags": list(self.missing_tags),
            "selected_pages": self.selected_pages,
        }


def validate_60_page_queue(pages: Iterable[QueuePage]) -> QueueValidation:
    items = tuple(sorted(pages, key=lambda item: item.id))
    ids = [item.id for item in items]
    duplicates = tuple(sorted(page_id for page_id, count in Counter(ids).items() if count > 1))
    counts = Counter(tag for page in items for tag in page.tags)
    missing = tuple(
        sorted(tag for tag, target in REQUIRED_COMPOSITION.items() if counts[tag] < target)
    )
    disposition = (
        "selection-ready" if len(items) == 60 and not duplicates and not missing else "undersized"
    )
    return QueueValidation(disposition, len(items), dict(counts), missing, duplicates)


@dataclass(frozen=True, slots=True)
class RegionGeometry:
    region_id: str
    polygon: tuple[tuple[int, int], ...]
    baseline: tuple[tuple[int, int], ...]
    reading_order: int
    semantic_type: str

    def __post_init__(self) -> None:
        if not self.region_id or len(self.polygon) < 3 or len(self.baseline) < 2:
            raise Wave1ContractError("region needs ID, polygon, and baseline")
        if self.reading_order < 0 or not self.semantic_type:
            raise Wave1ContractError("region needs reading order and semantic type")

    def to_dict(self) -> dict[str, object]:
        return {
            "baseline": [list(point) for point in self.baseline],
            "polygon": [list(point) for point in self.polygon],
            "reading_order": self.reading_order,
            "region_id": self.region_id,
            "semantic_type": self.semantic_type,
        }


@dataclass(frozen=True, slots=True)
class TableCellTruth:
    cell_id: str
    row: int
    column: int
    row_span: int = 1
    column_span: int = 1

    def __post_init__(self) -> None:
        if (
            not self.cell_id
            or min(self.row, self.column) < 0
            or min(self.row_span, self.column_span) < 1
        ):
            raise Wave1ContractError("table cells need non-negative coordinates and positive spans")


@dataclass(frozen=True, slots=True)
class TranscribedRegion:
    geometry: RegionGeometry
    literal_text: str
    line_breaks: tuple[int, ...]
    table_cells: tuple[TableCellTruth, ...] = ()

    def __post_init__(self) -> None:
        if any(offset < 0 or offset > len(self.literal_text) for offset in self.line_breaks):
            raise Wave1ContractError("line-break offsets must be within literal text")
        if len(self.line_breaks) != len(set(self.line_breaks)):
            raise Wave1ContractError("line-break offsets must be unique")

    def to_dict(self) -> dict[str, object]:
        return {
            "geometry": self.geometry.to_dict(),
            "line_breaks": list(self.line_breaks),
            "literal_text": self.literal_text,
            "table_cells": [
                {
                    "cell_id": cell.cell_id,
                    "column": cell.column,
                    "column_span": cell.column_span,
                    "row": cell.row,
                    "row_span": cell.row_span,
                }
                for cell in self.table_cells
            ],
        }


@dataclass(frozen=True, slots=True)
class CoverageDisposition:
    inventory_region_id: str
    disposition: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.inventory_region_id or self.disposition not in _COVERAGE:
            raise Wave1ContractError("unknown coverage disposition")
        if self.disposition == "non-content" and not self.reason:
            raise Wave1ContractError("non-content coverage needs a reason")


@dataclass(frozen=True, slots=True)
class IndependentTranscription:
    transcriber: str
    state: str
    regions: tuple[TranscribedRegion, ...]

    def __post_init__(self) -> None:
        if not self.transcriber or self.state not in _REVIEW_STATES:
            raise Wave1ContractError("transcription needs human transcriber and valid state")
        ids = tuple(region.geometry.region_id for region in self.regions)
        if len(ids) != len(set(ids)):
            raise Wave1ContractError("transcription region IDs must be unique")


@dataclass(frozen=True, slots=True)
class TranscriptionPackage:
    """A source/render-bound human truth package for a selected page."""

    version: str
    page: QueuePage
    inventory_region_ids: tuple[str, ...]
    coverage: tuple[CoverageDisposition, ...]
    transcriptions: tuple[IndependentTranscription, ...]
    adjudicator: str | None = None

    def __post_init__(self) -> None:
        if self.version != WAVE1_VERSION:
            raise Wave1ContractError("unsupported transcription package version")
        inventory = tuple(sorted(self.inventory_region_ids))
        if not inventory or len(inventory) != len(set(inventory)):
            raise Wave1ContractError(
                "transcription package needs a unique non-empty region inventory"
            )
        if inventory != self.page.inventory_region_ids:
            raise Wave1ContractError(
                "transcription package inventory must exactly match the queue page inventory"
            )
        object.__setattr__(self, "inventory_region_ids", inventory)
        covered = tuple(item.inventory_region_id for item in self.coverage)
        if set(covered) != set(inventory) or len(covered) != len(set(covered)):
            raise Wave1ContractError("coverage must name each inventory region exactly once")
        transcribed_ids = {
            item.inventory_region_id
            for item in self.coverage
            if item.disposition == "transcribed"
        }
        for transcription in self.transcriptions:
            truth_ids = {region.geometry.region_id for region in transcription.regions}
            if truth_ids != transcribed_ids:
                raise Wave1ContractError(
                    "each transcription must exactly match regions marked transcribed"
                )

    @property
    def adjudicated(self) -> bool:
        submitted = [
            item for item in self.transcriptions if item.state in {"submitted", "adjudicated"}
        ]
        humans = {item.transcriber for item in submitted}
        return (
            len(humans) >= 2
            and bool(self.adjudicator)
            and any(item.state == "adjudicated" for item in self.transcriptions)
        )

    @property
    def complete_coverage(self) -> bool:
        return all(item.disposition in {"transcribed", "non-content"} for item in self.coverage)

    def truth_digest(self) -> str:
        """Digest binds source/render, truth geometry, and human review state."""
        return hashlib.sha256(_canonical(self.to_dict()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "adjudicator": self.adjudicator,
            "coverage": [
                {
                    "disposition": item.disposition,
                    "inventory_region_id": item.inventory_region_id,
                    "reason": item.reason,
                }
                for item in self.coverage
            ],
            "inventory_region_ids": list(self.inventory_region_ids),
            "page": self.page.to_dict(),
            "transcriptions": [
                {
                    "regions": [region.to_dict() for region in transcription.regions],
                    "state": transcription.state,
                    "transcriber": transcription.transcriber,
                }
                for transcription in self.transcriptions
            ],
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class RawEngineArtifact:
    sha256: str
    byte_size: int
    engine: str
    engine_version: str
    model: str
    model_version: str
    tool: str
    tool_version: str

    def __post_init__(self) -> None:
        _sha(self.sha256, "raw engine output sha256")
        if self.byte_size < 1:
            raise Wave1ContractError("raw engine output must be non-empty")
        _ = self.run_identity

    @classmethod
    def from_bytes(
        cls,
        raw: bytes,
        *,
        engine: str,
        engine_version: str,
        model: str,
        model_version: str,
        tool: str,
        tool_version: str,
    ) -> RawEngineArtifact:
        if not isinstance(raw, bytes) or not raw:
            raise Wave1ContractError("raw engine output must be non-empty bytes")
        return cls(
            hashlib.sha256(raw).hexdigest(),
            len(raw),
            engine,
            engine_version,
            model,
            model_version,
            tool,
            tool_version,
        )

    @property
    def run_identity(self) -> ExpectedRunIdentity:
        return ExpectedRunIdentity(
            self.engine,
            self.engine_version,
            self.model,
            self.model_version,
            self.tool,
            self.tool_version,
        )

    def verify(self, raw: bytes) -> bool:
        return (
            isinstance(raw, bytes)
            and bool(raw)
            and self.byte_size == len(raw)
            and self.sha256 == hashlib.sha256(raw).hexdigest()
        )


@dataclass(frozen=True, slots=True)
class PageMeasurements:
    text_region_tp: int = 0
    text_region_fp: int = 0
    text_region_fn: int = 0
    order_correct_pairs: int = 0
    order_total_pairs: int = 0
    semantic_tp: int = 0
    semantic_fp: int = 0
    semantic_fn: int = 0
    table_tp: int = 0
    table_fp: int = 0
    table_fn: int = 0
    confidence_bins: tuple[tuple[float, float, int], ...] = ()
    runtime_ms: int | None = None
    memory_bytes: int | None = None
    vram_bytes: int | None = None
    output_bytes: int | None = None

    def _f1(self, tp: int, fp: int, fn: int) -> float:
        denominator = 2 * tp + fp + fn
        return 1.0 if denominator == 0 else 2 * tp / denominator

    @property
    def region_precision(self) -> float:
        return (
            1.0
            if self.text_region_tp + self.text_region_fp == 0
            else self.text_region_tp / (self.text_region_tp + self.text_region_fp)
        )

    @property
    def region_recall(self) -> float:
        return (
            1.0
            if self.text_region_tp + self.text_region_fn == 0
            else self.text_region_tp / (self.text_region_tp + self.text_region_fn)
        )

    @property
    def order_accuracy(self) -> float:
        return (
            1.0
            if self.order_total_pairs == 0
            else self.order_correct_pairs / self.order_total_pairs
        )

    @property
    def semantic_f1(self) -> float:
        return self._f1(self.semantic_tp, self.semantic_fp, self.semantic_fn)

    @property
    def table_f1(self) -> float:
        return self._f1(self.table_tp, self.table_fp, self.table_fn)

    @property
    def expected_calibration_error(self) -> float | None:
        total = sum(count for _, _, count in self.confidence_bins)
        if total == 0:
            return None
        return (
            sum(
                abs(confidence - accuracy) * count
                for confidence, accuracy, count in self.confidence_bins
            )
            / total
        )


@dataclass(frozen=True, slots=True)
class HardGateResult:
    disposition: str
    reasons: tuple[str, ...]


def hard_gate(
    queue: Sequence[QueuePage],
    packages: Mapping[str, TranscriptionPackage],
    artifacts: Mapping[str, tuple[RawEngineArtifact, bytes]],
    authoritative_packages: Mapping[
        str, tuple[AuthoritativeTruthPackage, AuthoritativeMaterial]
    ] | None = None,
) -> HardGateResult:
    """Reject missing, stale, unreviewed, or unverified truth/evidence shortcuts."""
    queue_state = validate_60_page_queue(queue)
    source_packages = authoritative_packages or {}
    reasons: list[str] = []
    if queue_state.disposition != "selection-ready":
        reasons.append(queue_state.disposition)
    reasons.extend(f"duplicate-page:{page_id}" for page_id in queue_state.duplicate_page_ids)
    scanned_prose_characters = {"clean-scanned-prose": 0, "degraded-scanned-prose": 0}
    code_tokens = 0
    for page in sorted(queue, key=lambda item: item.id):
        if page.expected_run is None:
            reasons.append(f"missing-expected-run-contract:{page.id}")
        package = packages.get(page.id)
        source_entry = source_packages.get(page.id)
        if package is not None and source_entry is not None:
            reasons.append(f"ambiguous-manual-and-authoritative-truth:{page.id}")
            continue
        if package is None and source_entry is None:
            reasons.append(f"missing-package:{page.id}")
            continue
        truth_texts: tuple[str, ...] = ()
        if package is not None:
            if (
                package.page.source_sha256 != page.source_sha256
                or package.page.render_sha256 != page.render_sha256
            ):
                reasons.append(f"stale-source-or-render:{page.id}")
            if (
                package.page.source_page_index != page.source_page_index
                or package.page.page_class != page.page_class
                or package.page.tags != page.tags
            ):
                reasons.append(f"mismatched-page-index-class-or-tags:{page.id}")
            if package.page.inventory_region_ids != page.inventory_region_ids:
                reasons.append(f"mismatched-region-inventory:{page.id}")
            if package.page.expected_run != page.expected_run:
                reasons.append(f"mismatched-expected-run-contract:{page.id}")
            if not package.complete_coverage:
                reasons.append(f"incomplete-coverage:{page.id}")
            if not package.adjudicated:
                reasons.append(f"single-review-or-unadjudicated:{page.id}")
            adjudicated = next(
                (item for item in package.transcriptions if item.state == "adjudicated"), None
            )
            if adjudicated is not None:
                truth_texts = tuple(region.literal_text for region in adjudicated.regions)
        else:
            assert source_entry is not None
            source_package, source_material = source_entry
            source_page = source_package.queue_page
            if (
                source_page.source_sha256 != page.source_sha256
                or source_page.render_sha256 != page.render_sha256
            ):
                reasons.append(f"stale-source-or-render:{page.id}")
            if (
                source_page.source_page_index != page.source_page_index
                or source_page.page_class != page.page_class
                or source_page.tags != page.tags
            ):
                reasons.append(f"mismatched-page-index-class-or-tags:{page.id}")
            if source_page.inventory_region_ids != page.inventory_region_ids:
                reasons.append(f"mismatched-region-inventory:{page.id}")
            if source_page.expected_run != page.expected_run:
                reasons.append(f"mismatched-expected-run-contract:{page.id}")
            material_verified = True
            try:
                source_package.verify_material_bundle(source_material)
            except ValueError:
                material_verified = False
                reasons.append(f"invalid-authoritative-material:{page.id}")
            status = source_package.status().disposition
            if status != "authoritative-ready":
                reasons.append(f"{status}:{page.id}")
            elif material_verified:
                truth_texts = tuple(region.literal_text for region in source_package.regions)
        if truth_texts:
            characters = sum(len(text) for text in truth_texts)
            for tag in scanned_prose_characters:
                if tag in page.tags:
                    scanned_prose_characters[tag] += characters
            if "code-terminal" in page.tags:
                code_tokens += sum(len(text.split()) for text in truth_texts)
        evidence = artifacts.get(page.id)
        if evidence is None or not evidence[0].verify(evidence[1]):
            reasons.append(f"missing-or-invalid-raw-output:{page.id}")
        elif page.expected_run is None or evidence[0].run_identity != page.expected_run:
            reasons.append(f"unexpected-engine-model-or-tool:{page.id}")
    for tag, count in sorted(scanned_prose_characters.items()):
        if count < 2_000:
            reasons.append(f"undersized-characters:{tag}")
    if code_tokens < 100:
        reasons.append("undersized-code-tokens")
    return HardGateResult("pass" if not reasons else "reject", tuple(sorted(reasons)))


def stratified_measurements(
    pages: Mapping[str, QueuePage], measurements: Mapping[str, PageMeasurements]
) -> dict[str, dict[str, float | int | None]]:
    """Report layout/semantic/calibration/resource metrics by class and tag."""
    groups: dict[str, list[PageMeasurements]] = defaultdict(list)
    for page_id, measurement in measurements.items():
        page = pages.get(page_id)
        if page is None:
            continue
        groups[f"class:{page.page_class}"].append(measurement)
        for tag in page.tags:
            groups[f"tag:{tag}"].append(measurement)
    report: dict[str, dict[str, float | int | None]] = {}
    for name in sorted(groups):
        items = groups[name]
        combined = PageMeasurements(
            text_region_tp=sum(item.text_region_tp for item in items),
            text_region_fp=sum(item.text_region_fp for item in items),
            text_region_fn=sum(item.text_region_fn for item in items),
            order_correct_pairs=sum(item.order_correct_pairs for item in items),
            order_total_pairs=sum(item.order_total_pairs for item in items),
            semantic_tp=sum(item.semantic_tp for item in items),
            semantic_fp=sum(item.semantic_fp for item in items),
            semantic_fn=sum(item.semantic_fn for item in items),
            table_tp=sum(item.table_tp for item in items),
            table_fp=sum(item.table_fp for item in items),
            table_fn=sum(item.table_fn for item in items),
            confidence_bins=tuple(bin_ for item in items for bin_ in item.confidence_bins),
        )
        report[name] = {
            "pages": len(items),
            "region_precision": combined.region_precision,
            "region_recall": combined.region_recall,
            "order_pair_accuracy": combined.order_accuracy,
            "semantic_f1": combined.semantic_f1,
            "table_f1": combined.table_f1,
            "expected_calibration_error": combined.expected_calibration_error,
            "runtime_ms": sum(item.runtime_ms or 0 for item in items),
            "memory_bytes": max((item.memory_bytes or 0 for item in items), default=0),
            "vram_bytes": max((item.vram_bytes or 0 for item in items), default=0),
            "output_bytes": sum(item.output_bytes or 0 for item in items),
        }
    return report
