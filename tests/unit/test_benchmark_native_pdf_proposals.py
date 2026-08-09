from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from pypdf import PdfWriter

from lispmdoc.benchmark import native_pdf_proposals as native_pdf_proposals_module
from lispmdoc.benchmark.native_pdf_proposals import (
    NativePdfProposalError,
    _sealed_execution,
    _snapshot_tool,
    _tool_record,
    _validate_proposal,
    build_native_pdf_proposal,
)


def _pdf(path: Path) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as output:
        writer.write(output)
    return path.read_bytes()


def _inventory(root: Path, source: bytes, *, origin: str = "native-pdf-objects") -> None:
    (root / "config").mkdir(exist_ok=True)
    truth = {
        "origin": origin,
        "status": "inventory-only",
        "mapping_gate": "pending",
        "layout_gate": "pending",
        "reading_order_gate": "pending",
        "semantics_gate": "pending",
        "object_extraction_gate": "pending" if origin == "native-pdf-objects" else "not-applicable",
        "evidence": None,
    }
    raw = {
        "format_version": "lispmdoc-wave2-representative-inventory-1",
        "manuals": [
            {
                "manual_id": "native-fixture",
                "source_path": "source.pdf",
                "source_sha256": hashlib.sha256(source).hexdigest(),
                "source_byte_size": len(source),
                "page_count": 1,
                "intended_page_classes": ["born-digital"],
                "candidate_pages": [
                    {"page_index": 0, "page_class": "born-digital", "tags": ["born-digital"]}
                ],
                "truth": truth,
            }
        ],
    }
    (root / "config/inventory.json").write_text(json.dumps(raw), encoding="utf-8")


def test_builds_no_ocr_evidence_workspace_with_raw_inventory(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "source.pdf")
    _inventory(tmp_path, source)

    result = build_native_pdf_proposal(
        tmp_path, inventory_path="config/inventory.json", workspace_root="work/proposals"
    )

    proposal = json.loads(result.proposal_path.read_text(encoding="utf-8"))
    assert result.pages == 1
    assert proposal["disposition"] == "evidence-only-no-ocr"
    assert proposal["pages"][0]["semantic_assertions"] == []
    assert any(item["code"] == "missing-pypdf-text" for item in proposal["pages"][0]["findings"])
    assert (result.workspace / "pypdf/p000001-content-stream.bin").is_file()
    assert (result.workspace / "poppler/p000001-bbox-layout.html").is_file()
    assert (result.workspace / "review/index.html").is_file()
    inventory = json.loads((result.workspace / "raw-inventory.json").read_text(encoding="utf-8"))
    assert inventory["files"] == sorted(inventory["files"], key=lambda item: item["path"])

    with pytest.raises(NativePdfProposalError, match="already exists"):
        build_native_pdf_proposal(
            tmp_path, inventory_path="config/inventory.json", workspace_root="work/proposals"
        )


def test_builder_rejects_inventory_symlink_and_ocr_only_candidates(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "source.pdf")
    _inventory(tmp_path, source)
    (tmp_path / "config/link.json").symlink_to(tmp_path / "config/inventory.json")
    with pytest.raises(NativePdfProposalError, match="contains a symlink"):
        build_native_pdf_proposal(tmp_path, inventory_path="config/link.json")

    _inventory(tmp_path, source, origin="ocr-derived")
    with pytest.raises(NativePdfProposalError, match="no native-pdf-objects"):
        build_native_pdf_proposal(tmp_path, inventory_path="config/inventory.json")


def test_builder_rejects_symlinked_workspace_parent(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "source.pdf")
    _inventory(tmp_path, source)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "work-link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(NativePdfProposalError, match="workspace root contains a symlink"):
        build_native_pdf_proposal(
            tmp_path, inventory_path="config/inventory.json", workspace_root="work-link/proposals"
        )


def test_strict_proposal_schema_rejects_semantic_assertion_and_unknown_page_field() -> None:
    page = {
        "manual_id": "fixture",
        "source_page_index": 0,
        "source_page_number": 1,
        "page_class": "born-digital",
        "composition_tags": ["born-digital"],
        "semantic_assertions": [],
        "page_bounds_pt": ["0", "0", "72", "72"],
        "render": {},
        "renderer": {},
        "renderer_command": {},
        "pypdf": {},
        "poppler": {},
        "comparison": {},
        "findings": [],
        "limitations": [],
    }
    proposal = {
        "schema_version": "lispmdoc-native-pdf-evidence-proposal-1",
        "disposition": "evidence-only-no-ocr",
        "inventory_sha256": "0" * 64,
        "source_snapshots": [],
        "pdftotext": {},
        "pages": [page],
        "review_project": "review/review-project.json",
    }
    _validate_proposal(proposal)
    page["semantic_assertions"] = ["table"]
    with pytest.raises(NativePdfProposalError, match="must not assert semantics"):
        _validate_proposal(proposal)
    page["semantic_assertions"] = []
    page["unexpected"] = True
    with pytest.raises(NativePdfProposalError, match="strict schema"):
        _validate_proposal(proposal)


