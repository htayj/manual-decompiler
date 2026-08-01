from __future__ import annotations

from lispmdoc.graphics import (
    BinaryMask,
    LabelReference,
    ScanLayer,
    component_omission_metrics,
    decompose_supplied_masks,
    detect_visible_primitives,
    edge_metrics,
    remove_labels,
    simplify_supplied_trace,
    tight_crop_metrics,
    vector_raster_decision,
)
from lispmdoc.raster import (
    CodecCandidate,
    CropCatalog,
    PixelBox,
    RasterBitmap,
    RasterRegion,
    codec_curve,
    evaluate_page_raster_policy,
    probe_external_capabilities,
    tight_crop,
)


def _mask(width: int, height: int, points: set[tuple[int, int]]) -> BinaryMask:
    return BinaryMask(
        width,
        height,
        tuple(tuple((x, y) in points for x in range(width)) for y in range(height)),
    )


def test_supplied_layers_are_exclusive_and_report_uncovered_foreground() -> None:
    source = _mask(5, 2, {(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)})
    kinds = ("text", "line-art", "continuous-tone", "halftone", "texture")
    layers = tuple(
        ScanLayer(kind, _mask(5, 2, {(index, 0)}), f"evidence-{index}")
        for index, kind in enumerate(kinds)
    )

    decomposition = decompose_supplied_masks(source, layers)

    assert not decomposition.uncovered_foreground
    overlapping = (*layers[:-1], ScanLayer("texture", _mask(5, 2, {(0, 0)}), "bad"))
    try:
        decompose_supplied_masks(source, overlapping)
    except ValueError as error:
        assert "overlaps" in str(error)
    else:
        raise AssertionError("overlapping classification masks must fail")


def test_label_removal_preserves_searchable_spatial_link() -> None:
    source = _mask(8, 4, {(x, 1) for x in range(8)} | {(3, 2)})
    label = LabelReference("label-A", "RESET", PixelBox(2, 1, 3, 2), "component-wire-7")

    separated = remove_labels(source, (label,))

    assert not ({(2, 1), (3, 1), (4, 1), (3, 2)} & separated.remaining_mask.foreground)
    assert separated.reinsertions[0].label_id == "label-A"
    assert separated.reinsertions[0].searchable_text == "RESET"
    assert separated.reinsertions[0].source_component_id == "component-wire-7"
    assert separated.reinsertions[0].source_box == label.box


def test_visible_primitive_records_never_claim_semantic_connectivity() -> None:
    rectangle = {(x, y) for x in range(1, 6) for y in range(1, 5) if x in {1, 5} or y in {1, 4}}
    proposals = detect_visible_primitives(_mask(8, 7, rectangle))

    assert proposals[0].kind == "rectangle"
    assert all(item.semantic_connectivity == "unmeasured" for item in proposals)
    assert proposals == detect_visible_primitives(_mask(8, 7, rectangle))


def test_rule_circle_arrow_leader_and_junction_candidates_are_visible_only() -> None:
    shapes = {
        "rule": {(x, 1) for x in range(6)},
        "leader-candidate": {(x, 1) for x in range(6)} | {(5, 2)},
        "arrow-candidate": {(x, 2) for x in range(6)} | {(4, 1), (4, 3)},
        "circle-candidate": {
            (2, 0),
            (3, 0),
            (1, 1),
            (4, 1),
            (0, 2),
            (5, 2),
            (0, 3),
            (5, 3),
            (1, 4),
            (4, 4),
            (2, 5),
            (3, 5),
        },
    }

    for expected, points in shapes.items():
        proposals = detect_visible_primitives(_mask(8, 8, points))
        assert expected in {proposal.kind for proposal in proposals}
        assert all(proposal.semantic_connectivity == "unmeasured" for proposal in proposals)
    arrow = detect_visible_primitives(_mask(8, 8, shapes["arrow-candidate"]))
    assert "junction-candidate" in {proposal.kind for proposal in arrow}


def test_supplied_trace_simplification_records_bounded_error() -> None:
    mask = _mask(10, 10, {(x, 5) for x in range(10)})
    proposal = simplify_supplied_trace(
        mask,
        ((0.0, 5.0), (2.0, 5.1), (5.0, 4.9), (9.0, 5.0)),
        tolerance_px=0.2,
    )

    assert proposal.points == ((0.0, 5.0), (9.0, 5.0))
    assert proposal.recorded_max_edge_error_px <= proposal.tolerance_px
    assert proposal.source_mask_sha256 == mask.sha256


