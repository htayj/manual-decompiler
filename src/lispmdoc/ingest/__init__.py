"""Immutable, low-cost PDF discovery and inspection.

This package deliberately does not render, OCR, repair, or otherwise write to a
source PDF.  It creates JSON-compatible evidence that later stages can use to
choose the least destructive processing route.
"""

from .discovery import DiscoveredPdf, discover_pdfs, group_by_source
from .fingerprint import (
    SourceChangedError,
    SourceFingerprint,
    SourceVerification,
    fingerprint_source,
    verify_source,
)
from .inspect import (
    DocumentInspection,
    IngestError,
    OptionalPdfDependencyError,
    PdfInspectionError,
    inspect_pdf,
)

__all__ = [
    "DiscoveredPdf",
    "DocumentInspection",
    "IngestError",
    "OptionalPdfDependencyError",
    "PdfInspectionError",
    "SourceChangedError",
    "SourceFingerprint",
    "SourceVerification",
    "discover_pdfs",
    "fingerprint_source",
    "group_by_source",
    "inspect_pdf",
    "verify_source",
]
