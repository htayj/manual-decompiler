from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from lispmdoc.benchmark import wave2
from lispmdoc.benchmark.wave2 import (
    Wave2Inventory,
    Wave2InventoryError,
    load_inventory,
    verify_inventory,
)


def _pdf(path: Path) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as output:
        writer.write(output)
    return path.read_bytes()


def _truth(
    *, origin: str = "independent-adjudicated", status: str = "inventory-only"
) -> dict[str, object]:
    return {
        "origin": origin,
        "status": status,
        "mapping_gate": "not-applicable" if origin == "independent-adjudicated" else "pending",
        "layout_gate": "pending",
        "reading_order_gate": "pending",
        "semantics_gate": "pending",
        "object_extraction_gate": "not-applicable",
        "evidence": None,
    }


def _inventory(path: Path, source: bytes, **manual_overrides: object) -> bytes:
    manual: dict[str, object] = {
        "manual_id": "fixture-manual",
        "source_path": "source.pdf",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_byte_size": len(source),
        "page_count": 1,
        "intended_page_classes": ["scan-gray"],
        "candidate_pages": [
            {
                "page_index": 0,
                "page_class": "scan-gray",
                "tags": ["clean-scanned-prose"],
            }
        ],
        "truth": _truth(),
    }
    manual.update(manual_overrides)
    raw = {
        "format_version": "lispmdoc-wave2-representative-inventory-1",
        "manuals": [manual],
    }
    content = json.dumps(raw, sort_keys=True).encode("utf-8")
    path.write_bytes(content)
    return content


def test_inventory_verifies_one_descriptor_read_and_remains_inventory_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source.pdf"
    source = _pdf(source_path)
    _inventory(tmp_path / "inventory.json", source)
    original_read = wave2._read_contained_regular
    source_reads = 0

    def counted_read(root: Path, relative: str, label: str) -> bytes:
        nonlocal source_reads
        if relative == "source.pdf":
            source_reads += 1
        return original_read(root, relative, label)

    monkeypatch.setattr(wave2, "_read_contained_regular", counted_read)
    inventory, _ = load_inventory(tmp_path, "inventory.json")
    report = verify_inventory(tmp_path, inventory)

    assert source_reads == 1
    assert report["eligible_pages"] == 0
    assert report["composition"] == {}
    assert report["selection_readiness"] == "undersized"


@pytest.mark.parametrize("source_path", ("../source.pdf", "/tmp/source.pdf"))
def test_inventory_rejects_source_path_escape(tmp_path: Path, source_path: str) -> None:
    source = _pdf(tmp_path / "source.pdf")
    _inventory(tmp_path / "inventory.json", source, source_path=source_path)
    with pytest.raises(Wave2InventoryError, match="contained relative path"):
        load_inventory(tmp_path, "inventory.json")


def test_inventory_rejects_symlinked_source_and_identity_drift(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "outside.pdf")
    (tmp_path / "source.pdf").symlink_to(tmp_path / "outside.pdf")
    _inventory(tmp_path / "inventory.json", source)
    inventory, _ = load_inventory(tmp_path, "inventory.json")
    with pytest.raises(Wave2InventoryError, match="contains a symlink"):
        verify_inventory(tmp_path, inventory)

    (tmp_path / "source.pdf").unlink()
    (tmp_path / "source.pdf").write_bytes(source + b"changed")
    with pytest.raises(Wave2InventoryError, match="identity drifted"):
        verify_inventory(tmp_path, inventory)


def test_inventory_rejects_duplicate_pages_and_malformed_tags(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "source.pdf")
    _inventory(
        tmp_path / "inventory.json",
        source,
        candidate_pages=[
            {"page_index": 0, "page_class": "scan-gray", "tags": ["clean-scanned-prose"]},
            {"page_index": 0, "page_class": "scan-gray", "tags": ["unknown"]},
        ],
    )
    with pytest.raises(Wave2InventoryError, match="unknown composition tag"):
        Wave2Inventory.from_bytes((tmp_path / "inventory.json").read_bytes())

    _inventory(
        tmp_path / "inventory.json",
        source,
        candidate_pages=[
            {"page_index": 0, "page_class": "scan-gray", "tags": ["clean-scanned-prose"]},
            {"page_index": 0, "page_class": "scan-gray", "tags": ["table"]},
        ],
    )
    with pytest.raises(Wave2InventoryError, match="indices must be unique"):
        Wave2Inventory.from_bytes((tmp_path / "inventory.json").read_bytes())


def test_ocr_derived_truth_never_becomes_selection_eligible(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "source.pdf")
    truth = _truth(origin="ocr-derived")
    _inventory(tmp_path / "inventory.json", source, truth=truth)
    inventory, _ = load_inventory(tmp_path, "inventory.json")
    report = verify_inventory(tmp_path, inventory)

    assert report["eligible_pages"] == 0
    assert report["pages"] == [
        {
            "manual_id": "fixture-manual",
            "page_class": "scan-gray",
            "page_index": 0,
            "selection_eligible": False,
            "tags": ["clean-scanned-prose"],
            "truth_origin": "ocr-derived",
        }
    ]


