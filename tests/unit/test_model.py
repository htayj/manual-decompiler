from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from lispmdoc.model import (
    AffineTransform,
    Box,
    Manifest,
    PageRecord,
    PageReference,
    Point,
    Rational,
    SceneObject,
    SourceRecord,
    StructureNode,
    StructureRecord,
    StylesRecord,
    StyleToken,
    canonical_json_bytes,
    content_id,
)

SHA = "a" * 64
CONFIG_SHA = "b" * 64


def test_canonical_json_and_content_ids_are_deterministic() -> None:
    left = {"z": "é", "a": [2, 1]}
    right = {"a": [2, 1], "z": "é"}

    assert canonical_json_bytes(left) == b'{"a":[2,1],"z":"\xc3\xa9"}'
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert content_id("region", left) == content_id("region", right)

    with pytest.raises(ValueError, match="floating-point"):
        canonical_json_bytes({"x": 0.5})


def test_half_open_box_geometry() -> None:
    box = Box(0, 0, 1000, 2000)
    adjacent = Box(1000, 0, 2000, 2000)

    assert box.width == 1000
    assert box.height == 2000
    assert box.contains_point(Point(0, 0))
    assert box.contains_point(Point(999, 1999))
    assert not box.contains_point(Point(1000, 1999))
    assert not box.intersects(adjacent)
    assert box.intersection(adjacent) is None

    with pytest.raises(ValueError, match="positive"):
        Box(1, 1, 1, 2)


def test_affine_transform_is_exact_and_round_trips() -> None:
    # 72 PDF points per inch to 1,000 micropoints per point, plus translation.
    transform = AffineTransform(1000, 0, 0, 1000, 500, -250)
    assert transform.apply(2, 3) == Point(2500, 2750)
    inverse = transform.inverse()
    assert inverse.apply(2500, 2750) == Point(2, 3)

    rational = AffineTransform(Rational(3, 2), 0, 0, 1, 0, 0)
    assert rational.apply_exact(1, 0) == (Fraction(3, 2), Fraction(0))
    with pytest.raises(ValueError, match="not an integer"):
        rational.apply(1, 0)
    with pytest.raises(ValueError, match="invertible"):
        AffineTransform(1, 0, 0, 0, 0, 0)


def _page_reference() -> PageReference:
    return PageReference(content_id("page", {"hash": SHA, "index": 0}), 1, "pages/p000001.json", 0)


def test_manifest_id_comes_from_source_bytes_not_source_alias() -> None:
    first = SourceRecord(SHA, 123, ("manuals/first.pdf",))
    second = SourceRecord(SHA, 123, ("renamed.pdf",))
    first_manifest = Manifest.for_source(first, (_page_reference(),), "default", CONFIG_SHA)
    second_manifest = Manifest.for_source(second, (_page_reference(),), "default", CONFIG_SHA)

    assert first_manifest.document_id == second_manifest.document_id
    assert json.loads(canonical_json_bytes(first_manifest)) == first_manifest.to_dict()


def test_page_structure_and_style_records_round_trip() -> None:
    object_id = content_id("region", {"page": SHA, "box": [0, 0, 200, 200]})
    page = PageRecord(
        id=PageRecord.derive_id(SHA, 0),
        sequence=1,
        source_page_index=0,
        page_box=Box(0, 0, 612000, 792000),
        page_class="born-digital",
        source_pdf_to_canonical=AffineTransform(1000, 0, 0, -1000, 0, 792000),
        render_pixels_to_canonical=AffineTransform(720, 0, 0, 720, 0, 0),
        source_page_sha256=SHA,
        objects=(SceneObject(object_id, "text", Box(0, 0, 200, 200), payload={"text": "(CAR X)"}),),
        reading_order=(object_id,),
    )
    assert PageRecord.from_dict(page.to_dict()) == page

    root = content_id("structure", {"document": "one"})
    paragraph = content_id("structure", {"paragraph": "one"})
    document = content_id("document", {"source_sha256": SHA})
    structure = StructureRecord(
        document,
        root,
        (
            StructureNode(root, "document", (paragraph,)),
            StructureNode(paragraph, "paragraph", region_ids=(object_id,), text="(CAR X)"),
        ),
    )
    assert StructureRecord.from_dict(structure.to_dict()) == structure

    style = StyleToken(content_id("style", {"name": "body"}), "body", "Times", 10000, leading=12000)
    styles = StylesRecord(document, (style,))
    assert StylesRecord.from_dict(styles.to_dict()) == styles


def test_schema_files_are_valid_json_and_accept_model_records() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema_root = Path(__file__).resolve().parents[2] / "schemas"
    manifest_schema = json.loads((schema_root / "manifest.schema.json").read_text())
    document_schema = json.loads((schema_root / "document.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(manifest_schema)
    jsonschema.Draft202012Validator.check_schema(document_schema)

    manifest = Manifest.for_source(
        SourceRecord(SHA, 123), (_page_reference(),), "default", CONFIG_SHA
    )
    jsonschema.Draft202012Validator(manifest_schema).validate(manifest.to_dict())
