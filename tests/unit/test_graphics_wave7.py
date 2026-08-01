from __future__ import annotations

from fractions import Fraction

from lispmdoc.graphics import (
    AnnotationEvidence,
    FormXObject,
    GraphicsResources,
    LoweringLimits,
    PDFOperator,
    Type3CharProcedure,
    lower_pdf_graphics,
    lower_type3_char_procedure,
)
from lispmdoc.model import AffineTransform, Rational


def _path(*paint: PDFOperator) -> tuple[PDFOperator, ...]:
    return (
        PDFOperator("m", (0, 0)),
        PDFOperator("l", (10, 0)),
        PDFOperator("l", (10, 10)),
        *paint,
    )


def _point_value(point: object, axis: str) -> Fraction:
    value = getattr(point, axis)
    return value.fraction


def test_exact_geometry_curves_rectangles_and_transforms_round_trip() -> None:
    operators = (
        PDFOperator("cm", (2, 0, 0, 3, 10, 20)),
        PDFOperator("m", (Fraction(1, 2), Fraction(1, 3))),
        PDFOperator("l", (2, 3)),
        PDFOperator("c", (3, 4, 5, 6, 7, 8)),
        PDFOperator("v", (9, 10, 11, 12)),
        PDFOperator("y", (13, 14, 15, 16)),
        PDFOperator("h"),
        PDFOperator("S"),
        PDFOperator("re", (1, 2, 3, 4)),
        PDFOperator("f*"),
    )

    result = lower_pdf_graphics(operators)
    first = result.proposals[0].path
    rectangle = result.proposals[1].path

    assert first is not None
    assert rectangle is not None
    assert _point_value(first.segments[0].points[0], "x") == 11
    assert _point_value(first.segments[0].points[0], "y") == 21
    assert _point_value(first.segments[1].points[0], "x") == 14
    assert _point_value(first.segments[1].points[0], "y") == 29
    assert [segment.command for segment in first.segments] == [
        "move",
        "line",
        "curve",
        "curve",
        "curve",
        "close",
    ]
    assert [segment.command for segment in rectangle.segments] == [
        "move",
        "line",
        "line",
        "line",
        "close",
    ]
    assert result.proposals[1].fill_rule == "evenodd"
    assert len(result.operator_dispositions) == len(operators)
    assert all(item.status == "consumed" for item in result.operator_dispositions)
    assert result.all_operators_accounted
    assert not result.whole_page_fallback


def test_graphics_state_style_colors_and_all_paint_operators_are_consumed() -> None:
    operations: list[PDFOperator] = [
        PDFOperator("q"),
        PDFOperator("w", (2,)),
        PDFOperator("d", ([3, 1], 2)),
        PDFOperator("j", (1,)),
        PDFOperator("J", (2,)),
        PDFOperator("RG", (1, 0, 0)),
        PDFOperator("rg", (0, 1, 0)),
        PDFOperator("gs", ("/Alpha",)),
    ]
    for paint in ("S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"):
        operations.extend(_path(PDFOperator(paint)))
    operations.extend(
        (
            PDFOperator("Q"),
            PDFOperator("G", (Fraction(1, 2),)),
            PDFOperator("g", (Fraction(1, 4),)),
            PDFOperator("K", (0, 0, 0, 1)),
            PDFOperator("k", (0, 1, 0, 0)),
            PDFOperator("CS", ("/DeviceRGB",)),
            PDFOperator("SC", (0, 0, 1)),
            PDFOperator("cs", ("/DeviceCMYK",)),
            PDFOperator("scn", (0, 0, 0, 1)),
            *_path(PDFOperator("S")),
        )
    )
    resources = GraphicsResources(
        ext_gstates={"/Alpha": {"/ca": Fraction(1, 2), "/CA": Fraction(3, 4), "/BM": "/Normal"}}
    )

    result = lower_pdf_graphics(tuple(operations), resources=resources)

    assert len(result.operator_dispositions) == len(operations)
    assert all(item.status == "consumed" for item in result.operator_dispositions)
    assert result.proposals[0].style is not None
    assert result.proposals[0].style.line_width.fraction == 2
    assert result.proposals[0].style.dash_array[0].fraction == 3
    assert result.proposals[0].style.fill_opacity.fraction == Fraction(1, 2)
    assert result.proposals[0].style.stroke_opacity.fraction == Fraction(3, 4)
    # Q restores defaults; subsequent direct color operators deliberately
    # modify colors but not the earlier line width.
    assert result.proposals[-1].style is not None
    assert result.proposals[-1].style.line_width.fraction == 1


