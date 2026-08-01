from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from lispmdoc.model import (
    AffineTransform,
    Box,
    Manifest,
    PageRecord,
    PageReference,
    SourceRecord,
    StructureNode,
    StructureRecord,
    StylesRecord,
    content_id,
)
from lispmdoc.package import pack_directory, write_authoring_tree
from lispmdoc.review import PageApproval, ReviewArtifacts, ReviewPage, ReviewProject
from lispmdoc.validate.replica import (
    AccessibilityEvidence,
    LayoutEvidence,
    PolicyEvidence,
    ReplicaAttestationInputs,
    ReplicaEvidence,
    ReproducibilityEvidence,
    SizeEvidence,
    TextAudit,
    VisualEvidence,
    accessibility_structure_evidence,
    attest_replica,
    validate_replica,
)


def _evidence(**changes: object) -> ReplicaEvidence:
    values: dict[str, object] = {
        "package_sha256": "a" * 64,
        "source_sha256": "b" * 64,
        "benchmark_sha256": "c" * 64,
        "renderer_sha256": "d" * 64,
        "review_set_sha256": "e" * 64,
        "structural_valid": True,
        "source_identity_exact": True,
        "benchmark_passes_all_page_classes": True,
        "high_risk_and_omissions_resolved": True,
        "every_page_approved": True,
        "text": TextAudit(200_000, 10_000, 0, True, True, True),
        "layout": LayoutEvidence(True, 0, 0, True),
        "visual": (VisualEvidence("born-digital", 0.999, 1.0, 1.0, None, 0),),
        "accessibility": AccessibilityEvidence(True, True, 0),
        "policy": PolicyEvidence(0, 0, 0, True, True, True, True),
        "reproducibility": ReproducibilityEvidence("f" * 64, "f" * 64, True, True),
        "size": SizeEvidence("born-digital", 1000, 1200, True),
    }
    values.update(changes)
    return ReplicaEvidence(**values)  # type: ignore[arg-type]


def test_replica_attestation_refuses_self_asserted_digest_strings() -> None:
    evidence = _evidence()
    report = validate_replica(evidence)
    assert report.ready
    assert report.text_wilson_upper_95 > 0
    with pytest.raises(ValueError, match="resolved artifact inputs"):
        attest_replica(evidence)


