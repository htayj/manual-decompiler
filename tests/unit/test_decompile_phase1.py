from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lispmdoc.config import Config
from lispmdoc.decompile import DecompileError, Phase1Orchestrator, RenderedPage
from lispmdoc.decompile import orchestrator as orchestrator_module
from lispmdoc.evidence import Artifact, ArtifactStore
from lispmdoc.model import canonical_json_bytes
from lispmdoc.ocr import (
    BBox,
    Capability,
    EngineEvidence,
    OCRLine,
    OCRPage,
    OCRRegion,
    OCRRequest,
    OCRSpan,
    OCRToken,
)


class FakeRenderer:
    name = "fake-renderer"
    version = "1.0"

    def __init__(self, hashes: tuple[str, ...]) -> None:
        self.hashes = hashes
        self.calls = 0

    def render(
        self, source: Path, inspection: dict[str, object], *, dpi: int
    ) -> tuple[RenderedPage, ...]:
        del source, inspection, dpi
        self.calls += 1
        return tuple(
            RenderedPage(
                index,
                1000,
                1200,
                digest,
                native_evidence={"crop_box": [0.0, 0.0, 612.0, 792.0], "render": index},
            )
            for index, digest in enumerate(self.hashes)
        )


class FakeAdapter:
    version = "1.0"

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[OCRRequest] = []

    def probe(self) -> Capability:
        return Capability(self.name, True, "available", version=self.version)

    def recognize(self, request: OCRRequest) -> OCRPage:
        self.calls.append(request)
        token = OCRToken("token-aaaaaaaaaaaa", f"{self.name} literal", BBox(10, 20, 30, 40), 0.9)
        span = OCRSpan("span-aaaaaaaaaaaa", token.text, token.bbox, (token,), 0.9)
        line = OCRLine("line-aaaaaaaaaaaa", token.text, token.bbox, (span,), 0.9)
        region = OCRRegion("region-aaaaaaaaaaaa", "text", token.bbox, (line,), 0.9)
        return OCRPage(
            request.page_id,
            request.width,
            request.height,
            self.name,
            (region,),
            EngineEvidence(self.name, self.version, {"literal": token.text}),
        )


def _inspection(source: Path) -> dict[str, object]:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return {
        "source": {"algorithm": "sha256", "sha256": digest, "byte_size": source.stat().st_size},
        "pages": [
            {
                "crop_box": [0, 0, 612, 792],
                "media_box": [0, 0, 612, 792],
                "classification": {"label": "born-digital"},
                "embedded_text": {"extraction_available": True, "non_whitespace_characters": 30},
            },
            {
                "crop_box": [0, 0, 612, 792],
                "media_box": [0, 0, 612, 792],
                "classification": {"label": "scan-bilevel"},
                "embedded_text": {"extraction_available": False, "non_whitespace_characters": 0},
            },
        ],
    }


def _config(tmp_path: Path) -> Config:
    return Config(profile="test", work_root=tmp_path / "work", ocr_engine="fake")


def test_phase1_routes_literal_pdf_text_and_scans_then_reuses_work_stage(tmp_path: Path) -> None:
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"immutable test source")
    renderer = FakeRenderer(("a" * 64, "b" * 64))
    pdf_text, scanned = FakeAdapter("pdf-text"), FakeAdapter("fake")
    runner = Phase1Orchestrator(renderer, (scanned,), pdf_text_adapter=pdf_text)

    first = runner.run(source, _inspection(source), _config(tmp_path))

    assert not first.cache_hit
    assert first.manifest.conformance_level == "review-required"
    assert [adapter.page_id for adapter in pdf_text.calls] == [first.pages[0].id]
    assert [adapter.page_id for adapter in scanned.calls] == [first.pages[1].id]
    assert first.pages[0].objects[0].payload["literal_text"] == "pdf-text literal"
    assert first.pages[1].objects[0].payload["literal_text"] == "fake literal"
    assert (first.work_path / "lmdoc" / "manifest.json").is_file()
    work_manifest = json.loads((first.work_path / "work-manifest.json").read_text())
    assert work_manifest["provenance"]["source"]["sha256"] == first.manifest.source.sha256
    assert all(tool.configuration_sha256 for tool in first.manifest.tools)
    assert work_manifest["ocr_evidence"]["pages"][first.pages[0].id]["route"] == "pdf-text"
    assert work_manifest["ocr_evidence"]["pages"][first.pages[1].id]["route"] == "fake"
    for page in first.pages:
        record_path = first.work_path / "lmdoc" / "evidence" / "records" / f"{page.id}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert page.page_evidence_sha256 == hashlib.sha256(canonical_json_bytes(record)).hexdigest()
        artifacts = tuple(Artifact.from_dict(item) for item in record["artifacts"])
        assert set(page.objects[0].evidence_refs) == {artifact.sha256 for artifact in artifacts}
        for artifact in artifacts:
            assert ArtifactStore(tmp_path / "work" / "evidence").get_bytes(artifact)

    second = runner.run(source, _inspection(source), _config(tmp_path))

    assert second.cache_hit
    assert second.stage_id == first.stage_id
    assert len(pdf_text.calls) == 1
    assert len(scanned.calls) == 1
    assert renderer.calls == 2  # renderer supplies cache artifact identity


