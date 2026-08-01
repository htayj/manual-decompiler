from __future__ import annotations

import hashlib

import pytest

from lispmdoc.benchmark import (
    BENCHMARK_MANIFEST_VERSION,
    BenchmarkCorpus,
    BenchmarkPage,
    EnginePageReport,
    GroundTruthRecord,
    ProvisionalGates,
    aggregate_engine_reports,
    ground_truth_digest,
)
from lispmdoc.ocr import GroundTruthRegion, evaluate_ground_truth

SHA_A = hashlib.sha256(b"a").hexdigest()
SHA_B = hashlib.sha256(b"b").hexdigest()
RAW_BYTES = b"raw OCR output"
RAW = hashlib.sha256(RAW_BYTES).hexdigest()


def _record(region_id: str, text: str, kind: str = "prose") -> GroundTruthRecord:
    return GroundTruthRecord(region_id, text, kind, "manual", "tester")


def _evaluation(page: BenchmarkPage, actual: str):
    truth = tuple(
        GroundTruthRegion(record.region_id, record.text, record.kind, record.required)
        for record in page.ground_truth
    )
    return evaluate_ground_truth(truth, {page.ground_truth[0].region_id: actual})


def test_aggregation_passes_provisional_gates_with_grounded_literal_evidence() -> None:
    clean = BenchmarkPage(SHA_A, 0, "born-digital", ("clean",), (_record("a", "ABC"),))
    degraded = BenchmarkPage(
        SHA_B, 0, "scan-gray", ("degraded",), (_record("b", "LOAD A", "code"),)
    )
    corpus = BenchmarkCorpus(BENCHMARK_MANIFEST_VERSION, (clean, degraded))
    reports = (
        EnginePageReport(
            "engine",
            clean.id,
            _evaluation(clean, "ABC"),
            ground_truth_digest(clean),
            "1.0",
            RAW_BYTES,
        ),
        EnginePageReport(
            "engine",
            degraded.id,
            _evaluation(degraded, "LOAD A"),
            ground_truth_digest(degraded),
            "1.0",
            RAW_BYTES,
        ),
    )

    report = aggregate_engine_reports(
        corpus,
        reports,
        gates=ProvisionalGates(
            minimum_clean_characters=1,
            minimum_degraded_characters=1,
            minimum_code_tokens=1,
            minimum_omission_pages=2,
            minimum_pages=2,
        ),
    )[0]

    assert report.disposition == "passed-provisional-gates"
    assert report.engine_version == "1.0"
    assert report.raw_output_digests == tuple(sorted(((clean.id, RAW), (degraded.id, RAW))))
    assert report.aggregate is not None
    assert report.aggregate.omissions.silently_omitted_regions == 0
    assert report.to_json() == report.to_json()


def test_aggregation_marks_wrong_truth_digest_ungrounded() -> None:
    page = BenchmarkPage(SHA_A, 0, "born-digital", ("clean",), (_record("a", "ABC"),))
    corpus = BenchmarkCorpus(BENCHMARK_MANIFEST_VERSION, (page,))
    report = EnginePageReport(
        "engine", page.id, _evaluation(page, "ABC"), "0" * 64, "1.0", RAW_BYTES
    )

    result = aggregate_engine_reports(corpus, (report,))[0]

    assert result.disposition == "ungrounded"
    assert result.ungrounded_page_ids == (page.id,)


def test_aggregation_never_passes_missing_required_strata() -> None:
    page = BenchmarkPage(SHA_A, 0, "born-digital", ("clean",), (_record("a", "ABC"),))
    corpus = BenchmarkCorpus(BENCHMARK_MANIFEST_VERSION, (page,))
    report = EnginePageReport(
        "engine",
        page.id,
        _evaluation(page, "ABC"),
        ground_truth_digest(page),
        "1.0",
        RAW_BYTES,
    )

    result = aggregate_engine_reports(corpus, (report,))[0]

    assert result.disposition == "insufficient-sample"
    assert {gate.name for gate in result.gates if gate.status == "insufficient-sample"} == {
        "total-pages",
        "clean-cer",
        "degraded-cer",
        "exact-code-token-accuracy",
        "silently-omitted-regions",
    }


def test_engine_report_derives_digest_from_supplied_raw_output() -> None:
    page = BenchmarkPage(SHA_A, 0, "born-digital", ("clean",), (_record("a", "ABC"),))

    with pytest.raises(ValueError, match="raw engine output"):
        EnginePageReport(
            "engine",
            page.id,
            _evaluation(page, "ABC"),
            ground_truth_digest(page),
            "1.0",
            "not-bytes",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="non-empty bytes"):
        EnginePageReport(
            "engine",
            page.id,
            _evaluation(page, "ABC"),
            ground_truth_digest(page),
            "1.0",
            b"",
        )
