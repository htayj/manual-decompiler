"""Deterministic supplied-mask decomposition and schematic candidate records."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import hypot, isfinite
from typing import Literal

from lispmdoc.raster import PixelBox

LayerKind = Literal["text", "line-art", "continuous-tone", "halftone", "texture"]
PrimitiveKind = Literal[
    "rule",
    "rectangle",
    "circle-candidate",
    "arrow-candidate",
    "leader-candidate",
    "junction-candidate",
]


@dataclass(frozen=True, slots=True)
class BinaryMask:
    width: int
    height: int
    rows: tuple[tuple[bool, ...], ...]

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("mask dimensions must be positive")
        if len(self.rows) != self.height or any(len(row) != self.width for row in self.rows):
            raise ValueError("mask rows do not match declared dimensions")
        if any(not isinstance(value, bool) for row in self.rows for value in row):
            raise ValueError("binary mask values must be bool")

    @classmethod
    def empty(cls, width: int, height: int) -> BinaryMask:
        return cls(width, height, tuple(tuple(False for _ in range(width)) for _ in range(height)))

    @property
    def foreground(self) -> frozenset[tuple[int, int]]:
        return frozenset(
            (x, y) for y, row in enumerate(self.rows) for x, value in enumerate(row) if value
        )

    @property
    def sha256(self) -> str:
        packed = bytes(value for row in self.rows for value in row)
        return sha256(f"{self.width}:{self.height}:".encode() + packed).hexdigest()


@dataclass(frozen=True, slots=True)
class ScanLayer:
    kind: LayerKind
    mask: BinaryMask
    evidence_id: str

    def __post_init__(self) -> None:
        if self.kind not in {"text", "line-art", "continuous-tone", "halftone", "texture"}:
            raise ValueError("unknown scan layer kind")
        if not self.evidence_id:
            raise ValueError("scan layer evidence id is required")


@dataclass(frozen=True, slots=True)
class ScanDecomposition:
    layers: tuple[ScanLayer, ...]
    uncovered_foreground: frozenset[tuple[int, int]]


@dataclass(frozen=True, slots=True)
class LabelReference:
    id: str
    text: str
    box: PixelBox
    source_component_id: str

    def __post_init__(self) -> None:
        if not self.id or not self.text or not self.source_component_id:
            raise ValueError("label id, searchable text, and source component are required")


@dataclass(frozen=True, slots=True)
class LabelReinsertion:
    label_id: str
    searchable_text: str
    source_component_id: str
    source_box: PixelBox
    anchor: tuple[float, float]


@dataclass(frozen=True, slots=True)
class LabelSeparation:
    remaining_mask: BinaryMask
    reinsertions: tuple[LabelReinsertion, ...]


@dataclass(frozen=True, slots=True)
class VisibleSchematicProposal:
    id: str
    kind: PrimitiveKind
    box: PixelBox
    evidence_pixels: tuple[tuple[int, int], ...]
    source_mask_sha256: str
    semantic_connectivity: Literal["unmeasured"] = "unmeasured"


@dataclass(frozen=True, slots=True)
class TraceLimits:
    max_input_points: int = 100_000
    max_output_points: int = 100_000

    def __post_init__(self) -> None:
        if self.max_input_points < 2 or self.max_output_points < 2:
            raise ValueError("trace limits must allow at least two points")


@dataclass(frozen=True, slots=True)
class TraceProposal:
    source_mask_sha256: str
    input_points: int
    points: tuple[tuple[float, float], ...]
    recorded_max_edge_error_px: float
    tolerance_px: float


def decompose_supplied_masks(
    source_foreground: BinaryMask,
    layers: tuple[ScanLayer, ...],
) -> ScanDecomposition:
    """Validate exclusive, dimension-matched supplied classification masks."""

    required = {"text", "line-art", "continuous-tone", "halftone", "texture"}
    kinds = [layer.kind for layer in layers]
    if set(kinds) != required or len(kinds) != len(required):
        raise ValueError("exactly one supplied layer for every scan class is required")
    claimed: set[tuple[int, int]] = set()
    source = source_foreground.foreground
    for layer in layers:
        if (layer.mask.width, layer.mask.height) != (
            source_foreground.width,
            source_foreground.height,
        ):
            raise ValueError(f"{layer.kind} layer dimensions differ from source")
        foreground = layer.mask.foreground
        if not foreground <= source:
            raise ValueError(f"{layer.kind} layer claims pixels outside source foreground")
        overlap = claimed & foreground
        if overlap:
            raise ValueError(f"{layer.kind} layer overlaps another supplied class")
        claimed.update(foreground)
    return ScanDecomposition(layers, frozenset(source - claimed))


def remove_labels(mask: BinaryMask, labels: tuple[LabelReference, ...]) -> LabelSeparation:
    """Remove supplied label boxes while retaining searchable spatial references."""

    remaining = set(mask.foreground)
    reinsertions: list[LabelReinsertion] = []
    seen: set[str] = set()
    for label in labels:
        if label.id in seen:
            raise ValueError(f"duplicate label id {label.id}")
        seen.add(label.id)
        if label.box.right > mask.width or label.box.bottom > mask.height:
            raise ValueError(f"label {label.id} lies outside its source mask")
        for y in range(label.box.y, label.box.bottom):
            for x in range(label.box.x, label.box.right):
                remaining.discard((x, y))
        reinsertions.append(
            LabelReinsertion(
                label.id,
                label.text,
                label.source_component_id,
                label.box,
                (label.box.x + label.box.width / 2, label.box.y + label.box.height / 2),
            )
        )
    return LabelSeparation(
        _mask_from_points(mask.width, mask.height, remaining), tuple(reinsertions)
    )


def detect_visible_primitives(
    mask: BinaryMask,
    *,
    max_components: int = 10_000,
) -> tuple[VisibleSchematicProposal, ...]:
    """Detect conservative visible-shape candidates without connectivity claims."""

    components = _components(mask.foreground)
    if len(components) > max_components:
        raise ValueError("component limit exceeded")
    proposals: list[VisibleSchematicProposal] = []
    for component_index, component in enumerate(components):
        box = _box(component)
        kind = _component_kind(component, box)
        proposals.append(
            VisibleSchematicProposal(
                _proposal_id(
                    mask.sha256,
                    component_index,
                    kind,
                    tuple(sorted(component, key=lambda point: (point[1], point[0]))),
                ),
                kind,
                box,
                tuple(sorted(component, key=lambda point: (point[1], point[0]))),
                mask.sha256,
            )
        )
        for point in sorted(component):
            if _neighbor_count(point, component) >= 3:
                junction_box = PixelBox(point[0], point[1], 1, 1)
                proposals.append(
                    VisibleSchematicProposal(
                        _proposal_id(mask.sha256, component_index, "junction-candidate", (point,)),
                        "junction-candidate",
                        junction_box,
                        (point,),
                        mask.sha256,
                    )
                )
    return tuple(proposals)


def simplify_supplied_trace(
    mask: BinaryMask,
    points: tuple[tuple[float, float], ...],
    *,
    tolerance_px: float,
    limits: TraceLimits | None = None,
) -> TraceProposal:
    """Simplify a supplied ordered trace and record its measured point-to-path error."""

    active_limits = limits or TraceLimits()
    if not isfinite(tolerance_px) or tolerance_px < 0:
        raise ValueError("trace tolerance must be finite and non-negative")
    if not 2 <= len(points) <= active_limits.max_input_points:
        raise ValueError("supplied trace point count is outside limits")
    if any(not (0 <= x <= mask.width and 0 <= y <= mask.height) for x, y in points):
        raise ValueError("supplied trace point lies outside mask bounds")
    simplified = _rdp(points, tolerance_px)
    if len(simplified) > active_limits.max_output_points:
        raise ValueError("simplified trace exceeds output point limit")
    error = max(_polyline_distance(point, simplified) for point in points)
    if error > tolerance_px + 1e-12:
        raise ValueError("simplifier exceeded the requested error bound")
    return TraceProposal(mask.sha256, len(points), simplified, error, tolerance_px)


def _component_kind(
    component: frozenset[tuple[int, int]],
    box: PixelBox,
) -> PrimitiveKind:
    if box.height == 1 or box.width == 1:
        return "rule"
    boundary = {
        (x, y)
        for x in range(box.x, box.right)
        for y in range(box.y, box.bottom)
        if x in {box.x, box.right - 1} or y in {box.y, box.bottom - 1}
    }
    if component == boundary:
        return "rectangle"
    center = (box.x + (box.width - 1) / 2, box.y + (box.height - 1) / 2)
    radii = [hypot(x - center[0], y - center[1]) for x, y in component]
    if len(component) >= 8 and max(radii) - min(radii) <= 1.25:
        return "circle-candidate"
    endpoints = sum(_neighbor_count(point, component) == 1 for point in component)
    if endpoints == 2 and max(box.width, box.height) >= 3 * min(box.width, box.height):
        return "leader-candidate"
    if endpoints >= 3:
        return "arrow-candidate"
    return "leader-candidate"


def _components(points: frozenset[tuple[int, int]]) -> tuple[frozenset[tuple[int, int]], ...]:
    pending = set(points)
    result: list[frozenset[tuple[int, int]]] = []
    while pending:
        start = min(pending, key=lambda point: (point[1], point[0]))
        pending.remove(start)
        component = {start}
        frontier = [start]
        while frontier:
            x, y = frontier.pop()
            for neighbor in (
                (x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx != 0 or dy != 0
            ):
                if neighbor in pending:
                    pending.remove(neighbor)
                    component.add(neighbor)
                    frontier.append(neighbor)
        result.append(frozenset(component))
    return tuple(result)


def _box(points: frozenset[tuple[int, int]]) -> PixelBox:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return PixelBox(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def _neighbor_count(point: tuple[int, int], component: frozenset[tuple[int, int]]) -> int:
    x, y = point
    return sum((x + dx, y + dy) in component for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)))


def _mask_from_points(width: int, height: int, points: set[tuple[int, int]]) -> BinaryMask:
    return BinaryMask(
        width,
        height,
        tuple(tuple((x, y) in points for x in range(width)) for y in range(height)),
    )


def _proposal_id(mask_hash: str, index: int, kind: str, evidence: object) -> str:
    payload = f"{mask_hash}:{index}:{kind}:{evidence!r}".encode()
    return f"scan-{sha256(payload).hexdigest()[:20]}"


def _rdp(
    points: tuple[tuple[float, float], ...],
    tolerance: float,
) -> tuple[tuple[float, float], ...]:
    kept = {0, len(points) - 1}
    pending = [(0, len(points) - 1)]
    while pending:
        start_index, end_index = pending.pop()
        if end_index - start_index <= 1:
            continue
        start, end = points[start_index], points[end_index]
        candidates = (
            (_segment_distance(points[index], start, end), index)
            for index in range(start_index + 1, end_index)
        )
        maximum, split = max(candidates, key=lambda item: (item[0], -item[1]))
        if maximum > tolerance:
            kept.add(split)
            pending.append((split, end_index))
            pending.append((start_index, split))
    return tuple(points[index] for index in sorted(kept))


def _polyline_distance(
    point: tuple[float, float],
    line: tuple[tuple[float, float], ...],
) -> float:
    return min(
        _segment_distance(point, start, end) for start, end in zip(line, line[1:], strict=False)
    )


def _segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == 0 and dy == 0:
        return hypot(point[0] - start[0], point[1] - start[1])
    position = max(
        0.0,
        min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)),
    )
    return hypot(point[0] - (start[0] + position * dx), point[1] - (start[1] + position * dy))
