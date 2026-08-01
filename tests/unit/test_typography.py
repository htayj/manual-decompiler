from __future__ import annotations

import pytest

from lispmdoc.licenses import FontLicenseDecision
from lispmdoc.typography import (
    FontResource,
    FontSubsetPlan,
    PdfFontInventory,
    ScanTypographyInference,
    SubstituteCandidate,
    distributable_font,
    distributable_subset,
    measure_typography,
    probe_capabilities,
    rank_substitutes,
)


def _font(char: str, source: str = "substitute") -> FontResource:
    return FontResource(char * 64, "Measured Sans", "MeasuredSans", source, "ttf")  # type: ignore[arg-type]


def test_pdf_font_contracts_and_scan_inference_are_typed() -> None:
    resource = _font("a", "pdf-embedded")
    inventory = PdfFontInventory(
        resource, "12 0 R", "Type1", "WinAnsi", "b" * 64, "c" * 64, "d" * 64
    )
    assert inventory.encoding == "WinAnsi"
    inference = ScanTypographyInference(
        "region-1", "Times", 10_000, 400, "normal", 12_000, 0, 42_000, 850
    )
    assert inference.confidence_milli == 850
    with pytest.raises(ValueError, match="Type3"):
        PdfFontInventory(resource, "13 0 R", "Type3", None, None, None, None)


def test_measured_substitute_ranking_is_deterministic_and_requires_full_coverage() -> None:
    candidates = (
        SubstituteCandidate(_font("b"), 10, 200, 1000),
        SubstituteCandidate(_font("a"), 10, 100, 1000),
        SubstituteCandidate(_font("c"), 1, 1, 900),
    )
    assert [item.resource.sha256 for item in rank_substitutes(candidates)] == [
        "c" * 64,
        "a" * 64,
        "b" * 64,
    ]


def test_distribution_gate_fails_closed_for_unknown_or_restricted_fonts() -> None:
    resource = _font("a", "pdf-embedded")
    plan = FontSubsetPlan(resource, (32, 65, 66), "woff2")
    with pytest.raises(PermissionError):
        distributable_font(resource, None)
    restricted = FontLicenseDecision("a" * 64, "restricted", "counsel", "not redistributable")
    with pytest.raises(PermissionError):
        distributable_subset(plan, restricted)
    embed_only = FontLicenseDecision(
        "a" * 64, "approved-embed", "counsel", "embed but do not subset"
    )
    with pytest.raises(PermissionError, match="subsetting"):
        distributable_subset(plan, embed_only)
    approved = FontLicenseDecision("a" * 64, "approved-subset", "counsel", "OFL verified")
    assert distributable_subset(plan, approved) == plan
    assert distributable_font(resource, approved) == resource


def test_metrics_enforce_line_baseline_and_exact_code_gates() -> None:
    metric = measure_typography(
        ("one", "two"), ("one", "two"), (1000, 2000), (1100, 2200), ("  (CAR X)",), ("  (CAR X)",)
    )
    assert metric.passes_replica_gate
    broken = measure_typography(
        ("one", "two"),
        ("one", "changed"),
        (1000, 2000),
        (1000, 2700),
        ("  (CAR X)",),
        (" (CAR X)",),
    )
    assert not broken.passes_replica_gate


def test_capability_probe_exposes_contracts_without_claiming_tools() -> None:
    capabilities = probe_capabilities()
    assert isinstance(capabilities.fonttools, bool)
    assert all("unavailable" in contract for contract in capabilities.contracts())
