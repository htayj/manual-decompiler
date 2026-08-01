from __future__ import annotations

from dataclasses import replace

from lispmdoc.layout import (
    LayoutEvidenceRegion,
    LayoutPageInput,
    RegionProposal,
    account_coverage,
    geometric_region_metrics,
    reading_order_pair_accuracy,
    reconstruct_layout,
    regions_from_ocr_page,
    table_cell_span_metrics,
)
from lispmdoc.model import Box, Point
from lispmdoc.ocr import BBox, EngineEvidence, OCRLine, OCRPage, OCRRegion

PAGE = Box(0, 0, 600000, 800000)


def _region(
    identifier: str,
    kind: str,
    box: Box,
    *,
    source: str = "pdf-object",
    order: int | None = None,
    properties: dict[str, object] | None = None,
    content_bearing: bool = True,
    exclusion: str | None = None,
) -> LayoutEvidenceRegion:
    return LayoutEvidenceRegion(
        identifier,
        box,
        kind,
        text=identifier,
        baseline=(Point(box.x0, box.y1 - 1000), Point(box.x1, box.y1 - 1000)),
        confidence_milli=950,
        source=source,  # type: ignore[arg-type]
        reading_order=order,
        content_bearing=content_bearing,
        intentional_exclusion_reason=exclusion,
        properties=properties or {},
    )


def test_born_digital_prefers_pdf_objects_and_builds_nested_physical_regions() -> None:
    regions = (
        _region("header", "header", Box(50000, 10000, 550000, 30000), order=0),
        _region("left-1", "text", Box(50000, 80000, 250000, 120000), order=1),
        _region("left-2", "code", Box(50000, 140000, 250000, 200000), order=2),
        _region("right-1", "equation", Box(350000, 80000, 550000, 120000), order=3),
        _region("right-2", "callout", Box(350000, 140000, 550000, 200000), order=4),
        _region("list", "list", Box(50000, 240000, 250000, 340000), order=5),
        _region(
            "item",
            "list-item",
            Box(60000, 260000, 240000, 290000),
            order=6,
            properties={"list_id": "list"},
        ),
        _region("table", "table", Box(300000, 240000, 550000, 360000), order=7),
        _region(
            "cell",
            "table-cell",
            Box(310000, 250000, 420000, 300000),
            order=8,
            properties={
                "column": 0,
                "column_span": 1,
                "row": 0,
                "row_span": 1,
                "table_id": "table",
            },
        ),
        _region("figure", "figure", Box(50000, 400000, 300000, 600000), order=9),
        _region(
            "caption",
            "caption",
            Box(50000, 610000, 300000, 640000),
            order=10,
            properties={"figure_id": "figure"},
        ),
        _region("marginal", "marginalia", Box(5000, 300000, 40000, 380000), order=11),
        _region("page-no", "page-number", Box(280000, 770000, 320000, 790000), order=12),
    )
    ignored_ocr = (
        _region(
            "ocr-only",
            "unknown",
            Box(1000, 1000, 2000, 2000),
            source="ocr-layout",
        ),
    )
    page = LayoutPageInput("page-1", PAGE, "born-digital", regions, ignored_ocr)

    result = reconstruct_layout(page)
    proposals = {
        source: proposal for proposal in result.proposals for source in proposal.source_region_ids
    }

    assert result.evidence_source == "pdf-object"
    assert "ocr-only" not in proposals
    assert result.coverage.passes
    assert result.coverage.supplied_content_regions == len(regions)
    assert proposals["cell"].parent_id == proposals["table"].id
    assert proposals["item"].parent_id == proposals["list"].id
    assert proposals["caption"].parent_id == proposals["figure"].id
    assert proposals["left-1"].baseline == regions[1].baseline
    assert any(proposal.kind == "column" for proposal in result.proposals)
    assert all(proposals[name].treatment for name in ("header", "marginal", "page-no"))
    assert proposals["header"].id not in result.reading_order.nodes
    assert proposals["page-no"].id not in result.reading_order.nodes
    assert result.to_dict() == reconstruct_layout(page).to_dict()


def test_scanned_page_uses_ocr_and_exposes_side_by_side_order_ambiguity() -> None:
    regions = (
        _region(
            "left",
            "text",
            Box(50000, 100000, 250000, 180000),
            source="ocr-layout",
        ),
        _region(
            "right",
            "text",
            Box(350000, 100000, 550000, 180000),
            source="ocr-layout",
        ),
    )
    page = LayoutPageInput("scan-page", PAGE, "scan-gray", ocr_regions=regions)

    result = reconstruct_layout(page)

    assert result.evidence_source == "ocr-layout"
    assert not result.reading_order.authoritative
    assert any(finding.code == "ambiguous-reading-order" for finding in result.findings)
    assert not any(edge.relation == "reading-next" for edge in result.reading_order.edges)
    assert result.coverage.passes


