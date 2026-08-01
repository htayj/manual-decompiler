from __future__ import annotations

from pathlib import Path

import pytest

from lispmdoc.model import AffineTransform
from lispmdoc.preprocess import (
    PageSubsetError,
    UnsafeOutputRootError,
    parse_page_subset,
    probe_render_backend,
    render_pdf,
)


def _write_pdf(path: Path, *, rotate: int = 0) -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Rotate {rotate} /Resources << /Font << /F1 7 0 R >> >> /Contents 5 0 R >>"
        ).encode(),
        (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Rotate {rotate} /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>"
        ).encode(),
        b"<< /Length 36 >>\nstream\nBT /F1 12 Tf 72 72 Td (First) Tj ET\nendstream",
        b"<< /Length 37 >>\nstream\nBT /F1 12 Tf 72 72 Td (Second) Tj ET\nendstream",
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


def test_page_subset_parser_is_canonical_and_strict() -> None:
    assert parse_page_subset(None, 5) == (1, 2, 3, 4, 5)
    assert parse_page_subset("5,3-4,1,3", 5) == (1, 3, 4, 5)
    assert parse_page_subset("3-", 5) == (3, 4, 5)
    with pytest.raises(PageSubsetError):
        parse_page_subset("0,2", 5)
    with pytest.raises(PageSubsetError):
        parse_page_subset("3-2", 5)
    with pytest.raises(PageSubsetError):
        parse_page_subset("one", 5)


def test_renderer_capability_probe_reports_a_version() -> None:
    backend = probe_render_backend()

    assert backend["name"] in {"pdftoppm", "pdftocairo", "pymupdf"}
    assert backend["version"]


def test_render_manifest_records_hashes_dimensions_and_exact_transforms(tmp_path: Path) -> None:
    source = tmp_path / "manual.pdf"
    output = tmp_path / "generated"
    _write_pdf(source)

    first = render_pdf(source, output, dpi=72, pages="2,1")
    second = render_pdf(source, output, dpi=72, pages="1-2")
    value = first.manifest.to_dict()
    page = value["pages"][0]

    assert not first.cache_reused
    assert second.cache_reused
    assert first.manifest.to_json() == second.manifest.to_json()
    assert value["selected_pages"] == [1, 2]
    assert value["source"]["sha256"]
    assert len(value["source"]["sha256"]) == 64
    assert page["source_page_sha256"]
    assert page["image"]["width_px"] == 612
    assert page["image"]["height_px"] == 792
    assert (
        page["normalization"]["pixel_to_canonical"]
        == page["normalization"]["normalized_pixels_to_canonical"]
    )
    source_transform = AffineTransform.from_dict(page["source_pdf_to_canonical"])
    pixels_transform = AffineTransform.from_dict(page["normalization"]["pixel_to_canonical"])
    assert source_transform.apply(0, 792).to_dict() == {"x": 0, "y": 0}
    assert pixels_transform.apply(612, 792).to_dict() == {"x": 612000, "y": 792000}
    assert (first.artifact_directory / page["image"]["path"]).is_file()


def test_rotation_swaps_canonical_page_dimensions_without_geometric_guessing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "rotated.pdf"
    _write_pdf(source, rotate=90)

    result = render_pdf(source, tmp_path / "generated", dpi=72)
    page = result.manifest.to_dict()["pages"][0]

    assert page["canonical_page_size_micropoints"] == {"width": 792000, "height": 612000}
    assert page["source_page_rotation_degrees"] == 90
    assert result.manifest.to_dict()["normalization"]["applied"] is False


def test_output_root_may_not_contain_source(tmp_path: Path) -> None:
    source = tmp_path / "manual.pdf"
    _write_pdf(source)

    with pytest.raises(UnsafeOutputRootError):
        render_pdf(source, tmp_path, dpi=72)
