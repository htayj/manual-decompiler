"""Resumable Phase 1 orchestration for literal LMDOC evidence.

This module deliberately coordinates injected renderers and OCR adapters rather
than owning a PDF renderer or an OCR engine.  That keeps source PDFs read-only,
makes cache identity explicit, and lets callers test routing without touching a
corpus or optional OCR software.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

from lispmdoc.config import Config
from lispmdoc.evidence import Artifact, ArtifactCorruptError, ArtifactStore, EvidenceRecord
from lispmdoc.ingest import DocumentInspection, SourceChangedError, SourceFingerprint, verify_source
from lispmdoc.model import (
    AffineTransform,
    Box,
    Manifest,
    PageRecord,
    PageReference,
    Rational,
    SceneObject,
    SourceRecord,
    StructureNode,
    StructureRecord,
    StylesRecord,
    ToolRecord,
    canonical_json_bytes,
    content_id,
    sha256_hex,
)
from lispmdoc.ocr import BBox, OCRAdapter, OCRPage, OCRRequest, OCRUnavailable, PDFTextAdapter
from lispmdoc.preprocess import source_pdf_to_canonical

_STAGE_VERSION = "decompile-phase1-v6"


class DecompileError(RuntimeError):
    """Raised for a non-recoverable Phase 1 orchestration failure."""


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """A renderer-owned artifact for one source PDF page.

    ``content_sha256`` names the exact rendered bytes (or another stable,
    renderer-defined representation) and is part of the stage cache key.
    """

    source_page_index: int
    width_px: int
    height_px: int
    content_sha256: str
    image_path: Path | None = None
    native_evidence: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.source_page_index < 0:
            raise ValueError("source_page_index must be non-negative")
        if self.width_px <= 0 or self.height_px <= 0:
            raise ValueError("rendered page pixel dimensions must be positive")
        if len(self.content_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.content_sha256
        ):
            raise ValueError("rendered page content_sha256 must be lower-case SHA-256")

    def cache_record(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "content_sha256": self.content_sha256,
            "height_px": self.height_px,
            "source_page_index": self.source_page_index,
            "width_px": self.width_px,
        }
        if self.native_evidence is not None:
            result["native_evidence"] = _json_value(self.native_evidence)
        return result


class PageRenderer(Protocol):
    """Injected read-only renderer contract used by Phase 1."""

    name: str
    version: str

    def render(
        self, source: Path, inspection: Mapping[str, Any], *, dpi: int
    ) -> Sequence[RenderedPage]: ...


@dataclass(frozen=True, slots=True)
class DecompileResult:
    stage_id: str
    cache_hit: bool
    work_path: Path
    manifest: Manifest
    pages: tuple[PageRecord, ...]
    structure: StructureRecord
    styles: StylesRecord
    ocr_evidence: Mapping[str, Any]


def _json_value(value: Any) -> Any:
    """Copy native evidence into a stable JSON-only work-manifest value."""
    if isinstance(value, Mapping):
        return {str(key): _json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"native evidence is not JSON-compatible: {type(value).__name__}")


def _json_digest(value: Any) -> str:
    """Hash inspection/native values, which may validly include floats."""
    encoded = json.dumps(
        _json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _tool_identity(component: object, *, fallback_name: str) -> dict[str, str]:
    name = str(getattr(component, "name", fallback_name))
    version = str(getattr(component, "version", "unknown"))
    return {"name": name, "version": version}


def _local_module_path(package_root: Path, module: str) -> Path | None:
    """Return a local module's source file without importing application code."""

    candidates: tuple[Path, ...]
    if module == "lispmdoc":
        candidates = (package_root / "__init__.py",)
    elif not module.startswith("lispmdoc."):
        return None
    else:
        relative = Path(*module.split(".")[1:])
        candidates = (package_root / f"{relative}.py", package_root / relative / "__init__.py")
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _module_name(path: Path, package_root: Path) -> str:
    relative = path.relative_to(package_root)
    if relative.name == "__init__.py":
        parts = relative.parent.parts
    else:
        parts = relative.with_suffix("").parts
    return ".".join(("lispmdoc", *parts))


