"""Integration adapters for the local Phase 1 pipeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .decompile import Phase1Orchestrator, RenderedPage
from .preprocess import probe_render_backend, render_pdf


class LocalPageRenderer:
    """Adapt the deterministic preprocessing renderer to the orchestrator."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        backend = probe_render_backend()
        self.name = backend["name"]
        self.version = backend["version"]

    def render(
        self, source: Path, inspection: Mapping[str, Any], *, dpi: int
    ) -> Sequence[RenderedPage]:
        result = render_pdf(source, self.output_root, dpi=dpi)
        pages = result.manifest.value.get("pages")
        if not isinstance(pages, list) or len(pages) != len(inspection.get("pages", [])):
            raise ValueError("render manifest does not cover every inspected page")
        rendered: list[RenderedPage] = []
        for value in pages:
            if not isinstance(value, dict):
                raise ValueError("render page evidence must be an object")
            image = value.get("image")
            if not isinstance(image, dict):
                raise ValueError("render page evidence has no image record")
            relative_path = image.get("path")
            if not isinstance(relative_path, str):
                raise ValueError("render page image path is invalid")
            rendered.append(
                RenderedPage(
                    source_page_index=int(value["source_page_index"]),
                    width_px=int(image["width_px"]),
                    height_px=int(image["height_px"]),
                    content_sha256=str(image["sha256"]),
                    image_path=result.artifact_directory / relative_path,
                    native_evidence=value,
                )
            )
        return tuple(rendered)


def phase1_orchestrator(work_root: Path) -> Phase1Orchestrator:
    from .ocr.adapters import default_adapters

    return Phase1Orchestrator(
        LocalPageRenderer(work_root / "render"),
        default_adapters(),
    )
