"""Crop, deduplication, codec-curve, and fail-closed raster policy helpers."""

from __future__ import annotations

import io
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from shutil import which

from PIL import Image, UnidentifiedImageError

from .types import (
    Capability,
    CodecCandidate,
    CodecCurve,
    PixelBox,
    RasterBitmap,
    RasterCrop,
    RasterPolicyDecision,
    RasterRegion,
)

MAX_ENCODED_RASTER_BYTES = 256 * 1024 * 1024
MAX_ENCODED_RASTER_PIXELS = 100_000_000
RASTER_CODECS: Mapping[str, tuple[str, str]] = {
    "png": ("PNG", ".png"),
    "jpeg": ("JPEG", ".jpg"),
    "webp": ("WEBP", ".webp"),
}


@dataclass(frozen=True, slots=True)
class EncodedRasterInfo:
    codec: str
    suffix: str
    width: int
    height: int


def inspect_encoded_raster(data: bytes) -> EncodedRasterInfo:
    """Decode bounded browser raster bytes and return their authoritative format."""

    if not data or len(data) > MAX_ENCODED_RASTER_BYTES:
        raise ValueError("raster asset byte size is empty or exceeds the safety limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "WEBP"]) as image:
                image.verify()
            with Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "WEBP"]) as image:
                width, height = image.size
                image_format = image.format
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        UnidentifiedImageError,
    ) as error:
        raise ValueError("raster asset is not a valid supported encoded image") from error
    if width < 1 or height < 1 or width * height > MAX_ENCODED_RASTER_PIXELS:
        raise ValueError("raster asset dimensions exceed the safety limit")
    for codec, (pillow_format, suffix) in RASTER_CODECS.items():
        if image_format == pillow_format:
            return EncodedRasterInfo(codec, suffix, width, height)
    raise ValueError(f"unsupported raster codec: {image_format!r}")


def validate_raster_mapping(
    payload: Mapping[str, object], info: EncodedRasterInfo
) -> None:
    """Require declared codec/dimensions and a full, tight source-pixel mapping."""

    asset = payload.get("asset")
    if not isinstance(asset, Mapping):
        raise ValueError("raster payload requires asset metadata")
    if (
        asset.get("codec") != info.codec
        or asset.get("width_px") != info.width
        or asset.get("height_px") != info.height
    ):
        raise ValueError("raster asset codec or dimensions do not match encoded bytes")
    crop = payload.get("source_crop")
    if not isinstance(crop, Mapping) or (
        crop.get("x"),
        crop.get("y"),
        crop.get("width"),
        crop.get("height"),
    ) != (0, 0, info.width, info.height):
        raise ValueError("raster requires a tight full-asset source_crop mapping")


def approved_photo_dominant_disposition(payload: Mapping[str, object]) -> bool:
    """Return true only for the explicit Wave-8 large-raster disposition."""

    approval = payload.get("manual_approval_id")
    return (
        payload.get("reason") == "continuous-tone-photo"
        and isinstance(approval, str)
        and bool(approval.strip())
        and payload.get("explicitly_photo_dominant") is True
        and payload.get("contains_meaningful_text_or_vector") is False
    )


def tight_crop(bitmap: RasterBitmap, *, background: bytes | None = None) -> RasterCrop:
    """Crop non-background pixels exactly; an all-background bitmap is rejected."""

    pixel_width = bitmap.channels
    background_pixel = background if background is not None else bytes([255] * pixel_width)
    if len(background_pixel) != pixel_width:
        raise ValueError("background must contain exactly one pixel")
    occupied: list[tuple[int, int]] = []
    for y in range(bitmap.height):
        for x in range(bitmap.width):
            offset = (y * bitmap.width + x) * pixel_width
            if bitmap.data[offset : offset + pixel_width] != background_pixel:
                occupied.append((x, y))
    if not occupied:
        raise ValueError("cannot crop an all-background bitmap")
    left = min(x for x, _ in occupied)
    top = min(y for _, y in occupied)
    right = max(x for x, _ in occupied) + 1
    bottom = max(y for _, y in occupied) + 1
    cropped = bytearray()
    for y in range(top, bottom):
        start = (y * bitmap.width + left) * pixel_width
        end = (y * bitmap.width + right) * pixel_width
        cropped.extend(bitmap.data[start:end])
    result = RasterBitmap(right - left, bottom - top, bitmap.channels, bytes(cropped))
    return RasterCrop(PixelBox(left, top, result.width, result.height), result, result.sha256)


