from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).resolve().parents[2] / "scripts/evaluate-chinual-r5"
    loader = importlib.machinery.SourceFileLoader("chinual_r5_evaluation_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def _identity(path: Path) -> dict[str, object]:
    return {
        "byte_size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _receipt_fixture(root: Path) -> Path:
    run = root / "work/chinual-slice/ocr-rerun-r5"
    names = (
        "run.json",
        "provenance/plan.json",
        "provenance/preflight.json",
        "provenance/evidence-seal.json",
        "provenance/raw-output-inventory.json",
    )
    for number, name in enumerate(names):
        path = run / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"evidence-{number}", encoding="utf-8")
    receipt = {
        "format_version": "lispmdoc-chinual-r5-receipt-1",
        "run_directory": "work/chinual-slice/ocr-rerun-r5",
        "roots": {name: _identity(run / name) for name in names},
    }
    path = root / "config/benchmarks/chinual-r5-receipt.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return run


def test_paddle_requires_matched_text_and_geometry_inventory() -> None:
    evaluator = _module()
    with pytest.raises(evaluator.EvaluationError, match="inventories differ"):
        evaluator._paddle_lines({"res": {"rec_texts": ["one"], "dt_polys": []}}, 91)


def test_surya_requires_exactly_one_page_result() -> None:
    evaluator = _module()
    with pytest.raises(evaluator.EvaluationError, match="exactly one result"):
        evaluator._surya_lines({"p000091": []}, 91)


def test_sealed_identity_rejects_changed_bytes(tmp_path: Path) -> None:
    evaluator = _module()
    evidence = tmp_path / "evidence.json"
    evidence.write_text("first", encoding="utf-8")
    identity = {"byte_size": 5, "sha256": "0" * 64}
    with pytest.raises(evaluator.EvaluationError, match="sealed identity"):
        evaluator._identity(evidence, identity, "test")


def test_external_receipt_rejects_joint_raw_and_inventory_mutation(tmp_path: Path) -> None:
    evaluator = _module()
    run = _receipt_fixture(tmp_path)
    raw = run / "paddleocr/raw/p000091.json"
    raw.parent.mkdir(parents=True)
    raw.write_text("changed raw", encoding="utf-8")
    inventory = run / "provenance/raw-output-inventory.json"
    inventory.write_text("changed inventory for changed raw", encoding="utf-8")
    with pytest.raises(evaluator.EvaluationError, match="receipt root"):
        evaluator._verify_external_receipt(tmp_path, run)


def test_external_receipt_rejects_joint_plan_and_seal_mutation(tmp_path: Path) -> None:
    evaluator = _module()
    run = _receipt_fixture(tmp_path)
    (run / "provenance/plan.json").write_text("changed plan", encoding="utf-8")
    (run / "provenance/evidence-seal.json").write_text("changed seal", encoding="utf-8")
    with pytest.raises(evaluator.EvaluationError, match="receipt root"):
        evaluator._verify_external_receipt(tmp_path, run)


def test_contained_rejects_in_root_and_escaping_symlinks(tmp_path: Path) -> None:
    evaluator = _module()
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "inside.json"
    inside.write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    (root / "in-root-link").symlink_to(inside)
    (root / "escaping-link").symlink_to(outside)
    for name in ("in-root-link", "escaping-link"):
        with pytest.raises(evaluator.EvaluationError, match="symlink"):
            evaluator._contained(root, name, "test")


def test_identity_read_rejects_bytes_changed_before_capture(tmp_path: Path) -> None:
    evaluator = _module()
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"version": 1}', encoding="utf-8")
    identity = _identity(manifest)
    manifest.write_text('{"version": 2}', encoding="utf-8")
    with pytest.raises(evaluator.EvaluationError, match="sealed identity"):
        evaluator._read_identity(manifest, identity, "manifest")


def test_verified_raw_buffer_survives_mutation_before_consumption(tmp_path: Path) -> None:
    evaluator = _module()
    raw = tmp_path / "raw.json"
    raw.write_text('{"res": {}}', encoding="utf-8")
    buffered = evaluator._read_identity(raw, _identity(raw), "raw")
    raw.write_text('{"changed": true}', encoding="utf-8")
    assert evaluator._json_bytes(buffered, "raw") == {"res": {}}


def test_surya_html_preserves_br_and_block_line_boundaries() -> None:
    evaluator = _module()
    assert evaluator._html_text("<div>one<br>two</div><p>three</p>") == "one\ntwo\nthree\n"


def test_code_predictions_preserve_indent_line_boundaries_and_unassigned_lines() -> None:
    evaluator = _module()
    from lispmdoc.benchmark.chinual_recovered import ChinualPageRecord, ChinualRegionRecord
    from lispmdoc.benchmark.wave1 import QueuePage

    region = ChinualRegionRecord(
        "block-001",
        "code",
        "  (one)\n\t(two)",
        "source",
        1,
        2,
        "a" * 64,
        "a" * 64,
        "authoritative",
        False,
    )
    record = ChinualPageRecord(
        91,
        QueuePage("a" * 64, 90, "b" * 64, "recovered", ("code-terminal",), ("block-001",)),
        "authoritative",
        (region,),
        (),
    )
    truth, predictions, matched, unassigned = evaluator._score_page(
        record,
        {"regions": [{"region_id": "block-001", "bbox": [0, 0, 10, 10]}]},
        (
            ("\n  (one)\n", (1, 1, 4, 2)),
            ("\t(two)\n", (1, 3, 4, 4)),
            ("header", (20, 1, 25, 2)),
        ),
    )
    assert truth[0].kind == "code"
    assert predictions["p000091/block-001"] == "  (one)\n\t(two)"
    assert matched == 1
    assert unassigned == [{"bbox": [20, 1, 25, 2], "text": "header"}]


def test_prose_predictions_keep_historical_whitespace_treatment() -> None:
    evaluator = _module()
    from lispmdoc.benchmark.chinual_recovered import ChinualPageRecord, ChinualRegionRecord
    from lispmdoc.benchmark.wave1 import QueuePage

    region = ChinualRegionRecord(
        "block-001",
        "body",
        "one two",
        "source",
        1,
        1,
        "a" * 64,
        "a" * 64,
        "authoritative",
        False,
    )
    record = ChinualPageRecord(
        91,
        QueuePage("a" * 64, 90, "b" * 64, "recovered", ("clean-scanned-prose",), ("block-001",)),
        "authoritative",
        (region,),
        (),
    )
    _, predictions, _, _ = evaluator._score_page(
        record,
        {"regions": [{"region_id": "block-001", "bbox": [0, 0, 10, 10]}]},
        (("  one  ", (1, 1, 4, 2)), ("\ttwo\n", (1, 3, 4, 4))),
    )

    assert predictions["p000091/block-001"] == "one two"
