from __future__ import annotations

import pytest

from lispmdoc.semantics import (
    PhysicalRegion,
    SemanticInferer,
    SemanticTruth,
    exact_code_fidelity,
    semantic_macro_f1,
)


def _regions() -> tuple[PhysicalRegion, ...]:
    return (
        PhysicalRegion("head-1", 1, 0, "CHAPTER 1"),
        PhysicalRegion("prose-1", 1, 1, "The first diplomatic line."),
        PhysicalRegion("prose-2", 1, 2, "The second diplomatic line."),
        PhysicalRegion("code-1", 1, 3, "  (DEFUN FOO (X))", "code"),
        PhysicalRegion("list-1", 1, 4, "1. Preserve punctuation."),
        PhysicalRegion("math-1", 1, 5, "x = y + 1"),
        PhysicalRegion("run-1", 1, 9, "LISP MACHINE MANUAL"),
        PhysicalRegion("run-2", 2, 9, "LISP MACHINE MANUAL"),
        PhysicalRegion("table-1", 2, 1, "a | b | c"),
        PhysicalRegion("caption-1", 2, 2, "Figure 1. Example"),
        PhysicalRegion("xref-1", 2, 3, "See Chapter 1"),
        PhysicalRegion("index-1", 2, 4, "FOO, 12, 15"),
        PhysicalRegion("terminal-1", 2, 5, "$ ls -l"),
    )


def test_conservative_inference_links_physical_evidence_and_keeps_code_exact() -> None:
    regions = _regions()
    result = SemanticInferer().infer(regions)
    kinds = {proposal.kind for proposal in result.proposals}
    assert {
        "chapter",
        "paragraph",
        "code",
        "list-item",
        "math",
        "running-matter",
        "table",
        "caption",
        "cross-reference",
        "index",
        "terminal",
    } <= kinds
    assert exact_code_fidelity(regions, result.proposals)
    assert all(proposal.physical_region_ids for proposal in result.proposals)
    assert any(finding.code == "MATH_REVIEW" for finding in result.findings)
    math = next(proposal for proposal in result.proposals if proposal.kind == "math")
    assert set(math.fallback) == {"diplomatic", "visual"}


def test_semantic_metrics_are_explicit_and_review_fixture_can_meet_gate() -> None:
    regions = (PhysicalRegion("r1", 1, 0, "CHAPTER 1"), PhysicalRegion("r2", 1, 1, "Text."))
    result = SemanticInferer().infer(regions)
    report = semantic_macro_f1(
        (SemanticTruth("r1", "chapter"), SemanticTruth("r2", "paragraph")), result.proposals
    )
    assert report.macro_f1 == pytest.approx(1.0)


def test_ambiguity_is_a_finding_and_missing_reference_is_rejected() -> None:
    regions = (PhysicalRegion("heading", 1, 0, "APPENDIX"),)
    result = SemanticInferer().infer(regions)
    assert any(finding.code == "AMBIGUOUS_SEMANTICS" for finding in result.findings)
    broken = result.proposals[0]
    with pytest.raises(ValueError, match="missing"):
        result.assert_references((PhysicalRegion("other", 1, 0, "other"),))
    assert broken.physical_region_ids == ("heading",)
