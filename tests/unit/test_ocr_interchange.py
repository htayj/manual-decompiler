from __future__ import annotations

import pytest

from lispmdoc.evidence import ArtifactStore
from lispmdoc.interchange import export_alto, export_pagexml, import_alto, import_pagexml
from lispmdoc.ocr import BBox, OCRRequest, PDFTextAdapter, PDFTextRun


def _page():  # type: ignore[no-untyped-def]
    return PDFTextAdapter().recognize(
        OCRRequest(
            page_id="page-1",
            width=1000,
            height=2000,
            embedded_text_runs=(PDFTextRun("(DEFUN FOO)", BBox(10, 20, 300, 60)),),
        )
    )


def test_native_output_is_exact_and_can_be_retained_in_artifact_store(tmp_path) -> None:  # type: ignore[no-untyped-def]
    page = _page()
    assert page.native_output is not None
    artifact = page.store_native_output(ArtifactStore(tmp_path / "evidence"))
    assert artifact is not None
    assert artifact.sha256 == page.native_output_sha256


@pytest.mark.parametrize(
    "exporter, importer", [(export_alto, import_alto), (export_pagexml, import_pagexml)]
)
def test_xml_interchange_round_trips_literal_text_and_geometry(exporter, importer) -> None:  # type: ignore[no-untyped-def]
    source = _page()
    first = exporter(source)
    second = exporter(source)
    imported = importer(first)
    assert first == second
    assert imported.width == source.width
    assert imported.height == source.height
    assert imported.regions[0].lines[0].text == "(DEFUN FOO)"
    assert imported.regions[0].lines[0].bbox == BBox(10, 20, 300, 60)
    assert imported.native_output == first


@pytest.mark.parametrize("importer", [import_alto, import_pagexml])
def test_xml_interchange_rejects_dtd(importer) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="DTD"):
        importer(b"<!DOCTYPE x><x/>")
