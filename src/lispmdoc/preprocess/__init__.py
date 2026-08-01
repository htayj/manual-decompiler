"""Deterministic, read-only PDF rendering foundations for Phase 1."""

from .operations import (
    PreprocessSettings,
    analyze_page_shape,
    canonical_settings_digest,
    detect_scanner_border,
    estimate_deskew,
    image_metrics,
    otsu_threshold,
    preprocess_image,
)
from .render import (
    PageSubsetError,
    RenderBackendUnavailableError,
    RenderManifest,
    RenderResult,
    SourceChangedDuringRenderError,
    UnsafeOutputRootError,
    compose_affine,
    parse_page_subset,
    probe_render_backend,
    render_pdf,
    source_pdf_to_canonical,
)

__all__ = [
    "PageSubsetError",
    "PreprocessSettings",
    "RenderBackendUnavailableError",
    "RenderManifest",
    "RenderResult",
    "SourceChangedDuringRenderError",
    "UnsafeOutputRootError",
    "analyze_page_shape",
    "canonical_settings_digest",
    "compose_affine",
    "detect_scanner_border",
    "estimate_deskew",
    "image_metrics",
    "otsu_threshold",
    "parse_page_subset",
    "probe_render_backend",
    "preprocess_image",
    "render_pdf",
    "source_pdf_to_canonical",
]
