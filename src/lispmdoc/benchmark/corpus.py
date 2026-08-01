"""Versioned, manually grounded OCR benchmark corpus records and selection."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

BENCHMARK_MANIFEST_VERSION = "lispmdoc-benchmark-1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PAGE_CLASSES = frozenset(
    {
        "born-digital",
        "hybrid",
        "scan-bilevel",
        "scan-gray",
        "scan-color",
        "schematic",
        "photo-or-illustration-dominant",
        "ambiguous",
    }
)


class BenchmarkManifestError(ValueError):
    """Raised for an invalid or ungrounded benchmark corpus manifest."""


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise BenchmarkManifestError(f"{name} must be a lower-case SHA-256 digest")
    return value


def _tags(value: Iterable[str], name: str) -> tuple[str, ...]:
    result = tuple(sorted(set(value)))
    if not result or any(not isinstance(tag, str) or not tag for tag in result):
        raise BenchmarkManifestError(f"{name} must contain at least one non-empty tag")
    return result


@dataclass(frozen=True, slots=True)
class GroundTruthRecord:
    """A human-entered literal transcription for one benchmark region.

    The manifest refuses any method other than ``manual``.  It therefore cannot
    turn model-generated text into benchmark truth by accident.
    """

    region_id: str
    text: str
    kind: str
    method: str
    recorded_by: str
    required: bool = True

    def __post_init__(self) -> None:
        if not self.region_id:
            raise BenchmarkManifestError("ground-truth region_id is required")
        if not isinstance(self.text, str):
            raise BenchmarkManifestError("ground-truth text must be a string")
        if not self.kind:
            raise BenchmarkManifestError("ground-truth kind is required")
        if self.method != "manual":
            raise BenchmarkManifestError(
                "ground truth must be manually transcribed; generated truth is forbidden"
            )
        if not self.recorded_by:
            raise BenchmarkManifestError("manual ground truth needs recorded_by provenance")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "method": self.method,
            "recorded_by": self.recorded_by,
            "region_id": self.region_id,
            "required": self.required,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GroundTruthRecord:
        return cls(
            region_id=str(value.get("region_id", "")),
            text=value.get("text", ""),
            kind=str(value.get("kind", "prose")),
            method=str(value.get("method", "")),
            recorded_by=str(value.get("recorded_by", "")),
            required=bool(value.get("required", True)),
        )


@dataclass(frozen=True, slots=True)
class BenchmarkPage:
    """One selected page with its human-authored evaluation regions."""

    source_sha256: str
    source_page_index: int
    page_class: str
    difficulty_tags: tuple[str, ...]
    ground_truth: tuple[GroundTruthRecord, ...]

    def __post_init__(self) -> None:
        _sha256(self.source_sha256, "source_sha256")
        if self.source_page_index < 0:
            raise BenchmarkManifestError("source_page_index must be non-negative")
        if self.page_class not in _PAGE_CLASSES:
            raise BenchmarkManifestError(f"unknown page_class: {self.page_class}")
        object.__setattr__(self, "difficulty_tags", _tags(self.difficulty_tags, "difficulty_tags"))
        if not self.ground_truth:
            raise BenchmarkManifestError("benchmark page requires manually entered ground truth")
        region_ids = tuple(record.region_id for record in self.ground_truth)
        if len(region_ids) != len(set(region_ids)):
            raise BenchmarkManifestError("ground-truth region IDs must be unique per page")

    @property
    def id(self) -> str:
        return f"{self.source_sha256}:{self.source_page_index}"

    def to_dict(self) -> dict[str, object]:
        return {
            "difficulty_tags": list(self.difficulty_tags),
            "ground_truth": [record.to_dict() for record in self.ground_truth],
            "page_class": self.page_class,
            "source_page_index": self.source_page_index,
            "source_sha256": self.source_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BenchmarkPage:
        records = value.get("ground_truth")
        tags = value.get("difficulty_tags")
        if not isinstance(records, list) or not isinstance(tags, list):
            raise BenchmarkManifestError(
                "benchmark page needs ground_truth and difficulty_tags lists"
            )
        return cls(
            source_sha256=_sha256(value.get("source_sha256"), "source_sha256"),
            source_page_index=_non_negative_int(
                value.get("source_page_index"), "source_page_index"
            ),
            page_class=str(value.get("page_class", "")),
            difficulty_tags=tuple(str(tag) for tag in tags),
            ground_truth=tuple(GroundTruthRecord.from_dict(record) for record in records),
        )


@dataclass(frozen=True, slots=True)
class BenchmarkCorpus:
    version: str
    pages: tuple[BenchmarkPage, ...]
    name: str = "lispmdoc evaluation corpus"

    def __post_init__(self) -> None:
        if self.version != BENCHMARK_MANIFEST_VERSION:
            raise BenchmarkManifestError(
                f"unsupported benchmark manifest version: {self.version!r}"
            )
        if not self.pages:
            raise BenchmarkManifestError("benchmark corpus must include at least one page")
        ids = tuple(page.id for page in self.pages)
        if len(ids) != len(set(ids)):
            raise BenchmarkManifestError("benchmark corpus contains duplicate source pages")
        if not self.name:
            raise BenchmarkManifestError("benchmark corpus name is required")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "pages": [page.to_dict() for page in sorted(self.pages, key=lambda page: page.id)],
            "version": self.version,
        }

    def to_json(self) -> str:
        return (
            json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BenchmarkCorpus:
        pages = value.get("pages")
        if not isinstance(pages, list):
            raise BenchmarkManifestError("benchmark corpus pages must be a list")
        return cls(
            version=str(value.get("version", "")),
            pages=tuple(BenchmarkPage.from_dict(item) for item in pages),
            name=str(value.get("name", "lispmdoc evaluation corpus")),
        )


def load_corpus(path: str | Path) -> BenchmarkCorpus:
    """Load a versioned JSON or YAML corpus manifest; no truth is generated."""
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        raw = json.loads(text)
    elif source.suffix.lower() in {".yaml", ".yml"}:
        raw = yaml.safe_load(text)
    else:
        raise BenchmarkManifestError("benchmark manifest must use .json, .yaml, or .yml")
    if not isinstance(raw, Mapping):
        raise BenchmarkManifestError("benchmark manifest root must be an object")
    return BenchmarkCorpus.from_dict(raw)


def _non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BenchmarkManifestError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class InspectionCandidate:
    """A page eligible for selection, with externally supplied difficulty tags."""

    source_sha256: str
    source_page_index: int
    page_class: str
    difficulty_tags: tuple[str, ...]

    def __post_init__(self) -> None:
        _sha256(self.source_sha256, "source_sha256")
        if self.source_page_index < 0:
            raise ValueError("source_page_index must be non-negative")
        if self.page_class not in _PAGE_CLASSES:
            raise ValueError(f"unknown page_class: {self.page_class}")
        object.__setattr__(self, "difficulty_tags", _tags(self.difficulty_tags, "difficulty_tags"))

    @property
    def id(self) -> str:
        return f"{self.source_sha256}:{self.source_page_index}"

    def to_dict(self) -> dict[str, object]:
        return {
            "difficulty_tags": list(self.difficulty_tags),
            "page_class": self.page_class,
            "source_page_index": self.source_page_index,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """Selection is a review queue, not a benchmark corpus or ground truth."""

    selected: tuple[InspectionCandidate, ...]
    insufficient_strata: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "insufficient_strata": [list(stratum) for stratum in self.insufficient_strata],
            "selected": [candidate.to_dict() for candidate in self.selected],
        }


def candidates_from_inspections(
    inspections: Iterable[Mapping[str, Any]],
    difficulty_tags: Mapping[tuple[str, int], Iterable[str]],
) -> tuple[InspectionCandidate, ...]:
    """Convert Stage 1 inspection records into explicitly tagged candidates."""
    candidates: list[InspectionCandidate] = []
    for inspection in inspections:
        source = inspection.get("source")
        pages = inspection.get("pages")
        if not isinstance(source, Mapping) or not isinstance(pages, list):
            raise BenchmarkManifestError("inspection needs source and pages records")
        digest = _sha256(source.get("sha256"), "inspection source sha256")
        for fallback_index, page in enumerate(pages):
            if not isinstance(page, Mapping):
                raise BenchmarkManifestError("inspection page must be an object")
            index = (
                _non_negative_int(page.get("page_number", fallback_index + 1), "page_number") - 1
            )
            classification = page.get("classification")
            page_class = (
                classification.get("label") if isinstance(classification, Mapping) else None
            )
            tags = difficulty_tags.get((digest, index))
            if tags is None:
                continue
            candidates.append(InspectionCandidate(digest, index, str(page_class), tuple(tags)))
    return tuple(sorted(candidates, key=lambda candidate: candidate.id))


def select_stratified_pages(
    candidates: Iterable[InspectionCandidate], *, per_stratum: int = 1
) -> SelectionResult:
    """Select stable representatives for every observed ``(class, difficulty)`` stratum.

    Candidates are sorted by durable source identity, never by filesystem order
    or random seed.  A candidate can represent several strata but appears only
    once in the final queue.
    """
    if per_stratum <= 0:
        raise ValueError("per_stratum must be positive")
    by_stratum: dict[tuple[str, str], list[InspectionCandidate]] = {}
    for candidate in candidates:
        for difficulty in candidate.difficulty_tags:
            by_stratum.setdefault((candidate.page_class, difficulty), []).append(candidate)
    selected: dict[str, InspectionCandidate] = {}
    insufficient: list[tuple[str, str]] = []
    for stratum in sorted(by_stratum):
        choices = sorted(by_stratum[stratum], key=lambda candidate: candidate.id)
        if len(choices) < per_stratum:
            insufficient.append(stratum)
        for candidate in choices[:per_stratum]:
            selected[candidate.id] = candidate
    return SelectionResult(
        selected=tuple(selected[key] for key in sorted(selected)),
        insufficient_strata=tuple(insufficient),
    )
