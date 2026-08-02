"""Deterministic native shaping of semantic spans into vector SVG fragments."""

from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lispmdoc.benchmark.bolio_formatting import SemanticTextSpan


@dataclass(frozen=True, slots=True)
class PhysicalTextStyle:
    family: str
    slant: str = "normal"
    weight: int = 400

    def __post_init__(self) -> None:
        if not self.family or self.slant not in {"normal", "italic", "oblique"}:
            raise ValueError("physical text style needs a family and supported slant")
        if not 1 <= self.weight <= 1000:
            raise ValueError("physical text weight must be 1..1000")


@dataclass(frozen=True, slots=True)
class NativeLayoutConfig:
    default_style: PhysicalTextStyle
    semantic_styles: Mapping[str, PhysicalTextStyle]
    block_styles: Mapping[str, PhysicalTextStyle]
    base_font_sizes: Mapping[str, float]
    line_heights: Mapping[str, float]
    foreground: str = "#17201d"
    optical_left_inset_factor: float = 0.13
    paragraph_indent: float = 100.0
    minimum_font_size: float = 34.0

    def style_for(self, span: SemanticTextSpan, kind: str) -> PhysicalTextStyle:
        style = self.semantic_styles.get(span.style)
        if style is None:
            style = self.block_styles.get(kind, self.default_style)
        if span.bold and style.weight < 700:
            return PhysicalTextStyle(style.family, style.slant, 700)
        return style


@dataclass(frozen=True, slots=True)
class NativeRegionLayout:
    svg_inner: str
    origin: tuple[float, float]
    font_size: float
    line_height: float
    line_count: int
    generated_bbox: tuple[float, float, float, float]
    spans: tuple[SemanticTextSpan, ...]
    font_runs: tuple[dict[str, object], ...]


def _markup(kind: str, spans: Sequence[SemanticTextSpan], config: NativeLayoutConfig) -> str:
    parts: list[str] = []
    for span in spans:
        style = config.style_for(span, kind)
        parts.append(
            f'<span font_family="{html.escape(style.family)}" style="{style.slant}" '
            f'weight="{style.weight}">{html.escape(span.text)}</span>'
        )
    return "".join(parts)


def _shape(
    *,
    kind: str,
    spans: Sequence[SemanticTextSpan],
    width: float,
    size: float,
    indent: float,
    config: NativeLayoutConfig,
) -> tuple[str, dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix="lispmdoc-pango-") as temporary:
        temporary_path = Path(temporary)
        source = temporary_path / "region.pango"
        svg = temporary_path / "region.svg"
        serialized = temporary_path / "layout.json"
        source.write_text(_markup(kind, spans, config), encoding="utf-8")
        command = [
            "pango-view",
            "--no-display",
            "--pixels",
            "--markup",
            "--background=transparent",
            f"--foreground={config.foreground}",
            f"--font={config.default_style.family} {size}",
            f"--width={round(width)}",
            "--wrap=word-char",
            f"--indent={round(indent)}",
            f"--line-spacing={config.line_heights[kind]}",
            "--margin=0",
            f"--output={svg}",
            f"--serialize-to={serialized}",
            str(source),
        ]
        subprocess.run(command, check=True, capture_output=True)
        svg_text = svg.read_text(encoding="utf-8")
        root_match = re.search(r"<svg\b[^>]*>(.*)</svg>\s*$", svg_text, flags=re.DOTALL)
        if root_match is None:
            raise ValueError("Pango did not emit an SVG document")
        inner = root_match.group(1)
        if "<image" in inner:
            raise ValueError("native text shaping unexpectedly emitted a raster image")
        shaped = json.loads(serialized.read_text(encoding="utf-8"))
        if not isinstance(shaped, dict):
            raise ValueError("Pango serialization is not an object")
        return inner, shaped


def layout_native_region(
    *,
    kind: str,
    spans: Sequence[SemanticTextSpan],
    bbox: tuple[int, int, int, int],
    first_line_indent: bool,
    config: NativeLayoutConfig,
) -> NativeRegionLayout:
    """Fit a single native shaped flow into scan-derived geometry."""

    if kind not in config.base_font_sizes or kind not in config.line_heights:
        raise ValueError(f"layout configuration does not support block kind {kind!r}")
    if not spans:
        raise ValueError("cannot shape an empty semantic span sequence")
    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        raise ValueError("native layout requires a positive scan-derived bounding box")
    size = config.base_font_sizes[kind]
    indent = config.paragraph_indent if first_line_indent else 0.0
    svg_inner: str | None = None
    shaped: dict[str, object] | None = None
    for _ in range(24):
        optical_inset = size * config.optical_left_inset_factor
        candidate_svg, candidate = _shape(
            kind=kind,
            spans=spans,
            width=x2 - x1 - optical_inset,
            size=size,
            indent=indent,
            config=config,
        )
        output = candidate.get("output")
        if not isinstance(output, dict):
            raise ValueError("Pango serialization lacks output metrics")
        width_fits = kind != "code" or not bool(output["is-wrapped"])
        logical_height = float(output["height"]) / 1024
        if width_fits and logical_height <= y2 - y1:
            svg_inner, shaped = candidate_svg, candidate
            break
        if size <= config.minimum_font_size:
            raise ValueError("native shaped layout does not fit its scan-derived region")
        size -= 1.0
    if svg_inner is None or shaped is None:
        raise ValueError("could not fit semantic spans into the review region")
    output = shaped["output"]
    assert isinstance(output, dict)
    origin = (x1 + size * config.optical_left_inset_factor, float(y1))
    generated = (
        origin[0],
        origin[1],
        origin[0] + float(output["width"]) / 1024,
        origin[1] + float(output["height"]) / 1024,
    )
    # Pango's integer-pixel width option can round the usable width by less
    # than half a pixel after the optical inset has been applied.
    if generated[2] > x2 + 0.5 or generated[3] > y2 + 0.5:
        raise ValueError(
            "native ink extents exceed the scan-derived region: "
            f"generated={generated!r}, region={bbox!r}"
        )
    lines = output.get("lines")
    if not isinstance(lines, list):
        raise ValueError("Pango serialization lacks line records")
    font_runs = tuple(
        run["font"]
        for line in lines
        if isinstance(line, dict) and isinstance(line.get("runs"), list)
        for run in line["runs"]
        if isinstance(run, dict) and isinstance(run.get("font"), dict)
    )
    return NativeRegionLayout(
        svg_inner,
        origin,
        size,
        size * config.line_heights[kind],
        len(lines),
        generated,
        tuple(spans),
        font_runs,
    )


def compose_native_svg(
    layouts: Sequence[NativeRegionLayout], *, width: int, height: int, background: str
) -> bytes:
    content = [
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="{html.escape(background)}"/>',
    ]
    for index, layout in enumerate(layouts):
        prefix = f"region-{index}-"
        inner = re.sub(r'id="([^"]+)"', rf'id="{prefix}\1"', layout.svg_inner)
        inner = re.sub(r'(?:xlink:href|href)="#([^"]+)"', rf'xlink:href="#{prefix}\1"', inner)
        inner = re.sub(r"url\(#([^)]+)\)", rf"url(#{prefix}\1)", inner)
        content.append(
            f'<g transform="translate({layout.origin[0]:.3f} {layout.origin[1]:.3f})">{inner}</g>'
        )
    content.append("</svg>")
    return ("\n".join(content) + "\n").encode("utf-8")


__all__ = [
    "NativeLayoutConfig",
    "NativeRegionLayout",
    "PhysicalTextStyle",
    "compose_native_svg",
    "layout_native_region",
]
