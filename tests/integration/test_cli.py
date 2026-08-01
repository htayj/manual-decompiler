from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from lispmdoc.cli import _ensure_output_root_does_not_contain_source, run


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
