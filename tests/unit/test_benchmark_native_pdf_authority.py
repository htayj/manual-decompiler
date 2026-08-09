from __future__ import annotations

import json
from pathlib import Path

import pytest

from lispmdoc.benchmark import native_pdf_authority as authority_module
from lispmdoc.benchmark.native_pdf_authority import (
    NativePdfAuthorityError,
    NativePdfAuthorityReceipt,
    _review_regions,
    _tracked_inventory,
    _validate_annotation_envelope,
    _validate_decision,
    _validate_fixed_decision,
    default_regions,
    validate_inventory_binding,
    validate_ligature_witness,
)
from lispmdoc.benchmark.wave2 import load_inventory


def _decision() -> dict[str, object]:
    regions = default_regions()
    return {
        "regions": regions,
        "reading_order": [region["id"] for region in regions if region["role"] != "running-matter"],
        "excluded_word_ids": ["word-092"],
        "finding_dispositions": {
            "proposal-000-raw-reading-order-disagreement": "accepted",
            "pypdf-ligature-specification": "accepted",
        },
        "region_dispositions": {region["id"]: "accept" for region in regions},
        "acceptance": {
            "layout": True,
            "reading_order": True,
            "semantics": True,
            "object_extraction": True,
        },
    }


def test_pending_receipt_is_empty_and_cannot_promote() -> None:
    receipt = NativePdfAuthorityReceipt.from_bytes(
        b'{"schema_version":"lispmdoc-native-pdf-authority-receipt-1","status":"pending","evidence":null}'
    )
    assert receipt.status == "pending" and receipt.evidence is None
    with pytest.raises(NativePdfAuthorityError, match="pending receipt must be empty"):
        NativePdfAuthorityReceipt.from_bytes(
            b'{"schema_version":"lispmdoc-native-pdf-authority-receipt-1","status":"pending","evidence":{}}'
        )


def test_native_decision_is_exact_word_partition_and_requires_all_gates() -> None:
    words = {f"word-{index:03d}" for index in range(93)}
    decision = _decision()
    _validate_decision(decision, words, set(decision["finding_dispositions"]))
    decision["regions"][0]["word_ids"].append("word-002")  # type: ignore[index]
    with pytest.raises(NativePdfAuthorityError, match="partitioned"):
        _validate_decision(
            decision,
            words,
            {"proposal-000-raw-reading-order-disagreement", "pypdf-ligature-specification"},
        )


def test_native_decision_rejects_partial_or_non_running_matter_exclusion() -> None:
    words = {f"word-{index:03d}" for index in range(93)}
    decision = _decision()
    decision["excluded_word_ids"] = ["word-003"]
    with pytest.raises(NativePdfAuthorityError, match="running-matter"):
        _validate_decision(
            decision,
            words,
            {"proposal-000-raw-reading-order-disagreement", "pypdf-ligature-specification"},
        )


def test_native_decision_cannot_promote_unresolved_findings() -> None:
    decision = _decision()
    decision["finding_dispositions"]["pypdf-ligature-specification"] = "needs-follow-up"
    with pytest.raises(NativePdfAuthorityError, match="requires every"):
        _validate_decision(
            decision,
            {f"word-{index:03d}" for index in range(93)},
            set(decision["finding_dispositions"]),
        )


def test_inventory_binding_rejects_wrong_source_and_tags() -> None:
    root = Path(__file__).parents[2]
    inventory, _ = load_inventory(root, "config/benchmarks/wave2-representative-candidates.json")
    source = {
        "sha256": "d014d9f5342a509d4ca329e308fcd842d55f3074089ae986242c4bccae1748dd",
        "byte_size": 450319,
    }
    page = {
        "source_page_index": 2,
        "page_class": "born-digital",
        "composition_tags": ["born-digital"],
    }
    validate_inventory_binding(inventory, source, page)
    with pytest.raises(NativePdfAuthorityError, match="source snapshot"):
        validate_inventory_binding(inventory, {**source, "sha256": "0" * 64}, page)
    with pytest.raises(NativePdfAuthorityError, match="class/tags"):
        validate_inventory_binding(inventory, source, {**page, "composition_tags": ["table"]})


def test_ligature_binding_rejects_shifted_or_unrelated_occurrences() -> None:
    words = [{"text": "x"} for _ in range(46)]
    for index, text in enumerate(["a", "full", "hardware", "specification", "of", "the"], 40):
        words[index] = {"text": text}
    calls = [{"text": ""} for _ in range(10)]
    calls[9] = {"text": "For a full hardware speciﬁcation of the processor, consult the"}
    validate_ligature_witness(words, calls)
    words[43] = {"text": "speciﬁcation"}
    with pytest.raises(NativePdfAuthorityError, match="corresponding"):
        validate_ligature_witness(words, calls)


def test_review_geometry_is_top_origin() -> None:
    words = [
        {
            "x_min": "0",
            "x_max": "1",
            "y_min": str(index * 10),
            "y_max": str(index * 10 + 1),
            "text": str(index),
        }
        for index in range(93)
    ]
    regions = _review_regions(words, ["0", "0", "100", "1000"])
    assert regions[0]["reference_box"][1] < regions[-1]["reference_box"][1]


def test_offline_fixed_contract_rejects_reordered_decision() -> None:
    regions = default_regions()
    authority = {
        "default_reading_order": [
            region["id"] for region in regions if region["role"] != "running-matter"
        ],
        "default_excluded_word_ids": ["word-092"],
    }
    decision = _decision()
    _validate_fixed_decision(decision, regions, authority)
    decision["reading_order"] = list(reversed(decision["reading_order"]))
    with pytest.raises(NativePdfAuthorityError, match="fixed review contract"):
        _validate_fixed_decision(decision, regions, authority)


def test_offline_annotation_envelope_rejects_handcrafted_region_fields() -> None:
    envelope = {
        "format_version": "1.0",
        "project_sha256": "a" * 64,
        "document_id": "native-pdf-k-machine-p3",
        "reviewer": "r",
        "saved_at": "2026-08-09T00:00:00Z",
        "annotations": {
            "pages": {"k-machine-p000003": {"disposition": "accept", "native_decision": {}}}
        },
    }
    _validate_annotation_envelope(envelope, "a" * 64)
    envelope["annotations"]["pages"]["k-machine-p000003"]["regions"] = {}
    with pytest.raises(NativePdfAuthorityError, match="unknown fields"):
        _validate_annotation_envelope(envelope, "a" * 64)


def test_authority_receipt_schema_is_valid_json_and_declares_strict_evidence() -> None:
    schema = json.loads(
        (Path(__file__).parents[2] / "schemas/native-pdf-authority-receipt.schema.json").read_text()
    )
    assert schema["$defs"]["evidence"]["additionalProperties"] is False
    assert schema["$defs"]["decision"]["additionalProperties"] is False


def test_tracked_inventory_is_read_once_before_semantic_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).parents[2]
    actual = (root / "config/benchmarks/wave2-representative-candidates.json").read_bytes()
    calls = 0

    def swapped(*_args: object, **_kwargs: object) -> bytes:
        nonlocal calls
        calls += 1
        return actual if calls == 1 else b"not the bytes first parsed"

    monkeypatch.setattr(authority_module, "_contained", swapped)
    inventory, content = _tracked_inventory(root)
    assert calls == 1
    assert content == actual and any(
        manual.manual_id == "k-machine" for manual in inventory.manuals
    )
