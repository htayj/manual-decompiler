from __future__ import annotations

import json
from pathlib import Path

import pytest

from lispmdoc.model import (
    AffineTransform,
    Box,
    PageRecord,
    SceneObject,
    StructureNode,
    StructureRecord,
    StylesRecord,
    StyleToken,
    content_id,
)
from lispmdoc.review import (
    CorrectionPatch,
    PatchSchemaError,
    StalePatchError,
    UnsupportedPatchOperation,
    apply_patch,
    load_patch,
    parse_patch,
    region_fingerprint,
    text_sha256,
)

PAGE_SHA = "a" * 64


def _records() -> tuple[PageRecord, StructureRecord, StylesRecord]:
    document_id = content_id("document", {"source": PAGE_SHA})
    first_id = content_id("region", {"page": PAGE_SHA, "number": 1})
    second_id = content_id("region", {"page": PAGE_SHA, "number": 2})
    body_style = content_id("style", {"name": "body"})
    heading_style = content_id("style", {"name": "heading"})
    first = SceneObject(first_id, "text", Box(0, 0, 100, 100), body_style, {"literal_text": "teh"})
    second = SceneObject(
        second_id, "text", Box(100, 0, 200, 100), body_style, {"literal_text": "second"}
    )
    page = PageRecord(
        PageRecord.derive_id(PAGE_SHA, 0),
        1,
        0,
        Box(0, 0, 1000, 1000),
        "born-digital",
        AffineTransform(1000, 0, 0, 1000, 0, 0),
        AffineTransform(1000, 0, 0, 1000, 0, 0),
        PAGE_SHA,
        (first, second),
        (first_id, second_id),
    )
    root_id = content_id("structure", {"document": document_id})
    structure = StructureRecord(document_id, root_id, (StructureNode(root_id, "document"),))
    styles = StylesRecord(
        document_id,
        (
            StyleToken(body_style, "body", "Times", 10000),
            StyleToken(heading_style, "heading", "Times", 14000),
        ),
    )
    return page, structure, styles


def _patch(
    page: PageRecord, region: SceneObject, operation: str, old_value: object, new_value: object
) -> dict[str, object]:
    return {
        "format_version": "1.0",
        "target_id": region.id,
        "guard": {
            "source_page_sha256": page.source_page_sha256,
            "region_id": region.id,
            "expected_region_fingerprint": region_fingerprint(region),
            "original_text_sha256": text_sha256(region),
        },
        "operation": operation,
        "reason": "reviewed against source",
        "reviewer": "tester",
        "old_value": old_value,
        "new_value": new_value,
    }


def test_replace_text_is_guarded_deterministic_and_immutable() -> None:
    page, structure, styles = _records()
    patch = parse_patch(_patch(page, page.objects[0], "replace-text", "teh", "the"))

    result = apply_patch(patch, page, structure, styles)

    assert page.objects[0].payload["literal_text"] == "teh"
    assert result.page.objects[0].payload["literal_text"] == "the"
    assert result.provenance.to_dict()["patch_sha256"] == patch.sha256
    assert (
        result.provenance.to_dict()
        == apply_patch(patch, page, structure, styles).provenance.to_dict()
    )
    with pytest.raises(StalePatchError, match="fingerprint"):
        apply_patch(patch, result.page, structure, styles)


def test_geometry_and_reading_order_allow_only_safe_changes() -> None:
    page, structure, styles = _records()
    geometry = parse_patch(
        _patch(
            page,
            page.objects[0],
            "replace-geometry",
            page.objects[0].box.to_dict(),
            {"x0": 1, "y0": 2, "x1": 90, "y1": 95},
        )
    )
    moved = apply_patch(geometry, page, structure, styles).page
    assert moved.objects[0].box == Box(1, 2, 90, 95)

    order = parse_patch(
        _patch(
            page,
            page.objects[0],
            "reorder-reading",
            list(page.reading_order),
            list(reversed(page.reading_order)),
        )
    )
    assert apply_patch(order, page, structure, styles).page.reading_order == tuple(
        reversed(page.reading_order)
    )

    invalid = parse_patch(
        _patch(
            page,
            page.objects[0],
            "replace-geometry",
            page.objects[0].box.to_dict(),
            {"x0": 0, "y0": 0, "x1": 1001, "y1": 100},
        )
    )
    with pytest.raises(ValueError, match="within the page"):
        apply_patch(invalid, page, structure, styles)


def test_stale_unknown_and_unsupported_operations_are_rejected() -> None:
    page, structure, styles = _records()
    stale = _patch(page, page.objects[0], "replace-text", "wrong", "the")
    with pytest.raises(StalePatchError, match="old_value"):
        apply_patch(parse_patch(stale), page, structure, styles)

    unknown = _patch(page, page.objects[0], "replace-text", "teh", "the")
    unknown["target_id"] = content_id("region", {"unknown": True})
    with pytest.raises(StalePatchError, match="target_id"):
        apply_patch(parse_patch(unknown), page, structure, styles)

    unsupported = parse_patch(
        _patch(page, page.objects[0], "relabel-semantics", {"kind": "text"}, {"kind": "caption"})
    )
    with pytest.raises(UnsupportedPatchOperation, match="not applied"):
        apply_patch(unsupported, page, structure, styles)


def test_loader_uses_local_schema_and_rejects_undeclared_split(tmp_path: Path) -> None:
    page, _structure, _styles = _records()
    value = _patch(page, page.objects[0], "replace-text", "teh", "the")
    path = tmp_path / "patch.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    loaded = load_patch(path)
    assert isinstance(loaded, CorrectionPatch)
    value["operation"] = "split-region"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(PatchSchemaError, match="operation"):
        load_patch(path)
