from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lispmdoc.benchmark.bolio_counters import derive_ti4ed_section_numbers
from lispmdoc.benchmark.chinual_recovered import (
    _DERIVATION_CLASSIFICATION,
    ChinualImportError,
    _mapping_evidence,
    diagnose_chinual_derivation_disagreements,
    import_chinual_recovered_slice,
)

_LIVE_ROOT = Path(__file__).resolve().parents[2]
_LIVE_MANIFEST = _LIVE_ROOT / "work/chinual-slice/replica-review-r33/replica-manifest.json"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _review(root: Path, review_name: str, annotations_name: str, page: dict[str, object]) -> None:
    asset = b"scan" if root.name.startswith("mapping-review-") else b"replica"
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


def _fixture(
    root: Path,
    *,
    stale_text: bool = False,
    source_override: bytes | None = None,
    source_span: tuple[int, int] = (1, 1),
    kind: str = "body",
    stored_text_override: bytes | None = None,
    review_source_override: str | None = None,
    scan_ocr_override: str | None = None,
    mapping_text: str | None = None,
) -> None:
    source_root = root / "source-material/reference-transcriptions/unlambda/extracted/lmman/orig4ed"
    source_root.mkdir(parents=True)
    source = source_override or b"Hello recovered source.\n"
    variables = b"(DEFPROP FIXTURE-CHAPTER /1 JUST-VALUE)\n"
    (source_root / "page.1").write_bytes(source)
    (source_root / "manual.vars").write_bytes(variables)
    counter_source = b".chapter Fixture\n.setq fixture-chapter chapter-number\n"
    (source_root / "fd_num.77").write_bytes(counter_source)
    (source_root.parent / "ti-4ed.sh").write_text(
        "(while read f; do\ndone)<<EOF\nfd_num.77\nEOF\n", encoding="utf-8"
    )
    counters = derive_ti4ed_section_numbers(source_root.parent, "fd_num.77")
    _json(
        root / "config/benchmarks/chinual-ti4ed-counter-receipt.json",
        {
            "format_version": "lispmdoc-ti4ed-counter-receipt-1",
            "manual_vars_sha256": counters.manual_vars_sha256,
            "order_sha256": counters.order_sha256,
            "proof_count": counters.proof_count,
            "proof_inventory_sha256": counters.proof_inventory_sha256,
            "sources": [
                {"order_index": item.order_index, "path": item.path, "sha256": item.sha256}
                for item in counters.sources
            ],
            "through": "fd_num.77",
            "ti_script_sha256": counters.ti_script_sha256,
        },
    )
    pdf = root / "source-material/bitsavers/pdf/mit/cadr/chinual_4thEd_Jul81.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"pdf")
    slice_root = root / "work/chinual-slice"
    (slice_root / "surya/pages").mkdir(parents=True)
    (slice_root / "surya/pages/results.json").write_bytes(b"{}")
    (slice_root / "source-alignment-proposals-v7.json").write_bytes(b"{}")
    stored_text = stored_text_override or (
        b"Stored review text." if stale_text else b"Hello recovered source."
    )
    page = {
        "page_number": 1,
        "scan_sha256": _sha(b"scan"),
        "replica_sha256": _sha(b"replica"),
        "regions": [
            {
                "region_id": "block-001",
                "kind": kind,
                "bbox": [0, 0, 1, 1],
                "source_path": "page.1",
                "source_span": list(source_span),
                "text_sha256": _sha(stored_text),
            }
        ],
    }
    manifest = {
        "pages": [page],
        "source_pdf_sha256": _sha(b"pdf"),
        "manual_vars_sha256": _sha(variables),
        "proposals_sha256": _sha(b"{}"),
        "layout_results_sha256": _sha(b"{}"),
        "source_files": {"fd_num.77": _sha(counter_source), "page.1": _sha(source)},
    }
    mapping_page = {
        "id": "page-000001",
        "reference_asset_id": "scan",
        "generated_asset_id": "scan",
        "regions": (
            [] if mapping_text is None else [{"id": "mapping-01", "canonical_text": mapping_text}]
        ),
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
    final_review_page = {
        **layout_page,
        "regions": [
            {
                "id": "block-001",
                "canonical_text": stored_text.decode("utf-8"),
                "source_text": review_source_override or stored_text.decode("utf-8"),
                "ocr_text": scan_ocr_override or stored_text.decode("utf-8"),
            }
        ],
    }
    _review(
        slice_root / "replica-review-r33",
        "review-project.json",
        "review-project.annotations.json",
        final_review_page,
    )


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


def test_import_rejects_final_review_text_not_bound_to_manifest_digest(tmp_path: Path) -> None:
    _fixture(tmp_path)
    review = tmp_path / "work/chinual-slice/replica-review-r33/review-project.json"
    value = json.loads(review.read_text(encoding="utf-8"))
    value["pages"][0]["regions"][0]["canonical_text"] = "unbound text"
    _json(review, value)
    with pytest.raises(ChinualImportError, match="review text does not match manifest"):
        import_chinual_recovered_slice(tmp_path)


def test_import_rejects_ti4ed_counter_root_drift(tmp_path: Path) -> None:
    _fixture(tmp_path)
    script = (
        tmp_path / "source-material/reference-transcriptions/unlambda/extracted/lmman/ti-4ed.sh"
    )
    script.write_text(
        script.read_text(encoding="utf-8") + "# receipt root drift\n",
        encoding="utf-8",
    )
    with pytest.raises(ChinualImportError, match="cannot derive digest-bound ti-4ed"):
        import_chinual_recovered_slice(tmp_path)


@pytest.mark.parametrize("source_name", ("page.1", "fd_num.77"))
def test_import_consumes_initial_source_buffer_after_mutation_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_name: str
) -> None:
    _fixture(tmp_path)
    source = (
        tmp_path
        / f"source-material/reference-transcriptions/unlambda/extracted/lmman/orig4ed/{source_name}"
    )
    initial = source.read_bytes()
    original_read_bytes = Path.read_bytes
    mutated = False

    def read_once_then_mutate(path: Path) -> bytes:
        nonlocal mutated
        value = original_read_bytes(path)
        if path == source and not mutated:
            mutated = True
            source.write_bytes(b"changed after verified buffer read\n")
        return value

    monkeypatch.setattr(Path, "read_bytes", read_once_then_mutate)
    result = import_chinual_recovered_slice(tmp_path)

    assert mutated
    assert source.read_bytes() != initial
    assert result.records[0].regions[0].literal_text == "Hello recovered source."