@pytest.mark.parametrize("origin", ("independent-adjudicated", "native-pdf-objects"))
def test_reviewed_truth_without_a_supported_receipt_verifier_is_rejected(
    tmp_path: Path, origin: str
) -> None:
    source = _pdf(tmp_path / "source.pdf")
    truth = _truth(origin=origin, status="reviewed")
    truth["evidence"] = {"kind": "self-attested"}
    _inventory(tmp_path / "inventory.json", source, truth=truth)
    with pytest.raises(Wave2InventoryError, match="supported contained receipt verifier"):
        load_inventory(tmp_path, "inventory.json")


def test_load_rejects_escaping_or_symlinked_inventory(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "source.pdf")
    _inventory(tmp_path / "inventory.json", source)
    with pytest.raises(Wave2InventoryError, match="contained relative path"):
        load_inventory(tmp_path, "../inventory.json")
    (tmp_path / "inventory-link.json").symlink_to(tmp_path / "inventory.json")
    with pytest.raises(Wave2InventoryError, match="contains a symlink"):
        load_inventory(tmp_path, "inventory-link.json")


@pytest.mark.parametrize(
    ("location", "field"),
    (
        (("root",), "unexpected"),
        (("manuals", 0), "unexpected"),
        (("manuals", 0, "candidate_pages", 0), "unexpected"),
        (("manuals", 0, "truth"), "unexpected"),
        (("manuals", 0, "truth", "evidence"), "unexpected"),
    ),
)
def test_inventory_rejects_unknown_fields(
    tmp_path: Path, location: tuple[object, ...], field: str
) -> None:
    source = _pdf(tmp_path / "source.pdf")
    _inventory(tmp_path / "inventory.json", source)
    raw: dict[str, Any] = json.loads((tmp_path / "inventory.json").read_text(encoding="utf-8"))
    target: Any = raw
    if location == ("root",):
        target = raw
    else:
        for part in location:
            target = target[part]
    if location[-1] == "evidence":
        target = {"kind": "placeholder"}
        raw["manuals"][0]["truth"]["evidence"] = target
    target[field] = True
    (tmp_path / "inventory.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(Wave2InventoryError, match="unknown fields"):
        load_inventory(tmp_path, "inventory.json")


def test_descriptor_bound_read_survives_path_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source.pdf"
    source = _pdf(source_path)
    _inventory(tmp_path / "inventory.json", source)
    replacement = tmp_path / "replacement.pdf"
    replacement.write_bytes(_pdf(replacement) + b"replacement-drift")
    original_open = os.open
    replaced = False

    def open_then_replace(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        nonlocal replaced
        fd = original_open(path, flags, mode, dir_fd=dir_fd)
        if path == "source.pdf" and dir_fd is not None and not replaced:
            replaced = True
            replacement.replace(source_path)
        return int(fd)

    monkeypatch.setattr(os, "open", open_then_replace)
    inventory, _ = load_inventory(tmp_path, "inventory.json")
    report = verify_inventory(tmp_path, inventory)

    assert replaced
    assert report["inventory_ready"] is True


def test_recovered_source_candidate_must_match_the_verified_queue_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _pdf(tmp_path / "source.pdf")
    truth = {
        "origin": "recovered-source-reviewed",
        "status": "reviewed",
        "mapping_gate": "verified",
        "layout_gate": "verified",
        "reading_order_gate": "not-applicable",
        "semantics_gate": "verified",
        "object_extraction_gate": "not-applicable",
        "evidence": {"kind": "chinual-recovered-slice"},
    }
    _inventory(
        tmp_path / "inventory.json",
        source,
        intended_page_classes=["scan-gray"],
        truth=truth,
    )
    inventory, _ = load_inventory(tmp_path, "inventory.json")

    class QueuePage:
        page_class = "scan-gray"
        tags = ("table",)

    monkeypatch.setattr(wave2, "_verify_chinual_evidence", lambda _root, _manual: {0: QueuePage()})
    with pytest.raises(Wave2InventoryError, match="class or tags drifted"):
        verify_inventory(tmp_path, inventory)


def test_tracked_candidates_use_the_reviewed_representative_pages() -> None:
    root = Path(__file__).resolve().parents[2]
    inventory = Wave2Inventory.from_bytes(
        (root / "config/benchmarks/wave2-representative-candidates.json").read_bytes()
    )
    pages = {manual.manual_id: manual.pages for manual in inventory.manuals}

    assert [page.page_index for page in pages["k-machine"]] == [2, 72, 90, 95]
    assert pages["k-machine"][3].tags == ("born-digital",)
    assert pages["clim-2"][0].page_index == 562
    assert pages["clim-2"][0].tags == ("born-digital", "multi-column-toc-index-list")
    assert pages["cadr-schematic"][0].page_index == 1
    assert pages["interlisp-oct-1978"][0].page_index == 99
    assert pages["symbolics-users-guide-jul86"][0].page_index == 49
