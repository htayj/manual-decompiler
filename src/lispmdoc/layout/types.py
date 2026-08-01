"""Deterministic physical-layout evidence, proposals, graphs, and findings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from lispmdoc.model import Box, Point

EvidenceSource = Literal["pdf-object", "ocr-layout"]
ProposalStatus = Literal["accepted-evidence", "proposed", "review"]


@dataclass(frozen=True, slots=True)
class LayoutEvidenceRegion:
    """One supplied physical region; labels remain evidence rather than truth."""

    id: str
    box: Box
    kind: str = "unknown"
    text: str | None = None
    baseline: tuple[Point, Point] | None = None
    polygon: tuple[Point, ...] = ()
    confidence_milli: int = 0
    source: EvidenceSource = "ocr-layout"
    reading_order: int | None = None
    content_bearing: bool = True
    intentional_exclusion_reason: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.kind:
            raise ValueError("layout evidence requires non-empty ID and kind")
        if not 0 <= self.confidence_milli <= 1000:
            raise ValueError("layout confidence must be in 0..1000")
        if self.reading_order is not None and self.reading_order < 0:
            raise ValueError("reading_order must be non-negative when supplied")
        if self.baseline is not None and self.baseline[0] == self.baseline[1]:
            raise ValueError("baseline endpoints must be distinct")
        if self.polygon and len(self.polygon) < 3:
            raise ValueError("region polygon requires at least three points")
        if self.intentional_exclusion_reason == "":
            raise ValueError("intentional exclusion reason must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "box": self.box.to_dict(),
            "confidence_milli": self.confidence_milli,
            "content_bearing": self.content_bearing,
            "id": self.id,
            "kind": self.kind,
            "polygon": [point.to_dict() for point in self.polygon],
            "properties": _json_mapping(self.properties),
            "source": self.source,
        }
        if self.text is not None:
            result["text"] = self.text
        if self.baseline is not None:
            result["baseline"] = [point.to_dict() for point in self.baseline]
        if self.reading_order is not None:
            result["reading_order"] = self.reading_order
        if self.intentional_exclusion_reason is not None:
            result["intentional_exclusion_reason"] = self.intentional_exclusion_reason
        return result


@dataclass(frozen=True, slots=True)
class LayoutPageInput:
    page_id: str
    page_box: Box
    page_class: str
    pdf_regions: tuple[LayoutEvidenceRegion, ...] = ()
    ocr_regions: tuple[LayoutEvidenceRegion, ...] = ()

    def __post_init__(self) -> None:
        if not self.page_id:
            raise ValueError("layout page_id is required")
        ids = [region.id for region in (*self.pdf_regions, *self.ocr_regions)]
        if len(ids) != len(set(ids)):
            raise ValueError("supplied layout evidence region IDs must be unique")
        for region in self.pdf_regions:
            if region.source != "pdf-object":
                raise ValueError("pdf_regions must use source='pdf-object'")
        for region in self.ocr_regions:
            if region.source != "ocr-layout":
                raise ValueError("ocr_regions must use source='ocr-layout'")


@dataclass(frozen=True, slots=True)
class RegionProposal:
    id: str
    kind: str
    box: Box
    parent_id: str | None
    source_region_ids: tuple[str, ...] = ()
    confidence_milli: int = 0
    status: ProposalStatus = "proposed"
    treatment: str | None = None
    intentional_exclusion_reason: str | None = None
    baseline: tuple[Point, Point] | None = None
    polygon: tuple[Point, ...] = ()
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.kind:
            raise ValueError("region proposal requires ID and kind")
        if not 0 <= self.confidence_milli <= 1000:
            raise ValueError("proposal confidence must be in 0..1000")
        if len(self.source_region_ids) != len(set(self.source_region_ids)):
            raise ValueError("proposal source region IDs must be unique")
        if self.treatment is not None and self.intentional_exclusion_reason is not None:
            raise ValueError("a proposal cannot be both treated and intentionally excluded")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "box": self.box.to_dict(),
            "confidence_milli": self.confidence_milli,
            "id": self.id,
            "kind": self.kind,
            "polygon": [point.to_dict() for point in self.polygon],
            "properties": _json_mapping(self.properties),
            "source_region_ids": list(self.source_region_ids),
            "status": self.status,
        }
        if self.parent_id is not None:
            result["parent_id"] = self.parent_id
        if self.treatment is not None:
            result["treatment"] = self.treatment
        if self.intentional_exclusion_reason is not None:
            result["intentional_exclusion_reason"] = self.intentional_exclusion_reason
        if self.baseline is not None:
            result["baseline"] = [point.to_dict() for point in self.baseline]
        return result


@dataclass(frozen=True, slots=True)
class LayoutEdge:
    source_id: str
    target_id: str
    relation: Literal["reading-next", "contains", "caption-for", "table-cell", "adjacent"]
    confidence_milli: int

    def __post_init__(self) -> None:
        if not self.source_id or not self.target_id or self.source_id == self.target_id:
            raise ValueError("layout edge requires distinct non-empty endpoints")
        if not 0 <= self.confidence_milli <= 1000:
            raise ValueError("layout edge confidence must be in 0..1000")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "confidence_milli": self.confidence_milli,
            "relation": self.relation,
            "source_id": self.source_id,
            "target_id": self.target_id,
        }


@dataclass(frozen=True, slots=True)
class LayoutFinding:
    code: str
    subject_ids: tuple[str, ...]
    message: str
    severity: Literal["info", "medium", "high"] = "medium"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "subject_ids": list(self.subject_ids),
        }


@dataclass(frozen=True, slots=True)
class ReadingOrderGraph:
    nodes: tuple[str, ...]
    edges: tuple[LayoutEdge, ...]
    deterministic_linearization: tuple[str, ...]
    authoritative: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "authoritative": self.authoritative,
            "deterministic_linearization": list(self.deterministic_linearization),
            "edges": [edge.to_dict() for edge in self.edges],
            "nodes": list(self.nodes),
        }


@dataclass(frozen=True, slots=True)
class CoverageReport:
    supplied_content_regions: int
    treated_regions: tuple[str, ...]
    intentionally_excluded_regions: tuple[str, ...]
    missing_regions: tuple[str, ...]
    multiply_accounted_regions: tuple[str, ...]

    @property
    def passes(self) -> bool:
        return not self.missing_regions and not self.multiply_accounted_regions

    def to_dict(self) -> dict[str, Any]:
        return {
            "intentionally_excluded_regions": list(self.intentionally_excluded_regions),
            "missing_regions": list(self.missing_regions),
            "multiply_accounted_regions": list(self.multiply_accounted_regions),
            "passes": self.passes,
            "supplied_content_regions": self.supplied_content_regions,
            "treated_regions": list(self.treated_regions),
        }


@dataclass(frozen=True, slots=True)
class LayoutResult:
    page_id: str
    evidence_source: EvidenceSource
    proposals: tuple[RegionProposal, ...]
    reading_order: ReadingOrderGraph
    coverage: CoverageReport
    findings: tuple[LayoutFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage.to_dict(),
            "evidence_source": self.evidence_source,
            "findings": [finding.to_dict() for finding in self.findings],
            "page_id": self.page_id,
            "proposals": [proposal.to_dict() for proposal in self.proposals],
            "reading_order": self.reading_order.to_dict(),
        }


def _json_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_value(value[key]) for key in sorted(value, key=str)}


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _json_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value