@pytest.mark.skipif(
    not _LIVE_MANIFEST.is_file(),
    reason="ignored Chinual corpus is unavailable in this checkout",
)
def test_live_chinual_counter_applies_the_twelve_resolved_heading_ids() -> None:
    result = import_chinual_recovered_slice(_LIVE_ROOT)
    expected = {
        (91, "block-004"),
        (93, "block-013"),
        (98, "block-001"),
        (98, "block-002"),
        (99, "block-007"),
        (101, "block-001"),
        (101, "block-014"),
        (102, "block-001"),
        (104, "block-001"),
        (106, "block-003"),
        (107, "block-003"),
        (108, "block-002"),
    }
    proof_locations = {(proof.source_path, proof.line) for proof in result.applied_section_proofs}
    observed = {
        (record.page_number, region.region_id)
        for record in result.records
        for region in record.regions
        if (region.source_path, region.start_line) in proof_locations
    }

    assert observed == expected
    assert len(result.applied_section_proofs) == 12
    assert len(result.authoritative_pages) == 11


def test_derivation_diagnosis_labels_asymmetric_text_witnesses_unbound(tmp_path: Path) -> None:
    _fixture(
        tmp_path,
        stale_text=True,
        review_source_override="independent review source witness",
    )
    report = diagnose_chinual_derivation_disagreements(tmp_path)
    mismatch = report["mismatches"][0]
    assert report["summary"]["stored_digest_bound_count"] == 1
    assert mismatch["category"] == "unresolved"
    assert mismatch["stored_r33_text"] == "Stored review text."
    assert mismatch["unbound_review_source_text_witness"] == "independent review source witness"
    assert mismatch["unbound_scan_ocr_text_witness"] == "Stored review text."
    assert mismatch["witness_binding"]["source_text"].startswith("unbound")
    assert mismatch["fresh_bolio_interval"] == "Hello recovered source."


