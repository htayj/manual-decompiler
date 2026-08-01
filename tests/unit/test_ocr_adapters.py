from __future__ import annotations

from pathlib import Path

import pytest

from lispmdoc.ocr import (
    BBox,
    OCRRequest,
    OCRUnavailable,
    PaddleOCRAdapter,
    PDFTextAdapter,
    PDFTextRun,
    SuryaAdapter,
    TesseractAdapter,
    YomiTokuAdapter,
    capability_report,
    recognize_subset,
)


def _write_text_pdf(path: Path, *, rotate: int = 0) -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/CropBox [10 20 610 780] /Rotate {rotate} "
            "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ).encode(),
        b"<< /Length 36 >>\nstream\nBT /F1 12 Tf 72 72 Td (Hello) Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
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


def test_pdf_text_adapter_retains_literal_evidence_and_geometry() -> None:
    request = OCRRequest(
        page_id="p000001",
        width=612_000,
        height=792_000,
        language="eng",
        embedded_text_runs=(PDFTextRun("(DEFUN FOO (X))", BBox(1, 2, 30, 40), native_id="42"),),
    )

    page = PDFTextAdapter().recognize(request)

    assert page.engine == "pdf-text"
    assert page.regions[0].text == "(DEFUN FOO (X))"
    assert page.regions[0].lines[0].spans[0].tokens[0].bbox == BBox(1, 2, 30, 40)
    assert page.regions[0].lines[0].spans[0].tokens[0].native_id == "42"
    assert page.to_dict() == page.to_dict()
    assert page.native_output is not None
    assert page.native_output_sha256 == page.to_dict()["native_output_sha256"]


def test_pdf_text_adapter_estimates_nonzero_crop_and_rotation_aware_geometry(
    tmp_path: Path,
) -> None:
    source = tmp_path / "rotated.pdf"
    _write_text_pdf(source, rotate=90)
    request = OCRRequest(
        page_id="p000001",
        width=760_000,
        height=600_000,
        pdf_path=str(source),
        pdf_page_number=0,
    )

    page = PDFTextAdapter().recognize(request)

    assert page.regions
    box = page.regions[0].bbox
    assert box is not None
    assert 0 <= box.x0 < box.x1 <= request.width
    assert 0 <= box.y0 < box.y1 <= request.height
    assert page.evidence.data["geometry_method"] == "estimated-from-pdf-text-matrix"


def test_tesseract_tsv_normalizes_tokens_lines_and_regions() -> None:
    request = OCRRequest(
        page_id="page", width=1_000, height=2_000, image_width_px=100, image_height_px=200
    )
    tsv = "\n".join(
        (
            "\t".join(
                (
                    "level",
                    "page_num",
                    "block_num",
                    "par_num",
                    "line_num",
                    "word_num",
                    "left",
                    "top",
                    "width",
                    "height",
                    "conf",
                    "text",
                )
            ),
            "5\t1\t2\t1\t1\t2\t30\t40\t20\t10\t90.5\tworld",
            "5\t1\t2\t1\t1\t1\t10\t40\t15\t10\t80\tHello",
            "5\t1\t3\t1\t1\t1\t10\t70\t10\t10\t-1\tnext",
        )
    )

    words = TesseractAdapter._parse_tsv(tsv, request)
    regions = TesseractAdapter._build_regions(words, request)

    assert [region.text for region in regions] == ["Hello world", "next"]
    assert regions[0].lines[0].spans[0].tokens[0].bbox == BBox(100, 400, 250, 500)
    assert regions[0].confidence == pytest.approx(0.8525)
    assert regions[1].confidence is None


def test_tesseract_requires_image_dimensions_for_canonical_geometry() -> None:
    request = OCRRequest(page_id="page", width=100, height=100)
    with pytest.raises(OCRUnavailable, match="image_width_px"):
        TesseractAdapter._pixel_box(0, 0, 1, 1, request)


def test_optional_adapters_report_placeholders_without_claiming_readiness() -> None:
    report = capability_report((YomiTokuAdapter(), SuryaAdapter(), PaddleOCRAdapter()))

    assert [item.engine for item in report] == ["paddleocr", "surya", "yomitoku"]
    assert all(item.status == "unavailable" and not item.available for item in report)
    assert all(
        "install" in (item.detail or "") or "detected" in (item.detail or "") for item in report
    )


def test_explicit_page_subset_preserves_request_order_and_rejects_duplicates() -> None:
    adapter = PDFTextAdapter()
    first = OCRRequest("first", 10, 10, embedded_text_runs=(PDFTextRun("one"),))
    second = OCRRequest("second", 10, 10, embedded_text_runs=(PDFTextRun("two"),))
    assert [page.page_id for page in recognize_subset(adapter, (second, first))] == [
        "second",
        "first",
    ]
    with pytest.raises(ValueError, match="duplicate"):
        recognize_subset(adapter, (first, first))
