from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lispmdoc.benchmark.chinual_recovered import ChinualImportError, import_chinual_recovered_slice


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _review(root: Path, review_name: str, annotations_name: str, page: dict[str, object]) -> None:
    asset = b"scan" if "mapping" in str(root) else b"replica"
    scan = b"scan"
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "assets/scan.png").write_bytes(scan)
    (root / "assets/replica.svg").write_bytes(asset)
    review = {
        "assets": {
            "scan": {"path": "assets/scan.png", "sha256": _sha(scan)},
            "replica": {"path": "assets/replica.svg", "sha256": _sha(asset)},
        },
        "pages": [page],
    }
    _json(root / review_name, review)
    _json(
        root / annotations_name,
        {
            "project_sha256": _sha((root / review_name).read_bytes()),
            "annotations": {"pages": {"page-000001": {"disposition": "accept", "regions": {}}}},
        },
    )


def _fixture(root: Path, *, stale_text: bool = False) -> None:
    source_root = root / "source-material/reference-transcriptions/unlambda/extracted/lmman/orig4ed"
    source_root.mkdir(parents=True)
    source = b"Hello recovered source.\n"
    variables = b""
    (source_root / "page.1").write_bytes(source)
    (source_root / "manual.vars").write_bytes(variables)
    pdf = root / "source-material/bitsavers/pdf/mit/cadr/chinual_4thEd_Jul81.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"pdf")
    slice_root = root / "work/chinual-slice"
    (slice_root / "surya/pages").mkdir(parents=True)
    (slice_root / "surya/pages/results.json").write_bytes(b"{}")
    (slice_root / "source-alignment-proposals-v7.json").write_bytes(b"{}")
    page = {
        "page_number": 1,
        "scan_sha256": _sha(b"scan"),
        "replica_sha256": _sha(b"replica"),
        "regions": [
            {
                "region_id": "block-001",
                "kind": "body",
                "bbox": [0, 0, 1, 1],
                "source_path": "page.1",
                "source_span": [1, 1],
                "text_sha256": "0" * 64 if stale_text else _sha(b"Hello recovered source."),
            }
        ],
    }
    manifest = {
        "pages": [page],
        "source_pdf_sha256": _sha(b"pdf"),
        "manual_vars_sha256": _sha(variables),
        "proposals_sha256": _sha(b"{}"),
        "layout_results_sha256": _sha(b"{}"),
        "source_files": {"page.1": _sha(source)},
    }
    mapping_page = {
        "id": "page-000001",
        "reference_asset_id": "scan",
        "generated_asset_id": "scan",
        "regions": [],
    }
    for revision in ("mapping-review-r1", "mapping-review-r2"):
        _review(
            slice_root / revision,
            "review-project.json",
            "review-project.annotations.json",
            mapping_page,
        )
    revisions = (
        ("replica-review-r25", "review-project.json", "review-project.annotations.json"),
        ("replica-review-r28", "correction-review.json", "correction-review.annotations.json"),
        ("replica-review-r30", "final-correction.json", "final-correction.annotations.json"),
        (
            "replica-review-r33",
            "final-alignment-review.json",
            "final-alignment-review.annotations.json",
        ),
        ("replica-review-r33", "final-byte-review.json", "final-byte-review.annotations.json"),
    )
    layout_page = {
        "id": "page-000001",
        "reference_asset_id": "scan",
        "generated_asset_id": "replica",
        "regions": [],
    }
    for directory, review, annotation_file in revisions:
        _json(slice_root / directory / "replica-manifest.json", manifest)
        _review(slice_root / directory, review, annotation_file, layout_page)


def test_imports_fresh_bolio_text_into_authoritative_wave1_queue(tmp_path: Path) -> None:
    _fixture(tmp_path)
    result = import_chinual_recovered_slice(tmp_path)
    assert result.records[0].disposition == "authoritative"
    assert result.records[0].regions[0].literal_text == "Hello recovered source."
    assert result.queue_pages[0].tags == ("clean-scanned-prose",)


def test_text_digest_disagreement_is_provisional_not_stored_truth(tmp_path: Path) -> None:
    _fixture(tmp_path, stale_text=True)
    result = import_chinual_recovered_slice(tmp_path)
    assert result.records[0].disposition == "provisional"
    assert result.records[0].regions[0].literal_text == "Hello recovered source."
    assert "fresh Bolio extraction" in result.evidence_gaps[0]


def test_stale_final_source_file_fails_closed(tmp_path: Path) -> None:
    _fixture(tmp_path)
    source = (
        tmp_path
        / "source-material/reference-transcriptions/unlambda/extracted/lmman/orig4ed/page.1"
    )
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(ChinualImportError, match="source file bytes"):
        import_chinual_recovered_slice(tmp_path)


def test_import_retains_the_initial_manifest_bytes_and_digest(tmp_path: Path) -> None:
    _fixture(tmp_path)
    result = import_chinual_recovered_slice(tmp_path)
    manifest = tmp_path / "work/chinual-slice/replica-review-r33/replica-manifest.json"
    initial = result.final_manifest_bytes
    manifest.write_text('{"changed": true}', encoding="utf-8")
    assert hashlib.sha256(initial).hexdigest() == result.final_manifest_sha256
