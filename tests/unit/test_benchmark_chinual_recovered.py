from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lispmdoc.benchmark.bolio_counters import derive_ti4ed_section_numbers
from lispmdoc.benchmark.chinual_recovered import (
    ChinualImportError,
    _mapping_evidence,
    diagnose_chinual_derivation_disagreements,
    import_chinual_recovered_slice,
)

_LIVE_ROOT = Path(__file__).resolve().parents[2]
_LIVE_MANIFEST = _LIVE_ROOT / "work/chinual-slice/replica-review-r33/replica-manifest.json"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _projection_entry(kind: str, semantic: str, physical: str) -> dict[str, object]:
    policy = (
        "code-leading-indent-projection-v1"
        if kind == "code"
        else "prose-layout-whitespace-projection-v1"
    )
    return {
        "page_number": 1,
        "region_id": "block-001",
        "kind": kind,
        "policy": policy,
        "semantic_sha256": _sha(semantic.encode("utf-8")),
        "physical_sha256": _sha(physical.encode("utf-8")),
    }


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
    source_selectors: list[dict[str, object]] | None = None,
    whitespace_entries: list[dict[str, object]] | None = None,
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
    _json(
        root / "config/benchmarks/chinual-source-selector-overlay.json",
        {
            "format_version": "lispmdoc-chinual-source-selector-overlay-1",
            "r33_manifest_sha256": _sha(
                (slice_root / "replica-review-r33/replica-manifest.json").read_bytes()
            ),
            "selectors": source_selectors or [],
        },
    )
    _json(
        root / "config/benchmarks/chinual-r33-whitespace-overlay.json",
        {
            "format_version": "lispmdoc-chinual-whitespace-overlay-1",
            "r33_manifest_sha256": _sha(
                (slice_root / "replica-review-r33/replica-manifest.json").read_bytes()
            ),
            "r33_review_sha256": _sha(
                (slice_root / "replica-review-r33/review-project.json").read_bytes()
            ),
            "entries": whitespace_entries or [],
        },
    )


def test_imports_fresh_bolio_text_into_authoritative_wave1_queue(tmp_path: Path) -> None:
    _fixture(tmp_path)
    result = import_chinual_recovered_slice(tmp_path)
    assert result.records[0].disposition == "authoritative"
    assert result.records[0].regions[0].literal_text == "Hello recovered source."
    assert result.queue_pages[0].tags == ("clean-scanned-prose",)


def test_text_digest_disagreement_without_a_receipt_fails_closed(tmp_path: Path) -> None:
    _fixture(tmp_path, stale_text=True)
    with pytest.raises(ChinualImportError, match="whitespace projection overlay"):
        import_chinual_recovered_slice(tmp_path)


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


def test_exact_source_selector_promotes_a_manifest_bound_fragment(tmp_path: Path) -> None:
    source = b"Hello recovered source.\n"
    selected = "Hello"
    _fixture(
        tmp_path,
        source_override=source,
        stored_text_override=selected.encode("utf-8"),
        source_selectors=[
            {
                "page_number": 1,
                "region_id": "block-001",
                "region_kind": "body",
                "source_path": "page.1",
                "source_sha256": _sha(source),
                "source_span": [1, 1],
                "selector": {"kind": "rendered-character-range", "start": 0, "end": 5},
                "selected_text_sha256": _sha(selected.encode("utf-8")),
            }
        ],
    )

    result = import_chinual_recovered_slice(tmp_path)

    assert result.records[0].disposition == "authoritative"
    assert result.records[0].regions[0].literal_text == selected
    assert [(item.page_number, item.region_id) for item in result.applied_source_selectors] == [
        (1, "block-001")
    ]


def test_selector_output_or_unused_target_fails_closed(tmp_path: Path) -> None:
    _fixture(tmp_path)
    overlay_path = tmp_path / "config/benchmarks/chinual-source-selector-overlay.json"
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    overlay["selectors"] = [
        {
            "page_number": 1,
            "region_id": "absent",
            "region_kind": "body",
            "source_path": "page.1",
            "source_sha256": _sha(b"Hello recovered source.\n"),
            "source_span": [1, 1],
            "selector": {"kind": "rendered-character-range", "start": 0, "end": 5},
            "selected_text_sha256": _sha(b"Hello"),
        }
    ]
    _json(overlay_path, overlay)
    with pytest.raises(ChinualImportError, match="targets absent from final manifest"):
        import_chinual_recovered_slice(tmp_path)


