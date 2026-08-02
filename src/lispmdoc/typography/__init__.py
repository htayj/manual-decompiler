"""Font provenance, policy, measurement, and capability contracts."""

from .engine import (
    TypographyCapabilities,
    choose_substitute,
    distributable_font,
    distributable_subset,
    probe_capabilities,
    rank_substitutes,
)
from .metrics import TypographyMetrics, measure_typography
from .native import (
    NativeLayoutConfig,
    NativeRegionLayout,
    PhysicalTextStyle,
    compose_native_svg,
    layout_native_region,
)
from .profiles import (
    CHINUAL_4E_LAYOUT,
    CHINUAL_MONO_FONT,
    CHINUAL_SANS_FONT,
    CHINUAL_SERIF_FONT,
    chinual_physical_style,
)
from .types import (
    FontResource,
    FontSubsetPlan,
    PdfFontInventory,
    ScanTypographyInference,
    SubstituteCandidate,
    UnresolvedGlyph,
)

__all__ = [
    "FontResource",
    "CHINUAL_4E_LAYOUT",
    "CHINUAL_MONO_FONT",
    "CHINUAL_SANS_FONT",
    "CHINUAL_SERIF_FONT",
    "FontSubsetPlan",
    "PdfFontInventory",
    "PhysicalTextStyle",
    "ScanTypographyInference",
    "SubstituteCandidate",
    "TypographyCapabilities",
    "TypographyMetrics",
    "NativeLayoutConfig",
    "NativeRegionLayout",
    "UnresolvedGlyph",
    "choose_substitute",
    "chinual_physical_style",
    "compose_native_svg",
    "distributable_font",
    "distributable_subset",
    "measure_typography",
    "layout_native_region",
    "probe_capabilities",
    "rank_substitutes",
]
