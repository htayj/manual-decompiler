"""Generate static accessible and paged views without changing canonical IR."""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Literal

from lispmdoc.model import (
    Manifest,
    PageRecord,
    SceneObject,
    StructureNode,
    StructureRecord,
    StylesRecord,
)
from lispmdoc.raster import (
    RASTER_CODECS,
    RASTER_REASON_CODES,
    PixelBox,
    RasterRegion,
    approved_photo_dominant_disposition,
    evaluate_page_raster_policy,
    inspect_encoded_raster,
    validate_raster_mapping,
)

VIEW_FORMAT_VERSION = "lispmdoc-views-3"


class RenderViewsError(ValueError):
    """The canonical records cannot safely produce the requested derived views."""


@dataclass(frozen=True, slots=True)
class ViewTreeResult:
    """Paths and deterministic non-fatal rendering dispositions."""

    root: Path
    html_path: Path
    css_path: Path
    plain_text_path: Path
    svg_paths: tuple[Path, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RenderCapabilities:
    chromium: bool
    resvg: bool
    harfbuzz: bool
    optional_pdf: bool

    @property
    def deterministic_browser_rendering_available(self) -> bool:
        """Tool discovery alone never establishes a pinned-renderer claim."""

        return False

    def contracts(self) -> tuple[str, ...]:
        contracts: list[str] = []
        if not self.chromium:
            contracts.append("Chromium unavailable; browser determinism is not claimed")
        if not self.resvg:
            contracts.append("pinned resvg unavailable; SVG raster determinism is not claimed")
        if not self.harfbuzz:
            contracts.append(
                "HarfBuzz unavailable; required shaping cannot be rendered in replica mode"
            )
        if not self.optional_pdf:
            contracts.append("optional text/vector PDF renderer unavailable")
        if self.chromium and self.resvg:
            contracts.append(
                "renderer tools detected but no pinned-renderer determinism is claimed"
            )
        return tuple(contracts)


def probe_capabilities() -> RenderCapabilities:
    chromium = shutil.which("chromium") is not None or shutil.which("chromium-browser") is not None
    return RenderCapabilities(
        chromium=chromium,
        resvg=shutil.which("resvg") is not None,
        harfbuzz=importlib.util.find_spec("uharfbuzz") is not None,
        optional_pdf=shutil.which("weasyprint") is not None,
    )


def write_view_tree(
    output_root: str | Path,
    manifest: Manifest,
    pages: Sequence[PageRecord],
    structure: StructureRecord,
    styles: StylesRecord,
    *,
    raster_policy: Literal["placeholder", "error"] = "placeholder",
    replica_mode: bool = False,
    raster_assets: Mapping[str, str | Path] | None = None,
    permitted_font_sha256: Sequence[str] = (),
) -> ViewTreeResult:
    """Write deterministic HTML/CSS/SVG views from canonical records.

    No PDF or source raster is read. Raster scene objects are represented by an
    explicit, bounded SVG placeholder by default, or rejected with
    ``raster_policy='error'``. This prevents a derived view from silently
    becoming a full-page scan fallback.
    """

    if raster_policy not in {"placeholder", "error"}:
        raise RenderViewsError("raster_policy must be 'placeholder' or 'error'")
    if replica_mode and raster_policy == "placeholder":
        raise RenderViewsError("replica mode forbids placeholder raster rendering")
    _validate_inputs(manifest, pages, structure, styles, replica_mode=replica_mode)
    root = _safe_root(Path(output_root))
    html_path = _safe_child(root, "text", "document.html")
    plain_text_path = _safe_child(root, "text", "document.txt")
    css_path = _safe_child(root, "styles", "document.css")
    svg_directory = _safe_child(root, "render", "pages")
    expected_svg_names = {f"{page.sequence:04d}.svg" for page in pages}
    _prune_view_directory(svg_directory, expected_svg_names)
    style_css = _css(styles)
    object_locations = _object_locations(pages)
    warnings: list[str] = []
    svg_paths: list[Path] = []
    for page in sorted(pages, key=lambda item: item.sequence):
        svg_path = _safe_child(svg_directory, f"{page.sequence:04d}.svg")
        svg, page_warnings = _svg_page(
            page,
            style_css,
            raster_policy,
            replica_mode=replica_mode,
            raster_assets=raster_assets or {},
            permitted_font_sha256=frozenset(permitted_font_sha256),
            output_root=root,
        )
        _write_bytes(svg_path, svg.encode("utf-8"))
        svg_paths.append(svg_path)
        warnings.extend(page_warnings)
    _prune_asset_directory(root / "assets", _expected_asset_names(pages, raster_assets or {}))
    html = _html_document(manifest, pages, structure, object_locations)
    plain_text = authoritative_plain_text(pages, structure)
    _write_bytes(html_path, html.encode("utf-8"))
    _write_bytes(plain_text_path, plain_text.encode("utf-8"))
    _write_bytes(css_path, style_css.encode("utf-8"))
    return ViewTreeResult(
        root, html_path, css_path, plain_text_path, tuple(svg_paths), tuple(sorted(warnings))
    )


def _validate_inputs(
    manifest: Manifest,
    pages: Sequence[PageRecord],
    structure: StructureRecord,
    styles: StylesRecord,
    *,
    replica_mode: bool = False,
) -> None:
    if structure.document_id != manifest.document_id or styles.document_id != manifest.document_id:
        raise RenderViewsError("manifest, structure, and styles must have the same document ID")
    expected = tuple(
        (reference.id, reference.sequence, reference.source_page_index)
        for reference in manifest.pages
    )
    actual = tuple((page.id, page.sequence, page.source_page_index) for page in pages)
    if actual != expected:
        raise RenderViewsError(
            "page records must match manifest IDs, sequences, and source-page order"
        )
    object_ids = {object_.id for page in pages for object_ in page.objects}
    missing = sorted(
        region_id
        for node in structure.nodes
        for region_id in node.region_ids
        if region_id not in object_ids
    )
    if missing:
        raise RenderViewsError(f"structure regions do not exist in page records: {missing!r}")
    style_ids = {token.id for token in styles.tokens}
    unknown_styles = sorted(
        object_.style_id
        for page in pages
        for object_ in page.objects
        if object_.style_id is not None and object_.style_id not in style_ids
    )
    if unknown_styles:
        raise RenderViewsError(f"scene objects reference missing style tokens: {unknown_styles!r}")
    if replica_mode:
        _validate_replica_text_coverage(pages, structure)


def _validate_replica_text_coverage(
    pages: Sequence[PageRecord], structure: StructureRecord
) -> None:
    """Require every authoritative scene-text object to have one stable textual home.

    A structure node may repeat a scene object's text only when its explicit
    text is identical to the ordered authoritative object text it references.
    That makes the derived semantic views faithful without turning a logical
    node into a second, conflicting transcription source.
    """

    object_text = _object_text(pages)
    references: dict[str, list[str]] = {object_id: [] for object_id in object_text}
    for node in structure.nodes:
        referenced_text = tuple(
            object_text[region_id] for region_id in node.region_ids if region_id in object_text
        )
        for region_id in node.region_ids:
            if region_id in references:
                references[region_id].append(node.id)
        if node.text is not None and referenced_text:
            expected = "\n".join(referenced_text)
            if node.text != expected:
                raise RenderViewsError(
                    "structure text conflicts with referenced authoritative scene text: "
                    f"{node.id}"
                )

    missing = sorted(object_id for object_id, owners in references.items() if not owners)
    repeated = sorted(object_id for object_id, owners in references.items() if len(owners) > 1)
    if missing or repeated:
        details: list[str] = []
        if missing:
            details.append(f"missing={missing!r}")
        if repeated:
            details.append(f"not-exactly-once={repeated!r}")
        raise RenderViewsError(
            "replica structure must cover authoritative scene text exactly once: "
            + "; ".join(details)
        )


def _safe_root(root: Path) -> Path:
    if root.exists() and root.is_symlink():
        raise RenderViewsError(f"view output root must not be a symlink: {root}")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _safe_child(root: Path, *parts: str) -> Path:
    if any(not part or Path(part).is_absolute() or ".." in Path(part).parts for part in parts):
        raise RenderViewsError("view artifact paths must be non-empty relative paths")
    candidate = root.joinpath(*parts)
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError:
        raise RenderViewsError(f"view artifact path escapes output root: {candidate}") from None
    return candidate


def _write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def _prune_view_directory(directory: Path, expected_names: set[str]) -> None:
    """Remove obsolete generated SVGs and reject foreign directory contents."""

    if not directory.exists():
        return
    if not directory.is_dir() or directory.is_symlink():
        raise RenderViewsError(f"view page output must be a real directory: {directory}")
    for child in directory.iterdir():
        if child.is_symlink() or not child.is_file() or child.suffix != ".svg":
            raise RenderViewsError(f"unexpected entry in generated view directory: {child}")
        if child.name not in expected_names:
            child.unlink()


def _expected_asset_names(
    pages: Sequence[PageRecord], raster_assets: Mapping[str, str | Path]
) -> set[str]:
    names: set[str] = set()
    for object_ in (object_ for page in pages for object_ in page.objects):
        asset = object_.payload.get("asset")
        digest = asset.get("sha256") if isinstance(asset, Mapping) else None
        if isinstance(digest, str) and digest in raster_assets:
            codec = asset.get("codec") if isinstance(asset, Mapping) else None
            suffix = (
                RASTER_CODECS[codec][1]
                if isinstance(codec, str) and codec in RASTER_CODECS
                else Path(raster_assets[digest]).suffix.lower()
            )
            names.add(f"{digest}{suffix}")
    return names


def _prune_asset_directory(directory: Path, expected_names: set[str]) -> None:
    if not directory.exists():
        return
    if not directory.is_dir() or directory.is_symlink():
        raise RenderViewsError(f"view asset output must be a real directory: {directory}")
    for child in directory.iterdir():
        if child.is_symlink() or not child.is_file():
            raise RenderViewsError(f"unexpected generated asset entry: {child}")
        if child.name not in expected_names:
            child.unlink()


def _object_locations(pages: Iterable[PageRecord]) -> dict[str, tuple[int, str]]:
    return {
        object_.id: (page.sequence, f"../render/pages/{page.sequence:04d}.svg#{object_.id}")
        for page in pages
        for object_ in page.objects
    }


def _html_document(
    manifest: Manifest,
    pages: Sequence[PageRecord],
    structure: StructureRecord,
    object_locations: Mapping[str, tuple[int, str]],
) -> str:
    nodes = {node.id: node for node in structure.nodes}
    page_links = "".join(
        f'<li><a href="../render/pages/{page.sequence:04d}.svg#page-{page.id}">'
        f"Page {page.sequence}</a></li>"
        for page in sorted(pages, key=lambda item: item.sequence)
    )
    object_text = _object_text(pages)
    body = _html_node(nodes[structure.root_id], nodes, object_locations, object_text)
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            f"  <title>{escape(manifest.document_id)}</title>",
            '  <link rel="stylesheet" href="../styles/document.css">',
            "</head>",
            "<body>",
            f'  <nav aria-label="Pages"><ol>{page_links}</ol></nav>',
            body,
            "</body>",
            "</html>",
            "",
        )
    )


