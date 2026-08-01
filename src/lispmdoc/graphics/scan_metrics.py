"""Wave 8 edge, component, crop, and vectorization decision metrics."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, hypot
from typing import Literal

from lispmdoc.raster import PixelBox

from .scan import BinaryMask


@dataclass(frozen=True, slots=True)
class EdgeMetrics:
    foreground_edge_recall: float
    symmetric_p95_edge_distance_px: float
    reference_edge_count: int
    candidate_edge_count: int
    passes: bool


@dataclass(frozen=True, slots=True)
class MissingComponent:
    id: str
    pixel_area: int
    area_mm2: float
    box: PixelBox
    disposition: str | None


@dataclass(frozen=True, slots=True)
class ComponentOmissionMetrics:
    missing: tuple[MissingComponent, ...]
    undisposed_above_threshold: tuple[MissingComponent, ...]
    passes: bool


@dataclass(frozen=True, slots=True)
class TightCropMetrics:
    expected: PixelBox
    actual: PixelBox
    tight: bool


@dataclass(frozen=True, slots=True)
class VectorRasterDecision:
    selected: Literal["vector", "raster", "blocked"]
    vector_bytes: int
    raster_bytes: int
    documented_value_code: str | None
    passes: bool


def edge_metrics(
    reference: BinaryMask,
    candidate: BinaryMask,
    *,
    recall_tolerance_px: float = 1.0,
    minimum_recall: float = 0.99,
    maximum_p95_distance_px: float = 1.5,
    max_edge_points: int = 100_000,
) -> EdgeMetrics:
    _same_dimensions(reference, candidate)
    reference_edges = _edges(reference)
    candidate_edges = _edges(candidate)
    if max_edge_points < 1 or len(reference_edges) + len(candidate_edges) > max_edge_points:
        raise ValueError("edge metric point limit exceeded")
    if not reference_edges:
        recall = 1.0 if not candidate_edges else 0.0
    else:
        recall = sum(
            _nearest_distance(point, candidate_edges) <= recall_tolerance_px
            for point in reference_edges
        ) / len(reference_edges)
    reference_distances = [_nearest_distance(point, candidate_edges) for point in reference_edges]
    candidate_distances = [_nearest_distance(point, reference_edges) for point in candidate_edges]
    p95 = max(_percentile95(reference_distances), _percentile95(candidate_distances))
    return EdgeMetrics(
        recall,
        p95,
        len(reference_edges),
        len(candidate_edges),
        recall >= minimum_recall and p95 <= maximum_p95_distance_px,
    )


def component_omission_metrics(
    reference: BinaryMask,
    candidate: BinaryMask,
    *,
    dpi: int = 300,
    maximum_undisposed_area_mm2: float = 0.25,
    dispositions: dict[str, str] | None = None,
) -> ComponentOmissionMetrics:
    _same_dimensions(reference, candidate)
    if dpi < 1:
        raise ValueError("DPI must be positive")
    disposition_map = dispositions or {}
    missing: list[MissingComponent] = []
    omitted_pixels = reference.foreground - candidate.foreground
    for index, component in enumerate(_components(frozenset(omitted_pixels))):
        component_id = f"component-{index:06d}"
        box = _box(component)
        area_mm2 = len(component) * (25.4 / dpi) ** 2
        missing.append(
            MissingComponent(
                component_id,
                len(component),
                area_mm2,
                box,
                disposition_map.get(component_id),
            )
        )
    undisposed = tuple(
        component
        for component in missing
        if component.area_mm2 > maximum_undisposed_area_mm2
        and not (component.disposition and component.disposition.strip())
    )
    return ComponentOmissionMetrics(tuple(missing), undisposed, not undisposed)


def tight_crop_metrics(mask: BinaryMask, actual: PixelBox) -> TightCropMetrics:
    points = mask.foreground
    if not points:
        raise ValueError("tight-crop metric requires foreground")
    expected = _box(points)
    return TightCropMetrics(expected, actual, expected == actual)


def vector_raster_decision(
    *,
    vector_bytes: int,
    raster_bytes: int,
    documented_value_code: str | None = None,
    fidelity_acceptable: bool,
) -> VectorRasterDecision:
    """Require vector byte advantage or an explicit semantic/fidelity value."""

    if vector_bytes < 0 or raster_bytes < 0:
        raise ValueError("candidate byte sizes must be non-negative")
    documented = bool(documented_value_code and documented_value_code.strip())
    if fidelity_acceptable and (vector_bytes < raster_bytes or documented):
        return VectorRasterDecision(
            "vector", vector_bytes, raster_bytes, documented_value_code, True
        )
    if fidelity_acceptable and not documented and raster_bytes <= vector_bytes:
        return VectorRasterDecision("raster", vector_bytes, raster_bytes, None, True)
    return VectorRasterDecision("blocked", vector_bytes, raster_bytes, documented_value_code, False)


def _edges(mask: BinaryMask) -> frozenset[tuple[int, int]]:
    points = mask.foreground
    return frozenset(
        point
        for point in points
        if any(
            neighbor not in points
            for neighbor in (
                (point[0] - 1, point[1]),
                (point[0] + 1, point[1]),
                (point[0], point[1] - 1),
                (point[0], point[1] + 1),
            )
        )
    )


def _nearest_distance(
    point: tuple[int, int],
    targets: frozenset[tuple[int, int]],
) -> float:
    if not targets:
        return float("inf")
    return min(hypot(point[0] - target[0], point[1] - target[1]) for target in targets)


def _percentile95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, ceil(0.95 * len(ordered)) - 1)]


def _same_dimensions(left: BinaryMask, right: BinaryMask) -> None:
    if (left.width, left.height) != (right.width, right.height):
        raise ValueError("metric masks must have identical dimensions")


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
