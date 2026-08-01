from __future__ import annotations

import hashlib
import io
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

import pytest
from PIL import Image

from lispmdoc.model import (
    AffineTransform,
    Box,
    Manifest,
    PageRecord,
    PageReference,
    SceneObject,
    SourceRecord,
    StructureNode,
    StructureRecord,
    StylesRecord,
    StyleToken,
    content_id,
)
from lispmdoc.render import (
    RenderViewsError,
    authoritative_plain_text,
    probe_capabilities,
    write_view_tree,
)

SHA = "a" * 64
CONFIG_SHA = "b" * 64


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), (20, 40, 60)).save(output, "PNG")
    return output.getvalue()


def _raster_payload(digest: str, *, alt_text: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "reason": "continuous-tone-photo",
        "asset": {"sha256": digest, "codec": "png", "width_px": 2, "height_px": 2},
        "source_crop": {"x": 0, "y": 0, "width": 2, "height": 2},
    }
    if alt_text is not None:
        payload["alt_text"] = alt_text
    return payload


class _AuthoritativeHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = dict(attrs).get("class", "").split()
        if self.depth or "lmdoc-authoritative-text" in classes:
            self.depth += 1
            if self.depth == 1:
                self.chunks.append("")

    def handle_endtag(self, _tag: str) -> None:
        if self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.chunks[-1] += data


def _records() -> tuple[Manifest, tuple[PageRecord, ...], StructureRecord, StylesRecord]:
    page_id = PageRecord.derive_id(SHA, 0)
    style_id = content_id("style", {"name": "body"})
    text_id = content_id("region", {"name": "text"})
    rule_id = content_id("region", {"name": "rule"})
    shape_id = content_id("region", {"name": "shape"})
    path_id = content_id("region", {"name": "path"})
    raster_id = content_id("region", {"name": "raster"})
    page = PageRecord(
        id=page_id,
        sequence=1,
        source_page_index=0,
        page_box=Box(0, 0, 612000, 792000),
        page_class="hybrid",
        source_pdf_to_canonical=AffineTransform(1000, 0, 0, -1000, 0, 792000),
        render_pixels_to_canonical=AffineTransform(720, 0, 0, 720, 0, 0),
        source_page_sha256=SHA,
        objects=(
            SceneObject(
                text_id,
                "text",
                Box(1000, 2000, 100000, 12000),
                style_id,
                {"literal_text": "<hostile & text>"},
            ),
            SceneObject(rule_id, "rule", Box(1000, 15000, 100000, 16000), payload={}),
            SceneObject(
                shape_id, "shape", Box(1000, 20000, 40000, 50000), payload={"shape": "ellipse"}
            ),
            SceneObject(
                path_id,
                "path",
                Box(1000, 60000, 40000, 90000),
                payload={"d": "M 1000 60000 L 40000 90000"},
            ),
            SceneObject(
                raster_id,
                "raster",
                Box(1000, 100000, 40000, 130000),
                payload={"asset": "photo.png"},
            ),
        ),
        reading_order=(text_id, rule_id, shape_id, path_id, raster_id),
    )
    source = SourceRecord(SHA, 123, ("manual.pdf",))
    manifest = Manifest.for_source(
        source,
        (PageReference(page_id, 1, "pages/p000001.json", 0),),
        "default",
        CONFIG_SHA,
    )
    root = content_id("structure", {"root": 1})
    heading = content_id("structure", {"heading": 1})
    paragraph = content_id("structure", {"paragraph": 1})
    structure = StructureRecord(
        manifest.document_id,
        root,
        (
            StructureNode(root, "document", (heading, paragraph)),
            StructureNode(heading, "heading", text="Manual <title>", properties={"level": 1}),
            StructureNode(paragraph, "paragraph", region_ids=(text_id,), text="<hostile & text>"),
        ),
    )
    styles = StylesRecord(
        manifest.document_id,
        (
            StyleToken(
                style_id,
                "body",
                'Unsafe";</style><script>',
                12000,
                leading=14400,
            ),
        ),
    )
    return manifest, (page,), structure, styles