def authoritative_plain_text(pages: Sequence[PageRecord], structure: StructureRecord) -> str:
    """Derive diplomatic text only from canonical structure/object evidence."""
    nodes = {node.id: node for node in structure.nodes}
    object_text = _object_text(pages)
    chunks: list[str] = []

    def visit(node: StructureNode) -> None:
        chunks.extend(_authoritative_node_chunks(node, object_text))
        for child in node.child_ids:
            visit(nodes[child])

    visit(nodes[structure.root_id])
    return "\n".join(chunks) + ("\n" if chunks else "")


def _html_node(
    node: StructureNode,
    nodes: Mapping[str, StructureNode],
    object_locations: Mapping[str, tuple[int, str]],
    object_text: Mapping[str, str],
) -> str:
    tag = _semantic_tag(node)
    attributes = _node_attributes(node)
    child_html = "".join(
        _html_node(nodes[child], nodes, object_locations, object_text) for child in node.child_ids
    )
    if node.text is not None:
        authoritative = (
            f'<span class="lmdoc-authoritative-text">{escape(node.text)}</span>'
        )
        source_links = "".join(
            f'<a class="lmdoc-source-region" '
            f'href="{escape(object_locations[region][1], quote=True)}" '
            f'aria-label="Source region {escape(region, quote=True)}"></a>'
            for region in node.region_ids
        )
    else:
        authoritative = "".join(
            f'<a class="lmdoc-source-region lmdoc-authoritative-text" '
            f'href="{escape(object_locations[region][1], quote=True)}">'
            f"{escape(object_text[region])}</a>"
            for region in node.region_ids
            if region in object_text
        )
        source_links = "".join(
            f'<a class="lmdoc-source-region" '
            f'href="{escape(object_locations[region][1], quote=True)}" '
            f'aria-label="Source region {escape(region, quote=True)}"></a>'
            for region in node.region_ids
            if region not in object_text
        )
    content = f"{authoritative}{source_links}{child_html}"
    return f"<{tag}{attributes}>{content}</{tag}>"