def _attestation_fixture(
    tmp_path: Path, *, page_count: int = 1
) -> tuple[ReplicaEvidence, ReplicaAttestationInputs]:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"source")
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    page_id = PageRecord.derive_durable_id(source_sha, 0)
    page = PageRecord(
        page_id,
        1,
        0,
        Box(0, 0, 1000, 1000),
        "born-digital",
        AffineTransform(1000, 0, 0, 1000, 0, 0),
        AffineTransform(1000, 0, 0, 1000, 0, 0),
        "a" * 64,
        source_pdf_sha256=source_sha,
    )
    pages = tuple(
        replace(
            page,
            id=PageRecord.derive_durable_id(source_sha, index),
            sequence=index + 1,
            source_page_index=index,
        )
        for index in range(page_count)
    )
    references = tuple(
        PageReference(
            page.id,
            page.sequence,
            f"pages/p{page.sequence:06d}.json",
            page.source_page_index,
        )
        for page in pages
    )
    manifest = Manifest.for_source(
        SourceRecord(source_sha, source_path.stat().st_size),
        references,
        "test",
        "b" * 64,
    )
    structure_id = content_id("structure", {"fixture": True})
    authoring = tmp_path / "authoring"
    write_authoring_tree(
        authoring,
        manifest=manifest,
        pages=pages,
        structure=StructureRecord(
            manifest.document_id, structure_id, (StructureNode(structure_id, "document"),)
        ),
        styles=StylesRecord(manifest.document_id, ()),
    )
    package_path = tmp_path / "package.lmdoc"
    pack_directory(authoring, package_path)
    package_sha = hashlib.sha256(package_path.read_bytes()).hexdigest()
    benchmark = tmp_path / "benchmark.json"
    renderer = tmp_path / "renderer.json"
    benchmark.write_bytes(b"benchmark")
    renderer.write_bytes(b"renderer")
    artifacts = ReviewArtifacts(
        source_sha,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        "f" * 64,
        "1" * 64,
        "2" * 64,
        "3" * 64,
        "4" * 64,
        "5" * 64,
        "6" * 64,
    )
    review_pages = tuple(ReviewPage(page.id, artifacts, "7" * 64) for page in pages)
    project = ReviewProject(
        manifest.document_id, review_pages, manifest_page_ids=tuple(page.id for page in pages)
    )
    review = tmp_path / "review.json"
    review.write_bytes(project.canonical_export() + b"\n")
    approvals = tmp_path / "approvals.json"
    approval_values = [
        PageApproval(
            page.id, reviewer, source_sha, "c" * 64, "d" * 64, "e" * 64, "7" * 64
        ).to_dict()
        for page in pages
        for reviewer in ("one", "two")
    ]
    approvals.write_text(json.dumps(approval_values), encoding="utf-8")
    visual = tmp_path / "visual.json"
    visual_values = [
        {
            "page_id": page.id,
            "page_class": "born-digital",
            "ssim": 0.999,
            "edge_recall": 1.0,
            "edge_displacement_p95": 1.0,
            "continuous_tone_ssim": None,
            "undisposed_components": 0,
        }
        for page in pages
    ]
    visual.write_text(json.dumps(visual_values), encoding="utf-8")
    one = tmp_path / "one" / "build.lmdoc"
    two = tmp_path / "two" / "build.lmdoc"
    one.parent.mkdir()
    two.parent.mkdir()
    one.write_bytes(package_path.read_bytes())
    two.write_bytes(package_path.read_bytes())
    evidence = _evidence(
        package_sha256=package_sha,
        source_sha256=source_sha,
        benchmark_sha256=hashlib.sha256(benchmark.read_bytes()).hexdigest(),
        renderer_sha256=hashlib.sha256(renderer.read_bytes()).hexdigest(),
        review_set_sha256=hashlib.sha256(project.canonical_export()).hexdigest(),
        visual=tuple(
            VisualEvidence("born-digital", 0.999, 1.0, 1.0, None, 0, page.id)
            for page in pages
        ),
        reproducibility=ReproducibilityEvidence(package_sha, package_sha, True, True),
        size=SizeEvidence(
            "born-digital", source_path.stat().st_size, package_path.stat().st_size, True
        ),
    )
    return evidence, ReplicaAttestationInputs(
        package_path, source_path, benchmark, renderer, review, approvals, visual, one, two
    )


def test_replica_attestation_resolves_artifacts_and_rejects_mismatched_builds(
    tmp_path: Path,
) -> None:
    evidence, inputs = _attestation_fixture(tmp_path)
    assert attest_replica(evidence, inputs).package_sha256 == evidence.package_sha256
    inputs.build_two_path.write_bytes(b"mismatch")
    with pytest.raises(ValueError, match="reproducibility build"):
        attest_replica(evidence, inputs)


def test_replica_attestation_rejects_subset_visual_or_approval_evidence(tmp_path: Path) -> None:
    evidence, inputs = _attestation_fixture(tmp_path)
    inputs.visual_evidence_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="visual evidence does not cover"):
        attest_replica(evidence, inputs)
    inputs.visual_evidence_path.write_text(
        json.dumps(
            [
                {
                    "page_id": evidence.visual[0].page_id,
                    "page_class": "born-digital",
                    "ssim": 0.999,
                    "edge_recall": 1.0,
                    "edge_displacement_p95": 1.0,
                    "continuous_tone_ssim": None,
                    "undisposed_components": 0,
                }
            ]
        ),
        encoding="utf-8",
    )
    inputs.approvals_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="review approvals"):
        attest_replica(evidence, inputs)