def _imported_local_modules(path: Path, package_root: Path) -> tuple[str, ...]:
    """Find statically imported local modules for cache-identity closure."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = _module_name(path, package_root)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names if alias.name.startswith("lispmdoc"))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative_name = "." * node.level + (node.module or "")
                base = importlib.util.resolve_name(relative_name, package)
            else:
                base = node.module or ""
            if not base.startswith("lispmdoc"):
                continue
            imported.add(base)
            # ``from package import module`` can load a child module even when
            # the package does not re-export it.  Include existing children.
            imported.update(f"{base}.{alias.name}" for alias in node.names)
    return tuple(sorted(imported))


def _pipeline_source_digests(package_root: Path) -> dict[str, str]:
    """Hash the complete local import closure that can affect Phase 1 output.

    This is deliberately source based rather than module-object based: imports
    can execute optional dependency probes and are not a safe cache-key input.
    Missing local imports are ignored because they cannot have participated in
    this running pipeline.
    """

    # Importing a child executes each package initializer first; those local
    # initializers are therefore part of the executable pipeline contract too.
    pending = [
        "lispmdoc",
        "lispmdoc.decompile",
        "lispmdoc.decompile.orchestrator",
        # The standard CLI pipeline supplies the renderer; its adapter logic
        # can change output while retaining the same external tool version.
        "lispmdoc.pipeline",
    ]
    seen: set[str] = set()
    sources: dict[str, str] = {}
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _local_module_path(package_root, module)
        if path is None:
            continue
        relative = path.relative_to(package_root).as_posix()
        sources[relative] = sha256(path.read_bytes()).hexdigest()
        for imported in _imported_local_modules(path, package_root):
            parts = imported.split(".")
            pending.extend(
                ".".join(parts[:index])
                for index in range(1, len(parts) + 1)
                if ".".join(parts[:index]) not in seen
            )
    return dict(sorted(sources.items()))


def _implementation_digest() -> str:
    """Digest all local Phase 1 pipeline contracts, transitively and deterministically."""

    package_root = Path(__file__).resolve().parents[1]
    return sha256_hex({"stage": _STAGE_VERSION, "sources": _pipeline_source_digests(package_root)})


def _number_fraction(value: object, name: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecompileError(f"inspection page {name} must be numeric")
    return Fraction(str(value))


def _page_geometry(page: Mapping[str, Any]) -> tuple[Box, AffineTransform]:
    crop = page.get("crop_box") or page.get("media_box")
    if not isinstance(crop, list) or len(crop) != 4:
        raise DecompileError("inspection page is missing a valid crop_box/media_box")
    x0, y0, x1, y1 = (_number_fraction(value, "box coordinate") for value in crop)
    rotation_value = page.get("rotation_degrees", 0)
    if isinstance(rotation_value, bool) or not isinstance(rotation_value, int):
        raise DecompileError("inspection page rotation_degrees must be an integer")
    try:
        source_transform, canonical_width, canonical_height = source_pdf_to_canonical(
            x0, y0, x1, y1, rotation_value % 360
        )
    except ValueError as error:
        raise DecompileError(f"invalid inspection page geometry: {error}") from error
    page_box = Box(0, 0, canonical_width, canonical_height)
    return page_box, source_transform


def _render_transform(page_box: Box, render: RenderedPage) -> AffineTransform:
    return AffineTransform(
        Rational.from_value(Fraction(page_box.width, render.width_px)),
        Rational(0),
        Rational(0),
        Rational.from_value(Fraction(page_box.height, render.height_px)),
        Rational(0),
        Rational(0),
    )


def _safe_box(box: BBox | None, page_box: Box) -> Box:
    """Clip OCR geometry or use a visible fallback while retaining raw evidence."""
    if box is None:
        return page_box
    x0 = max(page_box.x0, min(box.x0, page_box.x1 - 1))
    y0 = max(page_box.y0, min(box.y0, page_box.y1 - 1))
    x1 = max(x0 + 1, min(box.x1, page_box.x1))
    y1 = max(y0 + 1, min(box.y1, page_box.y1))
    return Box(x0, y0, x1, y1)


def _page_source_hash(
    source: SourceFingerprint, source_page_index: int, page: Mapping[str, Any]
) -> str:
    return _json_digest(
        {
            "inspection_page": page,
            "source_page_index": source_page_index,
            "source_sha256": source.sha256,
        }
    )


def _has_embedded_text(page: Mapping[str, Any]) -> bool:
    evidence = page.get("embedded_text")
    return (
        isinstance(evidence, Mapping)
        and bool(evidence.get("extraction_available"))
        and int(evidence.get("non_whitespace_characters", 0)) > 0
    )


def _route_engine(page: Mapping[str, Any], scanned_name: str) -> str:
    page_class = page.get("classification", {}).get("label")
    if page_class in {"born-digital", "hybrid"} and _has_embedded_text(page):
        return "pdf-text"
    return scanned_name


class Phase1Orchestrator:
    """Create deterministic, review-required Phase 1 records and work evidence."""

    def __init__(
        self,
        renderer: PageRenderer,
        adapters: Sequence[OCRAdapter],
        *,
        pdf_text_adapter: OCRAdapter | None = None,
    ) -> None:
        self._renderer = renderer
        self._adapters = {adapter.name: adapter for adapter in adapters}
        if len(self._adapters) != len(adapters):
            raise ValueError("OCR adapter names must be unique")
        self._pdf_text_adapter = (
            pdf_text_adapter or self._adapters.get("pdf-text") or PDFTextAdapter()
        )

    def run(
        self,
        source: Path,
        inspection: DocumentInspection | Mapping[str, Any],
        config: Config,
    ) -> DecompileResult:
        """Run or reuse a source-verified Phase 1 work stage.

        The renderer is called to recover its own cache artifact identities.
        Once the stage identity is known, a valid work manifest is reused before
        any OCR adapter is invoked.
        """
        source = source.resolve(strict=True)
        inspected = (
            inspection.to_dict()
            if isinstance(inspection, DocumentInspection)
            else _json_value(inspection)
        )
        expected = _inspection_fingerprint(inspected)
        self._verify_source(source, expected)
        pages = inspected.get("pages")
        if not isinstance(pages, list) or not pages:
            raise DecompileError("inspection has no usable pages")
        rendered = tuple(self._renderer.render(source, inspected, dpi=config.render_dpi))
        renders = _render_by_page(rendered, len(pages))
        scanned_adapter = self._selected_scanned_adapter(config.ocr_engine)
        used_adapters = self._used_adapters(pages, scanned_adapter)
        tool_records = self._tool_records(used_adapters)
        stage_id = self._stage_id(inspected, expected, config, renders, tool_records)
        work_path = config.work_root / "decompile" / stage_id
        cached = self._load_cached(
            work_path, stage_id, ArtifactStore(config.work_root / "evidence")
        )
        if cached is not None:
            self._verify_source(source, expected)
            return cached

        built = self._build_records(
            source,
            inspected,
            expected,
            config,
            pages,
            renders,
            scanned_adapter,
            tool_records,
            stage_id,
            work_path,
        )
        self._verify_source(source, expected)
        self._write_stage(built)
        return built

    def _selected_scanned_adapter(self, requested: str) -> OCRAdapter:
        if requested != "auto":
            adapter = self._adapters.get(requested)
            if adapter is None or adapter.name == "pdf-text":
                raise DecompileError(f"configured scanned OCR adapter is unavailable: {requested}")
            return adapter
        candidates = [
            adapter for name, adapter in sorted(self._adapters.items()) if name != "pdf-text"
        ]
        for adapter in candidates:
            if adapter.probe().available:
                return adapter
        raise DecompileError(
            "no available scanned OCR adapter; configure an installed adapter explicitly"
        )

    def _used_adapters(
        self, pages: Sequence[Mapping[str, Any]], scanned: OCRAdapter
    ) -> tuple[OCRAdapter, ...]:
        adapters: dict[str, OCRAdapter] = {scanned.name: scanned}
        if any(_route_engine(page, scanned.name) == "pdf-text" for page in pages):
            adapters[self._pdf_text_adapter.name] = self._pdf_text_adapter
        return tuple(adapters[name] for name in sorted(adapters))

    def _tool_records(self, adapters: Sequence[OCRAdapter]) -> tuple[ToolRecord, ...]:
        components: list[object] = [self._renderer, *adapters]
        records: dict[str, ToolRecord] = {}
        for component in components:
            identity = _tool_identity(component, fallback_name=type(component).__qualname__)
            probe = getattr(component, "probe", None)
            if callable(probe):
                capability = probe()
                identity["version"] = capability.version or identity["version"]
            records[identity["name"]] = ToolRecord(
                identity["name"], identity["version"], sha256_hex(identity)
            )
        return tuple(records[name] for name in sorted(records))

    def _stage_id(
        self,
        inspection: Mapping[str, Any],
        source: SourceFingerprint,
        config: Config,
        renders: Mapping[int, RenderedPage],
        tools: Sequence[ToolRecord],
    ) -> str:
        return _json_digest(
            {
                "config_sha256": config.digest,
                "implementation_sha256": _implementation_digest(),
                "inspection_sha256": _json_digest(inspection),
                "rendered_pages": [renders[index].cache_record() for index in sorted(renders)],
                "source_sha256": source.sha256,
                "stage": _STAGE_VERSION,
                "tools": [tool.to_dict() for tool in tools],
            }
        )

    def _build_records(
        self,
        source: Path,
        inspection: Mapping[str, Any],
        fingerprint: SourceFingerprint,
        config: Config,
        inspected_pages: Sequence[Mapping[str, Any]],
        renders: Mapping[int, RenderedPage],
        scanned_adapter: OCRAdapter,
        tools: Sequence[ToolRecord],
        stage_id: str,
        work_path: Path,
    ) -> DecompileResult:
        del inspection
        page_records: list[PageRecord] = []
        page_references: list[PageReference] = []
        page_nodes: list[StructureNode] = []
        evidence_pages: dict[str, Any] = {}
        evidence_store = ArtifactStore(config.work_root / "evidence")
        for index, inspected_page in enumerate(inspected_pages):
            page_box, source_transform = _page_geometry(inspected_page)
            render = renders[index]
            # Durable identity is PDF bytes plus zero-based source page index;
            # render/OCR digests are evidence that may legitimately evolve.
            page_hash = _page_source_hash(fingerprint, index, inspected_page)
            page_id = PageRecord.derive_durable_id(fingerprint.sha256, index)
            route = _route_engine(inspected_page, scanned_adapter.name)
            adapter = self._pdf_text_adapter if route == "pdf-text" else scanned_adapter
            ocr_page = self._recognize(adapter, source, index, page_id, page_box, render)
            evidence_record = _retain_page_evidence(
                evidence_store,
                config,
                adapter,
                page_id,
                render,
                ocr_page,
                route,
            )
            page_evidence_digest = sha256_hex(evidence_record)
            objects = _objects_from_ocr(
                page_id,
                page_box,
                ocr_page,
                tuple(item.sha256 for item in evidence_record.artifacts),
            )
            page_record = PageRecord(
                id=page_id,
                sequence=index + 1,
                source_page_index=index,
                page_box=page_box,
                page_class=str(inspected_page.get("classification", {}).get("label", "ambiguous")),
                source_pdf_to_canonical=source_transform,
                render_pixels_to_canonical=_render_transform(page_box, render),
                source_page_sha256=page_hash,
                objects=objects,
                reading_order=tuple(object_.id for object_ in objects),
                source_pdf_sha256=fingerprint.sha256,
                source_render_sha256=render.content_sha256,
                page_evidence_sha256=page_evidence_digest,
            )
            page_records.append(page_record)
            page_references.append(
                PageReference(page_id, index + 1, f"pages/p{index + 1:06d}.json", index)
            )
            page_node_id = content_id("structure", {"document": fingerprint.sha256, "page": index})
            page_nodes.append(
                StructureNode(
                    page_node_id, "page", region_ids=tuple(object_.id for object_ in objects)
                )
            )
            evidence_pages[page_id] = {
                "adapter": adapter.name,
                "render": render.cache_record(),
                "route": route,
                "evidence_record": evidence_record.to_dict(),
                "page_evidence_sha256": page_evidence_digest,
            }
        source_record = SourceRecord(fingerprint.sha256, fingerprint.byte_size, (source.name,))
        manifest = Manifest.for_source(
            source_record,
            tuple(page_references),
            config.profile,
            config.digest,
            tools=tuple(tools),
            conformance_level="review-required",
            known_limitations=(
                "Phase 1 output retains literal OCR evidence; semantic reconciliation is pending.",
                "Phase 1 output is never replacement-ready.",
            ),
        )
        root_id = content_id("structure", {"document": manifest.document_id, "kind": "document"})
        root = StructureNode(root_id, "document", child_ids=tuple(node.id for node in page_nodes))
        structure = StructureRecord(manifest.document_id, root_id, (root, *page_nodes))
        styles = StylesRecord(manifest.document_id, ())
        return DecompileResult(
            stage_id=stage_id,
            cache_hit=False,
            work_path=work_path,
            manifest=manifest,
            pages=tuple(page_records),
            structure=structure,
            styles=styles,
            ocr_evidence={"pages": evidence_pages},
        )

    @staticmethod
    def _recognize(
        adapter: OCRAdapter,
        source: Path,
        page_index: int,
        page_id: str,
        page_box: Box,
        render: RenderedPage,
    ) -> OCRPage:
        try:
            return adapter.recognize(
                OCRRequest(
                    page_id=page_id,
                    width=page_box.width,
                    height=page_box.height,
                    pdf_path=str(source),
                    pdf_page_number=page_index,
                    image_path=str(render.image_path) if render.image_path else None,
                    image_width_px=render.width_px,
                    image_height_px=render.height_px,
                )
            )
        except OCRUnavailable as error:
            raise DecompileError(
                f"OCR adapter {adapter.name} could not process page {page_index + 1}: {error}"
            ) from error

    @staticmethod
    def _verify_source(source: Path, expected: SourceFingerprint) -> None:
        try:
            verify_source(source, expected, raise_on_change=True)
        except SourceChangedError as error:
            raise DecompileError(
                f"source changed; refusing to use stale evidence: {source}"
            ) from error

    @staticmethod
    def _load_cached(
        work_path: Path, stage_id: str, evidence_store: ArtifactStore
    ) -> DecompileResult | None:
        path = work_path / "work-manifest.json"
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("stage_id") != stage_id or record.get("status") != "complete":
                return None
            lmdoc = record["lmdoc"]
            result = DecompileResult(
                stage_id=stage_id,
                cache_hit=True,
                work_path=work_path,
                manifest=Manifest.from_dict(lmdoc["manifest"]),
                pages=tuple(PageRecord.from_dict(item) for item in lmdoc["pages"]),
                structure=StructureRecord.from_dict(lmdoc["structure"]),
                styles=StylesRecord.from_dict(lmdoc["styles"]),
                ocr_evidence=record["ocr_evidence"],
            )
            _verify_cached_evidence(result, evidence_store)
            return result
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise DecompileError(
                f"cached stage is invalid or lacks retained evidence: {work_path}"
            ) from None

    @staticmethod
    def _write_stage(result: DecompileResult) -> None:
        target = result.work_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # A concurrently completed stage is valid only if it can be loaded
            # by a subsequent run; do not overwrite its evidence.
            raise DecompileError(f"work stage already exists but is not reusable: {target}")
        temporary = Path(tempfile.mkdtemp(prefix=f".{result.stage_id[:12]}-", dir=target.parent))
        try:
            lmdoc_root = temporary / "lmdoc"
            _write_canonical(lmdoc_root / "manifest.json", result.manifest)
            _write_canonical(lmdoc_root / "structure.json", result.structure)
            _write_canonical(lmdoc_root / "styles.json", result.styles)
            for reference, page in zip(result.manifest.pages, result.pages, strict=True):
                _write_canonical(lmdoc_root / reference.path, page)
            evidence_pages = result.ocr_evidence.get("pages")
            if isinstance(evidence_pages, Mapping):
                for page in result.pages:
                    page_evidence = evidence_pages.get(page.id)
                    record = (
                        page_evidence.get("evidence_record")
                        if isinstance(page_evidence, Mapping)
                        else None
                    )
                    if isinstance(record, Mapping):
                        _write_canonical(
                            lmdoc_root / "evidence" / "records" / f"{page.id}.json", record
                        )
            manifest = {
                "lmdoc": {
                    "manifest": result.manifest.to_dict(),
                    "pages": [page.to_dict() for page in result.pages],
                    "structure": result.structure.to_dict(),
                    "styles": result.styles.to_dict(),
                },
                "ocr_evidence": _json_value(result.ocr_evidence),
                "provenance": {
                    "config_sha256": result.manifest.configuration_sha256,
                    "source": result.manifest.source.to_dict(),
                    "tools": [tool.to_dict() for tool in result.manifest.tools],
                },
                "stage_id": result.stage_id,
                "status": "complete",
            }
            (temporary / "work-manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, target)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise


def _inspection_fingerprint(inspection: Mapping[str, Any]) -> SourceFingerprint:
    source = inspection.get("source")
    if not isinstance(source, Mapping):
        raise DecompileError("inspection is missing source fingerprint")
    try:
        return SourceFingerprint.from_mapping(source)
    except ValueError as error:
        raise DecompileError(f"inspection source fingerprint is invalid: {error}") from error


def _render_by_page(rendered: Sequence[RenderedPage], count: int) -> dict[int, RenderedPage]:
    by_page = {item.source_page_index: item for item in rendered}
    if len(by_page) != len(rendered) or set(by_page) != set(range(count)):
        raise DecompileError(
            "renderer must return exactly one artifact for every inspected source page"
        )
    return by_page


def _evidence_json_bytes(value: object) -> bytes:
    """Encode evidence faithfully without imposing canonical-IR float limits."""

    return json.dumps(
        _json_value(value),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _media_type(path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")


def _retain_page_evidence(
    store: ArtifactStore,
    config: Config,
    adapter: OCRAdapter,
    page_id: str,
    render: RenderedPage,
    ocr_page: OCRPage,
    route: str,
) -> EvidenceRecord:
    """Retain exact/raw and normalized evidence before canonical projection.

    Evidence bytes are held in the work-root content-addressed store.  The
    compact LMDOC tree carries only canonical records and a digest-bound
    evidence record, so a Phase 1 package never pretends to embed source scans.
    """

    artifacts: list[Artifact] = [
        store.put_bytes(
            _evidence_json_bytes(render.cache_record()),
            media_type="application/json",
            role="render-evidence",
        ),
        store.put_bytes(
            _evidence_json_bytes(ocr_page.to_dict()),
            media_type="application/json",
            role="normalized-ocr-page",
        ),
    ]
    native = ocr_page.store_native_output(store)
    if native is not None:
        artifacts.append(native)
    if render.image_path is not None:
        try:
            image_bytes = render.image_path.read_bytes()
        except OSError as error:
            raise DecompileError(f"render evidence cannot be read: {render.image_path}") from error
        if sha256(image_bytes).hexdigest() != render.content_sha256:
            raise DecompileError(
                f"render evidence hash mismatch for page {render.source_page_index + 1}"
            )
        artifacts.append(
            store.put_bytes(
                image_bytes,
                media_type=_media_type(render.image_path),
                role="rendered-page-image",
            )
        )
    artifacts.sort(key=lambda item: (item.role, item.sha256))
    identity = {
        "artifacts": [item.sha256 for item in artifacts],
        "page_id": page_id,
        "route": route,
    }
    return EvidenceRecord(
        id=content_id("evidence", identity),
        subject_id=page_id,
        producer=adapter.name,
        producer_version=str(getattr(adapter, "version", "unknown")),
        configuration_sha256=config.digest,
        artifacts=tuple(artifacts),
    )


def _verify_cached_evidence(result: DecompileResult, store: ArtifactStore) -> None:
    """Fail closed when a resumable stage has lost its external proof bytes."""

    pages = result.ocr_evidence.get("pages")
    if not isinstance(pages, Mapping):
        raise DecompileError("cached stage has no page evidence map")
    if set(pages) != {page.id for page in result.pages}:
        raise DecompileError("cached stage evidence pages do not match canonical pages")
    for page in result.pages:
        evidence = pages.get(page.id)
        record = evidence.get("evidence_record") if isinstance(evidence, Mapping) else None
        if not isinstance(record, Mapping):
            raise DecompileError(f"cached stage lacks evidence record for {page.id}")
        if page.page_evidence_sha256 != sha256_hex(dict(record)):
            raise DecompileError(f"cached page evidence digest is stale for {page.id}")
        artifacts_value = record.get("artifacts")
        if not isinstance(artifacts_value, list) or not artifacts_value:
            raise DecompileError(f"cached evidence record has no artifacts for {page.id}")
        artifacts: list[Artifact] = []
        for value in artifacts_value:
            if not isinstance(value, Mapping):
                raise DecompileError(f"cached evidence artifact is malformed for {page.id}")
            artifacts.append(Artifact.from_dict(dict(value)))
        artifact_digests = {artifact.sha256 for artifact in artifacts}
        if any(
            not set(object_.evidence_refs).issubset(artifact_digests) for object_ in page.objects
        ):
            raise DecompileError(f"cached scene evidence references are stale for {page.id}")
        try:
            for artifact in artifacts:
                store.get_bytes(artifact)
        except (ArtifactCorruptError, FileNotFoundError) as error:
            raise DecompileError(
                f"cached evidence artifact is missing or corrupt for {page.id}: {error}"
            ) from error


def _objects_from_ocr(
    page_id: str,
    page_box: Box,
    ocr: OCRPage,
    evidence_refs: tuple[str, ...],
) -> tuple[SceneObject, ...]:
    objects: list[SceneObject] = []
    for region in sorted(ocr.regions, key=lambda item: (item.reading_order, item.id)):
        for line in sorted(region.lines, key=lambda item: (item.reading_order, item.id)):
            object_id = content_id(
                "region", {"line_id": line.id, "page_id": page_id, "region_id": region.id}
            )
            objects.append(
                SceneObject(
                    object_id,
                    "text",
                    _safe_box(line.bbox or region.bbox, page_box),
                    payload={
                        "engine": ocr.engine,
                        "literal_text": line.text,
                        "ocr_line_id": line.id,
                        "ocr_region_id": region.id,
                    },
                    evidence_refs=evidence_refs,
                )
            )
    return tuple(objects)


def _write_canonical(path: Path, record: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(record) + b"\n")