def _object_text(pages: Sequence[PageRecord]) -> dict[str, str]:
    values: dict[str, str] = {}
    for page in pages:
        for object_ in page.objects:
            value = _literal_text(object_)
            if value is not None:
                values[object_.id] = value
    return values


def _authoritative_node_chunks(
    node: StructureNode, object_text: Mapping[str, str]
) -> tuple[str, ...]:
    if node.text is not None:
        return (node.text,)
    return tuple(object_text[region] for region in node.region_ids if region in object_text)


def _semantic_tag(node: StructureNode) -> str:
    if node.kind == "document":
        return "main"
    if node.kind == "heading":
        level = node.properties.get("level", 2)
        if isinstance(level, int) and not isinstance(level, bool) and 1 <= level <= 6:
            return f"h{level}"
        return "h2"
    return {
        "paragraph": "p",
        "list": "ol" if node.properties.get("ordered") is True else "ul",
        "list-item": "li",
        "table": "table",
        "table-row": "tr",
        "table-header": "th",
        "table-cell": "td",
        "figure": "figure",
        "caption": "figcaption",
        "code": "pre",
        "terminal": "pre",
        "math": "span",
        "cross-reference": "a",
        "note": "aside",
        "section": "section",
        "chapter": "section",
    }.get(node.kind, "section")


