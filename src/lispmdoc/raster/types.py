"""Deterministic bitmap, crop, codec, and raster-policy contracts."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Literal

RasterClass = Literal["continuous-tone", "halftone", "texture"]
RasterReasonCode = Literal[
    "continuous-tone-photo",
    "halftone-preservation",
    "texture-preservation",
    "trace-error-exceeded",
    "raster-smaller-at-error-bound",
    "manual-reviewed-exception",
]
RASTER_REASON_CODES: frozenset[str] = frozenset(
    {
        "continuous-tone-photo",
        "halftone-preservation",
        "texture-preservation",
        "trace-error-exceeded",
        "raster-smaller-at-error-bound",
        "manual-reviewed-exception",
    }
)


@dataclass(frozen=True, slots=True)
class PixelBox:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0 or self.width < 1 or self.height < 1:
            raise ValueError("pixel box coordinates must be non-negative and dimensions positive")

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class RasterBitmap:
    width: int
    height: int
    channels: Literal[1, 3, 4]
    data: bytes

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("bitmap dimensions must be positive")
        if len(self.data) != self.width * self.height * self.channels:
            raise ValueError("bitmap byte length does not match dimensions and channels")

    @property
    def sha256(self) -> str:
        header = f"{self.width}:{self.height}:{self.channels}:".encode()
        return sha256(header + self.data).hexdigest()


@dataclass(frozen=True, slots=True)
class RasterCrop:
    source_box: PixelBox
    bitmap: RasterBitmap
    content_sha256: str

    def __post_init__(self) -> None:
        if (
            self.source_box.width != self.bitmap.width
            or self.source_box.height != self.bitmap.height
        ):
            raise ValueError("crop box and bitmap dimensions disagree")
        if self.content_sha256 != self.bitmap.sha256:
            raise ValueError("crop hash does not match bitmap content")


@dataclass(frozen=True, slots=True)
class RasterRegion:
    id: str
    box: PixelBox
    raster_class: RasterClass
    reason_code: RasterReasonCode
    crop_sha256: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("raster region id is required")
        if self.raster_class not in {"continuous-tone", "halftone", "texture"}:
            raise ValueError("unknown raster region class")
        if self.reason_code not in RASTER_REASON_CODES:
            raise ValueError("raster reason code is mandatory and must be recognized")
        class_reasons = {
            "continuous-tone-photo": "continuous-tone",
            "halftone-preservation": "halftone",
            "texture-preservation": "texture",
        }
        required_class = class_reasons.get(self.reason_code)
        if required_class is not None and required_class != self.raster_class:
            raise ValueError("raster reason code is incompatible with region class")
        if len(self.crop_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.crop_sha256
        ):
            raise ValueError("raster region requires a SHA-256 crop hash")


@dataclass(frozen=True, slots=True)
class CodecCandidate:
    codec: str
    byte_size: int
    error: float
    available: bool

    def __post_init__(self) -> None:
        if not self.codec:
            raise ValueError("codec name is required")
        if self.byte_size < 0 or not isfinite(self.error) or self.error < 0:
            raise ValueError("codec size and error must be finite and non-negative")
        if not self.available and self.byte_size != 0:
            raise ValueError("unavailable codec candidates cannot report fabricated bytes")


@dataclass(frozen=True, slots=True)
class CodecCurve:
    candidates: tuple[CodecCandidate, ...]
    pareto: tuple[CodecCandidate, ...]


@dataclass(frozen=True, slots=True)
class RasterPolicyDecision:
    replica_ready: bool
    manual_approval_required: bool
    effective_coverage: float
    union_coverage: float
    bounding_coverage: float
    findings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    available: bool
    resolved_path: str | None
