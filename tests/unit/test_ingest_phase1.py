from __future__ import annotations

import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from lispmdoc.ingest import (  # noqa: E402
    PdfInspectionError,
    discover_pdfs,
    fingerprint_source,
    group_by_source,
    inspect_pdf,
    verify_source,
)


def _write_pdf(
    path: Path,
    *,
    width: int = 612,
    height: int = 792,
    text: str | None = None,
    bilevel_image: bool = False,
) -> None:
    """Write a tiny valid PDF fixture without requiring a PDF authoring library."""

    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    resources = b""
    if text is not None:
        resources += b" /Font << /F1 5 0 R >>"
    if bilevel_image:
        resources += b" /XObject << /Im0 6 0 R >>"
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Resources <<{resources.decode()} >> /Contents 4 0 R >>"
        ).encode()
    )
    content: list[bytes] = []
    if bilevel_image:
        content.append(f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q".encode())
    if text is not None:
        content.append(f"BT /F1 12 Tf 72 72 Td ({text}) Tj ET".encode())
    content_bytes = b"\n".join(content)
    objects.append(
        b"<< /Length "
        + str(len(content_bytes)).encode()
        + b" >>\nstream\n"
        + content_bytes
        + b"\nendstream"
    )
    if text is not None:
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    else:
        # Object 5 is unused, keeping the image object number stable.
        objects.append(b"<< /Producer (synthetic fixture) >>")
    if bilevel_image:
        pixels = bytes([0xAA]) * ((width * height + 7) // 8)
        compressed = zlib.compress(pixels)
        objects.append(
            (
                f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                "/ColorSpace /DeviceGray /BitsPerComponent 1 /Filter /FlateDecode "
                f"/Length {len(compressed)} >>\nstream\n"
            ).encode()
            + compressed
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


def test_discovery_is_recursive_sorted_and_retains_duplicate_aliases(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    _write_pdf(nested / "B.PDF", text="hello")
    _write_pdf(tmp_path / "a.pdf", text="hello")
    (tmp_path / "not-a-pdf.txt").write_text("ignore", encoding="utf-8")

    discovered = discover_pdfs(tmp_path)

    assert [entry.collection_path for entry in discovered] == ["a.pdf", "nested/B.PDF"]
    groups = group_by_source(discovered)
    assert len(groups) == 1
    assert [entry.collection_path for entry in next(iter(groups.values()))] == [
        "a.pdf",
        "nested/B.PDF",
    ]


def test_fingerprint_verification_detects_replaced_source(tmp_path: Path) -> None:
    source = tmp_path / "manual.pdf"
    _write_pdf(source, text="version one")
    saved = fingerprint_source(source)
    _write_pdf(source, text="version two")

    verification = verify_source(source, saved)

    assert not verification.matches
    assert verification.actual.byte_size == source.stat().st_size
    assert verification.expected.document_id.startswith("sha256:")


def test_inspection_classifies_born_digital_and_is_json_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "born.pdf"
    _write_pdf(source, text="Born digital embedded text")

    first = inspect_pdf(source, collection_root=tmp_path)
    second = inspect_pdf(source, collection_root=tmp_path)
    page = first.to_dict()["pages"][0]

    assert first.to_json() == second.to_json()
    assert first.to_dict()["source_verification"]["matches"] is True
    assert first.to_dict()["collection_path"] == "born.pdf"
    assert page["classification"]["label"] == "born-digital"
    assert page["embedded_text"]["non_whitespace_characters"] > 10


def test_inspection_distinguishes_scan_hybrid_and_schematic_evidence(tmp_path: Path) -> None:
    scan = tmp_path / "scan.pdf"
    hybrid = tmp_path / "hybrid.pdf"
    schematic = tmp_path / "schematic.pdf"
    _write_pdf(scan, bilevel_image=True)
    _write_pdf(hybrid, text="Existing Acrobat OCR text layer is present", bilevel_image=True)
    _write_pdf(schematic, width=792, height=612, bilevel_image=True)

    assert inspect_pdf(scan).to_dict()["pages"][0]["classification"]["label"] == "scan-bilevel"
    assert inspect_pdf(hybrid).to_dict()["pages"][0]["classification"]["label"] == "hybrid"
    schematic_page = inspect_pdf(schematic).to_dict()["pages"][0]
    assert schematic_page["classification"]["label"] == "schematic"
    assert "scan-bilevel" in schematic_page["classification"]["alternatives"]


def test_corrupt_pdf_has_explicit_failure(tmp_path: Path) -> None:
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"not a pdf")

    with pytest.raises(PdfInspectionError):
        inspect_pdf(source)
