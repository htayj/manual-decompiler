from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from lispmdoc.hashing import sha256_file
from lispmdoc.model import (
    AffineTransform,
    Box,
    Manifest,
    PageRecord,
    PageReference,
    SourceRecord,
    StructureNode,
    StructureRecord,
    StylesRecord,
    content_id,
)
from lispmdoc.package import (
    MIMETYPE,
    PackageError,
    inspect_package,
    pack_directory,
    write_authoring_tree,
)

SHA = "a" * 64


def test_package_is_deterministic_and_mimetype_is_first(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "manifest.json").write_text('{"schema_version":"1.0"}\n', encoding="utf-8")
    pages = source / "pages"
    pages.mkdir()
    (pages / "p000001.json").write_text("{}\n", encoding="utf-8")
    first = tmp_path / "first.lmdoc"
    second = tmp_path / "second.lmdoc"

    pack_directory(source, first)
    pack_directory(source, second)

    assert sha256_file(first) == sha256_file(second)
    assert inspect_package(first) == ["mimetype", "manifest.json", "pages/p000001.json"]
    with zipfile.ZipFile(first) as archive:
        assert archive.read("mimetype") == MIMETYPE.encode("ascii")


def test_package_rejects_output_inside_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(PackageError, match="inside"):
        pack_directory(source, source / "bad.lmdoc")


def test_package_refuses_to_replace_different_existing_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "manifest.json").write_text("{}\n", encoding="utf-8")
    output = tmp_path / "manual.lmdoc"
    output.write_bytes(b"user-modified output")

    with pytest.raises(PackageError, match="different package"):
        pack_directory(source, output)

    assert output.read_bytes() == b"user-modified output"


def test_authoring_tree_is_complete_and_uses_canonical_newlines(tmp_path: Path) -> None:
    page = PageRecord(
        id=PageRecord.derive_id(SHA, 0),
        sequence=1,
        source_page_index=0,
        page_box=Box(0, 0, 612000, 792000),
        page_class="born-digital",
        source_pdf_to_canonical=AffineTransform(1000, 0, 0, -1000, 0, 792000),
        render_pixels_to_canonical=AffineTransform(1000, 0, 0, 1000, 0, 0),
        source_page_sha256=SHA,
    )
    reference = PageReference(page.id, 1, "pages/p000001.json", 0)
    manifest = Manifest.for_source(SourceRecord(SHA, 123), (reference,), "english-manual", "b" * 64)
    root_id = content_id("structure", {"document": manifest.document_id})
    structure = StructureRecord(
        manifest.document_id,
        root_id,
        (StructureNode(root_id, "document"),),
    )
    styles = StylesRecord(manifest.document_id, ())
    destination = tmp_path / "authoring"

    write_authoring_tree(
        destination,
        manifest=manifest,
        pages=(page,),
        structure=structure,
        styles=styles,
    )

    files = sorted(
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    )
    assert files == [
        "manifest.json",
        "pages/p000001.json",
        "structure.json",
        "styles.json",
    ]
    assert (destination / "manifest.json").read_bytes().endswith(b"\n")