def test_edge_and_component_metrics_fail_missing_content() -> None:
    reference = _mask(
        30, 20, {(x, 2) for x in range(20)} | {(x, y) for x in range(5) for y in range(5, 15)}
    )
    candidate = _mask(30, 20, {(x, 2) for x in range(20)})

    edges = edge_metrics(reference, candidate)
    omission = component_omission_metrics(reference, candidate)

    assert not edges.passes
    assert edges.foreground_edge_recall < 0.99
    assert not omission.passes
    assert omission.undisposed_above_threshold[0].area_mm2 > 0.25
    disposed = component_omission_metrics(
        reference,
        candidate,
        dispositions={omission.missing[0].id: "reviewed-raster-region"},
    )
    assert disposed.passes


def test_tight_crop_hash_and_dedup_are_content_addressed() -> None:
    bitmap = RasterBitmap(
        4,
        3,
        1,
        bytes(
            [
                255,
                255,
                255,
                255,
                255,
                0,
                0,
                255,
                255,
                255,
                255,
                255,
            ]
        ),
    )
    crop = tight_crop(bitmap)
    catalog = CropCatalog()

    assert crop.source_box == PixelBox(1, 1, 2, 1)
    assert tight_crop_metrics(_mask(4, 3, {(1, 1), (2, 1)}), crop.source_box).tight
    assert catalog.add(crop) is crop
    assert catalog.add(crop) is crop
    assert len(catalog.crops) == 1


def test_codec_curve_excludes_unavailable_and_dominated_candidates() -> None:
    curve = codec_curve(
        (
            CodecCandidate("png", 100, 0.0, True),
            CodecCandidate("webp-lossless", 80, 0.0, True),
            CodecCandidate("avif", 0, 0.0, False),
            CodecCandidate("webp-lossy", 50, 1.0, True),
        )
    )

    assert [item.codec for item in curve.pareto] == ["webp-lossless", "webp-lossy"]
    assert "avif" not in {item.codec for item in curve.pareto}


def test_large_raster_policy_aggregates_split_regions_and_fails_closed() -> None:
    crop_hash = "a" * 64
    regions = (
        RasterRegion(
            "left",
            PixelBox(0, 0, 35, 100),
            "continuous-tone",
            "continuous-tone-photo",
            crop_hash,
        ),
        RasterRegion(
            "right",
            PixelBox(65, 0, 35, 100),
            "continuous-tone",
            "continuous-tone-photo",
            crop_hash,
        ),
    )

    blocked = evaluate_page_raster_policy(100, 100, regions)

    assert blocked.manual_approval_required
    assert blocked.union_coverage == 0.7
    assert blocked.effective_coverage == 1.0
    assert not blocked.replica_ready
    assert "large-raster-manual-approval-required" in blocked.findings
    approved_photo = evaluate_page_raster_policy(
        100,
        100,
        regions,
        manual_approval_id="review-123",
        explicitly_photo_dominant=True,
        contains_meaningful_text_or_vector=False,
    )
    assert approved_photo.replica_ready


def test_vectorization_requires_size_advantage_or_documented_value() -> None:
    smaller = vector_raster_decision(
        vector_bytes=40,
        raster_bytes=100,
        fidelity_acceptable=True,
    )
    documented = vector_raster_decision(
        vector_bytes=120,
        raster_bytes=100,
        documented_value_code="searchable-labels",
        fidelity_acceptable=True,
    )
    blocked = vector_raster_decision(
        vector_bytes=40,
        raster_bytes=100,
        fidelity_acceptable=False,
    )

    assert smaller.selected == "vector" and smaller.passes
    assert documented.selected == "vector" and documented.passes
    assert blocked.selected == "blocked" and not blocked.passes


def test_capability_probe_reports_only_observed_paths() -> None:
    capabilities = probe_external_capabilities(
        {"definitely-missing": "lispmdoc-definitely-missing-executable"}
    )

    assert len(capabilities) == 1
    assert not capabilities[0].available
    assert capabilities[0].resolved_path is None