def test_phase1_refuses_a_source_whose_inspection_fingerprint_is_stale(tmp_path: Path) -> None:
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"before")
    inspection = _inspection(source)
    source.write_bytes(b"after")
    renderer = FakeRenderer(("a" * 64, "b" * 64))
    runner = Phase1Orchestrator(
        renderer, (FakeAdapter("fake"),), pdf_text_adapter=FakeAdapter("pdf-text")
    )

    with pytest.raises(DecompileError, match="source changed"):
        runner.run(source, inspection, _config(tmp_path))

    assert renderer.calls == 0


def test_phase1_routes_missing_born_digital_text_to_scanned_adapter(tmp_path: Path) -> None:
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"source")
    inspection = _inspection(source)
    pages = inspection["pages"]
    assert isinstance(pages, list)
    page = pages[0]
    assert isinstance(page, dict)
    page["embedded_text"] = {"extraction_available": False, "non_whitespace_characters": 0}
    renderer = FakeRenderer(("a" * 64, "b" * 64))
    pdf_text, scanned = FakeAdapter("pdf-text"), FakeAdapter("fake")

    Phase1Orchestrator(renderer, (scanned,), pdf_text_adapter=pdf_text).run(
        source, inspection, _config(tmp_path)
    )

    assert not pdf_text.calls
    assert len(scanned.calls) == 2


def test_phase1_uses_rotated_canonical_page_geometry(tmp_path: Path) -> None:
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"source")
    inspection = _inspection(source)
    pages = inspection["pages"]
    assert isinstance(pages, list)
    first_page = pages[0]
    assert isinstance(first_page, dict)
    first_page["rotation_degrees"] = 90
    renderer = FakeRenderer(("a" * 64, "b" * 64))
    runner = Phase1Orchestrator(
        renderer, (FakeAdapter("fake"),), pdf_text_adapter=FakeAdapter("pdf-text")
    )

    result = runner.run(source, inspection, _config(tmp_path))

    assert result.pages[0].page_box.width == 792_000
    assert result.pages[0].page_box.height == 612_000
    assert result.pages[0].source_pdf_to_canonical.apply(0, 792).to_dict() == {
        "x": 0,
        "y": 612_000,
    }


def test_phase1_code_contract_change_invalidates_stage_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"source")
    renderer = FakeRenderer(("a" * 64, "b" * 64))
    pdf_text, scanned = FakeAdapter("pdf-text"), FakeAdapter("fake")
    runner = Phase1Orchestrator(renderer, (scanned,), pdf_text_adapter=pdf_text)
    first = runner.run(source, _inspection(source), _config(tmp_path))

    monkeypatch.setattr(orchestrator_module, "_implementation_digest", lambda: "c" * 64)
    second = runner.run(source, _inspection(source), _config(tmp_path))

    assert not second.cache_hit
    assert second.stage_id != first.stage_id
    assert len(pdf_text.calls) == 2
    assert len(scanned.calls) == 2


def test_pipeline_implementation_identity_covers_transitive_local_imports(tmp_path: Path) -> None:
    package = tmp_path / "lispmdoc"
    (package / "decompile").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "decompile" / "__init__.py").write_text("", encoding="utf-8")
    (package / "decompile" / "orchestrator.py").write_text(
        "from lispmdoc.config import Config\n", encoding="utf-8"
    )
    (package / "config.py").write_text("from .hashing import digest\n", encoding="utf-8")
    hashing = package / "hashing.py"
    hashing.write_text("digest = 'one'\n", encoding="utf-8")

    first = orchestrator_module._pipeline_source_digests(package)
    hashing.write_text("digest = 'two'\n", encoding="utf-8")
    second = orchestrator_module._pipeline_source_digests(package)

    assert set(first) == {
        "__init__.py",
        "config.py",
        "decompile/__init__.py",
        "decompile/orchestrator.py",
        "hashing.py",
    }
    assert first["hashing.py"] != second["hashing.py"]


@pytest.mark.parametrize("mode", ["deleted", "corrupt"])
def test_phase1_refuses_cached_stage_with_missing_or_corrupt_external_evidence(
    tmp_path: Path, mode: str
) -> None:
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"source")
    renderer = FakeRenderer(("a" * 64, "b" * 64))
    pdf_text, scanned = FakeAdapter("pdf-text"), FakeAdapter("fake")
    runner = Phase1Orchestrator(renderer, (scanned,), pdf_text_adapter=pdf_text)
    first = runner.run(source, _inspection(source), _config(tmp_path))
    record = json.loads(
        (
            first.work_path / "lmdoc" / "evidence" / "records" / f"{first.pages[0].id}.json"
        ).read_text()
    )
    artifact = Artifact.from_dict(record["artifacts"][0])
    artifact_path = ArtifactStore(tmp_path / "work" / "evidence").path_for(artifact.sha256)
    if mode == "deleted":
        artifact_path.unlink()
    else:
        artifact_path.write_bytes(b"corrupt")

    with pytest.raises(DecompileError, match="cached evidence artifact"):
        runner.run(source, _inspection(source), _config(tmp_path))
