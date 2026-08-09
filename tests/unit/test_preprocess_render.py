from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from PIL import Image

from lispmdoc.model import AffineTransform
from lispmdoc.preprocess import (
    PageSubsetError,
    UnsafeOutputRootError,
    parse_page_subset,
    probe_render_backend,
    render_pdf,
)
from lispmdoc.preprocess import render as render_module


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


def test_backend_override_legacy_form_and_digest_identity_are_strict(tmp_path: Path) -> None:
    source = tmp_path / "manual.pdf"
    _write_pdf(source)
    first_tool = tmp_path / "renderer-a"
    second_tool = tmp_path / "renderer-b"
    first_tool.write_bytes(b"first renderer")
    second_tool.write_bytes(b"second renderer")

    def fake_render(
        _source: Path, _page: int, _dpi: int, target: Path, _backend: dict[str, str]
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (612, 792), "white").save(target)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(render_module, "_render_one", fake_render)
    try:
        legacy = {"executable": first_tool.as_posix(), "name": "pdftoppm", "version": "fixture"}
        first = render_pdf(source, tmp_path / "output", dpi=72, pages=(1,), backend_override=legacy)
        first_value = first.manifest.to_dict()
        assert (
            first_value["backend"]["executable_sha256"]
            == hashlib.sha256(first_tool.read_bytes()).hexdigest()
        )
        with pytest.raises(render_module.RenderBackendUnavailableError, match="digest drifted"):
            render_pdf(
                source,
                tmp_path / "bad",
                dpi=72,
                pages=(1,),
                backend_override={
                    **legacy,
                    "identity_executable": "tools/pdftoppm",
                    "executable_sha256": "0" * 64,
                },
            )
        second = render_pdf(
            source,
            tmp_path / "output",
            dpi=72,
            pages=(1,),
            backend_override={
                "executable": second_tool.as_posix(),
                "executable_sha256": hashlib.sha256(second_tool.read_bytes()).hexdigest(),
                "identity_executable": "tools/pdftoppm",
                "name": "pdftoppm",
                "version": "fixture",
            },
        )
        assert not second.cache_reused
        assert first.artifact_directory != second.artifact_directory
    finally:
        monkeypatch.undo()


def test_strict_override_executes_sealed_bytes_after_pathname_replacement(tmp_path: Path) -> None:
    source = tmp_path / "manual.pdf"
    _write_pdf(source)
    tool = tmp_path / "renderer"
    original = b"sealed renderer bytes"
    tool.write_bytes(original)
    seen: list[tuple[str, bytes]] = []

    def fake_render(
        _source: Path, page: int, _dpi: int, target: Path, backend: dict[str, object]
    ) -> None:
        execution_path = backend["executable"]
        descriptor = backend["_execution_fd"]
        assert isinstance(execution_path, str) and isinstance(descriptor, int)
        seen.append((execution_path, os.pread(descriptor, len(original), 0)))
        if page == 1:
            tool.write_bytes(b"attacker replacement")
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (612, 792), "white").save(target)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(render_module, "_render_one", fake_render)
    try:
        render_pdf(
            source,
            tmp_path / "output",
            dpi=72,
            backend_override={
                "executable": tool.as_posix(),
                "executable_sha256": hashlib.sha256(original).hexdigest(),
                "identity_executable": "tools/pdftoppm",
                "name": "pdftoppm",
                "version": "fixture",
            },
        )
    finally:
        monkeypatch.undo()
    assert seen == [(seen[0][0], original), (seen[0][0], original)]
    assert seen[0][0].startswith("/proc/self/fd/")


def test_override_rejects_caller_supplied_execution_fd_and_closes_its_own_fd(
    tmp_path: Path,
) -> None:
    source = tmp_path / "manual.pdf"
    _write_pdf(source)
    tool = tmp_path / "renderer"
    tool.write_bytes(b"renderer")
    strict = {
        "executable": tool.as_posix(),
        "executable_sha256": hashlib.sha256(tool.read_bytes()).hexdigest(),
        "identity_executable": "tools/pdftoppm",
        "name": "pdftoppm",
        "version": "fixture",
    }
    with pytest.raises(render_module.RenderBackendUnavailableError, match="must contain"):
        render_pdf(
            source, tmp_path / "bad", dpi=72, backend_override={**strict, "execution_fd": "0"}
        )

    captured: list[int] = []
    sealed_descriptors: list[int] = []
    actual_seal = render_module._seal_verified_executable

    def capture_seal(*args: object, **kwargs: object) -> object:
        sealed = actual_seal(*args, **kwargs)  # type: ignore[arg-type]
        sealed_descriptors.append(sealed.descriptor)
        return sealed

    def fake_render(
        _source: Path, _page: int, _dpi: int, target: Path, backend: dict[str, object]
    ) -> None:
        descriptor = backend["_execution_fd"]
        assert isinstance(descriptor, int)
        captured.append(descriptor)
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (612, 792), "white").save(target)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(render_module, "_render_one", fake_render)
    monkeypatch.setattr(render_module, "_seal_verified_executable", capture_seal)
    try:
        render_pdf(source, tmp_path / "good", dpi=72, pages=(1,), backend_override=strict)
        cached = render_pdf(source, tmp_path / "good", dpi=72, pages=(1,), backend_override=strict)
    finally:
        monkeypatch.undo()
    assert cached.cache_reused
    with pytest.raises(OSError):
        os.fstat(captured[0])
    assert len(sealed_descriptors) == 2
    for descriptor in sealed_descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_sealing_short_write_closes_memfd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = tmp_path / "renderer"
    tool.write_bytes(b"renderer")
    created: list[int] = []
    actual_memfd_create = os.memfd_create

    def tracked_memfd_create(name: str, flags: int) -> int:
        descriptor = actual_memfd_create(name, flags)
        created.append(descriptor)
        return descriptor

    monkeypatch.setattr(render_module.os, "memfd_create", tracked_memfd_create)
    monkeypatch.setattr(render_module.os, "write", lambda _fd, _data: 0)
    with pytest.raises(render_module.RenderBackendUnavailableError, match="cannot create sealed"):
        render_module._seal_verified_executable(tool)
    with pytest.raises(OSError):
        os.fstat(created[0])


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