def _node_attributes(node: StructureNode) -> str:
    regions = " ".join(node.region_ids)
    attrs = [
        f'id="{node.id}"',
        f'data-canonical-id="{node.id}"',
        f'data-kind="{escape(node.kind, quote=True)}"',
    ]
    if regions:
        attrs.append(f'data-region-ids="{regions}"')
    if node.kind == "figure":
        attrs.append('role="group"')
    if node.kind == "math":
        attrs.append('data-math-fallback="diplomatic visual"')
    if node.kind == "cross-reference":
        href = node.properties.get("href")
        if isinstance(href, str) and href.startswith("#"):
            attrs.append(f'href="{escape(href, quote=True)}"')
    return " " + " ".join(attrs)


def _css(styles: StylesRecord) -> str:
    lines = [
        ":root { color-scheme: light; }",
        "body { margin: 2rem auto; max-width: 76ch; padding: 0 1rem; }",
        "main { display: block; }",
        ".lmdoc-source-region { margin-left: 0.4em; font-size: 0.8em; }",
        ".lmdoc-object { vector-effect: non-scaling-stroke; }",
        ".lmdoc-placeholder { fill: #f3f3f3; stroke: #900; stroke-width: 1000; }",
        ".lmdoc-placeholder-label { fill: #900; font-family: sans-serif; font-size: 9000px; }",
    ]
    for token in sorted(styles.tokens, key=lambda item: item.id):
        declarations = [
            f'font-family: "{_css_string(token.family)}"',
            f"font-size: {_point_value(token.size)}",
            f"font-style: {token.slant}",
            f"font-weight: {token.weight}",
            f"color: {token.color.lower()}",
            f"letter-spacing: {_point_value(token.tracking)}",
        ]
        if token.leading is not None:
            declarations.append(f"line-height: {_point_value(token.leading)}")
        lines.append(f".lmdoc-style-{token.id} {{ {'; '.join(declarations)}; }}")
    return "\n".join(lines) + "\n"


def _css_string(value: str) -> str:
    """Emit a CSS string without allowing a family name to terminate a rule."""

    safe: list[str] = []
    for character in value:
        if character.isascii() and (character.isalnum() or character in " -_"):
            safe.append(character)
        else:
            safe.append(f"\\{ord(character):x} ")
    return "".join(safe)


