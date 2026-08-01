"""Typed physical and logical IR additions used by replacement-ready stages.

These are additive to the Phase 1 ``SceneObject`` compatibility record.  New
extractors should emit these types; the Phase 1 migration can wrap its coarse
text objects without inventing glyph, vector, or font evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .geometry import Point


@dataclass(frozen=True, slots=True)
class Polygon:
    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            raise ValueError("polygon requires at least three points")

    def to_dict(self) -> dict[str, object]:
        return {"points": [point.to_dict() for point in self.points]}


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    sha256: str
    role: str

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or not self.role:
            raise ValueError("evidence reference requires SHA-256 and role")

    def to_dict(self) -> dict[str, str]:
        return {"sha256": self.sha256, "role": self.role}


@dataclass(frozen=True, slots=True)
class ReadingEdge:
    source_id: str
    target_id: str
    relation: Literal[
        "reading-next", "contains", "adjacent", "caption-for", "table-cell", "logical-to-physical"
    ]
    confidence_milli: int = 1000

    def __post_init__(self) -> None:
        if not self.source_id or not self.target_id or self.source_id == self.target_id:
            raise ValueError("reading edges require distinct non-empty endpoints")
        if not 0 <= self.confidence_milli <= 1000:
            raise ValueError("reading edge confidence must be 0..1000")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation": self.relation,
            "confidence_milli": self.confidence_milli,
        }


def linearize_reading_order(
    nodes: tuple[str, ...], edges: tuple[ReadingEdge, ...]
) -> tuple[str, ...]:
    """Topologically linearize only reading-next edges with stable ID tie-breaks."""
    node_set = set(nodes)
    if len(node_set) != len(nodes):
        raise ValueError("reading graph nodes must be unique")
    next_edges = tuple(edge for edge in edges if edge.relation == "reading-next")
    if any(edge.source_id not in node_set or edge.target_id not in node_set for edge in next_edges):
        raise ValueError("reading edge references a missing node")
    incoming = {node: 0 for node in nodes}
    outgoing: dict[str, list[str]] = {node: [] for node in nodes}
    for edge in next_edges:
        outgoing[edge.source_id].append(edge.target_id)
        incoming[edge.target_id] += 1
    result: list[str] = []
    ready = sorted(node for node, degree in incoming.items() if degree == 0)
    while ready:
        node = ready.pop(0)
        result.append(node)
        for target in sorted(outgoing[node]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
                ready.sort()
    if len(result) != len(nodes):
        raise ValueError("reading-next graph must be acyclic")
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ConformanceFacets:
    """Explicit, non-substitutable claims for final publication gates."""

    fidelity: Literal["unmeasured", "pass", "fail"] = "unmeasured"
    text: Literal["unmeasured", "pass", "fail"] = "unmeasured"
    structure: Literal["unmeasured", "pass", "fail"] = "unmeasured"
    accessibility: Literal["unmeasured", "pass", "fail"] = "unmeasured"
    reproducibility: Literal["unmeasured", "pass", "fail"] = "unmeasured"
    raster_policy: Literal["unmeasured", "pass", "fail"] = "unmeasured"
    size: Literal["unmeasured", "pass", "fail"] = "unmeasured"
    distribution_rights: Literal["unmeasured", "pass", "fail"] = "unmeasured"

    @property
    def replacement_ready(self) -> bool:
        return all(value == "pass" for value in self.to_dict().values())

    def to_dict(self) -> dict[str, str]:
        return {
            "fidelity": self.fidelity,
            "text": self.text,
            "structure": self.structure,
            "accessibility": self.accessibility,
            "reproducibility": self.reproducibility,
            "raster_policy": self.raster_policy,
            "size": self.size,
            "distribution_rights": self.distribution_rights,
        }
