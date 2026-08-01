"""LMDOC v1 canonical data model."""

from .canonical import (
    FORMAT_VERSION,
    CanonicalizationError,
    canonical_json_bytes,
    canonical_json_text,
    content_id,
    sha256_hex,
    stable_id,
)
from .geometry import AffineTransform, Box, MicroPoint, Point, Rational
from .ir import ConformanceFacets, EvidenceRef, Polygon, ReadingEdge, linearize_reading_order
from .records import (
    Manifest,
    PageRecord,
    PageReference,
    SceneObject,
    SourceRecord,
    StructureNode,
    StructureRecord,
    StylesRecord,
    StyleToken,
    ToolRecord,
)

__all__ = [
    "FORMAT_VERSION",
    "AffineTransform",
    "Box",
    "CanonicalizationError",
    "ConformanceFacets",
    "EvidenceRef",
    "Manifest",
    "MicroPoint",
    "PageRecord",
    "PageReference",
    "Point",
    "Polygon",
    "Rational",
    "ReadingEdge",
    "SceneObject",
    "SourceRecord",
    "StructureNode",
    "StructureRecord",
    "StyleToken",
    "StylesRecord",
    "ToolRecord",
    "canonical_json_bytes",
    "canonical_json_text",
    "content_id",
    "sha256_hex",
    "stable_id",
    "linearize_reading_order",
]
