"""Deterministic Wave 4 geometry, reading-order, and table metrics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from lispmdoc.model import Box


@dataclass(frozen=True, slots=True)
class DetectionMetrics:
    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision_milli(self) -> int:
        denominator = self.true_positive + self.false_positive
        return 1000 if denominator == 0 else self.true_positive * 1000 // denominator

    @property
    def recall_milli(self) -> int:
        denominator = self.true_positive + self.false_negative
        return 1000 if denominator == 0 else self.true_positive * 1000 // denominator

    @property
    def f1_milli(self) -> int:
        denominator = 2 * self.true_positive + self.false_positive + self.false_negative
        return 1000 if denominator == 0 else 2 * self.true_positive * 1000 // denominator

    def to_dict(self) -> dict[str, int]:
        return {
            "f1_milli": self.f1_milli,
            "false_negative": self.false_negative,
            "false_positive": self.false_positive,
            "precision_milli": self.precision_milli,
            "recall_milli": self.recall_milli,
            "true_positive": self.true_positive,
        }


@dataclass(frozen=True, slots=True)
class ReadingOrderMetrics:
    correct_pairs: int
    total_pairs: int
    missing_nodes: tuple[str, ...]

    @property
    def accuracy_milli(self) -> int:
        return 1000 if self.total_pairs == 0 else self.correct_pairs * 1000 // self.total_pairs

    def to_dict(self) -> dict[str, Any]:
        return {
            "accuracy_milli": self.accuracy_milli,
            "correct_pairs": self.correct_pairs,
            "missing_nodes": list(self.missing_nodes),
            "total_pairs": self.total_pairs,
        }


@dataclass(frozen=True, slots=True)
class TableMetrics:
    cells: DetectionMetrics
    spans: DetectionMetrics

    @property
    def combined_f1_milli(self) -> int:
        return (self.cells.f1_milli + self.spans.f1_milli) // 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "cells": self.cells.to_dict(),
            "combined_f1_milli": self.combined_f1_milli,
            "spans": self.spans.to_dict(),
        }


def geometric_region_metrics(
    predicted: Sequence[Any],
    truth: Sequence[Any],
    *,
    iou_threshold_milli: int = 500,
    require_kind_match: bool = True,
) -> DetectionMetrics:
    """Greedily one-to-one match highest-IoU regions with deterministic ties."""

    if not 0 <= iou_threshold_milli <= 1000:
        raise ValueError("IoU threshold must be in 0..1000")
    pairs: list[tuple[int, str, str, int, int]] = []
    for predicted_index, predicted_region in enumerate(predicted):
        for truth_index, truth_region in enumerate(truth):
            if require_kind_match and _kind(predicted_region) != _kind(truth_region):
                continue
            overlap = iou_milli(_box(predicted_region), _box(truth_region))
            if overlap >= iou_threshold_milli:
                pairs.append(
                    (
                        -overlap,
                        _identifier(predicted_region, predicted_index),
                        _identifier(truth_region, truth_index),
                        predicted_index,
                        truth_index,
                    )
                )
    matched_predicted: set[int] = set()
    matched_truth: set[int] = set()
    for _negative_iou, _predicted_id, _truth_id, predicted_index, truth_index in sorted(pairs):
        if predicted_index in matched_predicted or truth_index in matched_truth:
            continue
        matched_predicted.add(predicted_index)
        matched_truth.add(truth_index)
    true_positive = len(matched_predicted)
    return DetectionMetrics(
        true_positive,
        len(predicted) - true_positive,
        len(truth) - true_positive,
    )


def reading_order_pair_accuracy(
    predicted_order: Sequence[str], truth_order: Sequence[str]
) -> ReadingOrderMetrics:
    if len(predicted_order) != len(set(predicted_order)):
        raise ValueError("predicted reading order contains duplicate nodes")
    if len(truth_order) != len(set(truth_order)):
        raise ValueError("truth reading order contains duplicate nodes")
    predicted_positions = {node: index for index, node in enumerate(predicted_order)}
    missing = tuple(node for node in truth_order if node not in predicted_positions)
    correct = 0
    total = 0
    for left_index, left in enumerate(truth_order):
        for right in truth_order[left_index + 1 :]:
            total += 1
            if left in predicted_positions and right in predicted_positions:
                correct += int(predicted_positions[left] < predicted_positions[right])
    return ReadingOrderMetrics(correct, total, missing)


def table_cell_span_metrics(
    predicted_cells: Sequence[Any], truth_cells: Sequence[Any]
) -> TableMetrics:
    predicted_keys = {_cell_key(cell, include_span=False) for cell in predicted_cells}
    truth_keys = {_cell_key(cell, include_span=False) for cell in truth_cells}
    predicted_spans = {
        _cell_key(cell, include_span=True) for cell in predicted_cells if _span(cell) != (1, 1)
    }
    truth_spans = {
        _cell_key(cell, include_span=True) for cell in truth_cells if _span(cell) != (1, 1)
    }
    return TableMetrics(
        _set_metrics(predicted_keys, truth_keys),
        _set_metrics(predicted_spans, truth_spans),
    )


def iou_milli(left: Box, right: Box) -> int:
    intersection = left.intersection(right)
    if intersection is None:
        return 0
    union = left.area + right.area - intersection.area
    return intersection.area * 1000 // union


def _set_metrics(predicted: set[tuple[Any, ...]], truth: set[tuple[Any, ...]]) -> DetectionMetrics:
    true_positive = len(predicted & truth)
    return DetectionMetrics(
        true_positive,
        len(predicted - truth),
        len(truth - predicted),
    )


def _box(region: Any) -> Box:
    box = getattr(region, "box", None)
    if not isinstance(box, Box):
        raise TypeError("metric region must expose a model.Box as .box")
    return box


def _kind(region: Any) -> str:
    kind = getattr(region, "kind", None)
    if not isinstance(kind, str):
        raise TypeError("metric region must expose string .kind")
    return kind


def _identifier(region: Any, fallback: int) -> str:
    identifier = getattr(region, "id", None)
    return identifier if isinstance(identifier, str) else f"index-{fallback:08d}"


def _properties(cell: Any) -> dict[str, Any]:
    properties = getattr(cell, "properties", None)
    if not isinstance(properties, dict):
        raise TypeError("table cell must expose dict .properties")
    return properties


def _span(cell: Any) -> tuple[int, int]:
    properties = _properties(cell)
    return int(properties.get("row_span", 1)), int(properties.get("column_span", 1))


def _cell_key(cell: Any, *, include_span: bool) -> tuple[Any, ...]:
    properties = _properties(cell)
    base = (
        properties.get("table_id"),
        int(properties["row"]),
        int(properties["column"]),
    )
    return (*base, *_span(cell)) if include_span else base
