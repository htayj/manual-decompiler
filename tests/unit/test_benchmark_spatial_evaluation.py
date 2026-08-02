from __future__ import annotations

from lispmdoc.benchmark.authoritative import AuthoritativeRegionTruth, SourceSpan
from lispmdoc.benchmark.spatial_evaluation import (
    SpatialTextLine,
    evaluate_spatial_lines,
    semantic_ocr_text,
)
from lispmdoc.benchmark.wave1 import RegionGeometry


def _region(region_id: str, top: int, text: str, kind: str = "body") -> AuthoritativeRegionTruth:
    return AuthoritativeRegionTruth(
        RegionGeometry(
            region_id,
            ((0, top), (100, top), (100, top + 40), (0, top + 40)),
            ((0, top + 40), (100, top + 40)),
            top,
            "prose",
        ),
        text,
        (),
        kind,
        SourceSpan(1, 1),
    )


def test_semantic_profile_collapses_editor_wraps_but_preserves_code() -> None:
    assert semantic_ocr_text("A line\nwrapped  twice", "body") == "A line wrapped twice"
    assert semantic_ocr_text("(foo  x)\n(bar x)", "code") == "(foo  x)\n(bar x)"


def test_lines_are_spatially_grouped_with_explicit_unassigned_evidence() -> None:
    regions = (
        _region("first", 0, "First paragraph wraps"),
        _region("second", 60, "(code x)", "code"),
    )
    lines = (
        SpatialTextLine("First paragraph", (0, 1, 90, 15)),
        SpatialTextLine("wraps", (0, 18, 30, 30)),
        SpatialTextLine("(code x)", (0, 61, 50, 75)),
        SpatialTextLine("page footer", (0, 120, 80, 135)),
    )

    result = evaluate_spatial_lines(regions, lines)

    assert result.report.cer.errors == 0
    assert result.report.omissions.silently_omitted_regions == 0
    assert result.predictions == {"first": "First paragraph wraps", "second": "(code x)"}
    assert [line.text for line in result.unassigned_lines] == ["page footer"]