def test_static_views_are_deterministic_escaped_and_object_linked(tmp_path: Path) -> None:
    records = _records()
    first = write_view_tree(tmp_path / "one", *records)
    second = write_view_tree(tmp_path / "two", *records)
    html = first.html_path.read_text(encoding="utf-8")
    css = first.css_path.read_text(encoding="utf-8")
    svg = first.svg_paths[0].read_text(encoding="utf-8")

    assert first.html_path.read_bytes() == second.html_path.read_bytes()
    assert first.css_path.read_bytes() == second.css_path.read_bytes()
    assert svg.encode() == second.svg_paths[0].read_bytes()
    assert "<main" in html
    assert "<h1" in html
    assert "Manual &lt;title&gt;" in html
    assert "&lt;hostile &amp; text&gt;" in html
    assert "<hostile" not in html
    assert "<script>" not in html
    assert "</style><script>" not in css
    assert '<text x="1000" y="12000">&lt;hostile &amp; text&gt;</text>' in svg
    assert "<line " in svg
    assert "<ellipse " in svg
    assert '<path d="M 1000 60000 L 40000 90000"' in svg
    assert "raster asset omitted" in svg
    assert "<image" not in svg
    assert "data-canonical-id" in svg
    assert first.warnings and "raster asset omitted" in first.warnings[0]


def test_raster_error_policy_refuses_a_silent_fallback(tmp_path: Path) -> None:
    with pytest.raises(RenderViewsError, match="raster object"):
        write_view_tree(tmp_path / "views", *_records(), raster_policy="error")


def test_manifest_page_records_must_match_exactly(tmp_path: Path) -> None:
    manifest, pages, structure, styles = _records()
    other_page = PageRecord(
        id=pages[0].id,
        sequence=2,
        source_page_index=0,
        page_box=pages[0].page_box,
        page_class=pages[0].page_class,
        source_pdf_to_canonical=pages[0].source_pdf_to_canonical,
        render_pixels_to_canonical=pages[0].render_pixels_to_canonical,
        source_page_sha256=pages[0].source_page_sha256,
    )

    with pytest.raises(RenderViewsError, match="page records"):
        write_view_tree(tmp_path / "views", manifest, (other_page,), structure, styles)


def test_rerender_prunes_obsolete_generated_svg(tmp_path: Path) -> None:
    records = _records()
    root = tmp_path / "views"
    first = write_view_tree(root, *records)
    stale = first.svg_paths[0].with_name("9999.svg")
    stale.write_text("<svg/>", encoding="utf-8")

    second = write_view_tree(root, *records)

    assert not stale.exists()
    assert second.svg_paths[0].exists()


def test_replica_mode_copies_hashed_bounded_raster_and_writes_diplomatic_text(
    tmp_path: Path,
) -> None:
    manifest, pages, structure, styles = _records()
    asset = _png_bytes()
    digest = hashlib.sha256(asset).hexdigest()
    asset_path = tmp_path / "source.png"
    asset_path.write_bytes(asset)
    raster = SceneObject(
        content_id("region", {"asset": digest}),
        "raster",
        Box(1000, 1000, 20_000, 20_000),
        payload=_raster_payload(digest, alt_text="A bounded illustration"),
    )
    page = PageRecord(
        pages[0].id,
        1,
        0,
        pages[0].page_box,
        pages[0].page_class,
        pages[0].source_pdf_to_canonical,
        pages[0].render_pixels_to_canonical,
        pages[0].source_page_sha256,
        (pages[0].objects[0], raster),
        (pages[0].objects[0].id, raster.id),
    )
    result = write_view_tree(
        tmp_path / "views",
        manifest,
        (page,),
        structure,
        styles,
        raster_policy="error",
        replica_mode=True,
        raster_assets={digest: asset_path},
    )
    assert f"../../assets/{digest}.png" in result.svg_paths[0].read_text(encoding="utf-8")
    assert (result.root / "assets" / f"{digest}.png").read_bytes() == asset
    assert result.plain_text_path.read_text(encoding="utf-8") == authoritative_plain_text(
        (page,), structure
    )


