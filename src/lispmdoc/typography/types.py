"""Typed provenance and inference contracts for font reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


def _digest(value: str, name: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lower-case SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class FontResource:
    sha256: str
    family: str
    postscript_name: str | None
    source: Literal["pdf-embedded", "substitute", "vector-glyph", "system"]
    format: Literal["otf", "ttf", "woff2", "type1", "type3", "unknown"]
    variation_axes: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        _digest(self.sha256, "font sha256")
        if not self.family:
            raise ValueError("font family is required")
        if tuple(sorted(self.variation_axes)) != self.variation_axes:
            raise ValueError("font variation axes must be sorted")


@dataclass(frozen=True, slots=True)
class PdfFontInventory:
    """Input contract for evidence extracted from one PDF font dictionary."""

    resource: FontResource
    object_reference: str
    subtype: str
    encoding: str | None
    program_sha256: str | None
    to_unicode_sha256: str | None
    widths_sha256: str | None
    type3_procedures_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.object_reference or not self.subtype:
            raise ValueError("PDF font object reference and subtype are required")
        for name in (
            "program_sha256",
            "to_unicode_sha256",
            "widths_sha256",
            "type3_procedures_sha256",
        ):
            value = getattr(self, name)
            if value is not None:
                _digest(value, name)
        if self.subtype == "Type3" and self.type3_procedures_sha256 is None:
            raise ValueError("Type3 inventory requires procedure evidence")


@dataclass(frozen=True, slots=True)
class ScanTypographyInference:
    physical_region_id: str
    family_hint: str | None
    size_micropoints: int
    weight: int
    slant: Literal["normal", "italic", "oblique", "unknown"]
    leading_micropoints: int | None
    tracking_micropoints: int
    baseline_micropoints: int
    confidence_milli: int

    def __post_init__(self) -> None:
        if (
            not self.physical_region_id
            or self.size_micropoints <= 0
            or self.baseline_micropoints < 0
        ):
            raise ValueError("scan typography needs region, positive size, and baseline")
        if not 1 <= self.weight <= 1000 or not 0 <= self.confidence_milli <= 1000:
            raise ValueError("invalid scan typography weight/confidence")


@dataclass(frozen=True, slots=True)
class SubstituteCandidate:
    resource: FontResource
    line_width_error_milli: int
    baseline_error_micropoints: int
    glyph_coverage_milli: int

    def __post_init__(self) -> None:
        if self.line_width_error_milli < 0 or self.baseline_error_micropoints < 0:
            raise ValueError("substitute errors cannot be negative")
        if not 0 <= self.glyph_coverage_milli <= 1000:
            raise ValueError("glyph coverage must be 0..1000")


@dataclass(frozen=True, slots=True)
class FontSubsetPlan:
    resource: FontResource
    codepoints: tuple[int, ...]
    output_format: Literal["woff2", "woff", "otf"]

    def __post_init__(self) -> None:
        if not self.codepoints or tuple(sorted(set(self.codepoints))) != self.codepoints:
            raise ValueError("subset codepoints must be sorted and unique")
        if any(point < 0 or point > 0x10FFFF for point in self.codepoints):
            raise ValueError("subset codepoint is outside Unicode")


@dataclass(frozen=True, slots=True)
class UnresolvedGlyph:
    physical_region_id: str
    glyph_code: str
    reason: Literal[
        "missing-tounicode", "missing-substitute", "unknown-encoding", "license-blocked"
    ]
    visual_fallback_required: bool = True
