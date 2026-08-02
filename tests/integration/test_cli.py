from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
from PIL import Image

from lispmdoc.benchmark import (
    AUTHORITATIVE_TRUTH_VERSION,
    AuthoritativeRegionTruth,
    AuthoritativeTruthPackage,
    MappingAnchor,
    MappingEvidence,
    QueuePage,
    QueuePageBinding,
    RegionGeometry,
    SourceSpan,
    TextDerivation,
    TypesetterSourceProvenance,
)
from lispmdoc.cli import _ensure_output_root_does_not_contain_source, run


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_discover_without_fingerprinting_is_sorted(tmp_path: Path, capsys: object) -> None:
    (tmp_path / "z.PDF").write_bytes(b"not inspected")
    nested = tmp_path / "a"
    nested.mkdir()
    (nested / "b.pdf").write_bytes(b"not inspected")

    assert run(["discover", str(tmp_path), "--no-fingerprint"]) == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert [record["collection_path"] for record in output] == ["a/b.pdf", "z.PDF"]


def test_benchmark_cli_accounts_for_omission(tmp_path: Path, capsys: object) -> None:
    truth = tmp_path / "truth.json"
    predictions = tmp_path / "predictions.json"
    truth.write_text(
        json.dumps(
            [
                {"id": "prose", "text": "Hello, world."},
                {"id": "code", "kind": "code", "text": "(print x)"},
            ]
        ),
        encoding="utf-8",
    )
    predictions.write_text(json.dumps({"prose": "Hello world."}), encoding="utf-8")

    assert run(["benchmark-ocr", str(truth), str(predictions)]) == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["omissions"]["omitted_region_ids"] == ["code"]
    assert output["cer"]["errors"] >= 1


def test_generated_output_root_may_not_contain_source(tmp_path: Path) -> None:
    source = tmp_path / "source-material" / "manual.pdf"
    source.parent.mkdir()
    source.write_bytes(b"source")

    with pytest.raises(ValueError, match="contains immutable source"):
        _ensure_output_root_does_not_contain_source(tmp_path, source)


def test_safe_capability_queue_and_preprocess_reports(tmp_path: Path, capsys: object) -> None:
    assert run(["render-capabilities"]) == 0
    capabilities = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert "pdf_render" in capabilities
    assert "derived_views" in capabilities

    queue = tmp_path / "queue.json"
    queue.write_text(
        json.dumps({"version": "lispmdoc-benchmark-wave1", "pages": []}),
        encoding="utf-8",
    )
    assert run(["benchmark-queue-check", str(queue)]) == 1
    assert json.loads(capsys.readouterr().out)["disposition"] == "undersized"  # type: ignore[attr-defined]

    image = tmp_path / "page.png"
    Image.new("RGB", (8, 8), "white").save(image)
    assert run(["preprocess-proposal", str(image)]) == 0
    proposal = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert proposal["disposition"] == "proposal-only"
    assert image.read_bytes()


