from __future__ import annotations

from pathlib import Path

import pytest

from lispmdoc.review import (
    REVIEW_OPERATION_STATUS,
    PageApproval,
    ReviewArtifacts,
    ReviewFinding,
    ReviewPage,
    ReviewProject,
    patch_set_sha256,
)


def _page(page_id: str, marker: str) -> ReviewPage:
    return ReviewPage(
        page_id,
        ReviewArtifacts(
            marker * 64,
            "b" * 64,
            "c" * 64,
            "d" * 64,
            source_view_sha256="e" * 64,
            reconstruction_view_sha256="f" * 64,
            semantic_view_sha256="1" * 64,
            diff_overlay_sha256="2" * 64,
            reading_graph_sha256="3" * 64,
            engine_alternatives_sha256="4" * 64,
            raster_vector_decisions_sha256="5" * 64,
        ),
        "f" * 64,
    )


def _approval(page: ReviewPage, reviewer: str) -> PageApproval:
    artifacts = page.artifacts
    return PageApproval(
        page.page_id,
        reviewer,
        artifacts.source_sha256,
        artifacts.page_evidence_sha256,
        artifacts.ir_sha256,
        artifacts.render_sha256,
        page.patch_set_sha256,
    )


def test_project_export_and_approvals_are_deterministic_and_digest_bound() -> None:
    pages = tuple(_page(f"page-{index}", "a") for index in range(1, 11))
    project = ReviewProject(
        "document-1",
        tuple(reversed(pages)),
        manifest_page_ids=tuple(page.page_id for page in pages),
    )
    assert project.canonical_export() == project.canonical_export()
    assert len(project.required_second_pages()) == 1
    first = pages[0]
    approvals = tuple(_approval(page, "reviewer-a") for page in pages)
    status = project.approval_status(approvals)
    assert status[first.page_id] in {"approved", "missing-independent-second-approval"}
    required = project.required_second_pages()[0]
    second = next(page for page in pages if page.page_id == required)
    completed = approvals + (_approval(second, "reviewer-b"),)
    assert project.promotion_ready(completed)
    stale = PageApproval(first.page_id, "stale", "0" * 64, "b" * 64, "c" * 64, "d" * 64, "f" * 64)
    assert project.approval_status(completed + (stale,))[first.page_id] == status[first.page_id]


def test_severe_findings_block_promotion_and_require_second_review() -> None:
    page = _page("page-1", "a")
    project = ReviewProject(
        "document-1",
        (page,),
        (ReviewFinding("f", page.page_id, "OCR", "severe"),),
        (page.page_id,),
    )
    approvals = (_approval(page, "reviewer-a"), _approval(page, "reviewer-b"))
    assert project.severe_queue()
    assert not project.promotion_ready(approvals)
    resolved = ReviewProject(
        "document-1",
        (page,),
        (ReviewFinding("f", page.page_id, "OCR", "severe", True),),
        (page.page_id,),
    )
    assert resolved.promotion_ready(approvals)


def test_project_rejects_subset_of_manifest_pages_and_incomplete_artifacts() -> None:
    page = _page("page-1", "a")
    with pytest.raises(ValueError, match="exactly match"):
        ReviewProject("document-1", (page,), manifest_page_ids=("page-1", "page-2"))
    incomplete = ReviewPage(
        page.page_id,
        ReviewArtifacts("a" * 64, "b" * 64, "c" * 64, "d" * 64),
        "f" * 64,
    )
    project = ReviewProject("document-1", (incomplete,), manifest_page_ids=(incomplete.page_id,))
    assert not project.promotion_ready((_approval(incomplete, "reviewer"),))


def test_operation_registry_is_honest_and_static_shell_is_read_only() -> None:
    assert REVIEW_OPERATION_STATUS["replace-text"] == "supported"
    assert REVIEW_OPERATION_STATUS["split-region"].startswith("unsupported")
    shell = Path(__file__).resolve().parents[2] / "src/lispmdoc/review/static/review.js"
    assert "READ_ONLY" in shell.read_text(encoding="utf-8")
    assert patch_set_sha256(()) == patch_set_sha256(())
