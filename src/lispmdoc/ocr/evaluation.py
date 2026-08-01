"""Deterministic literal-text OCR benchmark metrics.

These metrics intentionally compare what an engine emitted, including case,
punctuation, and code whitespace tokens.  They are not a language-model
post-processing stage.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from .types import OCRPage, OCRRegion


@dataclass(frozen=True, slots=True)
class GroundTruthRegion:
    """A manually transcribed region with a stable evaluator-facing ID."""

    id: str
    text: str
    kind: str = "prose"
    required: bool = True


@dataclass(frozen=True, slots=True)
class OmissionAccounting:
    required_regions: int
    matched_regions: int
    omitted_region_ids: tuple[str, ...]
    omitted_characters: int
    extra_region_ids: tuple[str, ...]

    @property
    def silently_omitted_regions(self) -> int:
        return len(self.omitted_region_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "required_regions": self.required_regions,
            "matched_regions": self.matched_regions,
            "silently_omitted_regions": self.silently_omitted_regions,
            "omitted_region_ids": list(self.omitted_region_ids),
            "omitted_characters": self.omitted_characters,
            "extra_region_ids": list(self.extra_region_ids),
        }


@dataclass(frozen=True, slots=True)
class TextMetric:
    reference_units: int
    insertions: int
    deletions: int
    substitutions: int
    correct: int

    @property
    def errors(self) -> int:
        return self.insertions + self.deletions + self.substitutions

    @property
    def error_rate(self) -> float:
        return self.errors / self.reference_units if self.reference_units else 0.0

    @property
    def accuracy(self) -> float:
        return self.correct / self.reference_units if self.reference_units else 1.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "reference_units": self.reference_units,
            "insertions": self.insertions,
            "deletions": self.deletions,
            "substitutions": self.substitutions,
            "correct": self.correct,
            "errors": self.errors,
            "error_rate": self.error_rate,
            "accuracy": self.accuracy,
        }


@dataclass(frozen=True, slots=True)
class ExactCodeTokenMetric:
    reference_tokens: int
    exact_tokens: int
    omitted_tokens: int
    extra_tokens: int

    @property
    def accuracy(self) -> float:
        return self.exact_tokens / self.reference_tokens if self.reference_tokens else 1.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "reference_tokens": self.reference_tokens,
            "exact_tokens": self.exact_tokens,
            "omitted_tokens": self.omitted_tokens,
            "extra_tokens": self.extra_tokens,
            "accuracy": self.accuracy,
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Aggregate metrics. CER/WER retain literal case and punctuation."""

    cer: TextMetric
    wer: TextMetric
    punctuation: TextMetric
    case: TextMetric
    code_tokens: ExactCodeTokenMetric
    omissions: OmissionAccounting

    def to_dict(self) -> dict[str, object]:
        return {
            "cer": self.cer.to_dict(),
            "wer": self.wer.to_dict(),
            "punctuation": self.punctuation.to_dict(),
            "case": self.case.to_dict(),
            "code_tokens": self.code_tokens.to_dict(),
            "omissions": self.omissions.to_dict(),
        }


def _alignment(
    reference: list[str], actual: list[str]
) -> tuple[int, int, int, int, list[tuple[str | None, str | None]]]:
    """Return Levenshtein counts and a deterministic alignment.

    Tie breaking prefers a diagonal substitution, then deletion, then insertion
    so reports are stable across Python versions.
    """
    rows, columns = len(reference) + 1, len(actual) + 1
    matrix = [[0] * columns for _ in range(rows)]
    for row in range(rows):
        matrix[row][0] = row
    for column in range(columns):
        matrix[0][column] = column
    for row in range(1, rows):
        for column in range(1, columns):
            diagonal = matrix[row - 1][column - 1] + (reference[row - 1] != actual[column - 1])
            matrix[row][column] = min(
                diagonal, matrix[row - 1][column] + 1, matrix[row][column - 1] + 1
            )
    pairs: list[tuple[str | None, str | None]] = []
    row, column = len(reference), len(actual)
    while row or column:
        if (
            row
            and column
            and matrix[row][column]
            == matrix[row - 1][column - 1] + (reference[row - 1] != actual[column - 1])
        ):
            pairs.append((reference[row - 1], actual[column - 1]))
            row -= 1
            column -= 1
        elif row and matrix[row][column] == matrix[row - 1][column] + 1:
            pairs.append((reference[row - 1], None))
            row -= 1
        else:
            pairs.append((None, actual[column - 1]))
            column -= 1
    pairs.reverse()
    insertions = sum(left is None for left, _ in pairs)
    deletions = sum(right is None for _, right in pairs)
    substitutions = sum(
        left is not None and right is not None and left != right for left, right in pairs
    )
    correct = sum(left is not None and left == right for left, right in pairs)
    return insertions, deletions, substitutions, correct, pairs


