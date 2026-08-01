"""Semantic proposal metrics and exact diplomatic code checks."""

from __future__ import annotations

from dataclasses import dataclass

from .types import PhysicalRegion, SemanticProposal


@dataclass(frozen=True, slots=True)
class SemanticTruth:
    physical_region_id: str
    kind: str


@dataclass(frozen=True, slots=True)
class SemanticMetric:
    kind: str
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True, slots=True)
class SemanticReport:
    per_kind: tuple[SemanticMetric, ...]
    macro_f1: float


def semantic_macro_f1(
    truth: tuple[SemanticTruth, ...], proposals: tuple[SemanticProposal, ...]
) -> SemanticReport:
    expected = {(item.physical_region_id, item.kind) for item in truth}
    predicted = {
        (region_id, proposal.kind)
        for proposal in proposals
        for region_id in proposal.physical_region_ids
    }
    labels = sorted({kind for _, kind in expected | predicted})
    metrics: list[SemanticMetric] = []
    for label in labels:
        actual = {item for item in expected if item[1] == label}
        seen = {item for item in predicted if item[1] == label}
        true_positive = len(actual & seen)
        precision = true_positive / len(seen) if seen else 0.0
        recall = true_positive / len(actual) if actual else 0.0
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        metrics.append(SemanticMetric(label, precision, recall, f1))
    return SemanticReport(
        tuple(metrics), sum(item.f1 for item in metrics) / len(metrics) if metrics else 1.0
    )


def exact_code_fidelity(
    regions: tuple[PhysicalRegion, ...], proposals: tuple[SemanticProposal, ...]
) -> bool:
    """Code proposals must preserve source text byte-for-byte in their evidence property."""
    by_id = {region.id: region.text for region in regions}
    for proposal in proposals:
        if proposal.kind not in {"code", "terminal"}:
            continue
        recorded = dict(proposal.properties).get("diplomatic-text")
        if (
            len(proposal.physical_region_ids) != 1
            or recorded != by_id[proposal.physical_region_ids[0]]
        ):
            return False
    return True