@pytest.mark.parametrize("mode", ["duplicate", "out-of-order", "declared-out-of-order"])
def test_replica_attestation_requires_manifest_ordered_one_to_one_visual_evidence(
    tmp_path: Path, mode: str
) -> None:
    evidence, inputs = _attestation_fixture(tmp_path, page_count=2)
    records = json.loads(inputs.visual_evidence_path.read_text(encoding="utf-8"))
    if mode == "duplicate":
        inputs.visual_evidence_path.write_text(
            json.dumps([records[0], records[0]]), encoding="utf-8"
        )
    elif mode == "out-of-order":
        inputs.visual_evidence_path.write_text(
            json.dumps(list(reversed(records))), encoding="utf-8"
        )
    else:
        evidence = replace(evidence, visual=tuple(reversed(evidence.visual)))
    with pytest.raises(ValueError, match="visual evidence"):
        attest_replica(evidence, inputs)


def test_replica_attestation_rejects_aliases_or_shared_build_roots(tmp_path: Path) -> None:
    evidence, inputs = _attestation_fixture(tmp_path)
    alias = tmp_path / "alias.lmdoc"
    alias.symlink_to(inputs.build_one_path)
    alias_inputs = ReplicaAttestationInputs(
        inputs.package_path,
        inputs.source_path,
        inputs.benchmark_path,
        inputs.renderer_evidence_path,
        inputs.review_project_path,
        inputs.approvals_path,
        inputs.visual_evidence_path,
        inputs.build_one_path,
        alias,
    )
    with pytest.raises(ValueError, match="independently located"):
        attest_replica(evidence, alias_inputs)

    shared_root_inputs = ReplicaAttestationInputs(
        inputs.package_path,
        inputs.source_path,
        inputs.benchmark_path,
        inputs.renderer_evidence_path,
        inputs.review_project_path,
        inputs.approvals_path,
        inputs.visual_evidence_path,
        inputs.build_one_path,
        inputs.build_two_path,
        tmp_path,
        tmp_path,
    )
    with pytest.raises(ValueError, match="independently located"):
        attest_replica(evidence, shared_root_inputs)


@pytest.mark.parametrize(
    "change, code",
    [
        ({"high_risk_and_omissions_resolved": False}, "FINDINGS"),
        ({"policy": PolicyEvidence(1, 0, 0, True, True, True, True)}, "TREATMENT_OR_POLICY"),
        ({"visual": (VisualEvidence("scan", 0.90, 1.0, 0.0, None, 0),)}, "VISUAL"),
        (
            {"reproducibility": ReproducibilityEvidence("f" * 64, "0" * 64, True, True)},
            "REPRODUCIBILITY",
        ),
        ({"size": SizeEvidence("scan-dominant", 1000, 700)}, "SIZE"),
    ],
)
def test_any_missing_replica_gate_fails_closed(change, code) -> None:  # type: ignore[no-untyped-def]
    evidence = _evidence(**change)
    report = validate_replica(evidence)
    assert not report.ready
    assert code in report.failures
    with pytest.raises(ValueError, match=code):
        attest_replica(evidence)


def test_text_audit_requires_sample_size_and_exact_sensitive_content() -> None:
    short = TextAudit(1_000_000, 10_000, 0, True, True, True)
    assert not short.passes
    incorrect_code = TextAudit(100, 10_000, 0, False, True, True)
    assert not incorrect_code.passes


def test_accessibility_text_equivalence_is_diplomatic_and_fail_closed() -> None:
    assert accessibility_structure_evidence(
        semantic_html_valid=True,
        authoritative_text="A  B\n",
        rendered_text="A  B\n",
        critical_or_serious_violations=0,
    ).passes
    assert not accessibility_structure_evidence(
        semantic_html_valid=True,
        authoritative_text="A  B\n",
        rendered_text="A B\n",
        critical_or_serious_violations=0,
    ).passes