def test_clip_is_applied_after_path_end_and_state_restore_is_exact() -> None:
    operators = (
        PDFOperator("q"),
        PDFOperator("re", (0, 0, 100, 100)),
        PDFOperator("W*"),
        PDFOperator("n"),
        PDFOperator("cm", (1, 0, 0, 1, 10, 20)),
        *_path(PDFOperator("S")),
        PDFOperator("Q"),
        *_path(PDFOperator("S")),
    )

    result = lower_pdf_graphics(operators)

    assert len(result.clips) == 1
    assert result.clips[0].fill_rule == "evenodd"
    assert result.proposals[0].clip_ids == (result.clips[0].id,)
    assert result.proposals[1].clip_ids == ()
    first_path = result.proposals[0].path
    second_path = result.proposals[1].path
    assert first_path is not None and second_path is not None
    assert _point_value(first_path.segments[0].points[0], "x") == 10
    assert _point_value(first_path.segments[0].points[0], "y") == 20
    assert _point_value(second_path.segments[0].points[0], "x") == 0
    assert _point_value(second_path.segments[0].points[0], "y") == 0


def test_supported_form_xobject_composes_matrix_and_preserves_scope() -> None:
    form = FormXObject(
        "Mark",
        (*_path(PDFOperator("S")),),
        AffineTransform(
            Rational(1), Rational(0), Rational(0), Rational(1), Rational(5), Rational(7)
        ),
    )
    resources = GraphicsResources(forms={"/Mark": form})
    operators = (
        PDFOperator("cm", (2, 0, 0, 2, 10, 20)),
        PDFOperator("Do", ("/Mark",)),
    )

    first = lower_pdf_graphics(operators, resources=resources)
    second = lower_pdf_graphics(operators, resources=resources)
    path = first.proposals[0].path

    assert path is not None
    assert _point_value(path.segments[0].points[0], "x") == 20
    assert _point_value(path.segments[0].points[0], "y") == 34
    assert first.proposals[0].scope == ("page", "form:Mark@1")
    assert first.to_dict() == second.to_dict()
    assert len(first.operator_dispositions) == len(operators) + len(form.operators)


def test_malicious_form_cycle_and_operator_budget_are_bounded() -> None:
    resources = GraphicsResources()
    loop = FormXObject("Loop", (PDFOperator("Do", ("/Loop",)),), resources=resources)
    resources.forms["/Loop"] = loop

    cycle = lower_pdf_graphics(
        (PDFOperator("Do", ("/Loop",)),),
        resources=resources,
        limits=LoweringLimits(max_xobject_depth=4),
    )
    budget = lower_pdf_graphics(
        tuple(PDFOperator("q") for _ in range(5)),
        limits=LoweringLimits(max_operators=2),
    )

    assert any(
        item.status == "limit" and "cyclic" in item.reason for item in cycle.operator_dispositions
    )
    assert not cycle.proposals
    assert len(cycle.operator_dispositions) == 2
    assert [item.status for item in budget.operator_dispositions] == [
        "consumed",
        "consumed",
        "limit",
        "limit",
        "limit",
    ]