def test_transcription_cli_creates_truth_free_workspace(tmp_path: Path, capsys: object) -> None:
    digest = "a" * 64
    queue = tmp_path / "queue.json"
    queue.write_text(
        json.dumps(
            {
                "version": "lispmdoc-benchmark-wave1",
                "pages": [
                    {
                        "source_sha256": digest,
                        "source_page_index": 7,
                        "render_sha256": "b" * 64,
                        "page_class": "scan-bilevel",
                        "tags": ["clean-scanned-prose"],
                        "inventory_region_ids": ["region-1"],
                        "expected_run": {
                            "engine": "engine",
                            "engine_version": "1",
                            "model": "model",
                            "model_version": "1",
                            "tool": "driver",
                            "tool_version": "1",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"

    assert run(["benchmark-transcription-init", str(queue), str(workspace)]) == 0
    created = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    package = Path(created["template_paths"][0])
    value = json.loads(package.read_text(encoding="utf-8"))
    assert value["transcriptions"] == []
    assert value["coverage"] == [
        {
            "disposition": "needs-review",
            "inventory_region_id": "region-1",
            "reason": None,
        }
    ]

    assert run(["benchmark-transcription-check", str(package)]) == 1
    status = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert status["disposition"] == "human-review-required"


def test_bolio_extract_and_authoritative_check_cli(tmp_path: Path, capsys: object) -> None:
    source = tmp_path / "source.bolio"
    variables = tmp_path / "manual.vars"
    reference = tmp_path / "reference.txt"
    source.write_bytes(b".section Synthetic\n.setq synthetic section-page\nBody.\n")
    variables.write_bytes(b"(DEFPROP SYNTHETIC |section 1.2, page 3| JUST-VALUE)\n")

    assert run(
        [
            "benchmark-bolio-extract",
            str(source),
            str(variables),
            "--text-output",
            str(reference),
        ]
    ) == 0
    extraction = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert extraction["issue_count"] == 0
    assert reference.read_text(encoding="utf-8") == "1.2 Synthetic\n\nBody.\n"

    archive = tmp_path / "source.tar.gz"
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w") as tar:
        info = tarfile.TarInfo("reference.txt")
        info.size = len(reference.read_bytes())
        info.mtime = 0
        tar.addfile(info, io.BytesIO(reference.read_bytes()))
    archive.write_bytes(archive_buffer.getvalue())
    page = QueuePage(
        "a" * 64,
        2,
        _sha(b"scan"),
        "scan-gray",
        ("clean-scanned-prose",),
        ("section",),
    )
    package = AuthoritativeTruthPackage(
        AUTHORITATIVE_TRUTH_VERSION,
        page,
        QueuePageBinding.from_queue_page(page),
        TypesetterSourceProvenance(
            _sha(archive.read_bytes()),
            _sha(reference.read_bytes()),
            "synthetic edition",
            "reference.txt",
            ("strip-final-newline",),
            TextDerivation("source-literal", "utf-8"),
        ),
        MappingEvidence(
            (
                MappingAnchor("printed-page-number", "3", "3"),
                MappingAnchor("heading", "Synthetic", "Synthetic"),
            ),
            "human-mapping-review-required",
        ),
        (
            AuthoritativeRegionTruth(
                RegionGeometry(
                    "section",
                    ((0, 0), (10, 0), (10, 10)),
                    ((0, 10), (10, 10)),
                    0,
                    "section",
                ),
                "1.2 Synthetic",
                (),
                "heading",
                SourceSpan(1, 1),
                layout_verification_state="human-review-required",
            ),
        ),
    )
    package_path = tmp_path / "truth.json"
    package_path.write_text(package.to_json(), encoding="utf-8")
    pending_evaluator = tmp_path / "pending-evaluator.json"
    assert run([
        "benchmark-authoritative-check",
        str(package_path),
        "--source-archive",
        str(archive),
        "--source-file",
        str(reference),
        "--ground-truth-output",
        str(pending_evaluator),
    ]) == 1
    pending_status = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert pending_status["disposition"] == "human-mapping-review-required"
    assert not pending_evaluator.exists()
    project = {
        "assets": {
            "generated": {"path": "generated.svg", "sha256": _sha(b"generated")},
            "scan": {"path": "scan.png", "sha256": page.render_sha256},
        },
        "format_version": "1.0",
        "pages": [{
            "generated_asset_id": "generated",
            "id": page.id,
            "reference_asset_id": "scan",
            "regions": [{
                "canonical_text": "1.2 Synthetic",
                "id": "section",
                "source_text": "1.2 Synthetic",
            }],
        }],
    }
    project_path = tmp_path / "review-project.json"
    (tmp_path / "generated.svg").write_bytes(b"generated")
    (tmp_path / "scan.png").write_bytes(b"scan")
    project_path.write_text(json.dumps(project, sort_keys=True) + "\n", encoding="utf-8")
    annotations = {
        "annotations": {"pages": {page.id: {
            "disposition": "accept",
            "regions": {"section": {"disposition": "accept"}},
        }}},
        "format_version": "1.0",
        "project_sha256": _sha(project_path.read_bytes()),
        "reviewer": "reviewer",
    }
    annotations_path = tmp_path / "review.annotations.json"
    annotations_path.write_text(
        json.dumps(annotations, sort_keys=True) + "\n", encoding="utf-8"
    )
    reviewed_path = tmp_path / "reviewed-truth.json"
    assert run([
        "benchmark-authoritative-apply-review",
        str(package_path),
        str(project_path),
        str(annotations_path),
        str(reviewed_path),
    ]) == 0
    review_status = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert review_status["disposition"] == "authoritative-ready"
    evaluator_truth = tmp_path / "evaluator.json"

    assert run(
        [
            "benchmark-authoritative-check",
            str(reviewed_path),
            "--source-archive",
            str(archive),
            "--source-file",
            str(reference),
            "--review-project",
            str(project_path),
            "--review-annotations",
            str(annotations_path),
            "--ground-truth-output",
            str(evaluator_truth),
        ]
    ) == 0
    status = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert status["material_verified"] is True
    assert status["disposition"] == "authoritative-ready"
    assert json.loads(evaluator_truth.read_text(encoding="utf-8"))[0]["text"] == "1.2 Synthetic"
