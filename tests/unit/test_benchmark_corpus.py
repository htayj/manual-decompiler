from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from lispmdoc.benchmark import (
    BENCHMARK_MANIFEST_VERSION,
    BenchmarkCorpus,
    BenchmarkManifestError,
    BenchmarkPage,
    GroundTruthRecord,
    candidates_from_inspections,
    load_corpus,
    select_stratified_pages,
)

SHA_A = hashlib.sha256(b"a").hexdigest()
SHA_B = hashlib.sha256(b"b").hexdigest()
SHA_C = hashlib.sha256(b"c").hexdigest()


def _truth(region_id: str, text: str, kind: str = "prose") -> GroundTruthRecord:
    return GroundTruthRecord(region_id, text, kind, "manual", "tester")


def test_yaml_manifest_is_versioned_deterministic_and_manual(tmp_path: Path) -> None:
    corpus = BenchmarkCorpus(
        BENCHMARK_MANIFEST_VERSION,
        (
            BenchmarkPage(SHA_B, 1, "scan-gray", ("degraded",), (_truth("r2", "abc"),)),
            BenchmarkPage(SHA_A, 0, "born-digital", ("clean",), (_truth("r1", "xyz"),)),
        ),
    )
    path = tmp_path / "corpus.yaml"
    path.write_text(
        "version: lispmdoc-benchmark-1\nname: lispmdoc evaluation corpus\npages:\n"
        f"  - source_sha256: {SHA_A}\n"
        "    source_page_index: 0\n    page_class: born-digital\n    difficulty_tags: [clean]\n"
        "    ground_truth:\n      - region_id: r1\n        text: xyz\n        kind: prose\n"
        "        method: manual\n        recorded_by: tester\n        required: true\n",
        encoding="utf-8",
    )

    loaded = load_corpus(path)

    assert loaded.pages[0].id == f"{SHA_A}:0"
    assert corpus.to_json() == corpus.to_json()
    with pytest.raises(BenchmarkManifestError, match="generated"):
        GroundTruthRecord("bad", "text", "prose", "generated", "model")


def test_selection_is_stratified_and_independent_of_inspection_order() -> None:
    inspections = (
        {
            "source": {"sha256": SHA_B},
            "pages": [
                {"page_number": 1, "classification": {"label": "scan-gray"}},
                {"page_number": 2, "classification": {"label": "scan-gray"}},
            ],
        },
        {
            "source": {"sha256": SHA_A},
            "pages": [{"page_number": 1, "classification": {"label": "born-digital"}}],
        },
    )
    tags = {
        (SHA_B, 0): ("degraded",),
        (SHA_B, 1): ("degraded",),
        (SHA_A, 0): ("clean", "code"),
    }

    candidates = candidates_from_inspections(reversed(inspections), tags)
    selection = select_stratified_pages(candidates, per_stratum=2)

    assert [candidate.id for candidate in selection.selected] == [
        f"{SHA_B}:0",
        f"{SHA_B}:1",
        f"{SHA_A}:0",
    ]
    assert selection.insufficient_strata == (("born-digital", "clean"), ("born-digital", "code"))
