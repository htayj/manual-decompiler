"""Measured document typography profiles built on the generic native shaper."""

from __future__ import annotations

from lispmdoc.benchmark import SemanticTextSpan

from .native import NativeLayoutConfig, PhysicalTextStyle

CHINUAL_SERIF_FONT = "Liberation Serif"
CHINUAL_SANS_FONT = "Liberation Sans"
CHINUAL_MONO_FONT = "Liberation Mono"

CHINUAL_4E_LAYOUT = NativeLayoutConfig(
    default_style=PhysicalTextStyle(CHINUAL_SERIF_FONT),
    semantic_styles={
        "body": PhysicalTextStyle(CHINUAL_SERIF_FONT),
        "display-code": PhysicalTextStyle(CHINUAL_MONO_FONT),
        "font-2-italic": PhysicalTextStyle(CHINUAL_SERIF_FONT, "italic"),
        "font-3-inline-lisp": PhysicalTextStyle(CHINUAL_SANS_FONT),
        "definition-name": PhysicalTextStyle(CHINUAL_MONO_FONT, weight=700),
        "definition-argument": PhysicalTextStyle(CHINUAL_SERIF_FONT, "italic"),
        "definition-label": PhysicalTextStyle(CHINUAL_SERIF_FONT, "italic"),
        "list-item-label": PhysicalTextStyle(CHINUAL_MONO_FONT, weight=700),
        "section-title": PhysicalTextStyle(CHINUAL_SERIF_FONT, weight=700),
    },
    block_styles={"code": PhysicalTextStyle(CHINUAL_MONO_FONT)},
    base_font_sizes={"body": 44.0, "code": 40.0, "function": 46.0, "section": 53.0},
    line_heights={"body": 1.20, "code": 1.18, "function": 1.16, "section": 1.14},
    optical_left_inset_factor=0.13,
)


def chinual_physical_style(style: str, kind: str) -> tuple[str, str, int]:
    physical = CHINUAL_4E_LAYOUT.style_for(SemanticTextSpan("", style), kind)
    return physical.family, physical.slant, physical.weight


__all__ = [
    "CHINUAL_4E_LAYOUT",
    "CHINUAL_MONO_FONT",
    "CHINUAL_SANS_FONT",
    "CHINUAL_SERIF_FONT",
    "chinual_physical_style",
]
