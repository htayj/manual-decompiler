"""Conservative, reversible image preprocessing and page-shape evidence."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

from lispmdoc.hashing import sha256_file
from lispmdoc.model.geometry import AffineTransform, Rational


@dataclass(frozen=True, slots=True)
class PreprocessSettings:
    """Deterministic helper-render policy; source renders are always retained."""

    automatic_deskew: bool = True
    automatic_border_crop: bool = True
    deskew_max_millidegrees: int = 5000
    deskew_min_confidence_milli: int = 100
    deskew_max_disagreement_millidegrees: int = 250
    orientation_degrees: int | None = None
    illumination_correction: bool = False
    bleed_through_reduction: bool = False
    binarization: bool = False

    def __post_init__(self) -> None:
        if self.orientation_degrees not in {None, 0, 90, 180, 270}:
            raise ValueError("orientation_degrees must be None or a right angle")
        if self.deskew_max_millidegrees <= 0:
            raise ValueError("deskew_max_millidegrees must be positive")
        if not 0 <= self.deskew_min_confidence_milli <= 1000:
            raise ValueError("deskew_min_confidence_milli must be in 0..1000")

    def to_dict(self) -> dict[str, Any]:
        return {
            "automatic_border_crop": self.automatic_border_crop,
            "automatic_deskew": self.automatic_deskew,
            "binarization": self.binarization,
            "bleed_through_reduction": self.bleed_through_reduction,
            "deskew_max_disagreement_millidegrees": (self.deskew_max_disagreement_millidegrees),
            "deskew_max_millidegrees": self.deskew_max_millidegrees,
            "deskew_min_confidence_milli": self.deskew_min_confidence_milli,
            "illumination_correction": self.illumination_correction,
            "orientation_degrees": self.orientation_degrees,
        }


def preprocess_image(
    source_path: Path,
    helper_path: Path,
    overlay_path: Path,
    source_pixels_to_canonical: AffineTransform,
    *,
    settings: PreprocessSettings | None = None,
) -> dict[str, Any]:
    """Create an OCR-helper image while preserving exact reversible evidence."""

    policy = settings or PreprocessSettings()
    with Image.open(source_path) as opened:
        source = opened.convert("RGB")
    before = image_metrics(source)
    analysis = analyze_page_shape(source)
    border = detect_scanner_border(source)
    current = source.copy()
    helper_to_source = AffineTransform.identity()
    operations: list[dict[str, Any]] = []

    current, helper_to_source, orientation_record = _orientation(current, helper_to_source, policy)
    operations.append(orientation_record)

    deskew = estimate_deskew(current, policy.deskew_max_millidegrees)
    current, helper_to_source, deskew_record = _deskew(current, helper_to_source, deskew, policy)
    operations.append(deskew_record)

    # Border evidence was measured on the source render. A prior geometric
    # operation makes those coordinates stale, so it cannot be applied silently.
    if any(item["status"] == "applied" for item in operations):
        crop_record = _operation(
            "crop",
            "review" if border["candidate"] else "not-applied",
            border["confidence_milli"],
            "border coordinates require re-estimation after geometric correction",
            evidence=border,
        )
    else:
        current, helper_to_source, crop_record = _crop_border(
            current, helper_to_source, border, policy
        )
    operations.append(crop_record)

    if policy.illumination_correction:
        current = _correct_illumination(current)
        operations.append(
            _operation(
                "illumination-correction",
                "applied",
                1000,
                "explicit deterministic policy",
                parameters={"method": "background-divide", "blur_radius": 24},
            )
        )
    else:
        operations.append(
            _operation(
                "illumination-correction",
                "not-applied",
                0,
                "disabled; no automatic alteration without reviewed policy",
            )
        )

    if policy.bleed_through_reduction:
        current = current.filter(ImageFilter.MedianFilter(size=3))
        operations.append(
            _operation(
                "bleed-through-reduction",
                "applied",
                1000,
                "explicit deterministic policy",
                parameters={"method": "median", "radius_px": 1},
            )
        )
    else:
        operations.append(
            _operation(
                "bleed-through-reduction",
                "not-applied",
                0,
                "disabled; recto/verso evidence unavailable",
            )
        )

    operations.append(
        _operation(
            "dewarp",
            "review",
            0,
            "no validated page-surface model; source geometry retained",
        )
    )

    if policy.binarization:
        threshold = otsu_threshold(ImageOps.grayscale(current))
        current = ImageOps.grayscale(current).point(
            lambda value: 255 if value > threshold else 0, mode="1"
        )
        operations.append(
            _operation(
                "binarization",
                "applied",
                1000,
                "explicit deterministic policy",
                parameters={"method": "otsu", "threshold": threshold},
            )
        )
    else:
        operations.append(
            _operation(
                "binarization",
                "not-applied",
                0,
                "disabled; source tonal evidence retained",
            )
        )

    helper_path.parent.mkdir(parents=True, exist_ok=True)
    applied = any(item["status"] == "applied" for item in operations)
    if not applied:
        shutil.copyfile(source_path, helper_path)
    else:
        current.save(helper_path, format="PNG", compress_level=9, optimize=False)
    _debug_overlay(source, overlay_path, analysis, border, deskew)
    helper_metrics = image_metrics(current if applied else source)
    helper_to_canonical = _compose_affine(source_pixels_to_canonical, helper_to_source)
    return {
        "analysis": analysis,
        "applied": applied,
        "before_metrics": before,
        "debug_overlay": _image_record(overlay_path),
        "helper_metrics": helper_metrics,
        "helper_pixels_to_source_pixels": helper_to_source.to_dict(),
        "helper_pixels_to_canonical": helper_to_canonical.to_dict(),
        "ocr_helper_render": _image_record(helper_path),
        "operations": operations,
        "settings": policy.to_dict(),
    }


def analyze_page_shape(image: Image.Image) -> dict[str, Any]:
    """Return deterministic spread/foldout/gutter evidence without splitting."""

    gray = ImageOps.grayscale(image)
    width, height = gray.size
    ratio_milli = width * 1000 // max(height, 1)
    band_width = max(1, width // 100)
    center = width // 2
    center_mean = _region_mean(gray, (center - band_width, 0, center + band_width, height))
    left_mean = _region_mean(
        gray, (max(0, center - 6 * band_width), 0, max(1, center - 4 * band_width), height)
    )
    right_mean = _region_mean(
        gray,
        (
            min(width - 1, center + 4 * band_width),
            0,
            min(width, center + 6 * band_width),
            height,
        ),
    )
    neighbor_mean = (left_mean + right_mean) // 2
    gutter_contrast = abs(center_mean - neighbor_mean)
    spread_candidate = ratio_milli >= 1300 and gutter_contrast >= 8
    foldout_candidate = ratio_milli >= 1800
    return {
        "aspect_ratio_milli": ratio_milli,
        "center_gutter_contrast": gutter_contrast,
        "center_gutter_mean": center_mean,
        "disposition": ("review" if spread_candidate or foldout_candidate else "not-detected"),
        "foldout_candidate": foldout_candidate,
        "neighbor_band_mean": neighbor_mean,
        "spread_candidate": spread_candidate,
    }


def detect_scanner_border(image: Image.Image) -> dict[str, Any]:
    """Detect only strong dark scanner-bed strips touching an outer edge."""

    gray = ImageOps.grayscale(image)
    width, height = gray.size
    dark_threshold = 64
    required_milli = 850

    def dark_row(y: int) -> int:
        histogram = gray.crop((0, y, width, y + 1)).histogram()
        return sum(histogram[: dark_threshold + 1]) * 1000 // width

    def dark_column(x: int) -> int:
        histogram = gray.crop((x, 0, x + 1, height)).histogram()
        return sum(histogram[: dark_threshold + 1]) * 1000 // height

    top = _edge_run(height, dark_row, required_milli)
    bottom = _edge_run(height, lambda offset: dark_row(height - 1 - offset), required_milli)
    left = _edge_run(width, dark_column, required_milli)
    right = _edge_run(width, lambda offset: dark_column(width - 1 - offset), required_milli)
    candidate = any(value >= 2 for value in (top, bottom, left, right))
    safe = (
        candidate
        and left + right < width // 4
        and top + bottom < height // 4
        and width - left - right > 0
        and height - top - bottom > 0
    )
    confidence = 1000 if safe else 300 if candidate else 0
    return {
        "candidate": candidate,
        "confidence_milli": confidence,
        "crop_box": [left, top, width - right, height - bottom],
        "disposition": "scanner-border" if safe else "review" if candidate else "not-detected",
        "edge_dark_fraction_required_milli": required_milli,
        "removed_pixels": {"bottom": bottom, "left": left, "right": right, "top": top},
        "safe_to_apply": safe,
    }


def estimate_deskew(image: Image.Image, max_millidegrees: int = 5000) -> dict[str, Any]:
    """Estimate correction angle with independent fill and edge projections."""

    gray = _analysis_image(ImageOps.grayscale(image))
    fill = _projection_estimator(gray, max_millidegrees, edges=False)
    edge = _projection_estimator(gray, max_millidegrees, edges=True)
    disagreement = abs(fill["correction_millidegrees"] - edge["correction_millidegrees"])
    confidence = min(fill["confidence_milli"], edge["confidence_milli"])
    return {
        "confidence_milli": confidence,
        "correction_millidegrees": (
            fill["correction_millidegrees"] + edge["correction_millidegrees"]
        )
        // 2,
        "disagreement_millidegrees": disagreement,
        "estimators": [fill, edge],
    }


def image_metrics(image: Image.Image) -> dict[str, int | str]:
    gray = ImageOps.grayscale(image)
    histogram = gray.histogram()
    pixels = max(1, image.width * image.height)
    mean_milli = sum(value * count for value, count in enumerate(histogram)) * 1000 // pixels
    dark = sum(histogram[:128])
    extrema = cast(tuple[int, int], gray.getextrema())
    return {
        "dark_fraction_milli": dark * 1000 // pixels,
        "height_px": image.height,
        "luma_max": int(extrema[1]),
        "luma_mean_milli": mean_milli,
        "luma_min": int(extrema[0]),
        "mode": image.mode,
        "width_px": image.width,
    }


def otsu_threshold(gray: Image.Image) -> int:
    histogram = gray.histogram()
    total = sum(histogram)
    weighted_total = sum(index * count for index, count in enumerate(histogram))
    background_weight = 0
    background_sum = 0
    best_variance = Fraction(-1)
    best = 127
    for threshold, count in enumerate(histogram):
        background_weight += count
        if background_weight == 0:
            continue
        foreground_weight = total - background_weight
        if foreground_weight == 0:
            break
        background_sum += threshold * count
        mean_background = Fraction(background_sum, background_weight)
        mean_foreground = Fraction(weighted_total - background_sum, foreground_weight)
        variance = background_weight * foreground_weight * (mean_background - mean_foreground) ** 2
        if variance > best_variance:
            best_variance = variance
            best = threshold
    return best


def _orientation(
    image: Image.Image,
    helper_to_source: AffineTransform,
    settings: PreprocessSettings,
) -> tuple[Image.Image, AffineTransform, dict[str, Any]]:
    degrees = settings.orientation_degrees
    if degrees in {None, 0}:
        reason = (
            "automatic orientation requires text/layout evidence"
            if degrees is None
            else "explicit orientation is zero"
        )
        return image, helper_to_source, _operation("orientation", "not-applied", 0, reason)
    rotated = image.rotate(
        degrees, expand=True, resample=Image.Resampling.BICUBIC, fillcolor="white"
    )
    transform = _rotation_output_to_input(image.size, rotated.size, degrees)
    return (
        rotated,
        _compose_affine(helper_to_source, transform),
        _operation(
            "orientation",
            "applied",
            1000,
            "explicit deterministic policy",
            parameters={"degrees": degrees},
            output_to_input=transform,
        ),
    )


def _deskew(
    image: Image.Image,
    helper_to_source: AffineTransform,
    estimate: dict[str, Any],
    settings: PreprocessSettings,
) -> tuple[Image.Image, AffineTransform, dict[str, Any]]:
    correction = int(estimate["correction_millidegrees"])
    disagreement = int(estimate["disagreement_millidegrees"])
    confidence = int(estimate["confidence_milli"])
    evidence = {"estimators": estimate["estimators"], "disagreement_millidegrees": disagreement}
    if not settings.automatic_deskew:
        return (
            image,
            helper_to_source,
            _operation(
                "deskew", "not-applied", confidence, "automatic deskew disabled", evidence=evidence
            ),
        )
    if disagreement > settings.deskew_max_disagreement_millidegrees:
        return (
            image,
            helper_to_source,
            _operation(
                "deskew",
                "review",
                confidence,
                "deskew estimators disagree beyond policy threshold",
                evidence=evidence,
            ),
        )
    if confidence < settings.deskew_min_confidence_milli:
        return (
            image,
            helper_to_source,
            _operation(
                "deskew",
                "review",
                confidence,
                "deskew confidence below policy threshold",
                evidence=evidence,
            ),
        )
    if abs(correction) < 100:
        return (
            image,
            helper_to_source,
            _operation(
                "deskew",
                "not-applied",
                confidence,
                "estimated skew is below 0.1 degree",
                evidence=evidence,
            ),
        )
    degrees = correction / 1000
    rotated = image.rotate(
        degrees, expand=False, resample=Image.Resampling.BICUBIC, fillcolor="white"
    )
    transform = _rotation_output_to_input(image.size, rotated.size, degrees)
    return (
        rotated,
        _compose_affine(helper_to_source, transform),
        _operation(
            "deskew",
            "applied",
            confidence,
            "independent estimators agree",
            parameters={"correction_millidegrees": correction},
            evidence=evidence,
            output_to_input=transform,
        ),
    )


def _crop_border(
    image: Image.Image,
    helper_to_source: AffineTransform,
    border: dict[str, Any],
    settings: PreprocessSettings,
) -> tuple[Image.Image, AffineTransform, dict[str, Any]]:
    if not settings.automatic_border_crop:
        return (
            image,
            helper_to_source,
            _operation(
                "crop",
                "not-applied",
                int(border["confidence_milli"]),
                "automatic border crop disabled",
                evidence=border,
            ),
        )
    if not border["candidate"]:
        return (
            image,
            helper_to_source,
            _operation("crop", "not-applied", 0, "no scanner-bed border detected", evidence=border),
        )
    if not border["safe_to_apply"]:
        return (
            image,
            helper_to_source,
            _operation(
                "crop",
                "review",
                int(border["confidence_milli"]),
                "border candidate failed safe crop policy",
                evidence=border,
            ),
        )
    left, top, right, bottom = (int(value) for value in border["crop_box"])
    cropped = image.crop((left, top, right, bottom))
    transform = AffineTransform(
        Rational(1),
        Rational(0),
        Rational(0),
        Rational(1),
        Rational(left),
        Rational(top),
    )
    return (
        cropped,
        _compose_affine(helper_to_source, transform),
        _operation(
            "crop",
            "applied",
            int(border["confidence_milli"]),
            "strong edge-connected scanner-bed strips",
            parameters={"crop_box": [left, top, right, bottom]},
            evidence={"removed_regions_disposition": "scanner-border", **border},
            output_to_input=transform,
        ),
    )


def _operation(
    name: str,
    status: str,
    confidence_milli: int,
    reason: str,
    *,
    parameters: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    output_to_input: AffineTransform | None = None,
) -> dict[str, Any]:
    return {
        "confidence_milli": confidence_milli,
        "evidence": evidence or {},
        "name": name,
        "output_pixels_to_input_pixels": (output_to_input or AffineTransform.identity()).to_dict(),
        "parameters": parameters or {},
        "reason": reason,
        "status": status,
    }


def _projection_estimator(
    gray: Image.Image, max_millidegrees: int, *, edges: bool
) -> dict[str, Any]:
    working = gray.filter(ImageFilter.FIND_EDGES) if edges else gray
    threshold = otsu_threshold(working)
    if edges:
        mask = working.point(lambda value: 0 if value > threshold else 255)
    else:
        mask = working.point(lambda value: 0 if value <= threshold else 255)
    maximum_tenths = max(1, max_millidegrees // 100)
    coarse = range(-maximum_tenths, maximum_tenths + 1, 2)
    scored = [(angle, _projection_score(mask, angle * 0.1)) for angle in coarse]
    best_tenth, best_score = max(scored, key=lambda item: (item[1], -abs(item[0]), -item[0]))
    fine = range(best_tenth * 10 - 10, best_tenth * 10 + 11)
    fine_scored = [(angle, _projection_score(mask, angle * 0.01)) for angle in fine]
    best_hundredth, fine_score = max(
        fine_scored, key=lambda item: (item[1], -abs(item[0]), -item[0])
    )
    baseline = _projection_score(mask, 0.0)
    improvement = max(0, fine_score - baseline)
    confidence = min(1000, improvement * 1000 // max(fine_score, 1))
    return {
        "baseline_score": baseline,
        "best_score": max(best_score, fine_score),
        "confidence_milli": confidence,
        "correction_millidegrees": best_hundredth * 10,
        "name": "edge-projection" if edges else "fill-projection",
        "threshold": threshold,
    }


def _projection_score(mask: Image.Image, correction_degrees: float) -> int:
    rotated = mask.rotate(
        correction_degrees,
        expand=False,
        resample=Image.Resampling.NEAREST,
        fillcolor=255,
    )
    inverted = ImageOps.invert(rotated.convert("L"))
    rows = inverted.resize((1, inverted.height), resample=Image.Resampling.BOX)
    return sum(value * value * count for value, count in enumerate(rows.histogram()))


def _analysis_image(gray: Image.Image) -> Image.Image:
    maximum = max(gray.size)
    if maximum <= 640:
        return gray
    scale = Fraction(640, maximum)
    return gray.resize(
        (max(1, int(gray.width * scale)), max(1, int(gray.height * scale))),
        resample=Image.Resampling.BILINEAR,
    )


def _rotation_output_to_input(
    input_size: tuple[int, int], output_size: tuple[int, int], degrees: float
) -> AffineTransform:
    radians = math.radians(degrees)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    input_center_x = Fraction(input_size[0], 2)
    input_center_y = Fraction(input_size[1], 2)
    output_center_x = Fraction(output_size[0], 2)
    output_center_y = Fraction(output_size[1], 2)
    a = _rational(cosine)
    b = _rational(sine)
    c = _rational(-sine)
    d = _rational(cosine)
    e = input_center_x - a * output_center_x - c * output_center_y
    f = input_center_y - b * output_center_x - d * output_center_y
    return AffineTransform(*(Rational.from_value(value) for value in (a, b, c, d, e, f)))


def _rational(value: float) -> Fraction:
    return Fraction(value).limit_denominator(1_000_000_000)


def _compose_affine(outer: AffineTransform, inner: AffineTransform) -> AffineTransform:
    oa, ob, oc, od, oe, of = (
        outer.a.fraction,
        outer.b.fraction,
        outer.c.fraction,
        outer.d.fraction,
        outer.e.fraction,
        outer.f.fraction,
    )
    ia, ib, ic, id_, ie, if_ = (
        inner.a.fraction,
        inner.b.fraction,
        inner.c.fraction,
        inner.d.fraction,
        inner.e.fraction,
        inner.f.fraction,
    )
    return AffineTransform(
        Rational.from_value(oa * ia + oc * ib),
        Rational.from_value(ob * ia + od * ib),
        Rational.from_value(oa * ic + oc * id_),
        Rational.from_value(ob * ic + od * id_),
        Rational.from_value(oa * ie + oc * if_ + oe),
        Rational.from_value(ob * ie + od * if_ + of),
    )


def _correct_illumination(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    background = gray.filter(ImageFilter.GaussianBlur(radius=24))
    # Screen with the inverted low-frequency background raises shaded paper
    # while retaining dark marks; all inputs/settings remain recorded.
    corrected = ImageChops.screen(gray, ImageOps.invert(background))
    return corrected.convert("RGB")


def _edge_run(length: int, fraction: Any, required_milli: int) -> int:
    maximum = max(1, length // 8)
    run = 0
    for offset in range(maximum):
        if fraction(offset) < required_milli:
            break
        run += 1
    return run


def _region_mean(gray: Image.Image, box: tuple[int, int, int, int]) -> int:
    if box[2] <= box[0] or box[3] <= box[1]:
        return 0
    return int(ImageStat.Stat(gray.crop(box)).mean[0])


def _debug_overlay(
    source: Image.Image,
    path: Path,
    analysis: dict[str, Any],
    border: dict[str, Any],
    deskew: dict[str, Any],
) -> None:
    overlay = source.copy()
    draw = ImageDraw.Draw(overlay)
    left, top, right, bottom = (int(value) for value in border["crop_box"])
    color = (0, 180, 0) if border["safe_to_apply"] else (220, 0, 0)
    draw.rectangle((left, top, max(left, right - 1), max(top, bottom - 1)), outline=color, width=2)
    if analysis["spread_candidate"] or analysis["foldout_candidate"]:
        center = overlay.width // 2
        draw.line((center, 0, center, overlay.height - 1), fill=(0, 80, 255), width=2)
    # A short angle ray makes the estimator visible without adding font/runtime
    # dependencies to the deterministic overlay.
    correction = int(deskew["correction_millidegrees"])
    length = min(overlay.size) // 6
    radians = math.radians(correction / 1000)
    origin = (overlay.width // 2, overlay.height // 2)
    endpoint = (
        origin[0] + int(length * math.cos(radians)),
        origin[1] + int(length * math.sin(radians)),
    )
    draw.line((*origin, *endpoint), fill=(255, 140, 0), width=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(path, format="PNG", compress_level=9, optimize=False)


def _image_record(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        return {
            "format": image.format,
            "height_px": image.height,
            "mode": image.mode,
            "path": path.name,
            "sha256": sha256_file(path),
            "width_px": image.width,
        }


def canonical_settings_digest(settings: PreprocessSettings) -> str:
    """Stable policy serialization useful to page-local stage keys."""

    payload = json.dumps(settings.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