def test_tool_snapshot_cannot_be_changed_by_later_ambient_replacement(tmp_path: Path) -> None:
    ambient = tmp_path / "ambient-tool"
    ambient.write_bytes(b"#!/bin/sh\nprintf 'original-tool\\n'\n")
    snapshot = _snapshot_tool(tmp_path, ambient.as_posix(), "tool")
    ambient.write_bytes(b"replacement-tool")

    record = _tool_record(snapshot, "tools/tool")

    original = b"#!/bin/sh\nprintf 'original-tool\\n'\n"
    assert Path(snapshot).read_bytes() == original
    assert record["binary"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert record["version_argv"] == ["tools/tool", "-v"]


def test_sealed_tool_runs_original_bytes_after_snapshot_replacement(tmp_path: Path) -> None:
    tool = tmp_path / "tool"
    original = b"#!/bin/sh\nprintf 'sealed-original\\n'\n"
    tool.write_bytes(original)
    tool.chmod(0o500)
    digest = hashlib.sha256(original).hexdigest()
    descriptor, execution_path = _sealed_execution(tool.as_posix(), digest)
    try:
        tool.unlink()
        tool.write_bytes(b"#!/bin/sh\nprintf 'replacement\\n'\n")
        tool.chmod(0o500)
        record = _tool_record(
            execution_path,
            "tools/tool",
            descriptor,
            {"sha256": digest, "byte_size": len(original)},
        )
    finally:
        os.close(descriptor)
    assert record["version_stdout"] == "sealed-original\n"


def test_builder_keeps_pdftotext_sealed_and_closes_all_owned_fds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _pdf(tmp_path / "source.pdf")
    _inventory(tmp_path, source)
    descriptors: list[int] = []
    actual_seal = native_pdf_proposals_module._sealed_execution
    actual_render = native_pdf_proposals_module._render_records
    actual_render_pdf = native_pdf_proposals_module.render_pdf
    captured_overrides: list[dict[str, str]] = []

    def tracked_seal(executable: str, digest: str) -> tuple[int, str]:
        descriptor, execution_path = actual_seal(executable, digest)
        descriptors.append(descriptor)
        return descriptor, execution_path

    def replace_pdftotext_then_render(*args: object, **kwargs: object) -> object:
        snapshot = args[0]
        assert isinstance(snapshot, Path)
        tool = snapshot.parent.parent / "tools/pdftotext"
        tool.unlink()
        tool.write_bytes(b"attacker replacement")
        return actual_render(*args, **kwargs)  # type: ignore[arg-type]

    def capture_override(*args: object, **kwargs: object) -> object:
        override = kwargs["backend_override"]
        assert isinstance(override, dict)
        captured_overrides.append(dict(override))
        return actual_render_pdf(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(native_pdf_proposals_module, "_sealed_execution", tracked_seal)
    monkeypatch.setattr(
        native_pdf_proposals_module, "_render_records", replace_pdftotext_then_render
    )
    monkeypatch.setattr(native_pdf_proposals_module, "render_pdf", capture_override)
    result = build_native_pdf_proposal(
        tmp_path, inventory_path="config/inventory.json", workspace_root="work/proposals"
    )
    assert result.pages == 1
    assert len(descriptors) == 2
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    # The actual _render_records call made it through render_pdf; ensure its
    # renderer override is the public strict five-field contract only.
    assert len(captured_overrides) == 1
    assert set(captured_overrides[0]) == {
        "executable",
        "executable_sha256",
        "identity_executable",
        "name",
        "version",
    }


def test_builder_closes_sealed_fds_when_rendering_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _pdf(tmp_path / "source.pdf")
    _inventory(tmp_path, source)
    descriptors: list[int] = []
    actual_seal = native_pdf_proposals_module._sealed_execution

    def tracked_seal(executable: str, digest: str) -> tuple[int, str]:
        descriptor, execution_path = actual_seal(executable, digest)
        descriptors.append(descriptor)
        return descriptor, execution_path

    def fail_render(*_args: object, **_kwargs: object) -> object:
        raise NativePdfProposalError("forced render failure")

    monkeypatch.setattr(native_pdf_proposals_module, "_sealed_execution", tracked_seal)
    monkeypatch.setattr(native_pdf_proposals_module, "_render_records", fail_render)
    with pytest.raises(NativePdfProposalError, match="forced render failure"):
        build_native_pdf_proposal(
            tmp_path, inventory_path="config/inventory.json", workspace_root="work/proposals"
        )
    assert len(descriptors) == 2
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_two_distinct_workspace_roots_produce_byte_identical_evidence(tmp_path: Path) -> None:
    roots = [tmp_path / "first", tmp_path / "second"]
    results = []
    for root in roots:
        root.mkdir()
        source = _pdf(root / "source.pdf")
        _inventory(root, source)
        results.append(
            build_native_pdf_proposal(
                root, inventory_path="config/inventory.json", workspace_root="work/proposals"
            )
        )
    files = []
    for result in results:
        files.append(
            {
                path.relative_to(result.workspace).as_posix(): path.read_bytes()
                for path in result.workspace.rglob("*")
                if path.is_file()
            }
        )
    assert files[0] == files[1]
    assert results[0].proposal_path.read_bytes() == results[1].proposal_path.read_bytes()
    canonical = results[0].proposal_path.read_bytes()
    assert str(roots[0]).encode() not in canonical
    assert b"/home/tay/projects/lispmdoc" not in canonical