def _point_value(micropoints: int) -> str:
    sign = "-" if micropoints < 0 else ""
    absolute = abs(micropoints)
    whole, fractional = divmod(absolute, 1000)
    return f"{sign}{whole}pt" if fractional == 0 else f"{sign}{whole}.{fractional:03d}pt"


def _svg_page(
    page: PageRecord,
    style_css: str,
    raster_policy: Literal["placeholder", "error"],
    *,
    replica_mode: bool,
    raster_assets: Mapping[str, str | Path],
    permitted_font_sha256: frozenset[str],
    output_root: Path,
) -> tuple[str, list[str]]:
    if replica_mode:
        _validate_replica_page_rasters(page)
    warnings: list[str] = []
    objects: list[str] = []
    by_id = {object_.id: object_ for object_ in page.objects}
    reading_index = {object_id: index for index, object_id in enumerate(page.reading_order)}
    ordered = sorted(
        by_id.values(),
        key=lambda item: (item.z_index, reading_index.get(item.id, len(reading_index)), item.id),
    )
    clip_ids = {object_.id for object_ in ordered if object_.kind == "clip-path"}
    invalid_clip_refs: list[str] = []
    for object_ in ordered:
        reference = object_.payload.get("clip_path_id")
        if object_.kind != "clip-path" and (
            reference is not None and (not isinstance(reference, str) or reference not in clip_ids)
        ):
            invalid_clip_refs.append(object_.id)
    if invalid_clip_refs:
        raise RenderViewsError(f"scene objects reference missing clip paths: {invalid_clip_refs!r}")
    clip_definitions = _clip_definitions(ordered)
    for object_ in ordered:
        if object_.kind == "clip-path":
            continue
        markup, warning = _svg_object(
            object_,
            raster_policy,
            replica_mode=replica_mode,
            page=page,
            raster_assets=raster_assets,
            permitted_font_sha256=permitted_font_sha256,
            output_root=output_root,
        )
        objects.append(markup)
        if warning is not None:
            warnings.append(f"page {page.sequence}, object {object_.id}: {warning}")
    box = page.page_box
    width, height = box.width, box.height
    return (
        "\n".join(
            (
                '<?xml version="1.0" encoding="UTF-8"?>',
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
                f'width="{_point_value(width)}" height="{_point_value(height)}" role="img" '
                f'aria-labelledby="page-{page.id}-title">',
                f'<title id="page-{page.id}-title">Page {page.sequence}</title>',
                f"<style>{style_css}</style>",
                *clip_definitions,
                f'<g id="page-{page.id}" data-canonical-id="{page.id}">',
                *objects,
                "</g>",
                "</svg>",
                "",
            )
        ),
        warnings,
    )


def _validate_replica_page_rasters(page: PageRecord) -> None:
    raster_objects = tuple(object_ for object_ in page.objects if object_.kind == "raster")
    if not raster_objects:
        return
    regions: list[RasterRegion] = []
    for object_ in raster_objects:
        reason = object_.payload.get("reason")
        if reason not in RASTER_REASON_CODES:
            raise RenderViewsError(
                f"replica raster requires an allowed payload.reason: {object_.id}"
            )
        box = object_.box
        regions.append(
            RasterRegion(
                object_.id,
                PixelBox(
                    box.x0 - page.page_box.x0,
                    box.y0 - page.page_box.y0,
                    box.width,
                    box.height,
                ),
                "continuous-tone",
                "continuous-tone-photo",
                "0" * 64,
            )
        )
    approved = all(
        approved_photo_dominant_disposition(object_.payload) for object_ in raster_objects
    )
    decision = evaluate_page_raster_policy(
        page.page_box.width,
        page.page_box.height,
        tuple(regions),
        manual_approval_id="aggregate-approved" if approved else None,
        explicitly_photo_dominant=approved,
        contains_meaningful_text_or_vector=not approved,
    )
    if not decision.replica_ready:
        raise RenderViewsError(
            "replica raster aggregate violates Wave-8 policy: "
            + ", ".join(decision.findings)
        )


