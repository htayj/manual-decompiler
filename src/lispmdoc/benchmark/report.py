"""Deterministic aggregation and provisional OCR benchmark gates."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256

from lispmdoc.ocr import (
    EvaluationReport,
    ExactCodeTokenMetric,
    OmissionAccounting,
    TextMetric,
)

from .corpus import BenchmarkCorpus, BenchmarkPage

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ProvisionalGates:
    clean_cer_maximum: float = 0.005
    degraded_cer_maximum: float = 0.02
    code_token_minimum: float = 0.995
    minimum_clean_characters: int = 1000
    minimum_degraded_characters: int = 1000
    minimum_code_tokens: int = 100
    minimum_omission_pages: int = 40
    minimum_pages: int = 40


@dataclass(frozen=True, slots=True)
class EnginePageReport:
    """A precomputed OCR evaluation linked to an immutable truth digest."""

    engine: str
    page_id: str
    evaluation: EvaluationReport
    ground_truth_sha256: str
    engine_version: str
    raw_output: bytes

    def __post_init__(self) -> None:
        if not self.engine.strip() or not self.engine_version.strip():
            raise ValueError("engine report requires engine name and version")
        if not _SHA256.fullmatch(self.ground_truth_sha256):
            raise ValueError("ground-truth digest must be a lower-case SHA-256")
        if not isinstance(self.raw_output, bytes) or not self.raw_output:
            raise ValueError("raw engine output must be non-empty bytes")

    @property
    def raw_output_sha256(self) -> str:
        return sha256(self.raw_output).hexdigest()


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    status: str
    actual: float | int | None
    threshold: float | int
    sample_size: int

    def to_dict(self) -> dict[str, object]:
        return {
            "actual": self.actual,
            "name": self.name,
            "sample_size": self.sample_size,
            "status": self.status,
            "threshold": self.threshold,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkEngineReport:
    engine: str
    engine_version: str
    disposition: str
    evaluations: int
    raw_output_digests: tuple[tuple[str, str], ...]
    missing_page_ids: tuple[str, ...]
    ungrounded_page_ids: tuple[str, ...]
    aggregate: EvaluationReport | None
    gates: tuple[GateResult, ...]

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "disposition": self.disposition,
            "engine": self.engine,
            "engine_version": self.engine_version,
            "evaluations": self.evaluations,
            "gates": [gate.to_dict() for gate in self.gates],
            "missing_page_ids": list(self.missing_page_ids),
            "raw_output_digests": [
                {"page_id": page_id, "sha256": digest}
                for page_id, digest in self.raw_output_digests
            ],
            "ungrounded_page_ids": list(self.ungrounded_page_ids),
        }
        if self.aggregate is not None:
            result["aggregate"] = self.aggregate.to_dict()
        return result

    def to_json(self) -> str:
        return (
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )


def ground_truth_digest(page: BenchmarkPage) -> str:
    """Bind a result to the exact manually transcribed page records."""
    payload = json.dumps(
        page.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def aggregate_engine_reports(
    corpus: BenchmarkCorpus,
    reports: Iterable[EnginePageReport],
    *,
    gates: ProvisionalGates | None = None,
) -> tuple[BenchmarkEngineReport, ...]:
    """Aggregate engine evaluations without running an OCR engine.

    Results with unknown pages or incorrect truth digests are explicitly
    ``ungrounded`` and cannot pass a gate. Missing pages and undersized strata
    produce ``insufficient-sample`` rather than a quality claim.
    """
    selected_gates = gates or ProvisionalGates()
    by_page = {page.id: page for page in corpus.pages}
    by_engine: dict[tuple[str, str], list[EnginePageReport]] = {}
    for report in reports:
        by_engine.setdefault((report.engine, report.engine_version), []).append(report)
    return tuple(
        _aggregate_one(engine, version, reports_for_engine, by_page, selected_gates)
        for (engine, version), reports_for_engine in sorted(by_engine.items())
    )


def _aggregate_one(
    engine: str,
    engine_version: str,
    reports: Iterable[EnginePageReport],
    pages: Mapping[str, BenchmarkPage],
    gates: ProvisionalGates,
) -> BenchmarkEngineReport:
    selected: dict[str, EnginePageReport] = {}
    ungrounded: set[str] = set()
    for report in sorted(
        reports,
        key=lambda item: (item.page_id, item.ground_truth_sha256),
    ):
        page = pages.get(report.page_id)
        if page is None or report.ground_truth_sha256 != (
            ground_truth_digest(page) if page else ""
        ):
            ungrounded.add(report.page_id)
            continue
        if report.page_id in selected:
            ungrounded.add(report.page_id)
            continue
        selected[report.page_id] = report
    missing = tuple(sorted(set(pages).difference(selected)))
    raw_output_digests = tuple(
        (page_id, selected[page_id].raw_output_sha256) for page_id in sorted(selected)
    )
    if ungrounded:
        return BenchmarkEngineReport(
            engine,
            engine_version,
            "ungrounded",
            len(selected),
            raw_output_digests,
            missing,
            tuple(sorted(ungrounded)),
            None,
            (),
        )
    selected_pairs = tuple(
        (pages[page_id], selected[page_id].evaluation) for page_id in sorted(selected)
    )
    aggregate = _aggregate_evaluations(evaluation for _, evaluation in selected_pairs)
    gate_results = _evaluate_gates(selected_pairs, aggregate, gates)
    if missing or any(gate.status == "insufficient-sample" for gate in gate_results):
        disposition = "insufficient-sample"
    elif any(gate.status == "fail" for gate in gate_results):
        disposition = "failed-provisional-gates"
    else:
        disposition = "passed-provisional-gates"
    return BenchmarkEngineReport(
        engine,
        engine_version,
        disposition,
        len(selected),
        raw_output_digests,
        missing,
        (),
        aggregate,
        gate_results,
    )


def _aggregate_evaluations(evaluations: Iterable[EvaluationReport]) -> EvaluationReport:
    items = tuple(evaluations)
    return EvaluationReport(
        cer=_sum_text(item.cer for item in items),
        wer=_sum_text(item.wer for item in items),
        punctuation=_sum_text(item.punctuation for item in items),
        case=_sum_text(item.case for item in items),
        code_tokens=ExactCodeTokenMetric(
            sum(item.code_tokens.reference_tokens for item in items),
            sum(item.code_tokens.exact_tokens for item in items),
            sum(item.code_tokens.omitted_tokens for item in items),
            sum(item.code_tokens.extra_tokens for item in items),
        ),
        omissions=OmissionAccounting(
            sum(item.omissions.required_regions for item in items),
            sum(item.omissions.matched_regions for item in items),
            tuple(sorted(region for item in items for region in item.omissions.omitted_region_ids)),
            sum(item.omissions.omitted_characters for item in items),
            tuple(sorted(region for item in items for region in item.omissions.extra_region_ids)),
        ),
    )


def _sum_text(metrics: Iterable[TextMetric]) -> TextMetric:
    items = tuple(metrics)
    return TextMetric(
        sum(item.reference_units for item in items),
        sum(item.insertions for item in items),
        sum(item.deletions for item in items),
        sum(item.substitutions for item in items),
        sum(item.correct for item in items),
    )


def _evaluate_gates(
    selected: Iterable[tuple[BenchmarkPage, EvaluationReport]],
    aggregate: EvaluationReport,
    gates: ProvisionalGates,
) -> tuple[GateResult, ...]:
    pairs = tuple(selected)
    clean = _aggregate_evaluations(
        report for page, report in pairs if "clean" in page.difficulty_tags
    )
    degraded = _aggregate_evaluations(
        report for page, report in pairs if "degraded" in page.difficulty_tags
    )
    code = _aggregate_evaluations(
        report
        for page, report in pairs
        if any(record.kind == "code" for record in page.ground_truth)
    )
    return (
        _minimum_gate(
            "total-pages",
            float(len(pairs)),
            len(pairs),
            gates.minimum_pages,
            gates.minimum_pages,
        ),
        _maximum_gate(
            "clean-cer",
            clean.cer.error_rate,
            clean.cer.reference_units,
            gates.clean_cer_maximum,
            gates.minimum_clean_characters,
        ),
        _maximum_gate(
            "degraded-cer",
            degraded.cer.error_rate,
            degraded.cer.reference_units,
            gates.degraded_cer_maximum,
            gates.minimum_degraded_characters,
        ),
        _minimum_gate(
            "exact-code-token-accuracy",
            code.code_tokens.accuracy,
            code.code_tokens.reference_tokens,
            gates.code_token_minimum,
            gates.minimum_code_tokens,
        ),
        _maximum_gate(
            "silently-omitted-regions",
            float(aggregate.omissions.silently_omitted_regions),
            len(pairs),
            0,
            gates.minimum_omission_pages,
        ),
    )


def _maximum_gate(
    name: str, actual: float, sample_size: int, threshold: float | int, minimum: int
) -> GateResult:
    if sample_size < minimum:
        return GateResult(name, "insufficient-sample", None, threshold, sample_size)
    return GateResult(
        name, "pass" if actual <= threshold else "fail", actual, threshold, sample_size
    )


def _minimum_gate(
    name: str, actual: float, sample_size: int, threshold: float, minimum: int
) -> GateResult:
    if sample_size < minimum:
        return GateResult(name, "insufficient-sample", None, threshold, sample_size)
    return GateResult(
        name, "pass" if actual >= threshold else "fail", actual, threshold, sample_size
    )