@dataclass(slots=True)
class CropCatalog:
    """Content-addressed crop registry; identical crops share one stored object."""

    _crops: dict[str, RasterCrop] = field(default_factory=dict)

    def add(self, crop: RasterCrop) -> RasterCrop:
        prior = self._crops.setdefault(crop.content_sha256, crop)
        if prior.bitmap != crop.bitmap:
            raise ValueError("SHA-256 collision between unequal crop bitmaps")
        return prior

    @property
    def crops(self) -> tuple[RasterCrop, ...]:
        return tuple(self._crops[key] for key in sorted(self._crops))


def codec_curve(candidates: Iterable[CodecCandidate]) -> CodecCurve:
    """Return stable candidates and the non-dominated byte/error frontier."""

    ordered = tuple(sorted(candidates, key=lambda item: (item.error, item.byte_size, item.codec)))
    available = tuple(item for item in ordered if item.available)
    pareto = tuple(
        item
        for item in available
        if not any(
            other != item
            and other.byte_size <= item.byte_size
            and other.error <= item.error
            and (other.byte_size < item.byte_size or other.error < item.error)
            for other in available
        )
    )
    return CodecCurve(ordered, pareto)


def evaluate_page_raster_policy(
    page_width: int,
    page_height: int,
    regions: tuple[RasterRegion, ...],
    *,
    manual_approval_id: str | None = None,
    explicitly_photo_dominant: bool = False,
    contains_meaningful_text_or_vector: bool = True,
    max_regions: int = 10_000,
) -> RasterPolicyDecision:
    """Evaluate aggregate and enclosing coverage so split crops cannot evade policy."""

    if page_width < 1 or page_height < 1:
        raise ValueError("page dimensions must be positive")
    if max_regions < 1 or len(regions) > max_regions:
        raise ValueError("raster region count exceeds policy limit")
    for region in regions:
        if region.box.right > page_width or region.box.bottom > page_height:
            raise ValueError(f"raster region {region.id} lies outside the page")
    page_area = page_width * page_height
    union_coverage = _rectangle_union_area(regions) / page_area
    if regions:
        left = min(region.box.x for region in regions)
        top = min(region.box.y for region in regions)
        right = max(region.box.right for region in regions)
        bottom = max(region.box.bottom for region in regions)
        bounding_coverage = ((right - left) * (bottom - top)) / page_area
    else:
        bounding_coverage = 0.0
    effective_coverage = max(union_coverage, bounding_coverage)
    large = effective_coverage > 0.8
    findings: list[str] = []
    approved = bool(manual_approval_id and manual_approval_id.strip())
    if large and not approved:
        findings.append("large-raster-manual-approval-required")
    if large and not explicitly_photo_dominant:
        findings.append("large-raster-not-explicitly-photo-dominant")
    if large and contains_meaningful_text_or_vector:
        findings.append("large-raster-contains-meaningful-text-or-vector")
    return RasterPolicyDecision(
        replica_ready=not findings,
        manual_approval_required=large,
        effective_coverage=effective_coverage,
        union_coverage=union_coverage,
        bounding_coverage=bounding_coverage,
        findings=tuple(findings),
    )


def _rectangle_union_area(regions: tuple[RasterRegion, ...]) -> int:
    x_values = sorted({value for region in regions for value in (region.box.x, region.box.right)})
    area = 0
    for left, right in zip(x_values, x_values[1:], strict=False):
        intervals = sorted(
            (region.box.y, region.box.bottom)
            for region in regions
            if region.box.x < right and region.box.right > left
        )
        covered_y = 0
        if intervals:
            start, end = intervals[0]
            for next_start, next_end in intervals[1:]:
                if next_start > end:
                    covered_y += end - start
                    start, end = next_start, next_end
                else:
                    end = max(end, next_end)
            covered_y += end - start
        area += (right - left) * covered_y
    return area


def probe_external_capabilities(
    commands: Mapping[str, str] | None = None,
) -> tuple[Capability, ...]:
    """Probe known executables without installing or invoking them."""

    requested = commands or {
        "avif": "avifenc",
        "jpeg-xl": "cjxl",
        "potrace": "potrace",
        "webp": "cwebp",
    }
    results = []
    for name, command in sorted(requested.items()):
        path = which(command)
        results.append(Capability(name, path is not None, path))
    return tuple(results)
