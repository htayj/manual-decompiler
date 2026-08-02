"""Spatially align OCR lines to reviewed source-backed regions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from lispmdoc.ocr.evaluation import EvaluationReport, GroundTruthRegion, evaluate_ground_truth

from .authoritative import AuthoritativeRegionTruth


@dataclass(frozen=True, slots=True)
class SpatialTextLine:
    """One OCR line or block in source-render pixel coordinates."""

    text: str
    bbox: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        left, top, right, bottom = self.bbox
        if right <= left or bottom <= top:
            raise ValueError("spatial OCR boxes must have positive area")


@dataclass(frozen=True, slots=True)
class SpatialEvaluation:
    report: EvaluationReport
    predictions: dict[str, str]
    unassigned_lines: tuple[SpatialTextLine, ...]


def semantic_ocr_text(text: str, kind: str) -> str:
    """Remove typesetter/editor wrapping without weakening code comparison."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if kind == "code":
        return normalized.strip("\n")
    return re.sub(r"\s+", " ", normalized).strip()


def _bounds(region: AuthoritativeRegionTruth) -> tuple[float, float, float, float]:
    xs = [point[0] for point in region.geometry.polygon]
    ys = [point[1] for point in region.geometry.polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap_area(
    left: tuple[float, float, float, float], right: tuple[float, float, float, float]
) -> float:
    width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    return width * height


def evaluate_spatial_lines(
    regions: tuple[AuthoritativeRegionTruth, ...],
    lines: tuple[SpatialTextLine, ...],
) -> SpatialEvaluation:
    """Assign OCR lines by overlap, then evaluate semantic text by stable region ID."""

    assigned: dict[str, list[SpatialTextLine]] = {
        region.geometry.region_id: [] for region in regions
    }
    unassigned: list[SpatialTextLine] = []
    region_bounds = [(region, _bounds(region)) for region in regions]
    for line in lines:
        candidates = [
            (_overlap_area(line.bbox, bounds), region)
            for region, bounds in region_bounds
            if _overlap_area(line.bbox, bounds) > 0
        ]
        if not candidates:
            unassigned.append(line)
            continue
        _, region = max(
            candidates,
            key=lambda item: (item[0], -item[1].geometry.reading_order),
        )
        assigned[region.geometry.region_id].append(line)

    predictions: dict[str, str] = {}
    truth: list[GroundTruthRegion] = []
    for region in regions:
        region_id = region.geometry.region_id
        ordered = sorted(assigned[region_id], key=lambda item: (item.bbox[1], item.bbox[0]))
        prediction = " ".join(item.text.strip() for item in ordered if item.text.strip())
        evaluator_kind = "code" if region.kind == "code" else "prose"
        predictions[region_id] = semantic_ocr_text(prediction, evaluator_kind)
        truth.append(
            GroundTruthRegion(
                region_id,
                semantic_ocr_text(region.literal_text, evaluator_kind),
                evaluator_kind,
                region.required,
            )
        )
    return SpatialEvaluation(
        evaluate_ground_truth(truth, predictions),
        predictions,
        tuple(unassigned),
    )