def _svg_object(
    object_: SceneObject,
    raster_policy: Literal["placeholder", "error"],
    *,
    replica_mode: bool,
    page: PageRecord,
    raster_assets: Mapping[str, str | Path],
    permitted_font_sha256: frozenset[str],
    output_root: Path,
) -> tuple[str, str | None]:
    classes = "lmdoc-object" + (f" lmdoc-style-{object_.style_id}" if object_.style_id else "")
    transform = object_.payload.get("transform")
    transform_attribute = (
        f' transform="{escape(transform, quote=True)}"' if isinstance(transform, str) else ""
    )
    opacity = (
        f' opacity="{object_.opacity_milli / 1000:.3f}"' if object_.opacity_milli != 1000 else ""
    )
    font_sha256 = object_.payload.get("font_sha256")
    if font_sha256 is not None and (
        not isinstance(font_sha256, str) or not _is_sha256(font_sha256)
    ):
        raise RenderViewsError(f"text font reference must be a SHA-256 string: {object_.id}")
    if replica_mode and isinstance(font_sha256, str) and font_sha256 not in permitted_font_sha256:
        raise RenderViewsError(f"text font reference is not permitted: {object_.id}")
    font_attribute = (
        f' data-font-sha256="{escape(font_sha256, quote=True)}"'
        if isinstance(font_sha256, str)
        else ""
    )
    clip_path_id = object_.payload.get("clip_path_id")
    clip_attribute = (
        f' clip-path="url(#{escape(clip_path_id, quote=True)})"'
        if isinstance(clip_path_id, str)
        else ""
    )
    start = (
        f'<g id="{object_.id}" data-canonical-id="{object_.id}" class="{classes}"'
        f"{transform_attribute}{clip_attribute}{font_attribute}{opacity}>"
    )
    end = "</g>"
    box = object_.box
    if object_.kind in {"text", "text-block", "line", "span", "token", "glyph"}:
        text = _literal_text(object_)
        if text is None:
            return _unsupported(start, end, box, "text unavailable", replica_mode)
        if object_.payload.get("requires_shaping") is True and not probe_capabilities().harfbuzz:
            if replica_mode:
                raise RenderViewsError(f"text requires unavailable shaping: {object_.id}")
            return _placeholder(
                start, end, box, "unshaped text"
            ), "text requires unavailable shaping"
        if (
            replica_mode
            and object_.payload.get("requires_font_reference") is True
            and not isinstance(font_sha256, str)
        ):
            raise RenderViewsError(f"text font reference is not permitted: {object_.id}")
        glyphs = object_.payload.get("glyph_positions")
        if (
            replica_mode
            and object_.payload.get("requires_shaping") is True
            and not isinstance(glyphs, list)
        ):
            raise RenderViewsError(f"required shaping lacks positioned glyphs: {object_.id}")
        if isinstance(glyphs, list):
            positioned = _positioned_glyphs(glyphs)
            if positioned is None:
                return _unsupported(start, end, box, "invalid glyph positions", replica_mode)
            return f"{start}<text>{positioned}</text>{end}", None
        return f'{start}<text x="{box.x0}" y="{box.y1}">{escape(text)}</text>{end}', None
    if object_.kind == "rule":
        x1 = _integer_payload(object_, "x1", box.x0)
        y1 = _integer_payload(object_, "y1", box.y0)
        x2 = _integer_payload(object_, "x2", box.x1)
        y2 = _integer_payload(object_, "y2", box.y0)
        if None not in (x1, y1, x2, y2):
            return f'{start}<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" />{end}', None
        return _unsupported(start, end, box, "unsupported rule", replica_mode)
    if object_.kind == "shape":
        shape = object_.payload.get("shape", "rect")
        if shape == "rect":
            return _rect(start, end, box), None
        if shape == "ellipse":
            return (
                f'{start}<ellipse cx="{(box.x0 + box.x1) // 2}" cy="{(box.y0 + box.y1) // 2}" '
                f'rx="{box.width // 2}" ry="{box.height // 2}" />{end}',
                None,
            )
        return _unsupported(start, end, box, "unsupported shape", replica_mode)
    if object_.kind == "path":
        path_data = object_.payload.get("d")
        if isinstance(path_data, str) and path_data:
            return f'{start}<path d="{escape(path_data, quote=True)}" />{end}', None
        return _unsupported(start, end, box, "path unavailable", replica_mode)
    if object_.kind == "raster":
        return _raster_object(
            object_, start, end, raster_policy, replica_mode, page, raster_assets, output_root
        )
    if object_.kind in {"group", "link", "annotation"}:
        return _unsupported(start, end, box, f"unsupported {object_.kind}", replica_mode)
    return _unsupported(start, end, box, "unsupported object", replica_mode)


