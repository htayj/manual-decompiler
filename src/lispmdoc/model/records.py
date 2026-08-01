"""Immutable, validation-oriented LMDOC v1 IR records.

These records intentionally keep the package split visible: a manifest names
the pages, while page, structure, and style files can be loaded independently.
All references are explicit string IDs so JSON Schema and a later cross-file
validator can check them without reconstructing Python object graphs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .canonical import FORMAT_VERSION, content_id
from .geometry import AffineTransform, Box

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z][a-z0-9-]*-[0-9a-f]{12,64}$")
_PAGE_CLASSES = frozenset(
    {
        "born-digital",
        "hybrid",
        "scan-bilevel",
        "scan-gray",
        "scan-color",
        "schematic",
        "photo-or-illustration-dominant",
        "ambiguous",
    }
)
_CONFORMANCE = frozenset(
    {"replacement-ready", "review-required", "facsimile-required", "size-non-goal"}
)


def _id(value: str, name: str = "id") -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{name} must be a stable content-derived LMDOC ID")
    return value


def _sha256(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a lower-case SHA-256 hex digest")
    return value


def _positive(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _unique(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicate IDs")
    return values


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """Identity of an immutable input PDF, derived from its exact bytes."""

    sha256: str
    byte_size: int
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _sha256(self.sha256, "source.sha256")
        _positive(self.byte_size, "source.byte_size")
        if any(not path or path.startswith("/") for path in self.aliases):
            raise ValueError("source aliases must be non-empty relative paths")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"sha256": self.sha256, "byte_size": self.byte_size}
        if self.aliases:
            result["aliases"] = list(self.aliases)
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SourceRecord:
        return cls(value["sha256"], value["byte_size"], tuple(value.get("aliases", ())))


@dataclass(frozen=True, slots=True)
class ToolRecord:
    """Exact identity of a tool or model that contributed package evidence."""

    name: str
    version: str
    configuration_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.version:
            raise ValueError("tool name and version are required")
        if self.configuration_sha256 is not None:
            _sha256(self.configuration_sha256, "tool.configuration_sha256")

    def to_dict(self) -> dict[str, str]:
        result = {"name": self.name, "version": self.version}
        if self.configuration_sha256 is not None:
            result["configuration_sha256"] = self.configuration_sha256
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ToolRecord:
        return cls(value["name"], value["version"], value.get("configuration_sha256"))


@dataclass(frozen=True, slots=True)
class PageReference:
    """A manifest entry which fixes canonical physical-page ordering."""

    id: str
    sequence: int
    path: str
    source_page_index: int

    def __post_init__(self) -> None:
        _id(self.id, "page reference id")
        _positive(self.sequence, "page sequence")
        if (
            not self.path.startswith("pages/")
            or not self.path.endswith(".json")
            or ".." in self.path.split("/")
        ):
            raise ValueError("page paths must be normalized paths below pages/")
        if (
            isinstance(self.source_page_index, bool)
            or not isinstance(self.source_page_index, int)
            or self.source_page_index < 0
        ):
            raise ValueError("source_page_index must be a zero-based non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "sequence": self.sequence,
            "path": self.path,
            "source_page_index": self.source_page_index,
        }
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PageReference:
        return cls(value["id"], value["sequence"], value["path"], value["source_page_index"])


@dataclass(frozen=True, slots=True)
class Manifest:
    """The canonical ``manifest.json`` record for one LMDOC package."""

    document_id: str
    source: SourceRecord
    pages: tuple[PageReference, ...]
    profile: str
    configuration_sha256: str
    tools: tuple[ToolRecord, ...] = ()
    rights_notes: tuple[str, ...] = ()
    conformance_level: Literal[
        "replacement-ready", "review-required", "facsimile-required", "size-non-goal"
    ] = "review-required"
    known_limitations: tuple[str, ...] = ()
    format_version: str = FORMAT_VERSION

    def __post_init__(self) -> None:
        _id(self.document_id, "document_id")
        if self.format_version != FORMAT_VERSION:
            raise ValueError(f"this model writes only LMDOC {FORMAT_VERSION}")
        if not self.profile:
            raise ValueError("profile is required")
        _sha256(self.configuration_sha256, "configuration_sha256")
        if not self.pages:
            raise ValueError("a document must have at least one page")
        sequences = tuple(page.sequence for page in self.pages)
        if sequences != tuple(range(1, len(self.pages) + 1)):
            raise ValueError("manifest pages must have contiguous canonical sequence numbers")
        _unique(tuple(page.id for page in self.pages), "manifest page IDs")
        if self.conformance_level not in _CONFORMANCE:
            raise ValueError("unknown conformance level")

    @classmethod
    def for_source(
        cls,
        source: SourceRecord,
        pages: tuple[PageReference, ...],
        profile: str,
        configuration_sha256: str,
        **kwargs: Any,
    ) -> Manifest:
        """Build the durable document ID from source bytes, not a filename."""
        return cls(
            document_id=content_id("document", {"source_sha256": source.sha256}),
            source=source,
            pages=pages,
            profile=profile,
            configuration_sha256=configuration_sha256,
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "format_version": self.format_version,
            "document_id": self.document_id,
            "source": self.source.to_dict(),
            "pages": [page.to_dict() for page in self.pages],
            "profile": self.profile,
            "configuration_sha256": self.configuration_sha256,
            "tools": [tool.to_dict() for tool in self.tools],
            "rights_notes": list(self.rights_notes),
            "conformance_level": self.conformance_level,
            "known_limitations": list(self.known_limitations),
        }
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Manifest:
        return cls(
            document_id=value["document_id"],
            source=SourceRecord.from_dict(value["source"]),
            pages=tuple(PageReference.from_dict(item) for item in value["pages"]),
            profile=value["profile"],
            configuration_sha256=value["configuration_sha256"],
            tools=tuple(ToolRecord.from_dict(item) for item in value.get("tools", ())),
            rights_notes=tuple(value.get("rights_notes", ())),
            conformance_level=value.get("conformance_level", "review-required"),
            known_limitations=tuple(value.get("known_limitations", ())),
            format_version=value.get("format_version", FORMAT_VERSION),
        )


@dataclass(frozen=True, slots=True)
class SceneObject:
    """Physical scene-graph object; payload stays type-specific but explicit."""

    id: str
    kind: Literal[
        "text",
        "text-block",
        "line",
        "span",
        "token",
        "glyph",
        "group",
        "clip-path",
        "rule",
        "shape",
        "path",
        "raster",
        "link",
        "annotation",
    ]
    box: Box
    style_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    z_index: int = 0
    opacity_milli: int = 1000
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _id(self.id, "scene object id")
        if self.style_id is not None:
            _id(self.style_id, "scene object style_id")
        if self.kind not in {
            "text",
            "text-block",
            "line",
            "span",
            "token",
            "glyph",
            "group",
            "clip-path",
            "rule",
            "shape",
            "path",
            "raster",
            "link",
            "annotation",
        }:
            raise ValueError("unknown scene object kind")
        if isinstance(self.z_index, bool) or not isinstance(self.z_index, int):
            raise ValueError("scene object z_index must be an integer")
        if not 0 <= self.opacity_milli <= 1000:
            raise ValueError("scene object opacity_milli must be 0..1000")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("scene object evidence references must be unique")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "box": self.box.to_dict(),
            "payload": self.payload,
            "z_index": self.z_index,
            "opacity_milli": self.opacity_milli,
            "evidence_refs": list(self.evidence_refs),
        }
        if self.style_id is not None:
            result["style_id"] = self.style_id
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SceneObject:
        return cls(
            value["id"],
            value["kind"],
            Box.from_dict(value["box"]),
            value.get("style_id"),
            value.get("payload", {}),
            value.get("z_index", 0),
            value.get("opacity_milli", 1000),
            tuple(value.get("evidence_refs", ())),
        )


@dataclass(frozen=True, slots=True)
class PageRecord:
    """The streamable per-page canonical record in ``pages/pNNNNNN.json``."""

    id: str
    sequence: int
    source_page_index: int
    page_box: Box
    page_class: str
    source_pdf_to_canonical: AffineTransform
    render_pixels_to_canonical: AffineTransform
    source_page_sha256: str
    objects: tuple[SceneObject, ...] = ()
    reading_order: tuple[str, ...] = ()
    # ``source_page_sha256`` was a Phase 1 synthetic digest.  Retain it for
    # compatibility, but do not treat it as durable page identity.
    source_pdf_sha256: str | None = None
    source_render_sha256: str | None = None
    page_evidence_sha256: str | None = None
    reading_edges: tuple[dict[str, Any], ...] = ()
    format_version: str = FORMAT_VERSION

    def __post_init__(self) -> None:
        _id(self.id, "page id")
        _positive(self.sequence, "page sequence")
        if (
            isinstance(self.source_page_index, bool)
            or not isinstance(self.source_page_index, int)
            or self.source_page_index < 0
        ):
            raise ValueError("source_page_index must be zero-based and non-negative")
        if self.page_class not in _PAGE_CLASSES:
            raise ValueError("unknown page class")
        _sha256(self.source_page_sha256, "source_page_sha256")
        for name in ("source_pdf_sha256", "source_render_sha256", "page_evidence_sha256"):
            value = getattr(self, name)
            if value is not None:
                _sha256(value, name)
        if self.format_version != FORMAT_VERSION:
            raise ValueError(f"this model writes only LMDOC {FORMAT_VERSION}")
        object_ids = tuple(item.id for item in self.objects)
        _unique(object_ids, "page scene object IDs")
        _unique(self.reading_order, "page reading order")
        unknown = set(self.reading_order).difference(object_ids)
        if unknown:
            raise ValueError(f"reading order references missing scene objects: {sorted(unknown)!r}")
        for edge in self.reading_edges:
            if not isinstance(edge, dict):
                raise ValueError("reading edges must be JSON objects")
            if edge.get("source_id") not in object_ids or edge.get("target_id") not in object_ids:
                raise ValueError("reading edges must reference page scene objects")

    @classmethod
    def derive_id(cls, source_page_sha256: str, source_page_index: int) -> str:
        _sha256(source_page_sha256, "source_page_sha256")
        return content_id(
            "page",
            {"source_page_sha256": source_page_sha256, "source_page_index": source_page_index},
        )

    @classmethod
    def derive_durable_id(cls, source_pdf_sha256: str, source_page_index: int) -> str:
        """Page identity derived solely from immutable PDF bytes and page index."""
        _sha256(source_pdf_sha256, "source_pdf_sha256")
        return content_id(
            "page", {"source_pdf_sha256": source_pdf_sha256, "source_page_index": source_page_index}
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "format_version": self.format_version,
            "id": self.id,
            "sequence": self.sequence,
            "source_page_index": self.source_page_index,
            "page_box": self.page_box.to_dict(),
            "page_class": self.page_class,
            "source_pdf_to_canonical": self.source_pdf_to_canonical.to_dict(),
            "render_pixels_to_canonical": self.render_pixels_to_canonical.to_dict(),
            "source_page_sha256": self.source_page_sha256,
            "objects": [object_.to_dict() for object_ in self.objects],
            "reading_order": list(self.reading_order),
            "reading_edges": list(self.reading_edges),
        }
        if self.source_pdf_sha256 is not None:
            result["source_pdf_sha256"] = self.source_pdf_sha256
        if self.source_render_sha256 is not None:
            result["source_render_sha256"] = self.source_render_sha256
        if self.page_evidence_sha256 is not None:
            result["page_evidence_sha256"] = self.page_evidence_sha256
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PageRecord:
        return cls(
            id=value["id"],
            sequence=value["sequence"],
            source_page_index=value["source_page_index"],
            page_box=Box.from_dict(value["page_box"]),
            page_class=value["page_class"],
            source_pdf_to_canonical=AffineTransform.from_dict(value["source_pdf_to_canonical"]),
            render_pixels_to_canonical=AffineTransform.from_dict(
                value["render_pixels_to_canonical"]
            ),
            source_page_sha256=value["source_page_sha256"],
            objects=tuple(SceneObject.from_dict(item) for item in value.get("objects", ())),
            reading_order=tuple(value.get("reading_order", ())),
            source_pdf_sha256=value.get("source_pdf_sha256"),
            source_render_sha256=value.get("source_render_sha256"),
            page_evidence_sha256=value.get("page_evidence_sha256"),
            reading_edges=tuple(value.get("reading_edges", ())),
            format_version=value.get("format_version", FORMAT_VERSION),
        )


@dataclass(frozen=True, slots=True)
class StructureNode:
    """Logical hierarchy node, independently linked to its physical regions."""

    id: str
    kind: str
    child_ids: tuple[str, ...] = ()
    region_ids: tuple[str, ...] = ()
    text: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _id(self.id, "structure node id")
        if not self.kind:
            raise ValueError("structure node kind is required")
        for child_id in self.child_ids:
            _id(child_id, "structure child ID")
        for region_id in self.region_ids:
            _id(region_id, "structure region ID")
        _unique(self.child_ids, "structure child IDs")
        _unique(self.region_ids, "structure region IDs")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "child_ids": list(self.child_ids),
            "region_ids": list(self.region_ids),
            "properties": self.properties,
        }
        if self.text is not None:
            result["text"] = self.text
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StructureNode:
        return cls(
            value["id"],
            value["kind"],
            tuple(value.get("child_ids", ())),
            tuple(value.get("region_ids", ())),
            value.get("text"),
            value.get("properties", {}),
        )


@dataclass(frozen=True, slots=True)
class StructureRecord:
    """The canonical logical document hierarchy stored in ``structure.json``."""

    document_id: str
    root_id: str
    nodes: tuple[StructureNode, ...]
    format_version: str = FORMAT_VERSION

    def __post_init__(self) -> None:
        _id(self.document_id, "document_id")
        _id(self.root_id, "root_id")
        if self.format_version != FORMAT_VERSION:
            raise ValueError(f"this model writes only LMDOC {FORMAT_VERSION}")
        ids = tuple(node.id for node in self.nodes)
        _unique(ids, "structure node IDs")
        if self.root_id not in ids:
            raise ValueError("structure root_id must name a node")
        referenced = {child for node in self.nodes for child in node.child_ids}
        if not referenced.issubset(ids):
            raise ValueError("structure child IDs must name structure nodes")
        # A node can have at most one parent, and DFS detects a containment cycle.
        if len(referenced) != sum(len(node.child_ids) for node in self.nodes):
            raise ValueError("structure containment cannot give a node multiple parents")
        by_id = {node.id: node for node in self.nodes}
        visited: set[str] = set()
        active: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in active:
                raise ValueError("structure containment must be acyclic")
            if node_id in visited:
                return
            active.add(node_id)
            for child_id in by_id[node_id].child_ids:
                visit(child_id)
            active.remove(node_id)
            visited.add(node_id)

        visit(self.root_id)
        if visited != set(ids):
            raise ValueError("all structure nodes must be reachable from root_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "document_id": self.document_id,
            "root_id": self.root_id,
            "nodes": [node.to_dict() for node in self.nodes],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StructureRecord:
        return cls(
            value["document_id"],
            value["root_id"],
            tuple(StructureNode.from_dict(item) for item in value["nodes"]),
            value.get("format_version", FORMAT_VERSION),
        )


@dataclass(frozen=True, slots=True)
class StyleToken:
    """Reusable typography/drawing token in integer micropoints where geometric."""

    id: str
    name: str
    family: str
    size: int
    weight: int = 400
    slant: Literal["normal", "italic", "oblique"] = "normal"
    color: str = "#000000"
    tracking: int = 0
    leading: int | None = None

    def __post_init__(self) -> None:
        _id(self.id, "style token id")
        if not self.name or not self.family:
            raise ValueError("style name and family are required")
        _positive(self.size, "style size")
        if (
            isinstance(self.weight, bool)
            or not isinstance(self.weight, int)
            or not 1 <= self.weight <= 1000
        ):
            raise ValueError("style weight must be an integer from 1 through 1000")
        if self.slant not in {"normal", "italic", "oblique"}:
            raise ValueError("unknown style slant")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color):
            raise ValueError("style color must be a six-digit RGB hex color")
        if isinstance(self.tracking, bool) or not isinstance(self.tracking, int):
            raise ValueError("style tracking must be integer micropoints")
        if self.leading is not None:
            _positive(self.leading, "style leading")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "family": self.family,
            "size": self.size,
            "weight": self.weight,
            "slant": self.slant,
            "color": self.color,
            "tracking": self.tracking,
        }
        if self.leading is not None:
            result["leading"] = self.leading
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StyleToken:
        return cls(**value)


@dataclass(frozen=True, slots=True)
class StylesRecord:
    """The canonical ``styles.json`` record."""

    document_id: str
    tokens: tuple[StyleToken, ...]
    format_version: str = FORMAT_VERSION

    def __post_init__(self) -> None:
        _id(self.document_id, "document_id")
        if self.format_version != FORMAT_VERSION:
            raise ValueError(f"this model writes only LMDOC {FORMAT_VERSION}")
        _unique(tuple(token.id for token in self.tokens), "style token IDs")
        names = tuple(token.name for token in self.tokens)
        if len(names) != len(set(names)):
            raise ValueError("style token names must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "document_id": self.document_id,
            "tokens": [token.to_dict() for token in self.tokens],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StylesRecord:
        return cls(
            value["document_id"],
            tuple(StyleToken.from_dict(item) for item in value["tokens"]),
            value.get("format_version", FORMAT_VERSION),
        )
