"""Immutable truth contracts for recovered typesetter-source manuals.

This module is deliberately separate from the human-transcription contracts.
An authoritative record can use recovered pre-print source as literal truth,
but it must bind that claim to exact bytes, a selected rendered PDF page, and
explicit evidence for the source-to-page mapping.  Generated OCR output is not
an allowed truth method.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .wave1 import QueuePage, RegionGeometry

AUTHORITATIVE_TRUTH_VERSION = "lispmdoc-authoritative-typesetter-truth-1"
_SHA256_LENGTH = 64
_DERIVATION_METHODS = frozenset({"source-literal", "converted-text"})
_ANCHOR_KINDS = frozenset(
    {"printed-page-number", "heading", "section-label", "source-footer", "scan-footer"}
)
_MAPPING_STATES = frozenset({"verified", "human-mapping-review-required"})
_LAYOUT_STATES = frozenset({"verified", "human-review-required", "discrepancy"})
_NORMALIZATION_RULES = frozenset({"none", "normalize-lf", "unicode-nfc", "strip-final-newline"})
_TEXT_ENCODINGS = frozenset({"utf-8", "ascii"})


class AuthoritativeTruthError(ValueError):
    """Raised when purported recovered-source truth is not reproducible."""


def _sha256(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise AuthoritativeTruthError(f"{name} must be a lower-case SHA-256 digest")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AuthoritativeTruthError(f"{name} must be a non-empty, trimmed string")
    if "\x00" in value:
        raise AuthoritativeTruthError(f"{name} must not contain NUL")
    return value


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AuthoritativeTruthError(f"{name} must be an integer")
    return value


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AuthoritativeTruthError(f"{name} must be an object")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise AuthoritativeTruthError(f"{name} must be an array")
    return value


def _safe_relative_path(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise AuthoritativeTruthError(f"{name} must be a safe relative POSIX path")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in value
        or "\x00" in value
    ):
        raise AuthoritativeTruthError(f"{name} must be a safe relative POSIX path")
    return value


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """An inclusive, line-oriented range in one recovered source file."""

    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        if self.start_line < 1 or self.end_line < self.start_line:
            raise AuthoritativeTruthError("source span must be a non-empty positive line range")

    def select(self, text: str) -> str:
        lines = text.splitlines(keepends=True)
        line_count = len(lines)
        if self.end_line > line_count:
            raise AuthoritativeTruthError("source span exceeds selected text-authority artifact")
        return "".join(lines[self.start_line - 1 : self.end_line])

    def to_dict(self) -> dict[str, int]:
        return {"end_line": self.end_line, "start_line": self.start_line}

    @classmethod
    def from_dict(cls, value: object) -> SourceSpan:
        record = _mapping(value, "source_span")
        return cls(
            _integer(record.get("start_line"), "source_span.start_line"),
            _integer(record.get("end_line"), "source_span.end_line"),
        )


@dataclass(frozen=True, slots=True)
class TextDerivation:
    """Exact derivation of truth text from source, never from OCR output."""

    method: str
    text_encoding: str
    converter_identity: str | None = None
    converter_sha256: str | None = None
    converted_text_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.method not in _DERIVATION_METHODS:
            raise AuthoritativeTruthError(
                "truth method must be source-literal or converted-text; "
                "generated/OCR truth is forbidden"
            )
        if self.text_encoding not in _TEXT_ENCODINGS:
            raise AuthoritativeTruthError("text encoding must be an explicitly supported encoding")
        converter_values = (
            self.converter_identity,
            self.converter_sha256,
            self.converted_text_sha256,
        )
        if self.method == "source-literal":
            if any(value is not None for value in converter_values):
                raise AuthoritativeTruthError("source-literal truth must not claim a converter")
            return
        if not all(value is not None for value in converter_values):
            raise AuthoritativeTruthError(
                "converted-text truth requires converter identity, converter digest, "
                "and output digest"
            )
        _text(self.converter_identity, "converter_identity")
        _sha256(self.converter_sha256, "converter_sha256")
        _sha256(self.converted_text_sha256, "converted_text_sha256")

    def truth_artifact(self, *, source_file: bytes, converted_text: bytes | None) -> bytes:
        if self.method == "source-literal":
            return source_file
        if converted_text is None:
            raise AuthoritativeTruthError(
                "converted-text verification needs converter and output bytes"
            )
        return converted_text

    def verify(self, *, converter: bytes | None, converted_text: bytes | None) -> None:
        if self.method == "source-literal":
            if converter is not None or converted_text is not None:
                raise AuthoritativeTruthError(
                    "source-literal verification must not supply converter bytes"
                )
            return
        if converter is None or converted_text is None:
            raise AuthoritativeTruthError(
                "converted-text verification needs converter and output bytes"
            )
        assert self.converter_sha256 is not None
        assert self.converted_text_sha256 is not None
        if hashlib.sha256(converter).hexdigest() != self.converter_sha256:
            raise AuthoritativeTruthError("converter SHA-256 does not match provenance")
        if hashlib.sha256(converted_text).hexdigest() != self.converted_text_sha256:
            raise AuthoritativeTruthError("converted text SHA-256 does not match provenance")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "converted_text_sha256": self.converted_text_sha256,
            "converter_identity": self.converter_identity,
            "converter_sha256": self.converter_sha256,
            "method": self.method,
            "text_encoding": self.text_encoding,
        }

    @classmethod
    def from_dict(cls, value: object) -> TextDerivation:
        record = _mapping(value, "text_derivation")
        return cls(
            str(record.get("method", "")),
            str(record.get("text_encoding", "")),
            record.get("converter_identity"),
            record.get("converter_sha256"),
            record.get("converted_text_sha256"),
        )


@dataclass(frozen=True, slots=True)
class SupportingSourceFile:
    """An additional source input required to reproduce a converted truth artifact."""

    source_path: str
    sha256: str

    def __post_init__(self) -> None:
        _safe_relative_path(self.source_path, "supporting source path")
        _sha256(self.sha256, "supporting source SHA-256")

    def to_dict(self) -> dict[str, str]:
        return {"sha256": self.sha256, "source_path": self.source_path}

    @classmethod
    def from_dict(cls, value: object) -> SupportingSourceFile:
        record = _mapping(value, "supporting source file")
        return cls(str(record.get("source_path", "")), str(record.get("sha256", "")))


@dataclass(frozen=True, slots=True)
class TypesetterSourceProvenance:
    """Checksummed archive and file identity for recovered typesetter input."""

    source_archive_sha256: str
    source_file_sha256: str
    edition_identity: str
    source_path: str
    normalization_rules: tuple[str, ...]
    text_derivation: TextDerivation
    supporting_files: tuple[SupportingSourceFile, ...] = ()

    def __post_init__(self) -> None:
        _sha256(self.source_archive_sha256, "source_archive_sha256")
        _sha256(self.source_file_sha256, "source_file_sha256")
        _text(self.edition_identity, "edition_identity")
        _safe_relative_path(self.source_path, "source_path")
        if not self.normalization_rules:
            raise AuthoritativeTruthError("normalization rules must be explicit, including 'none'")
        if len(self.normalization_rules) != len(set(self.normalization_rules)):
            raise AuthoritativeTruthError("normalization rules must not repeat")
        if "none" in self.normalization_rules and len(self.normalization_rules) != 1:
            raise AuthoritativeTruthError(
                "normalization rule 'none' cannot be combined with transforms"
            )
        for rule in self.normalization_rules:
            _text(rule, "normalization rule")
            if rule not in _NORMALIZATION_RULES:
                raise AuthoritativeTruthError("normalization rule is unsupported")
        paths = tuple(item.source_path for item in self.supporting_files)
        if tuple(sorted(paths)) != paths or len(paths) != len(set(paths)):
            raise AuthoritativeTruthError("supporting source files must be sorted and unique")
        if self.source_path in paths:
            raise AuthoritativeTruthError("primary source file must not repeat as supporting input")

    def normalize(self, text: str) -> str:
        """Apply the only supported, declared text transformations in order."""
        normalized = text
        for rule in self.normalization_rules:
            if rule == "none":
                continue
            if rule == "normalize-lf":
                normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
            elif rule == "unicode-nfc":
                normalized = unicodedata.normalize("NFC", normalized)
            elif rule == "strip-final-newline":
                if normalized.endswith("\n"):
                    normalized = normalized[:-1]
            else:  # Defensive: fail closed if the allowlist changes incorrectly.
                raise AuthoritativeTruthError("normalization rule is unsupported")
        return normalized

    def decode_truth_artifact(
        self, *, source_file: bytes, converted_text: bytes | None
    ) -> str:
        artifact = self.text_derivation.truth_artifact(
            source_file=source_file, converted_text=converted_text
        )
        try:
            return artifact.decode(self.text_derivation.text_encoding, errors="strict")
        except UnicodeDecodeError as error:
            raise AuthoritativeTruthError(
                "text-authority artifact cannot be decoded as declared"
            ) from error

    def verify_material(
        self,
        *,
        source_archive: bytes,
        source_file: bytes,
        converter: bytes | None = None,
        converted_text: bytes | None = None,
        supporting_files: Mapping[str, bytes] | None = None,
    ) -> None:
        if not isinstance(source_archive, bytes) or not source_archive:
            raise AuthoritativeTruthError("source archive bytes must be non-empty")
        if hashlib.sha256(source_archive).hexdigest() != self.source_archive_sha256:
            raise AuthoritativeTruthError("source archive SHA-256 does not match provenance")
        if not isinstance(source_file, bytes) or not source_file:
            raise AuthoritativeTruthError("source file bytes must be non-empty")
        if hashlib.sha256(source_file).hexdigest() != self.source_file_sha256:
            raise AuthoritativeTruthError("source file SHA-256 does not match provenance")
        supplied_supporting = supporting_files or {}
        expected_supporting = {item.source_path: item for item in self.supporting_files}
        if set(supplied_supporting) != set(expected_supporting):
            raise AuthoritativeTruthError(
                "supporting source inputs must exactly match provenance paths"
            )
        for path, item in expected_supporting.items():
            content = supplied_supporting[path]
            if not isinstance(content, bytes) or not content:
                raise AuthoritativeTruthError("supporting source input bytes must be non-empty")
            if hashlib.sha256(content).hexdigest() != item.sha256:
                raise AuthoritativeTruthError("supporting source SHA-256 does not match provenance")
        archive_inputs = {self.source_path: source_file, **supplied_supporting}
        try:
            with tarfile.open(fileobj=io.BytesIO(source_archive), mode="r:*") as archive:
                members: dict[str, tarfile.TarInfo] = {}
                for member in archive.getmembers():
                    if member.name in archive_inputs:
                        if member.name in members:
                            raise AuthoritativeTruthError(
                                "source archive contains a duplicate declared member path"
                            )
                        members[member.name] = member
                if set(members) != set(archive_inputs):
                    raise AuthoritativeTruthError(
                        "declared source paths must exist as exact archive members"
                    )
                for path, expected_bytes in archive_inputs.items():
                    member = members[path]
                    if not member.isfile():
                        raise AuthoritativeTruthError(
                            "declared source archive members must be regular files"
                        )
                    stream = archive.extractfile(member)
                    if stream is None or stream.read() != expected_bytes:
                        raise AuthoritativeTruthError(
                            "declared source bytes do not match their archive members"
                        )
        except (tarfile.TarError, OSError) as error:
            raise AuthoritativeTruthError(
                "source archive must be a readable tar archive"
            ) from error
        self.text_derivation.verify(converter=converter, converted_text=converted_text)

    def to_dict(self) -> dict[str, object]:
        return {
            "edition_identity": self.edition_identity,
            "normalization_rules": list(self.normalization_rules),
            "source_archive_sha256": self.source_archive_sha256,
            "source_file_sha256": self.source_file_sha256,
            "source_path": self.source_path,
            "supporting_files": [item.to_dict() for item in self.supporting_files],
            "text_derivation": self.text_derivation.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> TypesetterSourceProvenance:
        record = _mapping(value, "provenance")
        rules = _list(record.get("normalization_rules"), "normalization_rules")
        supporting = _list(record.get("supporting_files", []), "supporting_files")
        return cls(
            str(record.get("source_archive_sha256", "")),
            str(record.get("source_file_sha256", "")),
            str(record.get("edition_identity", "")),
            str(record.get("source_path", "")),
            tuple(str(rule) for rule in rules),
            TextDerivation.from_dict(record.get("text_derivation")),
            tuple(SupportingSourceFile.from_dict(item) for item in supporting),
        )


@dataclass(frozen=True, slots=True)
class QueuePageBinding:
    """Digest-bound identity of the exact selected page and its rendered raster."""

    source_sha256: str
    source_page_index: int
    render_sha256: str
    queue_page_sha256: str

    def __post_init__(self) -> None:
        _sha256(self.source_sha256, "binding.source_sha256")
        _sha256(self.render_sha256, "binding.render_sha256")
        _sha256(self.queue_page_sha256, "binding.queue_page_sha256")
        if self.source_page_index < 0:
            raise AuthoritativeTruthError("binding.source_page_index must be non-negative")

    @classmethod
    def from_queue_page(cls, page: QueuePage) -> QueuePageBinding:
        return cls(
            page.source_sha256,
            page.source_page_index,
            page.render_sha256,
            hashlib.sha256(_canonical_json(page.to_dict()).encode("utf-8")).hexdigest(),
        )

    def verify(self, page: QueuePage) -> None:
        expected = self.from_queue_page(page)
        if self != expected:
            raise AuthoritativeTruthError(
                "authoritative truth is not bound to this exact QueuePage source, render, and index"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "queue_page_sha256": self.queue_page_sha256,
            "render_sha256": self.render_sha256,
            "source_page_index": self.source_page_index,
            "source_sha256": self.source_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> QueuePageBinding:
        record = _mapping(value, "queue_binding")
        return cls(
            str(record.get("source_sha256", "")),
            _integer(record.get("source_page_index"), "queue_binding.source_page_index"),
            str(record.get("render_sha256", "")),
            str(record.get("queue_page_sha256", "")),
        )


@dataclass(frozen=True, slots=True)
class MappingAnchor:
    """A matched identifier present in both source lineage and scanned page."""

    kind: str
    source_value: str
    scan_value: str
    match_method: str = "literal"

    def __post_init__(self) -> None:
        if self.kind not in _ANCHOR_KINDS:
            raise AuthoritativeTruthError("mapping anchor has an unsupported kind")
        _text(self.source_value, "mapping anchor source_value")
        _text(self.scan_value, "mapping anchor scan_value")
        if self.match_method not in {"literal", "normalized"}:
            raise AuthoritativeTruthError(
                "mapping anchor match_method must be literal or normalized"
            )
        if self.match_method == "literal" and self.source_value != self.scan_value:
            raise AuthoritativeTruthError(
                "literal mapping anchors must have identical source and scan values"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "match_method": self.match_method,
            "scan_value": self.scan_value,
            "source_value": self.source_value,
        }

    @classmethod
    def from_dict(cls, value: object) -> MappingAnchor:
        record = _mapping(value, "mapping anchor")
        return cls(
            str(record.get("kind", "")),
            str(record.get("source_value", "")),
            str(record.get("scan_value", "")),
            str(record.get("match_method", "literal")),
        )


@dataclass(frozen=True, slots=True)
class MappingEvidence:
    """Evidence sufficient to auto-accept a page mapping or route it to review."""

    anchors: tuple[MappingAnchor, ...]
    verification_state: str

    def __post_init__(self) -> None:
        if not self.anchors:
            raise AuthoritativeTruthError(
                "mapping evidence requires at least one source/scan anchor"
            )
        anchor_keys = tuple(
            (anchor.kind, anchor.source_value, anchor.scan_value, anchor.match_method)
            for anchor in self.anchors
        )
        if len(anchor_keys) != len(set(anchor_keys)):
            raise AuthoritativeTruthError("mapping evidence anchors must be unique")
        if self.verification_state not in _MAPPING_STATES:
            raise AuthoritativeTruthError("mapping evidence has an unsupported verification state")
        if self.verification_state == "verified":
            kinds = {anchor.kind for anchor in self.anchors}
            if len(self.anchors) < 2 or not kinds & {
                "printed-page-number",
                "source-footer",
                "scan-footer",
            }:
                raise AuthoritativeTruthError(
                    "verified mapping needs two anchors including a page-number or footer anchor"
                )

    @property
    def ready(self) -> bool:
        return self.verification_state == "verified"

    def to_dict(self) -> dict[str, object]:
        return {
            "anchors": [anchor.to_dict() for anchor in self.anchors],
            "verification_state": self.verification_state,
        }

    @classmethod
    def from_dict(cls, value: object) -> MappingEvidence:
        record = _mapping(value, "mapping_evidence")
        return cls(
            tuple(
                MappingAnchor.from_dict(item)
                for item in _list(record.get("anchors"), "anchors")
            ),
            str(record.get("verification_state", "")),
        )


@dataclass(frozen=True, slots=True)
class AuthoritativeRegionTruth:
    """Literal text, geometry, and source range for one page region."""

    geometry: RegionGeometry
    literal_text: str
    line_breaks: tuple[int, ...]
    kind: str
    source_span: SourceSpan
    required: bool = True
    layout_verification_state: str = "human-review-required"

    def __post_init__(self) -> None:
        if not isinstance(self.literal_text, str):
            raise AuthoritativeTruthError("authoritative literal_text must be a string")
        if not self.kind.strip() or self.kind != self.kind.strip():
            raise AuthoritativeTruthError("authoritative region kind is required")
        if any(
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
            or offset > len(self.literal_text)
            for offset in self.line_breaks
        ):
            raise AuthoritativeTruthError("line-break offsets must be within literal text")
        if tuple(sorted(self.line_breaks)) != self.line_breaks or len(self.line_breaks) != len(
            set(self.line_breaks)
        ):
            raise AuthoritativeTruthError("line-break offsets must be sorted and unique")
        if self.layout_verification_state not in _LAYOUT_STATES:
            raise AuthoritativeTruthError("region layout_verification_state is unsupported")

    def to_dict(self) -> dict[str, object]:
        return {
            "geometry": self.geometry.to_dict(),
            "kind": self.kind,
            "line_breaks": list(self.line_breaks),
            "layout_verification_state": self.layout_verification_state,
            "literal_text": self.literal_text,
            "required": self.required,
            "source_span": self.source_span.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> AuthoritativeRegionTruth:
        record = _mapping(value, "authoritative region")
        geometry = _mapping(record.get("geometry"), "geometry")
        polygon = _points(geometry.get("polygon"), "geometry.polygon")
        baseline = _points(geometry.get("baseline"), "geometry.baseline")
        text = record.get("literal_text")
        if not isinstance(text, str):
            raise AuthoritativeTruthError("authoritative literal_text must be a string")
        required = record.get("required", True)
        if not isinstance(required, bool):
            raise AuthoritativeTruthError("authoritative region required must be boolean")
        return cls(
            RegionGeometry(
                str(geometry.get("region_id", "")),
                polygon,
                baseline,
                _integer(geometry.get("reading_order"), "geometry.reading_order"),
                str(geometry.get("semantic_type", "")),
            ),
            text,
            tuple(
                _integer(item, "line_break")
                for item in _list(record.get("line_breaks"), "line_breaks")
            ),
            str(record.get("kind", "")),
            SourceSpan.from_dict(record.get("source_span")),
            required,
            str(record.get("layout_verification_state", "human-review-required")),
        )


def _points(value: object, name: str) -> tuple[tuple[int, int], ...]:
    result: list[tuple[int, int]] = []
    for index, point in enumerate(_list(value, name)):
        values = _list(point, f"{name}[{index}]")
        if len(values) != 2:
            raise AuthoritativeTruthError(f"{name}[{index}] must contain two coordinates")
        result.append(
            (
                _integer(values[0], f"{name}[{index}][0]"),
                _integer(values[1], f"{name}[{index}][1]"),
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class AuthoritativeTruthStatus:
    disposition: str
    page_id: str
    truth_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "disposition": self.disposition,
            "page_id": self.page_id,
            "truth_sha256": self.truth_sha256,
        }


@dataclass(frozen=True, slots=True)
class AuthoritativeReviewEvidence:
    """Human review record bound to the exact UI project and annotation bytes."""

    reviewer: str
    project_sha256: str
    annotations_sha256: str

    def __post_init__(self) -> None:
        _text(self.reviewer, "authoritative reviewer")
        _sha256(self.project_sha256, "review project SHA-256")
        _sha256(self.annotations_sha256, "review annotations SHA-256")

    def to_dict(self) -> dict[str, str]:
        return {
            "annotations_sha256": self.annotations_sha256,
            "project_sha256": self.project_sha256,
            "reviewer": self.reviewer,
        }

    @classmethod
    def from_dict(cls, value: object) -> AuthoritativeReviewEvidence:
        record = _mapping(value, "authoritative review evidence")
        return cls(
            str(record.get("reviewer", "")),
            str(record.get("project_sha256", "")),
            str(record.get("annotations_sha256", "")),
        )


@dataclass(frozen=True, slots=True)
class AuthoritativeMaterial:
    """Exact local bytes required to verify one source-backed truth package."""

    source_archive: bytes
    source_file: bytes
    converter: bytes | None = None
    converted_text: bytes | None = None
    supporting_files: tuple[tuple[str, bytes], ...] = ()
    review_project: bytes | None = None
    review_annotations: bytes | None = None
    review_assets: tuple[tuple[str, bytes], ...] = ()

    def __post_init__(self) -> None:
        paths = tuple(path for path, _ in self.supporting_files)
        if tuple(sorted(paths)) != paths or len(paths) != len(set(paths)):
            raise AuthoritativeTruthError("material supporting files must be sorted and unique")
        for path, content in self.supporting_files:
            _safe_relative_path(path, "material supporting source path")
            if not isinstance(content, bytes) or not content:
                raise AuthoritativeTruthError(
                    "material supporting source bytes must be non-empty"
                )
        if (self.review_project is None) != (self.review_annotations is None):
            raise AuthoritativeTruthError(
                "review project and annotation bytes must be supplied together"
            )
        asset_ids = tuple(asset_id for asset_id, _ in self.review_assets)
        if tuple(sorted(asset_ids)) != asset_ids or len(asset_ids) != len(set(asset_ids)):
            raise AuthoritativeTruthError("review assets must have sorted, unique IDs")
        if self.review_project is None and self.review_assets:
            raise AuthoritativeTruthError("review assets require project and annotation bytes")
        if any(
            not asset_id or not isinstance(content, bytes) or not content
            for asset_id, content in self.review_assets
        ):
            raise AuthoritativeTruthError("review assets require IDs and non-empty bytes")

    @property
    def supporting_file_map(self) -> Mapping[str, bytes]:
        return dict(self.supporting_files)


@dataclass(frozen=True, slots=True)
class AuthoritativeTruthPackage:
    """A fully source-, render-, and mapping-bound truth package for one page."""

    version: str
    queue_page: QueuePage
    queue_binding: QueuePageBinding
    provenance: TypesetterSourceProvenance
    mapping_evidence: MappingEvidence
    regions: tuple[AuthoritativeRegionTruth, ...]
    review_evidence: AuthoritativeReviewEvidence | None = None

    def __post_init__(self) -> None:
        if self.version != AUTHORITATIVE_TRUTH_VERSION:
            raise AuthoritativeTruthError("unsupported authoritative truth package version")
        self.queue_binding.verify(self.queue_page)
        if not self.regions:
            raise AuthoritativeTruthError("authoritative truth package needs at least one region")
        region_ids = tuple(region.geometry.region_id for region in self.regions)
        if len(region_ids) != len(set(region_ids)):
            raise AuthoritativeTruthError("authoritative truth region IDs must be unique")
        if tuple(sorted(region_ids)) != self.queue_page.inventory_region_ids:
            raise AuthoritativeTruthError(
                "authoritative truth regions must exactly match the QueuePage inventory"
            )

    @property
    def ready(self) -> bool:
        return self.review_evidence is not None and self.mapping_evidence.ready and all(
            region.layout_verification_state == "verified" for region in self.regions
        )

    def status(self) -> AuthoritativeTruthStatus:
        layout_states = {region.layout_verification_state for region in self.regions}
        disposition = "authoritative-ready"
        if "discrepancy" in layout_states:
            disposition = "source-scan-discrepancy"
        elif not self.mapping_evidence.ready:
            disposition = "human-mapping-review-required"
        elif "human-review-required" in layout_states:
            disposition = "human-layout-review-required"
        elif self.review_evidence is None:
            disposition = "human-review-evidence-required"
        return AuthoritativeTruthStatus(
            disposition,
            self.queue_page.id,
            self.truth_digest(),
        )

    def verify_material(
        self,
        *,
        source_archive: bytes,
        source_file: bytes,
        converter: bytes | None = None,
        converted_text: bytes | None = None,
        supporting_files: Mapping[str, bytes] | None = None,
    ) -> None:
        self.provenance.verify_material(
            source_archive=source_archive,
            source_file=source_file,
            converter=converter,
            converted_text=converted_text,
            supporting_files=supporting_files,
        )
        authority_text = self.provenance.decode_truth_artifact(
            source_file=source_file, converted_text=converted_text
        )
        for region in self.regions:
            selected = region.source_span.select(authority_text)
            if self.provenance.normalize(selected) != region.literal_text:
                raise AuthoritativeTruthError(
                    "authoritative literal_text does not exactly match its normalized source span"
                )

    def verify_material_bundle(self, material: AuthoritativeMaterial) -> None:
        """Verify a path-resolved material bundle without trusting its filenames."""
        self.verify_material(
            source_archive=material.source_archive,
            source_file=material.source_file,
            converter=material.converter,
            converted_text=material.converted_text,
            supporting_files=material.supporting_file_map,
        )
        if self.review_evidence is None:
            if material.review_project is not None:
                raise AuthoritativeTruthError(
                    "unreviewed truth package must not receive detached review bytes"
                )
            return
        if material.review_project is None or material.review_annotations is None:
            raise AuthoritativeTruthError(
                "reviewed truth verification requires exact project and annotation bytes"
            )
        from .review_annotations import apply_review_annotations

        reviewed = apply_review_annotations(
            self,
            project_bytes=material.review_project,
            annotations_bytes=material.review_annotations,
            asset_bytes=dict(material.review_assets),
        )
        if reviewed != self:
            raise AuthoritativeTruthError(
                "review bytes do not reproduce the authoritative truth review state"
            )

    def truth_digest(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_dict()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "mapping_evidence": self.mapping_evidence.to_dict(),
            "provenance": self.provenance.to_dict(),
            "queue_binding": self.queue_binding.to_dict(),
            "queue_page": self.queue_page.to_dict(),
            "regions": [region.to_dict() for region in self.regions],
            "review_evidence": self.review_evidence.to_dict() if self.review_evidence else None,
            "version": self.version,
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict()) + "\n"

    @classmethod
    def from_dict(cls, value: object) -> AuthoritativeTruthPackage:
        # Kept local to avoid making the existing workspace module depend on
        # this optional provenance path.
        from .workspace import queue_page_from_dict

        record = _mapping(value, "authoritative truth package")
        review_evidence = record.get("review_evidence")
        return cls(
            str(record.get("version", "")),
            queue_page_from_dict(record.get("queue_page")),
            QueuePageBinding.from_dict(record.get("queue_binding")),
            TypesetterSourceProvenance.from_dict(record.get("provenance")),
            MappingEvidence.from_dict(record.get("mapping_evidence")),
            tuple(
                AuthoritativeRegionTruth.from_dict(item)
                for item in _list(record.get("regions"), "regions")
            ),
            (
                AuthoritativeReviewEvidence.from_dict(review_evidence)
                if review_evidence is not None
                else None
            ),
        )

    @classmethod
    def from_json(cls, value: str) -> AuthoritativeTruthPackage:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise AuthoritativeTruthError("authoritative truth JSON is invalid") from error
        return cls.from_dict(parsed)
