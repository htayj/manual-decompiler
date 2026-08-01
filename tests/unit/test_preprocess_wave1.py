from __future__ import annotations

import zlib
from pathlib import Path

from PIL import Image, ImageDraw

from lispmdoc.ingest.pdf_extract import extract_simple_page_image
from lispmdoc.model import AffineTransform
from lispmdoc.preprocess import (
    PreprocessSettings,
    analyze_page_shape,
    detect_scanner_border,
    estimate_deskew,
    preprocess_image,
    render_pdf,
)


def _write_scan_pdf(path: Path, *, extra_text: bool = False) -> None:
    width, height = 64, 80
    pixels = bytes([0xAA]) * ((width + 7) // 8 * height)
    compressed = zlib.compress(pixels)
    content = f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q".encode()
    resources = "/XObject << /Im0 5 0 R >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Resources << {resources} >> /Contents 4 0 R >>"
        ).encode(),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            "/ColorSpace /DeviceGray /BitsPerComponent 1 /Filter /FlateDecode "
            f"/Length {len(compressed)} >>\nstream\n"
        ).encode()
        + compressed
        + b"\nendstream",
    ]
    if extra_text:
        text = b"BT (not simple) Tj ET\n"
        content = content + b"\n" + text
        objects[3] = (
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(output)


def _lined_page(angle: float = 0.0) -> Image.Image:
    image = Image.new("L", (500, 700), 255)
    draw = ImageDraw.Draw(image)
    for y in range(100, 600, 30):
        for x in range(60, 430, 60):
            draw.rectangle((x, y, x + 42, y + 5), fill=0)
    return image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)