def test_svg_preserves_clip_z_order_and_positioned_glyphs(tmp_path: Path) -> None:
    manifest, pages, structure, styles = _records()
    clip_id = content_id("region", {"clip": 1})
    glyph_id = content_id("region", {"glyph": 1})
    clip = SceneObject(clip_id, "clip-path", Box(1000, 2000, 10000, 12000), z_index=-1)
    glyphs = SceneObject(
        glyph_id,
        "glyph",
        Box(1000, 2000, 10000, 12000),
        payload={
            "literal_text": "AB",
            "clip_path_id": clip_id,
            "glyph_positions": [
                {"text": "A", "x": 1000, "y": 6000},
                {"text": "B", "x": 5000, "y": 6000},
            ],
        },
        z_index=3,
    )
    page = PageRecord(
        pages[0].id,
        1,
        0,
        pages[0].page_box,
        pages[0].page_class,
        pages[0].source_pdf_to_canonical,
        pages[0].render_pixels_to_canonical,
        pages[0].source_page_sha256,
        (pages[0].objects[0], glyphs, clip),
        (pages[0].objects[0].id, glyphs.id, clip.id),
    )
    result = write_view_tree(tmp_path / "views", manifest, (page,), structure, styles)
    svg = result.svg_paths[0].read_text(encoding="utf-8")

    assert f'<clipPath id="{clip_id}">' in svg
    assert f'clip-path="url(#{clip_id})"' in svg
    assert '<tspan x="1000" y="6000">A</tspan><tspan x="5000" y="6000">B</tspan>' in svg


def test_rerender_prunes_stale_hashed_raster_assets(tmp_path: Path) -> None:
    manifest, pages, structure, styles = _records()
    asset = _png_bytes()
    digest = hashlib.sha256(asset).hexdigest()
    source = tmp_path / "asset.png"
    source.write_bytes(asset)
    raster = SceneObject(
        content_id("region", {"prune": digest}),
        "raster",
        Box(1000, 1000, 20_000, 20_000),
        payload=_raster_payload(digest),
    )
    page = PageRecord(
        pages[0].id,
        1,
        0,
        pages[0].page_box,
        pages[0].page_class,
        pages[0].source_pdf_to_canonical,
        pages[0].render_pixels_to_canonical,
        pages[0].source_page_sha256,
        (pages[0].objects[0], raster),
        (pages[0].objects[0].id, raster.id),
    )
    root = tmp_path / "views"
    write_view_tree(root, manifest, (page,), structure, styles, raster_assets={digest: source})
    stale = root / "assets" / f"{digest}.png"
    assert stale.exists()

    write_view_tree(root, *(_records()))

    assert not stale.exists()


