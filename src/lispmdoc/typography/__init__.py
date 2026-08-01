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
    "FontSubsetPlan",
    "PdfFontInventory",
    "ScanTypographyInference",
    "SubstituteCandidate",
    "TypographyCapabilities",
    "TypographyMetrics",
    "UnresolvedGlyph",
    "choose_substitute",
    "distributable_font",
    "distributable_subset",
    "measure_typography",
    "probe_capabilities",
    "rank_substitutes",
]
