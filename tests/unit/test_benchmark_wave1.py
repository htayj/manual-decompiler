from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from lispmdoc.benchmark import (
    REQUIRED_COMPOSITION,
    WAVE1_VERSION,
    CoverageDisposition,
    ExpectedRunIdentity,
    IndependentTranscription,
    PageMeasurements,
    QueuePage,
    RawEngineArtifact,
    RegionGeometry,
    TranscribedRegion,
    TranscriptionPackage,
    Wave1ContractError,
    hard_gate,
    stratified_measurements,
    validate_60_page_queue,
)

EXPECTED_RUN = ExpectedRunIdentity(
    "synthetic-ocr",
    "1.0.0",
    "synthetic-model",
    "2026-07-29",
    "benchmark-driver",
    "2.0.0",
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _artifact(
    raw: bytes, identity: ExpectedRunIdentity = EXPECTED_RUN
) -> RawEngineArtifact:
    return RawEngineArtifact.from_bytes(
        raw,
        engine=identity.engine,
        engine_version=identity.engine_version,
        model=identity.model,
        model_version=identity.model_version,
        tool=identity.tool,
        tool_version=identity.tool_version,
    )


def _page(index: int, tags: tuple[str, ...] = ("clean-scanned-prose",)) -> QueuePage:
    return QueuePage(
        _digest(f"source-{index}"),
        index,
        _digest(f"render-{index}"),
        "scan-gray",
        tags,
        ("r1",),
        EXPECTED_RUN,
    )


def _package(
    page: QueuePage, second_human: bool = True, literal_text: str = "manual text"
) -> TranscriptionPackage:
    region_id = page.inventory_region_ids[0]
    geometry = RegionGeometry(
        region_id, ((0, 0), (1, 0), (1, 1)), ((0, 1), (1, 1)), 0, "prose"
    )
    region = TranscribedRegion(geometry, literal_text, ())
    first = IndependentTranscription("alice", "submitted", (region,))
    records = (first,)
    if second_human:
        records += (IndependentTranscription("bob", "adjudicated", (region,)),)
    return TranscriptionPackage(
        WAVE1_VERSION,
        page,
        page.inventory_region_ids,
        (CoverageDisposition(region_id, "transcribed"),),
        records,
        "carol",
    )


def test_queue_composition_and_hard_gate_reject_unadjudicated_or_missing_raw_evidence() -> None:
    pages: list[QueuePage] = []
    index = 0
    for tag, count in REQUIRED_COMPOSITION.items():
        for _ in range(count):
            pages.append(_page(index, (tag,)))
            index += 1
    pages.extend(_page(index + offset, ("clean-scanned-prose",)) for offset in range(60 - index))
    assert validate_60_page_queue(pages).disposition == "selection-ready"
    packages = {page.id: _package(page, second_human=False) for page in pages}

    result = hard_gate(pages, packages, {})

    assert result.disposition == "reject"
    assert any(reason.startswith("single-review-or-unadjudicated") for reason in result.reasons)
    assert any(reason.startswith("missing-or-invalid-raw-output") for reason in result.reasons)


def test_adjudicated_package_and_raw_bytes_can_pass_small_complete_contract() -> None:
    page = _page(0)
    package = _package(page)
    raw = b"native engine output"

    result = hard_gate(
        (page,),
        {page.id: package},
        {
            page.id: (
                _artifact(raw),
                raw,
            )
        },
    )

    assert result.disposition == "reject"  # the queue is intentionally not a real 60-page corpus
    assert not any("single-review" in reason or "raw-output" in reason for reason in result.reasons)


def test_transcription_package_rejects_invented_truth_and_missing_inventory_coverage() -> None:
    page = _page(0)
    invented = TranscribedRegion(
        RegionGeometry(
            "invented", ((0, 0), (1, 0), (1, 1)), ((0, 1), (1, 1)), 0, "prose"
        ),
        "invented",
        (),
    )
    records = (
        IndependentTranscription("alice", "submitted", (invented,)),
        IndependentTranscription("bob", "adjudicated", (invented,)),
    )

    with pytest.raises(Wave1ContractError, match="marked transcribed"):
        TranscriptionPackage(
            WAVE1_VERSION,
            page,
            ("r1",),
            (CoverageDisposition("r1", "transcribed"),),
            records,
            "carol",
        )
    with pytest.raises(Wave1ContractError, match="each inventory region exactly once"):
        TranscriptionPackage(WAVE1_VERSION, page, ("r1",), (), (), "carol")


@pytest.mark.parametrize(
    ("replacement", "reason"),
    (
        (
            lambda page: QueuePage(
                page.source_sha256,
                page.source_page_index + 1,
                page.render_sha256,
                page.page_class,
                page.tags,
                page.inventory_region_ids,
                page.expected_run,
            ),
            "mismatched-page-index-class-or-tags",
        ),
        (
            lambda page: QueuePage(
                page.source_sha256,
                page.source_page_index,
                page.render_sha256,
                page.page_class,
                page.tags,
                ("invented",),
                page.expected_run,
            ),
            "mismatched-region-inventory",
        ),
        (
            lambda page: QueuePage(
                page.source_sha256,
                page.source_page_index,
                page.render_sha256,
                "born-digital",
                page.tags,
                page.inventory_region_ids,
                page.expected_run,
            ),
            "mismatched-page-index-class-or-tags",
        ),
        (
            lambda page: QueuePage(
                page.source_sha256,
                page.source_page_index,
                page.render_sha256,
                page.page_class,
                ("table",),
                page.inventory_region_ids,
                page.expected_run,
            ),
            "mismatched-page-index-class-or-tags",
        ),
    ),
)
def test_hard_gate_binds_page_index_class_and_tags(replacement, reason: str) -> None:
    page = _page(0)
    package = _package(replacement(page))
    raw = b"native engine output"
    artifact = _artifact(raw)

    result = hard_gate((page,), {page.id: package}, {page.id: (artifact, raw)})

    assert any(item.startswith(reason) for item in result.reasons)


def test_raw_engine_evidence_rejects_empty_output() -> None:
    with pytest.raises(Wave1ContractError, match="non-empty bytes"):
        RawEngineArtifact.from_bytes(
            b"",
            engine="synthetic-ocr",
            engine_version="1.0.0",
            model="synthetic-model",
            model_version="2026-07-29",
            tool="benchmark-driver",
            tool_version="2.0.0",
        )


@pytest.mark.parametrize(
    "actual_identity",
    (
        replace(EXPECTED_RUN, engine="other-engine"),
        replace(EXPECTED_RUN, engine_version="1.0.1"),
        replace(EXPECTED_RUN, model="other-model"),
        replace(EXPECTED_RUN, model_version="2026-07-30"),
        replace(EXPECTED_RUN, tool="other-driver"),
        replace(EXPECTED_RUN, tool_version="2.0.1"),
    ),
)
def test_hard_gate_rejects_every_expected_run_identity_mismatch(
    actual_identity: ExpectedRunIdentity,
) -> None:
    page = _page(0)
    raw = b"native engine output"

    result = hard_gate(
        (page,),
        {page.id: _package(page)},
        {page.id: (_artifact(raw, actual_identity), raw)},
    )

    assert any(
        reason.startswith("unexpected-engine-model-or-tool") for reason in result.reasons
    )


def test_hard_gate_fails_closed_without_expected_run_contract() -> None:
    selected = _page(0)
    page = QueuePage(
        selected.source_sha256,
        selected.source_page_index,
        selected.render_sha256,
        selected.page_class,
        selected.tags,
        selected.inventory_region_ids,
    )
    raw = b"native engine output"

    result = hard_gate(
        (page,),
        {page.id: _package(page)},
        {page.id: (_artifact(raw), raw)},
    )

    assert any(reason.startswith("missing-expected-run-contract") for reason in result.reasons)
    assert any(reason.startswith("unexpected-engine-model-or-tool") for reason in result.reasons)


def test_honest_complete_synthetic_wave1_package_passes() -> None:
    pages: list[QueuePage] = []
    packages: dict[str, TranscriptionPackage] = {}
    artifacts: dict[str, tuple[RawEngineArtifact, bytes]] = {}
    index = 0
    for tag, count in REQUIRED_COMPOSITION.items():
        for _ in range(count):
            page = _page(index, (tag,))
            if tag in {"clean-scanned-prose", "degraded-scanned-prose"}:
                text = "x" * 200
            elif tag == "code-terminal":
                text = " ".join(f"token-{number}" for number in range(20))
            else:
                text = "manual text"
            raw = f"native output page {index}".encode()
            pages.append(page)
            packages[page.id] = _package(page, literal_text=text)
            artifacts[page.id] = (
                _artifact(raw),
                raw,
            )
            index += 1

    result = hard_gate(pages, packages, artifacts)

    assert result.disposition == "pass"
    assert result.reasons == ()


def test_stratified_metrics_include_layout_semantics_tables_calibration_and_resources() -> None:
    page = _page(0, ("clean-scanned-prose", "code-terminal"))
    measurements = PageMeasurements(
        text_region_tp=99,
        text_region_fp=1,
        text_region_fn=1,
        order_correct_pairs=199,
        order_total_pairs=200,
        semantic_tp=49,
        semantic_fp=1,
        semantic_fn=1,
        table_tp=9,
        table_fp=1,
        table_fn=1,
        confidence_bins=((0.9, 0.85, 10),),
        runtime_ms=10,
        memory_bytes=20,
        vram_bytes=30,
        output_bytes=40,
    )

    report = stratified_measurements({page.id: page}, {page.id: measurements})

    assert report["class:scan-gray"]["region_recall"] == 0.99
    assert round(float(report["tag:code-terminal"]["expected_calibration_error"] or 0), 6) == 0.05
    assert report["tag:clean-scanned-prose"]["output_bytes"] == 40