def test_replica_mode_rejects_full_page_raster_and_unavailable_required_shaping(
    tmp_path: Path,
) -> None:
    manifest, pages, structure, styles = _records()
    full = SceneObject(
        content_id("region", {"full": True}), "raster", pages[0].page_box, payload={}
    )
    page = PageRecord(
        pages[0].id,
        1,
        0,
        pages[0].page_box,
        pages[0].page_class,
        pages[0].source_pdf_to_canonical,
        pages[0].render_pixels_to_canonical,
        pages[0].source_page_sha256,
        (pages[0].objects[0], full),
        (pages[0].objects[0].id, full.id),
    )
    with pytest.raises(RenderViewsError, match="payload.reason"):
        write_view_tree(
            tmp_path / "full",
            manifest,
            (page,),
            structure,
            styles,
            raster_policy="error",
            replica_mode=True,
        )
    if not probe_capabilities().harfbuzz:
        shaped = SceneObject(
            pages[0].objects[0].id,
            "text",
            pages[0].objects[0].box,
            payload={"literal_text": "x", "requires_shaping": True},
        )
        page = PageRecord(
            pages[0].id,
            1,
            0,
            pages[0].page_box,
            pages[0].page_class,
            pages[0].source_pdf_to_canonical,
            pages[0].render_pixels_to_canonical,
            pages[0].source_page_sha256,
            (shaped,),
            (shaped.id,),
        )
        shaped_structure = StructureRecord(
            manifest.document_id,
            structure.root_id,
            (
                structure.nodes[0],
                structure.nodes[1],
                replace(structure.nodes[2], text="x"),
            ),
        )
        with pytest.raises(RenderViewsError, match="shaping"):
            write_view_tree(
                tmp_path / "shape",
                manifest,
                (page,),
                shaped_structure,
                styles,
                raster_policy="error",
                replica_mode=True,
            )


def test_page_only_structure_html_contains_exact_plain_text_once(tmp_path: Path) -> None:
    manifest, pages, structure, styles = _records()
    text_id = pages[0].objects[0].id
    root = content_id("structure", {"page-only-root": True})
    paragraph = content_id("structure", {"page-only-paragraph": True})
    page_only = StructureRecord(
        manifest.document_id,
        root,
        (
            StructureNode(root, "document", (paragraph,)),
            StructureNode(paragraph, "paragraph", region_ids=(text_id,)),
        ),
    )

    result = write_view_tree(tmp_path / "page-only", manifest, pages, page_only, styles)
    parser = _AuthoritativeHTML()
    parser.feed(result.html_path.read_text(encoding="utf-8"))

    plain = result.plain_text_path.read_text(encoding="utf-8")
    assert plain == "<hostile & text>\n"
    assert "\n".join(parser.chunks) + "\n" == plain
    assert parser.chunks == ["<hostile & text>"]


def test_replica_rejects_unreferenced_authoritative_scene_text(tmp_path: Path) -> None:
    manifest, pages, structure, styles = _records()
    extra = SceneObject(
        content_id("region", {"unreferenced": True}),
        "text",
        Box(1000, 14000, 100000, 24000),
        payload={"literal_text": "silently omitted before this check"},
    )
    page = replace(
        pages[0],
        objects=(*pages[0].objects, extra),
        reading_order=(*pages[0].reading_order, extra.id),
    )

    with pytest.raises(RenderViewsError, match="exactly once.*missing"):
        write_view_tree(
            tmp_path / "unreferenced",
            manifest,
            (page,),
            structure,
            styles,
            raster_policy="error",
            replica_mode=True,
        )


def test_replica_rejects_duplicate_or_conflicting_scene_text_structure(tmp_path: Path) -> None:
    manifest, pages, structure, styles = _records()
    text_id = pages[0].objects[0].id
    paragraph = structure.nodes[2]
    duplicate = StructureNode(
        content_id("structure", {"duplicate-text-reference": True}),
        "note",
        region_ids=(text_id,),
    )
    duplicate_structure = StructureRecord(
        manifest.document_id,
        structure.root_id,
        (
            replace(structure.nodes[0], child_ids=(*structure.nodes[0].child_ids, duplicate.id)),
            structure.nodes[1],
            paragraph,
            duplicate,
        ),
    )
    with pytest.raises(RenderViewsError, match="not-exactly-once"):
        write_view_tree(
            tmp_path / "duplicate",
            manifest,
            pages,
            duplicate_structure,
            styles,
            raster_policy="error",
            replica_mode=True,
        )

    conflicting_structure = StructureRecord(
        manifest.document_id,
        structure.root_id,
        (
            structure.nodes[0],
            structure.nodes[1],
            replace(paragraph, text="normalized but not authoritative"),
        ),
    )
    with pytest.raises(RenderViewsError, match="conflicts with referenced authoritative"):
        write_view_tree(
            tmp_path / "conflicting",
            manifest,
            pages,
            conflicting_structure,
            styles,
            raster_policy="error",
            replica_mode=True,
        )