def _text_metric(
    reference: str, actual: str, word_mode: bool = False
) -> tuple[TextMetric, list[tuple[str | None, str | None]]]:
    reference_items = reference.split() if word_mode else list(reference)
    actual_items = actual.split() if word_mode else list(actual)
    insertions, deletions, substitutions, correct, pairs = _alignment(reference_items, actual_items)
    return TextMetric(len(reference_items), insertions, deletions, substitutions, correct), pairs


def _sum_metrics(metrics: Iterable[TextMetric]) -> TextMetric:
    items = tuple(metrics)
    return TextMetric(
        sum(item.reference_units for item in items),
        sum(item.insertions for item in items),
        sum(item.deletions for item in items),
        sum(item.substitutions for item in items),
        sum(item.correct for item in items),
    )


def _is_punctuation(char: str) -> bool:
    return unicodedata.category(char).startswith("P")


def _is_cased(char: str) -> bool:
    return char.lower() != char.upper()


def _selected_alignment_metric(
    pairs: Iterable[tuple[str | None, str | None]], predicate: Callable[[str], bool]
) -> TextMetric:
    """Build an error metric only over reference units selected by predicate."""
    reference_units = insertions = deletions = substitutions = correct = 0
    for expected, observed in pairs:
        if expected is not None and predicate(expected):
            reference_units += 1
            if observed is None:
                deletions += 1
            elif observed == expected:
                correct += 1
            else:
                substitutions += 1
        elif expected is None and observed is not None and predicate(observed):
            insertions += 1
    return TextMetric(reference_units, insertions, deletions, substitutions, correct)


def _prediction_map(
    prediction: OCRPage | Mapping[str, str] | Iterable[OCRRegion],
) -> dict[str, str]:
    if isinstance(prediction, OCRPage):
        regions = prediction.regions
        return {region.id: region.text for region in regions}
    if isinstance(prediction, Mapping):
        return {str(key): str(value) for key, value in prediction.items()}
    return {region.id: region.text for region in prediction}


def _code_tokens(text: str) -> list[str]:
    """Whitespace tokens preserve punctuation and command syntax exactly."""
    return text.split()


def evaluate_ground_truth(
    ground_truth: Iterable[GroundTruthRegion],
    prediction: OCRPage | Mapping[str, str] | Iterable[OCRRegion],
) -> EvaluationReport:
    """Evaluate predicted text against manually transcribed regions by ID.

    Missing required IDs (and present but empty required regions) are recorded
    as omissions.  Text in extra IDs is retained as an explicit count rather
    than silently discarded.  Duplicate ground-truth IDs are rejected because
    they make omission accounting ambiguous.
    """
    truth = tuple(ground_truth)
    truth_ids = [region.id for region in truth]
    if len(set(truth_ids)) != len(truth_ids):
        raise ValueError("ground-truth region IDs must be unique")
    predicted = _prediction_map(prediction)
    cer_metrics: list[TextMetric] = []
    wer_metrics: list[TextMetric] = []
    punctuation_metrics: list[TextMetric] = []
    case_metrics: list[TextMetric] = []
    omitted: list[str] = []
    omitted_characters = 0
    code_reference = code_exact = code_omitted = code_extra = 0
    for expected in truth:
        actual = predicted.get(expected.id, "")
        if expected.required and not actual:
            omitted.append(expected.id)
            omitted_characters += len(expected.text)
        cer_metric, character_pairs = _text_metric(expected.text, actual)
        cer_metrics.append(cer_metric)
        wer_metric, _ = _text_metric(expected.text, actual, word_mode=True)
        wer_metrics.append(wer_metric)
        punctuation_metrics.append(_selected_alignment_metric(character_pairs, _is_punctuation))
        case_metrics.append(_selected_alignment_metric(character_pairs, _is_cased))
        if expected.kind == "code":
            expected_tokens, actual_tokens = _code_tokens(expected.text), _code_tokens(actual)
            code_reference += len(expected_tokens)
            code_exact += sum(
                left == right for left, right in zip(expected_tokens, actual_tokens, strict=False)
            )
            code_omitted += max(0, len(expected_tokens) - len(actual_tokens))
            code_extra += max(0, len(actual_tokens) - len(expected_tokens))
    expected_set = set(truth_ids)
    required = sum(region.required for region in truth)
    omissions = OmissionAccounting(
        required_regions=required,
        matched_regions=required - len(omitted),
        omitted_region_ids=tuple(sorted(omitted)),
        omitted_characters=omitted_characters,
        extra_region_ids=tuple(
            sorted(region_id for region_id in predicted if region_id not in expected_set)
        ),
    )
    return EvaluationReport(
        cer=_sum_metrics(cer_metrics),
        wer=_sum_metrics(wer_metrics),
        punctuation=_sum_metrics(punctuation_metrics),
        case=_sum_metrics(case_metrics),
        code_tokens=ExactCodeTokenMetric(code_reference, code_exact, code_omitted, code_extra),
        omissions=omissions,
    )
