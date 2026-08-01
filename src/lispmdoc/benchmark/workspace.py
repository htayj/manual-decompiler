"""Strict JSON I/O and blank workspaces for independent human transcription."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .wave1 import (
    WAVE1_VERSION,
    CoverageDisposition,
    ExpectedRunIdentity,
    IndependentTranscription,
    QueuePage,
    RegionGeometry,
    TableCellTruth,
    TranscribedRegion,
    TranscriptionPackage,
    Wave1ContractError,
)

WORKSPACE_VERSION = "lispmdoc-transcription-workspace-1"


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Wave1ContractError(f"{name} must be an object")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise Wave1ContractError(f"{name} must be an array")
    return value


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise Wave1ContractError(f"{name} must be an integer")
    return value


def _points(value: object, name: str) -> tuple[tuple[int, int], ...]:
    points: list[tuple[int, int]] = []
    for index, point in enumerate(_list(value, name)):
        coordinates = _list(point, f"{name}[{index}]")
        if len(coordinates) != 2:
            raise Wave1ContractError(f"{name}[{index}] must contain two coordinates")
        points.append(
            (
                _integer(coordinates[0], f"{name}[{index}][0]"),
                _integer(coordinates[1], f"{name}[{index}][1]"),
            )
        )
    return tuple(points)


def expected_run_from_dict(value: object) -> ExpectedRunIdentity:
    record = _mapping(value, "expected_run")
    return ExpectedRunIdentity(
        str(record.get("engine", "")),
        str(record.get("engine_version", "")),
        str(record.get("model", "")),
        str(record.get("model_version", "")),
        str(record.get("tool", "")),
        str(record.get("tool_version", "")),
    )


def queue_page_from_dict(value: object) -> QueuePage:
    record = _mapping(value, "queue page")
    tags = _list(record.get("tags"), "queue page tags")
    inventory = _list(record.get("inventory_region_ids", []), "queue page inventory_region_ids")
    expected = record.get("expected_run")
    return QueuePage(
        str(record.get("source_sha256", "")),
        _integer(record.get("source_page_index"), "source_page_index"),
        str(record.get("render_sha256", "")),
        str(record.get("page_class", "")),
        tuple(str(tag) for tag in tags),
        tuple(str(region_id) for region_id in inventory),
        expected_run_from_dict(expected) if expected is not None else None,
    )


def queue_to_dict(pages: Sequence[QueuePage]) -> dict[str, object]:
    return {
        "pages": [page.to_dict() for page in sorted(pages, key=lambda item: item.id)],
        "version": WAVE1_VERSION,
    }


def load_wave1_queue(path: str | Path) -> tuple[QueuePage, ...]:
    root = _mapping(json.loads(Path(path).read_text(encoding="utf-8")), "queue")
    if root.get("version") != WAVE1_VERSION:
        raise Wave1ContractError("unsupported queue version")
    pages = tuple(queue_page_from_dict(item) for item in _list(root.get("pages"), "pages"))
    ids = tuple(page.id for page in pages)
    if len(ids) != len(set(ids)):
        raise Wave1ContractError("queue contains duplicate source pages")
    return pages


def _geometry_from_dict(value: object) -> RegionGeometry:
    record = _mapping(value, "geometry")
    return RegionGeometry(
        str(record.get("region_id", "")),
        _points(record.get("polygon"), "polygon"),
        _points(record.get("baseline"), "baseline"),
        _integer(record.get("reading_order"), "reading_order"),
        str(record.get("semantic_type", "")),
    )


def _table_cell_from_dict(value: object) -> TableCellTruth:
    record = _mapping(value, "table cell")
    return TableCellTruth(
        str(record.get("cell_id", "")),
        _integer(record.get("row"), "table row"),
        _integer(record.get("column"), "table column"),
        _integer(record.get("row_span", 1), "table row_span"),
        _integer(record.get("column_span", 1), "table column_span"),
    )


def _region_from_dict(value: object) -> TranscribedRegion:
    record = _mapping(value, "transcribed region")
    breaks = _list(record.get("line_breaks"), "line_breaks")
    cells = _list(record.get("table_cells", []), "table_cells")
    text = record.get("literal_text")
    if not isinstance(text, str):
        raise Wave1ContractError("literal_text must be a string")
    return TranscribedRegion(
        _geometry_from_dict(record.get("geometry")),
        text,
        tuple(_integer(offset, "line break") for offset in breaks),
        tuple(_table_cell_from_dict(cell) for cell in cells),
    )


def transcription_package_from_dict(value: object) -> TranscriptionPackage:
    record = _mapping(value, "transcription package")
    if record.get("version") != WAVE1_VERSION:
        raise Wave1ContractError("unsupported transcription package version")
    inventory = _list(record.get("inventory_region_ids"), "inventory_region_ids")
    coverage = []
    for item in _list(record.get("coverage"), "coverage"):
        disposition = _mapping(item, "coverage item")
        reason = disposition.get("reason")
        coverage.append(
            CoverageDisposition(
                str(disposition.get("inventory_region_id", "")),
                str(disposition.get("disposition", "")),
                str(reason) if reason is not None else None,
            )
        )
    transcriptions = []
    for item in _list(record.get("transcriptions"), "transcriptions"):
        transcription = _mapping(item, "transcription")
        regions = _list(transcription.get("regions"), "transcription regions")
        transcriptions.append(
            IndependentTranscription(
                str(transcription.get("transcriber", "")),
                str(transcription.get("state", "")),
                tuple(_region_from_dict(region) for region in regions),
            )
        )
    adjudicator = record.get("adjudicator")
    return TranscriptionPackage(
        WAVE1_VERSION,
        queue_page_from_dict(record.get("page")),
        tuple(str(region_id) for region_id in inventory),
        tuple(coverage),
        tuple(transcriptions),
        str(adjudicator) if adjudicator is not None else None,
    )


def load_transcription_package(path: str | Path) -> TranscriptionPackage:
    return transcription_package_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def blank_transcription_package(page: QueuePage) -> TranscriptionPackage:
    """Create a truth-free review template; OCR text is never copied into it."""
    if not page.inventory_region_ids:
        raise Wave1ContractError("cannot transcribe a page without a region inventory")
    return TranscriptionPackage(
        WAVE1_VERSION,
        page,
        page.inventory_region_ids,
        tuple(
            CoverageDisposition(region_id, "needs-review")
            for region_id in page.inventory_region_ids
        ),
        (),
        None,
    )


@dataclass(frozen=True, slots=True)
class WorkspaceResult:
    output_root: Path
    index_path: Path
    queue_sha256: str
    template_paths: tuple[Path, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "disposition": "human-transcription-required",
            "index_path": str(self.index_path),
            "output_root": str(self.output_root),
            "queue_sha256": self.queue_sha256,
            "template_paths": [str(path) for path in self.template_paths],
        }


def initialize_transcription_workspace(
    pages: Sequence[QueuePage], output_root: str | Path
) -> WorkspaceResult:
    """Write blank per-page packages without seeding them with engine text."""
    root = Path(output_root)
    if root.exists():
        raise FileExistsError(f"transcription workspace already exists: {root}")
    if not pages:
        raise Wave1ContractError("transcription workspace requires at least one page")
    queue_payload = queue_to_dict(pages)
    queue_sha256 = hashlib.sha256(_canonical_bytes(queue_payload)).hexdigest()
    page_root = root / "pages"
    page_root.mkdir(parents=True)
    index_pages: list[dict[str, object]] = []
    paths: list[Path] = []
    for page in sorted(pages, key=lambda item: item.id):
        package = blank_transcription_package(page)
        payload = _canonical_bytes(package.to_dict())
        relative = Path("pages") / (
            f"{page.source_sha256[:16]}-p{page.source_page_index + 1:06d}.json"
        )
        path = root / relative
        path.write_bytes(payload)
        paths.append(path)
        index_pages.append(
            {
                "page_id": page.id,
                "package_path": relative.as_posix(),
                "package_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    index = {
        "pages": index_pages,
        "queue_sha256": queue_sha256,
        "version": WORKSPACE_VERSION,
    }
    index_path = root / "index.json"
    index_path.write_bytes(_canonical_bytes(index))
    return WorkspaceResult(root, index_path, queue_sha256, tuple(paths))


def transcription_status(package: TranscriptionPackage) -> dict[str, object]:
    return {
        "adjudicated": package.adjudicated,
        "complete_coverage": package.complete_coverage,
        "disposition": (
            "adjudicated"
            if package.adjudicated and package.complete_coverage
            else "human-review-required"
        ),
        "page_id": package.page.id,
        "package_sha256": package.truth_digest(),
        "transcriber_count": len({item.transcriber for item in package.transcriptions}),
    }