def test_replica_rejects_fake_png_and_large_split_raster_evasion(tmp_path: Path) -> None:
    manifest, pages, structure, styles = _records()
    fake = b"not actually png"
    digest = hashlib.sha256(fake).hexdigest()
    source = tmp_path / "fake.png"
    source.write_bytes(fake)
    bounded = SceneObject(
        content_id("region", {"fake": digest}),
        "raster",
        Box(1_000, 1_000, 20_000, 20_000),
        payload=_raster_payload(digest, alt_text="fake"),
    )
    page = PageRecord(
        pages[0].id,
        1,
        0,
        pages[0].page_box,
        pages[0].page_class,
        pages[0].source_pdf_to_canonical,
        pages[0].render_pixels_to_canonical,
        pages[0].source_page_sha256,
        (pages[0].objects[0], bounded),
        (pages[0].objects[0].id, bounded.id),
    )
    with pytest.raises(RenderViewsError, match="valid supported encoded image"):
        write_view_tree(
            tmp_path / "fake-view",
            manifest,
            (page,),
            structure,
            styles,
            raster_policy="error",
            replica_mode=True,
            raster_assets={digest: source},
        )

    real = _png_bytes()
    real_digest = hashlib.sha256(real).hexdigest()
    real_source = tmp_path / "real.png"
    real_source.write_bytes(real)
    left = SceneObject(
        content_id("region", {"split": "left"}),
        "raster",
        Box(0, 0, 300_000, 792_000),
        payload=_raster_payload(real_digest, alt_text="left"),
    )
    right = SceneObject(
        content_id("region", {"split": "right"}),
        "raster",
        Box(300_000, 0, 600_000, 792_000),
        payload=_raster_payload(real_digest, alt_text="right"),
    )
    split_page = PageRecord(
        pages[0].id,
        1,
        0,
        pages[0].page_box,
        pages[0].page_class,
        pages[0].source_pdf_to_canonical,
        pages[0].render_pixels_to_canonical,
        pages[0].source_page_sha256,
        (pages[0].objects[0], left, right),
        (pages[0].objects[0].id, left.id, right.id),
    )
    with pytest.raises(RenderViewsError, match="Wave-8"):
        write_view_tree(
            tmp_path / "split-view",
            manifest,
            (split_page,),
            structure,
            styles,
            raster_policy="error",
            replica_mode=True,
            raster_assets={real_digest: real_source},
        )

    approval = {
        "manual_approval_id": "review-photo-spread-1",
        "explicitly_photo_dominant": True,
        "contains_meaningful_text_or_vector": False,
    }
    approved_left = SceneObject(
        left.id, left.kind, left.box, payload={**left.payload, **approval}
    )
    approved_right = SceneObject(
        right.id, right.kind, right.box, payload={**right.payload, **approval}
    )
    approved_page = PageRecord(
        pages[0].id,
        1,
        0,
        pages[0].page_box,
        pages[0].page_class,
        pages[0].source_pdf_to_canonical,
        pages[0].render_pixels_to_canonical,
        pages[0].source_page_sha256,
        (pages[0].objects[0], approved_left, approved_right),
        (pages[0].objects[0].id, approved_left.id, approved_right.id),
    )
    result = write_view_tree(
        tmp_path / "approved-split-view",
        manifest,
        (approved_page,),
        structure,
        styles,
        raster_policy="error",
        replica_mode=True,
        raster_assets={real_digest: real_source},
    )
    assert result.svg_paths[0].read_text(encoding="utf-8").count("<image ") == 2