def test_unknown_positional_labels_stay_review_proposals() -> None:
    regions = (
        _region("top", "unknown", Box(100000, 5000, 500000, 25000)),
        _region("bottom-number", "unknown", Box(280000, 775000, 320000, 795000)),
        _region("side", "unknown", Box(0, 300000, 50000, 360000)),
    )
    regions = (regions[0], replace(regions[1], text="42"), regions[2])

    result = reconstruct_layout(LayoutPageInput("page", PAGE, "born-digital", regions))
    proposals = {
        source: proposal for proposal in result.proposals for source in proposal.source_region_ids
    }

    assert proposals["top"].kind == "header"
    assert proposals["bottom-number"].kind == "page-number"
    assert proposals["side"].kind == "marginalia"
    assert all(proposal.status == "review" for proposal in proposals.values())
    assert (
        len([finding for finding in result.findings if finding.code == "physical-label-review"])
        == 3
    )


def test_coverage_requires_exactly_one_treatment_or_intentional_exclusion() -> None:
    evidence = (
        _region("content", "text", Box(0, 0, 100, 100)),
        _region(
            "dust",
            "unknown",
            Box(200, 0, 300, 100),
            exclusion="reviewed-scanner-dust",
        ),
    )
    result = reconstruct_layout(LayoutPageInput("page", PAGE, "born-digital", evidence))
    duplicate = next(
        proposal for proposal in result.proposals if proposal.source_region_ids == ("content",)
    )

    passing = result.coverage
    failing = account_coverage(evidence, [*result.proposals, replace(duplicate, id="duplicate")])

    assert passing.passes
    assert passing.intentionally_excluded_regions == ("dust",)
    assert not failing.passes
    assert failing.multiply_accounted_regions == ("content",)


def test_ocr_page_adapter_preserves_normalized_physical_evidence() -> None:
    line = OCRLine("line", "literal", BBox(10, 20, 100, 40), reading_order=2)
    region = OCRRegion(
        "region",
        "code",
        BBox(10, 20, 100, 60),
        (line,),
        confidence=0.875,
        reading_order=4,
        native_id="native-region",
    )
    page = OCRPage(
        "page",
        600,
        800,
        "layout-engine",
        (region,),
        EngineEvidence("layout-engine", "1", {}),
    )

    adapted = regions_from_ocr_page(page)

    assert adapted[0].box == Box(10, 20, 100, 60)
    assert adapted[0].kind == "code"
    assert adapted[0].text == "literal"
    assert adapted[0].confidence_milli == 875
    assert adapted[0].reading_order == 4
    assert adapted[0].properties["native_id"] == "native-region"


def test_wave4_metrics_measure_geometry_order_and_table_spans() -> None:
    truth_regions = (
        _region("a", "text", Box(0, 0, 100, 100)),
        _region("b", "text", Box(200, 0, 300, 100)),
    )
    predicted_regions = (
        _region("pa", "text", Box(0, 0, 100, 100)),
        _region("pb", "text", Box(200, 0, 300, 100)),
    )
    geometry = geometric_region_metrics(predicted_regions, truth_regions)
    order = reading_order_pair_accuracy(("a", "c", "b"), ("a", "b", "c"))
    cell_a = RegionProposal(
        "cell-a",
        "table-cell",
        Box(0, 0, 100, 100),
        None,
        properties={
            "column": 0,
            "column_span": 2,
            "row": 0,
            "row_span": 1,
            "table_id": "table",
        },
    )
    cell_b = RegionProposal(
        "cell-b",
        "table-cell",
        Box(0, 100, 100, 200),
        None,
        properties={
            "column": 0,
            "column_span": 1,
            "row": 1,
            "row_span": 1,
            "table_id": "table",
        },
    )
    table = table_cell_span_metrics((cell_a, cell_b), (cell_a, cell_b))

    assert geometry.precision_milli == 1000
    assert geometry.recall_milli == 1000
    assert geometry.f1_milli == 1000
    assert order.total_pairs == 3
    assert order.correct_pairs == 2
    assert order.accuracy_milli == 666
    assert table.cells.f1_milli == 1000
    assert table.spans.f1_milli == 1000
    assert table.combined_f1_milli == 1000
