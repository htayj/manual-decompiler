"""Proposal-only semantic records, linked back to immutable physical regions."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal


def stable_id(kind: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode()
    return f"{kind}-{sha256(payload).hexdigest()[:20]}"


@dataclass(frozen=True, slots=True)
class PhysicalRegion:
    """Layout-owned evidence supplied to semantic inference; text is diplomatic."""

    id: str
    page_sequence: int
    reading_order: int
    text: str
    kind: str = "text"

    def __post_init__(self) -> None:
        if not self.id or self.page_sequence < 1 or self.reading_order < 0:
            raise ValueError("physical region needs ID, positive page, and non-negative order")


SemanticKind = Literal[
    "part",
    "chapter",
    "section",
    "paragraph",
    "list",
    "list-item",
    "table",
    "figure",
    "caption",
    "note",
    "code",
    "terminal",
    "index",
    "cross-reference",
    "math",
    "running-matter",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class SemanticProposal:
    """Proposed node that cannot change any physical-region text."""

    id: str
    kind: SemanticKind
    physical_region_ids: tuple[str, ...]
    source: Literal["heuristic", "generated", "human"] = "heuristic"
    parent_id: str | None = None
    properties: tuple[tuple[str, str], ...] = ()
    fallback: tuple[Literal["diplomatic", "visual"], ...] = ()

    def __post_init__(self) -> None:
        if not self.id or not self.physical_region_ids:
            raise ValueError("semantic nodes require IDs and at least one physical region")
        if len(set(self.physical_region_ids)) != len(self.physical_region_ids):
            raise ValueError("semantic node physical references must be unique")
        if self.source == "generated" and self.kind == "unknown":
            raise ValueError("generated proposals must name a concrete proposed kind")
        if self.kind == "math" and set(self.fallback) != {"diplomatic", "visual"}:
            raise ValueError("math proposals require diplomatic and visual fallbacks")


@dataclass(frozen=True, slots=True)
class SemanticFinding:
    id: str
    code: str
    severity: Literal["medium", "high"]
    physical_region_ids: tuple[str, ...]
    message: str


@dataclass(frozen=True, slots=True)
class SemanticResult:
    proposals: tuple[SemanticProposal, ...]
    findings: tuple[SemanticFinding, ...]

    def assert_references(self, regions: tuple[PhysicalRegion, ...]) -> None:
        identifiers = {region.id for region in regions}
        for proposal in self.proposals:
            if not set(proposal.physical_region_ids).issubset(identifiers):
                raise ValueError("semantic proposal references a missing physical region")
