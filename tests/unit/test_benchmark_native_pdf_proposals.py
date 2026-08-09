from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter

from lispmdoc.benchmark.native_pdf_proposals import (
    NativePdfProposalError,
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

    record = _tool_record(snapshot)

    original = b"#!/bin/sh\nprintf 'original-tool\\n'\n"
    assert Path(snapshot).read_bytes() == original
    assert record["binary"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert record["version_argv"] == [snapshot, "-v"]
