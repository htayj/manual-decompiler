from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lispmdoc.cli import run
from lispmdoc.config import Config
from lispmdoc.decompile import Phase1Orchestrator, RenderedPage
from lispmdoc.ocr import Capability, EngineEvidence, OCRPage, OCRRequest
from lispmdoc.package import inspect_package, pack_directory
from lispmdoc.render import write_view_tree
from lispmdoc.validate import validate_lmdoc


class _Renderer:
    name = "e2e-renderer"
    version = "1"

    def render(
        self, source: Path, inspection: dict[str, object], *, dpi: int
    ) -> tuple[RenderedPage, ...]:
        del source, inspection, dpi
        return (RenderedPage(0, 100, 100, "a" * 64, native_evidence={"fixture": True}),)


class _Adapter:
    name = "e2e-ocr"
    version = "1"

    def probe(self) -> Capability:
        return Capability(self.name, True, "fixture", version=self.version)

    def recognize(self, request: OCRRequest) -> OCRPage:
        return OCRPage(
            request.page_id,
            request.width,
            request.height,
            self.name,
            (),
            EngineEvidence(self.name, self.version, {"fixture": True}),
            native_output=b'{"fixture":"native"}',
            native_output_media_type="application/json",
        )


def test_phase1_evidence_views_package_and_review_export_form_one_safe_flow(
    tmp_path: Path, capsys: object
) -> None:
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"immutable source")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    inspection: dict[str, object] = {
        "source": {
            "algorithm": "sha256",
            "sha256": source_sha256,
            "byte_size": source.stat().st_size,
        },
        "pages": [
            {
                "crop_box": [0, 0, 612, 792],
                "media_box": [0, 0, 612, 792],
                "classification": {"label": "scan-bilevel"},
                "embedded_text": {"extraction_available": False, "non_whitespace_characters": 0},
            }
        ],
    }
    result = Phase1Orchestrator(_Renderer(), (_Adapter(),)).run(
        source,
        inspection,
        Config(profile="test", work_root=tmp_path / "work", ocr_engine="e2e-ocr"),
    )
    authoring = result.work_path / "lmdoc"
    views = write_view_tree(
        authoring, result.manifest, result.pages, result.structure, result.styles
    )
    package = tmp_path / "manual.lmdoc"
    pack_directory(authoring, package)

    tree_report = validate_lmdoc(authoring)
    package_report = validate_lmdoc(package)
    assert tree_report.is_structurally_valid
    assert package_report.is_structurally_valid
    assert any(finding.code == "EVIDENCE_ARTIFACT_EXTERNAL" for finding in package_report.findings)
    assert f"evidence/records/{result.pages[0].id}.json" in inspect_package(package)
    assert views.plain_text_path.read_text(encoding="utf-8") == ""

    review_path = tmp_path / "review.json"
    assert run(["review-export", str(authoring), str(review_path)]) == 0
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review["pages"][0]["page_id"] == result.pages[0].id
    capsys.readouterr()