def test_selector_rejects_line_break_projection_for_code_region(tmp_path: Path) -> None:
    source = b".lisp\nHello\nsource\n.end_lisp\n"
    _fixture(
        tmp_path,
        kind="code",
        source_override=source,
        source_span=(1, 4),
        stored_text_override=b"Hello source",
        source_selectors=[
            {
                "page_number": 1,
                "region_id": "block-001",
                "region_kind": "code",
                "source_path": "page.1",
                "source_sha256": _sha(source),
                "source_span": [1, 4],
                "selector": {
                    "kind": "rendered-character-range",
                    "start": 0,
                    "end": 12,
                    "projection": "line-breaks-to-spaces",
                },
                "selected_text_sha256": _sha(b"Hello source"),
            }
        ],
    )

    with pytest.raises(ChinualImportError, match="cannot select exact source component"):
        import_chinual_recovered_slice(tmp_path)


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
    assert len(result.authoritative_pages) == 20
    assert len(result.applied_whitespace_receipts) == 20


def test_valid_whitespace_receipt_promotes_semantic_text_without_rewriting_it(
    tmp_path: Path,
) -> None:
    semantic = "Hello recovered source."
    physical = "Hello   recovered source."
    _fixture(
        tmp_path,
        stored_text_override=physical.encode("utf-8"),
        whitespace_entries=[_projection_entry("body", semantic, physical)],
    )
    imported = import_chinual_recovered_slice(tmp_path)
    assert imported.records[0].disposition == "authoritative"
    assert imported.records[0].regions[0].literal_text == semantic
    assert len(imported.applied_whitespace_receipts) == 1
    report = diagnose_chinual_derivation_disagreements(tmp_path)
    assert report["mismatches"] == []
    assert report["summary"]["resolved_projection_count"] == 1
    assert report["resolved_projections"][0]["physical_r33_text"] == physical


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


def test_whitespace_overlay_rejects_extra_or_mutated_receipts(tmp_path: Path) -> None:
    semantic = "Hello recovered source."
    physical = "Hello   recovered source."
    _fixture(
        tmp_path,
        stored_text_override=physical.encode("utf-8"),
        whitespace_entries=[_projection_entry("body", semantic, physical)],
    )
    overlay_path = tmp_path / "config/benchmarks/chinual-r33-whitespace-overlay.json"
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    overlay["entries"].append({**overlay["entries"][0], "region_id": "extra"})
    _json(overlay_path, overlay)
    with pytest.raises(ChinualImportError, match="whitespace projection overlay"):
        import_chinual_recovered_slice(tmp_path)


def test_whitespace_overlay_rejects_mutated_semantic_digest(tmp_path: Path) -> None:
    semantic = "Hello recovered source."
    physical = "Hello   recovered source."
    _fixture(
        tmp_path,
        stored_text_override=physical.encode("utf-8"),
        whitespace_entries=[_projection_entry("body", semantic, physical)],
    )
    overlay_path = tmp_path / "config/benchmarks/chinual-r33-whitespace-overlay.json"
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    overlay["entries"][0]["semantic_sha256"] = "0" * 64
    _json(overlay_path, overlay)
    with pytest.raises(ChinualImportError, match="whitespace projection overlay"):
        import_chinual_recovered_slice(tmp_path)


def test_code_projection_rejects_internal_whitespace_change(tmp_path: Path) -> None:
    semantic = "  (hello world)\n"
    physical = "\t(hello  world)\n"
    _fixture(
        tmp_path,
        kind="code",
        source_override=b".lisp\n  (hello world)\n.end_lisp\n",
        source_span=(1, 3),
        stored_text_override=physical.encode("utf-8"),
        whitespace_entries=[_projection_entry("code", semantic, physical)],
    )
    with pytest.raises(ChinualImportError, match="whitespace projection overlay"):
        import_chinual_recovered_slice(tmp_path)
