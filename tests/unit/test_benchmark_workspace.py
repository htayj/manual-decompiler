from __future__ import annotations

import json
from pathlib import Path

import pytest

from lispmdoc.benchmark import (
    ExpectedRunIdentity,
    QueuePage,
    Wave1ContractError,
    initialize_transcription_workspace,
    load_transcription_package,
    load_wave1_queue,
    queue_to_dict,
    transcription_package_from_dict,
    transcription_status,
)


def _page() -> QueuePage:
    return QueuePage(
        "a" * 64,
        11,
        "b" * 64,
        "scan-bilevel",
        ("clean-scanned-prose", "code-terminal"),
        ("heading", "body", "footer"),
        ExpectedRunIdentity("paddleocr", "3.7.0", "PP-OCRv5", "locked", "driver", "1"),
    )


def test_queue_and_blank_package_round_trip_without_ocr_text(tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps(queue_to_dict((_page(),))), encoding="utf-8")

    queue = load_wave1_queue(queue_path)
    result = initialize_transcription_workspace(queue, tmp_path / "workspace")
    package = load_transcription_package(result.template_paths[0])

    assert package.page == _page()
    assert package.transcriptions == ()
    assert {item.disposition for item in package.coverage} == {"needs-review"}
    assert transcription_status(package)["disposition"] == "human-review-required"
    assert result.index_path.is_file()


def test_workspace_is_deterministic_and_never_overwrites(tmp_path: Path) -> None:
    first = initialize_transcription_workspace((_page(),), tmp_path / "first")
    second = initialize_transcription_workspace((_page(),), tmp_path / "second")

    assert first.queue_sha256 == second.queue_sha256
    assert first.index_path.read_bytes() == second.index_path.read_bytes()
    assert first.template_paths[0].read_bytes() == second.template_paths[0].read_bytes()
    with pytest.raises(FileExistsError):
        initialize_transcription_workspace((_page(),), tmp_path / "first")


def test_package_parser_rejects_unversioned_or_generated_shortcuts() -> None:
    with pytest.raises(Wave1ContractError, match="unsupported transcription"):
        transcription_package_from_dict({"version": "latest"})

    package = {
        "version": "lispmdoc-benchmark-wave1",
        "page": _page().to_dict(),
        "inventory_region_ids": ["heading", "body", "footer"],
        "coverage": [
            {
                "inventory_region_id": region_id,
                "disposition": "transcribed",
                "reason": None,
            }
            for region_id in ("heading", "body", "footer")
        ],
        "transcriptions": [],
        "adjudicator": None,
    }
    parsed = transcription_package_from_dict(package)
    assert not parsed.adjudicated
    assert not parsed.complete_coverage or parsed.transcriptions == ()
