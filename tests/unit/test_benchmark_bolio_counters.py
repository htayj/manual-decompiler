from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from lispmdoc.benchmark.bolio_counters import (
    BolioCounterError,
    derive_ti4ed_section_numbers,
    ti4ed_ordered_prefix,
    verify_ti4ed_counter_receipt,
)


def _write(root: Path, name: str, text: str) -> None:
    path = root / "orig4ed" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture(root: Path, *, order: tuple[str, ...] = ("first.1", "second.2")) -> None:
    (root / "orig4ed").mkdir(parents=True, exist_ok=True)
    (root / "orig4ed/manual.vars").write_text(
        "(DEFPROP FIRST-CHAPTER /5 JUST-VALUE)\n(DEFPROP SECOND-CHAPTER /6 JUST-VALUE)\n",
        encoding="utf-8",
    )
    _write(
        root,
        "first.1",
        ".chapter First\n.setq first-chapter chapter-number\n"
        ".section One\n.subsection One A\n.subsection One B\n.section Two\n",
    )
    _write(
        root,
        "second.2",
        ".chapter Second\n.setq second-chapter chapter-number\n.section Three\n",
    )
    (root / "ti-4ed.sh").write_text(
        "(while read f; do\ndone)<<EOF\n" + "\n".join(order) + "\nEOF\n",
        encoding="utf-8",
    )


def test_derives_chapter_section_and_subsection_proofs_from_source_only(tmp_path: Path) -> None:
    _fixture(tmp_path)

    result = derive_ti4ed_section_numbers(tmp_path, "second.2")

    assert [
        (proof.source_path, proof.line, proof.number, proof.title) for proof in result.proofs
    ] == [
        ("first.1", 1, "5.", "First"),
        ("first.1", 3, "5.1", "One"),
        ("first.1", 4, "5.1.1", "One A"),
        ("first.1", 5, "5.1.2", "One B"),
        ("first.1", 6, "5.2", "Two"),
        ("second.2", 1, "6.", "Second"),
        ("second.2", 3, "6.1", "Three"),
    ]
    proof = result.proof_for("second.2", 3)
    assert proof is not None
    assert proof.matches(source_path="second.2", source_sha256=proof.source_sha256, line=3)
    assert not proof.matches(source_path="second.2", source_sha256="0" * 64, line=3)
    assert proof.order_sha256 == result.order_sha256
    assert proof.manual_vars_sha256 == result.manual_vars_sha256
    assert (
        result.manual_vars_sha256
        == hashlib.sha256((tmp_path / "orig4ed/manual.vars").read_bytes()).hexdigest()
    )


def test_fails_closed_on_missing_or_conflicting_chapter_anchors(tmp_path: Path) -> None:
    _fixture(tmp_path)
    _write(tmp_path, "second.2", ".chapter Second\n.section Three\n")
    with pytest.raises(BolioCounterError, match="lacks a chapter-number anchor"):
        derive_ti4ed_section_numbers(tmp_path, "second.2")

    _fixture(tmp_path)
    _write(
        tmp_path,
        "first.1",
        ".chapter First\n.setq first-chapter chapter-number\n"
        ".setq second-chapter chapter-number\n.section One\n",
    )
    with pytest.raises(BolioCounterError, match="conflicting chapter-number anchors"):
        derive_ti4ed_section_numbers(tmp_path, "second.2")


def test_fails_closed_on_non_increasing_chapter_order(tmp_path: Path) -> None:
    _fixture(tmp_path)
    _write(
        tmp_path,
        "second.2",
        ".chapter Second\n.setq first-chapter chapter-number\n.section Three\n",
    )
    with pytest.raises(BolioCounterError, match="non-increasing"):
        derive_ti4ed_section_numbers(tmp_path, "second.2")


@pytest.mark.parametrize(
    ("manual_value", "message"),
    (
        (None, "absent from manual.vars"),
        ("/5", "malformed manual.vars value"),
        ("|section 5.2, page 1|", "conflicts with derived section 5.1"),
    ),
)
def test_section_page_anchors_must_match_manual_vars_and_structural_counter(
    tmp_path: Path, manual_value: str | None, message: str
) -> None:
    _fixture(tmp_path)
    _write(
        tmp_path,
        "first.1",
        ".chapter First\n.setq first-chapter chapter-number\n"
        ".section One\n.setq first-section section-page\n",
    )
    manual = "(DEFPROP FIRST-CHAPTER /5 JUST-VALUE)\n(DEFPROP SECOND-CHAPTER /6 JUST-VALUE)\n"
    if manual_value is not None:
        manual += f"(DEFPROP FIRST-SECTION {manual_value} JUST-VALUE)\n"
    (tmp_path / "orig4ed/manual.vars").write_text(manual, encoding="utf-8")

    with pytest.raises(BolioCounterError, match=message):
        derive_ti4ed_section_numbers(tmp_path, "second.2")


def test_refuses_ambiguous_glob_expansion_and_hash_drift(tmp_path: Path) -> None:
    _fixture(tmp_path, order=("first.*", "second.2"))
    _write(tmp_path, "first.3", ".chapter Duplicate\n.setq first-chapter chapter-number\n")
    with pytest.raises(BolioCounterError, match="not an unambiguous single source"):
        ti4ed_ordered_prefix(tmp_path, "second.2")

    _fixture(tmp_path)
    result = derive_ti4ed_section_numbers(tmp_path, "second.2")
    proof = result.proof_for("first.1", 3)
    assert proof is not None
    _write(
        tmp_path,
        "first.1",
        ".chapter First\n.setq first-chapter chapter-number\n.section Changed\n",
    )
    assert (
        proof.source_sha256
        != hashlib.sha256((tmp_path / "orig4ed/first.1").read_bytes()).hexdigest()
    )


def test_real_ti4ed_full_order_is_refused_after_the_safe_prefix() -> None:
    root = Path("source-material/reference-transcriptions/unlambda/extracted/lmman")
    with pytest.raises(BolioCounterError, match="not an unambiguous single source"):
        ti4ed_ordered_prefix(root, "fd_hac.90")


def test_counter_receipt_binds_script_variables_and_order(tmp_path: Path) -> None:
    _fixture(tmp_path)
    result = derive_ti4ed_section_numbers(tmp_path, "second.2")
    receipt = {
        "format_version": "lispmdoc-ti4ed-counter-receipt-1",
        "manual_vars_sha256": result.manual_vars_sha256,
        "order_sha256": result.order_sha256,
        "proof_count": result.proof_count,
        "proof_inventory_sha256": result.proof_inventory_sha256,
        "sources": [
            {"order_index": item.order_index, "path": item.path, "sha256": item.sha256}
            for item in result.sources
        ],
        "through": "second.2",
        "ti_script_sha256": result.ti_script_sha256,
    }
    verify_ti4ed_counter_receipt(result, receipt)
    receipt["proof_inventory_sha256"] = "0" * 64
    with pytest.raises(BolioCounterError, match="does not bind current roots"):
        verify_ti4ed_counter_receipt(result, receipt)
    receipt["proof_inventory_sha256"] = result.proof_inventory_sha256
    receipt["proof_count"] = 0
    with pytest.raises(BolioCounterError, match="does not bind current roots"):
        verify_ti4ed_counter_receipt(result, receipt)
    receipt["proof_count"] = result.proof_count
    receipt["order_sha256"] = "0" * 64
    with pytest.raises(BolioCounterError, match="does not bind current roots"):
        verify_ti4ed_counter_receipt(result, receipt)