def _clip_definitions(objects: Sequence[SceneObject]) -> tuple[str, ...]:
    """Render explicit rectangular clip paths before z-ordered paint objects."""

    definitions: list[str] = []
    for object_ in objects:
        if object_.kind != "clip-path":
            continue
        if object_.payload.get("shape", "rect") != "rect":
            raise RenderViewsError(f"clip path must be rectangular: {object_.id}")
        box = object_.box
        definitions.append(
            f'<defs><clipPath id="{object_.id}"><rect x="{box.x0}" y="{box.y0}" '
            f'width="{box.width}" height="{box.height}" /></clipPath></defs>'
        )
    return tuple(definitions)


def _literal_text(object_: SceneObject) -> str | None:
    text = object_.payload.get("literal_text", object_.payload.get("text"))
    return text if isinstance(text, str) else None


def _positioned_glyphs(glyphs: list[object]) -> str | None:
    spans: list[str] = []
    for item in glyphs:
        if not isinstance(item, Mapping):
            return None
        text, x, y = item.get("text"), item.get("x"), item.get("y")
        if (
            not isinstance(text, str)
            or isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, int)
            or not isinstance(y, int)
        ):
            return None
        spans.append(f'<tspan x="{x}" y="{y}">{escape(text)}</tspan>')
    return "".join(spans)


def _unsupported(
    start: str, end: str, box: Any, message: str, replica_mode: bool
) -> tuple[str, str | None]:
    if replica_mode:
        raise RenderViewsError(message)
    return _placeholder(start, end, box, message), message


def _raster_object(
    object_: SceneObject,
    start: str,
    end: str,
    raster_policy: Literal["placeholder", "error"],
    replica_mode: bool,
    page: PageRecord,
    raster_assets: Mapping[str, str | Path],
    output_root: Path,
) -> tuple[str, str | None]:
    asset = object_.payload.get("asset")
    digest = asset.get("sha256") if isinstance(asset, Mapping) else None
    if isinstance(digest, str) and _is_sha256(digest) and digest in raster_assets:
        source = Path(raster_assets[digest])
        if not source.is_file():
            raise RenderViewsError(f"raster asset is missing or hash-mismatched: {digest}")
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise RenderViewsError(f"raster asset is missing or hash-mismatched: {digest}")
        try:
            info = inspect_encoded_raster(data)
            validate_raster_mapping(object_.payload, info)
        except ValueError as error:
            raise RenderViewsError(f"invalid raster asset {object_.id}: {error}") from error
        suffix = info.suffix
        target = _safe_child(output_root, "assets", f"{digest}{suffix}")
        _write_bytes(target, data)
        box = object_.box
        href = f"../../assets/{digest}{suffix}"
        alt = object_.payload.get("alt_text")
        if replica_mode and (not isinstance(alt, str) or not alt.strip()):
            raise RenderViewsError(
                f"replica raster requires non-empty alternative text: {object_.id}"
            )
        alt_attribute = f' aria-label="{escape(alt, quote=True)}"' if isinstance(alt, str) else ""
        return (
            f'{start}<image href="{href}" x="{box.x0}" y="{box.y0}" '
            f'width="{box.width}" height="{box.height}"{alt_attribute} />{end}',
            None,
        )
    message = "raster asset omitted"
    if raster_policy == "error" or replica_mode:
        raise RenderViewsError(f"raster object cannot be represented in page SVG: {object_.id}")
    return _placeholder(start, end, object_.box, message), message


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


def _integer_payload(object_: SceneObject, name: str, default: int) -> int | None:
    value = object_.payload.get(name, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _rect(start: str, end: str, box: Any) -> str:
    return (
        f'{start}<rect x="{box.x0}" y="{box.y0}" width="{box.width}" height="{box.height}" />{end}'
    )


def _placeholder(start: str, end: str, box: Any, message: str) -> str:
    return (
        f'{start}<rect class="lmdoc-placeholder" x="{box.x0}" y="{box.y0}" '
        f'width="{box.width}" height="{box.height}" />'
        f'<text class="lmdoc-placeholder-label" x="{box.x0}" y="{box.y1}">'
        f"{escape(message)}</text>{end}"
    )
