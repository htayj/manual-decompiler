"""Supplied PDF graphics operator, resource, annotation, and Type3 contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lispmdoc.model import AffineTransform


@dataclass(frozen=True, slots=True)
class PDFOperator:
    name: str
    operands: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("PDF operator name is required")


@dataclass(frozen=True, slots=True)
class FormXObject:
    name: str
    operators: tuple[PDFOperator, ...]
    matrix: AffineTransform = field(default_factory=AffineTransform.identity)
    resources: GraphicsResources | None = None


@dataclass(frozen=True, slots=True)
class GraphicsResources:
    forms: dict[str, FormXObject] = field(default_factory=dict)
    ext_gstates: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AnnotationEvidence:
    subtype: str
    rect: tuple[Any, Any, Any, Any]
    contents: str | None = None
    action_uri: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Type3CharProcedure:
    font_id: str
    glyph_name: str
    operators: tuple[PDFOperator, ...]
    evidence_sha256: str
    resources: GraphicsResources = field(default_factory=GraphicsResources)

    def __post_init__(self) -> None:
        if not self.font_id or not self.glyph_name:
            raise ValueError("Type3 procedure requires font and glyph names")
        if len(self.evidence_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.evidence_sha256
        ):
            raise ValueError("Type3 procedure evidence requires a lower-case SHA-256")


def operators_from_pypdf(
    operations: list[tuple[list[Any], bytes]],
) -> tuple[PDFOperator, ...]:
    """Normalize pypdf ContentStream operations without interpreting them."""

    return tuple(
        PDFOperator(operator.decode("latin-1"), tuple(operands))
        for operands, operator in operations
    )