def test_simple_page_image_extracts_decoded_pixels_without_rendering(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    _write_scan_pdf(source)
    before = source.read_bytes()

    result = extract_simple_page_image(source, 1, tmp_path / "extracted").to_dict()

    assert result["status"] == "extracted"
    assert result["lossless_kind"] == "decoded-pixel-lossless"
    assert result["encoded_stream_preserved"] is False
    assert result["width_px"] == 64
    assert result["height_px"] == 80
    assert source.read_bytes() == before


def test_page_image_extraction_rejects_mixed_page_operators(tmp_path: Path) -> None:
    source = tmp_path / "mixed.pdf"
    _write_scan_pdf(source, extra_text=True)

    result = extract_simple_page_image(source, 1, tmp_path / "extracted").to_dict()

    assert result["status"] == "not-applicable"
    assert "non-image" in result["reason"]
    assert "BT" in result["unsupported_operators"]


def test_deskew_estimators_recover_synthetic_geometry_within_gate() -> None:
    estimate = estimate_deskew(_lined_page(2.3))

    assert abs(estimate["correction_millidegrees"] + 2300) <= 250
    assert estimate["disagreement_millidegrees"] <= 250
    assert estimate["confidence_milli"] >= 100


def test_border_crop_is_dispositioned_and_transform_is_exact(tmp_path: Path) -> None:
    source = tmp_path / "border.png"
    helper = tmp_path / "helper.png"
    overlay = tmp_path / "overlay.png"
    image = Image.new("RGB", (200, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 199, 299), outline="black", width=5)
    draw.rectangle((40, 80, 160, 90), fill="black")
    image.save(source)

    border = detect_scanner_border(image)
    result = preprocess_image(
        source,
        helper,
        overlay,
        AffineTransform.identity(),
        settings=PreprocessSettings(automatic_deskew=False),
    )
    crop = next(item for item in result["operations"] if item["name"] == "crop")
    transform = AffineTransform.from_dict(result["helper_pixels_to_source_pixels"])

    assert border["crop_box"] == [5, 5, 195, 295]
    assert crop["status"] == "applied"
    assert crop["evidence"]["removed_regions_disposition"] == "scanner-border"
    assert transform.apply(0, 0).to_dict() == {"x": 5, "y": 5}
    with Image.open(helper) as helper_image:
        assert helper_image.size == (190, 290)
    assert overlay.is_file()


def test_spread_and_foldout_are_review_evidence_not_automatic_splits() -> None:
    image = Image.new("L", (500, 220), 100)
    ImageDraw.Draw(image).rectangle((247, 0, 253, 219), fill=255)

    analysis = analyze_page_shape(image)

    assert analysis["spread_candidate"]
    assert analysis["foldout_candidate"]
    assert analysis["disposition"] == "review"


def test_estimator_disagreement_cannot_silently_change_helper(
    tmp_path: Path, monkeypatch: object
) -> None:
    import lispmdoc.preprocess.operations as operations

    source = tmp_path / "source.png"
    helper = tmp_path / "helper.png"
    overlay = tmp_path / "overlay.png"
    _lined_page().convert("RGB").save(source)
    disagreement = {
        "confidence_milli": 900,
        "correction_millidegrees": 1000,
        "disagreement_millidegrees": 900,
        "estimators": [
            {"name": "first", "correction_millidegrees": 550},
            {"name": "second", "correction_millidegrees": 1450},
        ],
    }
    monkeypatch.setattr(operations, "estimate_deskew", lambda *_args, **_kwargs: disagreement)  # type: ignore[attr-defined]

    result = preprocess_image(source, helper, overlay, AffineTransform.identity())
    deskew = next(item for item in result["operations"] if item["name"] == "deskew")

    assert deskew["status"] == "review"
    assert not result["applied"]
    assert helper.read_bytes() == source.read_bytes()


def test_render_manifest_separates_source_helper_overlay_and_extraction(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    _write_scan_pdf(source)
    before = source.read_bytes()

    first = render_pdf(source, tmp_path / "work", dpi=72)
    second = render_pdf(source, tmp_path / "work", dpi=72)
    clean_root = render_pdf(source, tmp_path / "other-work", dpi=72)
    page = first.manifest.to_dict()["pages"][0]

    assert page["source_render"]["path"] != page["ocr_helper_render"]["path"]
    assert page["preprocessing"]["debug_overlay"]["sha256"]
    assert page["lossless_page_image"]["status"] == "extracted"
    assert second.cache_reused
    assert first.manifest.to_json() == second.manifest.to_json()
    assert first.manifest.to_json() == clean_root.manifest.to_json()
    for record in ("source_render", "ocr_helper_render"):
        first_path = first.artifact_directory / page[record]["path"]
        other_path = clean_root.artifact_directory / page[record]["path"]
        assert first_path.read_bytes() == other_path.read_bytes()
    overlay_path = page["preprocessing"]["debug_overlay"]["path"]
    assert (first.artifact_directory / overlay_path).read_bytes() == (
        clean_root.artifact_directory / overlay_path
    ).read_bytes()
    assert source.read_bytes() == before


def test_explicit_orientation_has_exact_reversible_transform(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    helper = tmp_path / "helper.png"
    overlay = tmp_path / "overlay.png"
    Image.new("RGB", (200, 300), "white").save(source)

    result = preprocess_image(
        source,
        helper,
        overlay,
        AffineTransform.identity(),
        settings=PreprocessSettings(
            automatic_deskew=False,
            automatic_border_crop=False,
            orientation_degrees=90,
        ),
    )
    transform = AffineTransform.from_dict(result["helper_pixels_to_source_pixels"])

    with Image.open(helper) as helper_image:
        assert helper_image.size == (300, 200)
    assert transform.apply(0, 0).to_dict() == {"x": 200, "y": 0}
    assert transform.apply(300, 200).to_dict() == {"x": 0, "y": 300}


def test_explicit_tonal_operations_are_recorded_and_dewarp_stays_reviewable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    helper = tmp_path / "helper.png"
    overlay = tmp_path / "overlay.png"
    _lined_page().convert("RGB").save(source)
    settings = PreprocessSettings(
        automatic_deskew=False,
        automatic_border_crop=False,
        illumination_correction=True,
        bleed_through_reduction=True,
        binarization=True,
    )

    result = preprocess_image(
        source, helper, overlay, AffineTransform.identity(), settings=settings
    )
    statuses = {item["name"]: item["status"] for item in result["operations"]}

    assert statuses["illumination-correction"] == "applied"
    assert statuses["bleed-through-reduction"] == "applied"
    assert statuses["binarization"] == "applied"
    assert statuses["dewarp"] == "review"