def test_mapping_report_does_not_promote_page_wide_substring_to_region_binding(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path, stale_text=True, mapping_text="Hello recovered source.")
    report = diagnose_chinual_derivation_disagreements(tmp_path)
    mapping = report["mismatches"][0]["mapping_revisions"]
    assert mapping["r1"]["page"]["accepted"] is True
    assert mapping["r1"]["exact_region"] == {
        "accepted": False,
        "disposition": "absent",
        "present": False,
    }
    assert "fresh_interval_occurs" not in mapping["r1"]


@pytest.mark.parametrize("disposition", (None, "reject"))
def test_mapping_report_marks_missing_or_rejected_page_annotation_not_accepted(
    tmp_path: Path, disposition: str | None
) -> None:
    _fixture(tmp_path, stale_text=True)
    annotations_path = (
        tmp_path / "work/chinual-slice/mapping-review-r1/review-project.annotations.json"
    )
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    pages = annotations["annotations"]["pages"]
    if disposition is None:
        pages.clear()
    else:
        pages["page-000001"]["disposition"] = disposition
    _json(annotations_path, annotations)
    report = diagnose_chinual_derivation_disagreements(tmp_path)
    page = report["mismatches"][0]["mapping_revisions"]["r1"]["page"]
    assert page["accepted"] is False
    assert page["disposition"] == ("absent" if disposition is None else "reject")


def test_mapping_annotation_mutation_after_import_is_rejected(tmp_path: Path) -> None:
    _fixture(tmp_path)
    imported = import_chinual_recovered_slice(tmp_path)
    annotations_path = (
        tmp_path / "work/chinual-slice/mapping-review-r1/review-project.annotations.json"
    )
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    annotations["annotations"]["pages"]["page-000001"]["disposition"] = "reject"
    _json(annotations_path, annotations)
    with pytest.raises(ChinualImportError, match="changed after recovered import"):
        _mapping_evidence(tmp_path, imported, 1, "block-001")


def test_existing_ledger_key_becomes_unresolved_when_scan_witness_drifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixture(
        tmp_path,
        source_override=b".lisp\nHello recovered source.\n.end_lisp\n",
        source_span=(1, 3),
        kind="code",
        stored_text_override=b"Hello   recovered source.",
        scan_ocr_override="Hello recovered source.",
    )
    monkeypatch.setitem(
        _DERIVATION_CLASSIFICATION,
        (1, "block-001"),
        ("layout-whitespace-normalization", "test layout witness"),
    )
    assert diagnose_chinual_derivation_disagreements(tmp_path)["mismatches"][0]["category"] == (
        "layout-whitespace-normalization"
    )
    review_path = tmp_path / "work/chinual-slice/replica-review-r33/review-project.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["pages"][0]["regions"][0]["ocr_text"] = "unrelated witness"
    _json(review_path, review)
    mismatch = diagnose_chinual_derivation_disagreements(tmp_path)["mismatches"][0]
    assert mismatch["category"] == "unresolved"
    assert (
        mismatch["classification_predicates"]["scan_canonical_token_similarity_at_least_0_90"]
        is False
    )


def test_existing_span_ledger_key_rejects_an_arbitrary_interior_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixture(tmp_path, stored_text_override=b"recovered")
    monkeypatch.setitem(
        _DERIVATION_CLASSIFICATION,
        (1, "block-001"),
        ("source-span-not-exact", "test fragment witness"),
    )
    mismatch = diagnose_chinual_derivation_disagreements(tmp_path)["mismatches"][0]
    assert mismatch["category"] == "unresolved"
    assert mismatch["classification_predicates"]["proper_prefix_or_suffix"] is False


def test_existing_layout_ledger_key_rejects_arbitrary_body_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixture(
        tmp_path,
        stored_text_override=b"Hello   recovered source.",
        scan_ocr_override="Hello recovered source.",
    )
    monkeypatch.setitem(
        _DERIVATION_CLASSIFICATION,
        (1, "block-001"),
        ("layout-whitespace-normalization", "test layout witness"),
    )
    mismatch = diagnose_chinual_derivation_disagreements(tmp_path)["mismatches"][0]
    assert mismatch["category"] == "unresolved"
    assert mismatch["classification_predicates"]["source_has_structural_layout_signal"] is False