def test_path_budget_discards_partial_path_and_allows_later_geometry() -> None:
    result = lower_pdf_graphics(
        (
            PDFOperator("m", (0, 0)),
            PDFOperator("l", (1, 0)),
            PDFOperator("l", (2, 0)),
            PDFOperator("l", (3, 0)),
            PDFOperator("l", (4, 0)),
            PDFOperator("l", (5, 0)),
            PDFOperator("S"),
            PDFOperator("re", (10, 10, 1, 1)),
            PDFOperator("f"),
            *_path(PDFOperator("S")),
        ),
        limits=LoweringLimits(max_path_segments=5),
    )

    assert len(result.operator_dispositions) == 13
    assert result.operator_dispositions[5].status == "limit"
    assert all(
        item.status == "consumed"
        for item in result.operator_dispositions
        if item.operator_index != 5
    )
    assert len(result.proposals) == 2
    assert result.proposals[0].path is not None
    assert result.proposals[0].path.segments[0].points[0].x.fraction == 10


def test_unsupported_malformed_pattern_shading_and_blend_are_disposed() -> None:
    resources = GraphicsResources(
        ext_gstates={
            "/Blend": {"/BM": "/Multiply"},
            "/Mask": {"/SMask": "mask-object"},
        }
    )
    operators = (
        PDFOperator("Q"),
        PDFOperator("m", (1,)),
        PDFOperator("cs", ("/Pattern",)),
        PDFOperator("gs", ("/Blend",)),
        PDFOperator("gs", ("/Mask",)),
        PDFOperator("sh", ("/Gradient",)),
        PDFOperator("BI"),
        PDFOperator("unknown-op", (1, 2)),
    )

    result = lower_pdf_graphics(operators, resources=resources)
    statuses = [item.status for item in result.operator_dispositions]

    assert len(statuses) == len(operators)
    assert statuses[:2] == ["malformed", "malformed"]
    assert all(status in {"unsupported", "malformed"} for status in statuses)
    assert result.unresolved_count == len(operators)
    assert not result.whole_page_fallback


def test_annotations_are_retained_or_explicitly_disposed_without_execution() -> None:
    annotations = (
        AnnotationEvidence(
            "Link",
            (0, 0, 100, 20),
            action_uri="https://example.invalid/not-fetched",
        ),
        AnnotationEvidence("Movie", (0, 0, 20, 20)),
        AnnotationEvidence("Text", (10, 10, 0, 0)),
    )

    result = lower_pdf_graphics((), annotations=annotations)

    assert len(result.proposals) == 1
    assert result.proposals[0].kind == "annotation"
    assert result.proposals[0].payload["action_uri"] == "https://example.invalid/not-fetched"
    assert [item.status for item in result.annotation_dispositions] == [
        "consumed",
        "unsupported",
        "malformed",
    ]


def test_empty_ext_gstate_and_empty_path_end_are_valid_noops() -> None:
    result = lower_pdf_graphics(
        (PDFOperator("gs", ("/Empty",)), PDFOperator("n"), PDFOperator("S")),
        resources=GraphicsResources(ext_gstates={"/Empty": {}}),
    )

    assert [item.status for item in result.operator_dispositions] == [
        "consumed",
        "consumed",
        "consumed",
    ]
    assert not result.proposals


def test_type3_char_procedure_retains_metrics_evidence_and_bounded_findings() -> None:
    procedure = Type3CharProcedure(
        "Type3Font",
        "A",
        (
            PDFOperator("d1", (600, 0, 0, 0, 600, 700)),
            PDFOperator("re", (0, 0, 500, 700)),
            PDFOperator("f"),
            PDFOperator("sh", ("/UnsupportedShading",)),
        ),
        "a" * 64,
    )

    result = lower_type3_char_procedure(procedure)

    assert [metric.fraction for metric in result.metrics] == [600, 0, 0, 0, 600, 700]
    assert result.lowering.proposals[0].payload["type3_evidence_sha256"] == "a" * 64
    assert result.lowering.proposals[0].scope == ("type3", "Type3Font", "A")
    assert result.lowering.operator_dispositions[-1].status == "unsupported"
    assert result.lowering.unresolved_count == 1
    assert result.lowering.fidelity_status == "unmeasured"
