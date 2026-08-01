"""Guarded, declarative corrections for immutable LMDOC v1 records.

Patches are deliberately separate from generated views and from the canonical
records they modify. Applying one returns replacement dataclasses plus a stable
provenance record; callers decide when and where to persist those results.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from lispmdoc.model import Box, PageRecord, SceneObject, StructureRecord, StylesRecord
from lispmdoc.model.canonical import (
    FORMAT_VERSION,
    CanonicalizationError,
    canonical_json_bytes,
    sha256_hex,
)


class PatchError(ValueError):
    """Base class for invalid, stale, or inapplicable correction patches."""


class PatchSchemaError(PatchError):
    """The patch did not conform to the local LMDOC override schema."""


class StalePatchError(PatchError):
    """A source, region, fingerprint, text, or old-value guard did not match."""


class UnsupportedPatchOperation(PatchError):
    """The declared operation is intentionally not safe to apply yet."""


PatchOperation = Literal[
    "replace-text",
    "replace-geometry",
    "relabel-semantics",
    "reorder-reading",
    "replace-style",
]


@dataclass(frozen=True, slots=True)
class PatchGuard:
    source_page_sha256: str
    region_id: str
    expected_region_fingerprint: str
    original_text_sha256: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PatchGuard:
        try:
            return cls(
                source_page_sha256=_string(value["source_page_sha256"], "guard.source_page_sha256"),
                region_id=_string(value["region_id"], "guard.region_id"),
                expected_region_fingerprint=_string(
                    value["expected_region_fingerprint"], "guard.expected_region_fingerprint"
                ),
                original_text_sha256=_string(
                    value["original_text_sha256"], "guard.original_text_sha256"
                ),
            )
        except KeyError as error:
            raise PatchError(f"missing required patch guard member: {error.args[0]}") from error

    def to_dict(self) -> dict[str, str]:
        return {
            "source_page_sha256": self.source_page_sha256,
            "region_id": self.region_id,
            "expected_region_fingerprint": self.expected_region_fingerprint,
            "original_text_sha256": self.original_text_sha256,
        }


@dataclass(frozen=True, slots=True)
class CorrectionPatch:
    """A validated schema-level correction, still guarded before application."""

    target_id: str
    guard: PatchGuard
    operation: PatchOperation
    reason: str
    reviewer: str
    old_value: Any
    new_value: Any
    format_version: str = FORMAT_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CorrectionPatch:
        _validate_override_schema(value)
        guard = value.get("guard")
        if not isinstance(
            guard, Mapping
        ):  # Schema validation keeps this defensive branch unreachable.
            raise PatchSchemaError("guard must be an object")
        operation = _string(value["operation"], "operation")
        return cls(
            target_id=_string(value["target_id"], "target_id"),
            guard=PatchGuard.from_dict(guard),
            operation=operation,  # type: ignore[arg-type]
            reason=_string(value["reason"], "reason"),
            reviewer=_string(value["reviewer"], "reviewer"),
            old_value=value["old_value"],
            new_value=value["new_value"],
            format_version=_string(value["format_version"], "format_version"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "target_id": self.target_id,
            "guard": self.guard.to_dict(),
            "operation": self.operation,
            "reason": self.reason,
            "reviewer": self.reviewer,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }

    @property
    def sha256(self) -> str:
        return sha256_hex(self.to_dict())


@dataclass(frozen=True, slots=True)
class AppliedPatch:
    """Deterministic accepted-correction provenance; it contains no wall clock."""

    patch_sha256: str
    target_id: str
    guard: PatchGuard
    operation: PatchOperation
    reason: str
    reviewer: str
    old_value: Any
    new_value: Any

    @classmethod
    def from_patch(cls, patch: CorrectionPatch) -> AppliedPatch:
        return cls(
            patch.sha256,
            patch.target_id,
            patch.guard,
            patch.operation,
            patch.reason,
            patch.reviewer,
            patch.old_value,
            patch.new_value,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_sha256": self.patch_sha256,
            "target_id": self.target_id,
            "guard": self.guard.to_dict(),
            "operation": self.operation,
            "reason": self.reason,
            "reviewer": self.reviewer,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }


@dataclass(frozen=True, slots=True)
class PatchApplication:
    """New immutable records and provenance after exactly one accepted patch."""

    page: PageRecord
    structure: StructureRecord
    styles: StylesRecord
    provenance: AppliedPatch


def override_schema_path() -> Path:
    """Locate the repository-shipped schema without consulting the network."""
    return Path(__file__).resolve().parents[3] / "schemas" / "overrides.schema.json"


def load_patch(path: Path, *, schema_path: Path | None = None) -> CorrectionPatch:
    """Load and validate an override JSON document from a local path."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PatchSchemaError(f"cannot load patch {path}: {error}") from error
    if not isinstance(value, Mapping):
        raise PatchSchemaError("override patch root must be an object")
    return parse_patch(value, schema_path=schema_path)


def parse_patch(value: Mapping[str, Any], *, schema_path: Path | None = None) -> CorrectionPatch:
    """Schema-check a mapping locally and convert it into a correction record."""
    _validate_override_schema(value, schema_path=schema_path)
    return CorrectionPatch.from_dict(value)


def region_fingerprint(region: SceneObject) -> str:
    """Return the canonical fingerprint protected by ``expected_region_fingerprint``."""
    return sha256_hex(region.to_dict())


def text_sha256(region: SceneObject) -> str:
    """Return the exact UTF-8 hash of the diplomatic text for patch guards."""
    return hashlib.sha256(_region_text(region).encode("utf-8")).hexdigest()


def apply_patch(
    patch: CorrectionPatch,
    page: PageRecord,
    structure: StructureRecord,
    styles: StylesRecord,
) -> PatchApplication:
    """Apply one guard-checked correction without writing any files or views."""
    region = _guarded_region(patch, page)
    if structure.document_id != styles.document_id:
        raise PatchError("structure and styles must belong to the same document")

    if patch.operation == "replace-text":
        replacement = _replace_text(patch, region)
        page = _replace_region(page, replacement)
    elif patch.operation == "replace-geometry":
        replacement = _replace_geometry(patch, region, page)
        page = _replace_region(page, replacement)
    elif patch.operation == "reorder-reading":
        page = _replace_reading_order(patch, page)
    elif patch.operation == "replace-style":
        replacement = _replace_style(patch, region, styles)
        page = _replace_region(page, replacement)
    elif patch.operation == "relabel-semantics":
        raise UnsupportedPatchOperation(
            "relabel-semantics is not applied until semantic-label semantics are versioned"
        )
    else:  # Defensive in case a caller creates the dataclass without schema parsing.
        raise UnsupportedPatchOperation(f"unsupported correction operation: {patch.operation!r}")

    return PatchApplication(page, structure, styles, AppliedPatch.from_patch(patch))


def apply_patches(
    patches: tuple[CorrectionPatch, ...],
    page: PageRecord,
    structure: StructureRecord,
    styles: StylesRecord,
) -> tuple[PageRecord, StructureRecord, StylesRecord, tuple[AppliedPatch, ...]]:
    """Apply ordered patches atomically in memory; a failure returns no partial result."""
    accepted: list[AppliedPatch] = []
    current_page, current_structure, current_styles = page, structure, styles
    for patch in patches:
        result = apply_patch(patch, current_page, current_structure, current_styles)
        current_page, current_structure, current_styles = (
            result.page,
            result.structure,
            result.styles,
        )
        accepted.append(result.provenance)
    return current_page, current_structure, current_styles, tuple(accepted)


def _validate_override_schema(value: Mapping[str, Any], schema_path: Path | None = None) -> None:
    path = schema_path or override_schema_path()
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise PatchSchemaError(f"cannot load local override schema {path}: {error}") from error
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise PatchSchemaError(f"override patch {location}: {first.message}")
    try:
        canonical_json_bytes(value)
    except CanonicalizationError as error:
        raise PatchSchemaError(
            f"override patch is not canonically serializable: {error}"
        ) from error


def _guarded_region(patch: CorrectionPatch, page: PageRecord) -> SceneObject:
    if patch.target_id != patch.guard.region_id:
        raise StalePatchError("target_id must equal guard.region_id")
    if patch.guard.source_page_sha256 != page.source_page_sha256:
        raise StalePatchError("source page hash guard does not match target page")
    region = next((item for item in page.objects if item.id == patch.target_id), None)
    if region is None:
        raise StalePatchError(f"target region does not exist on page: {patch.target_id}")
    if region_fingerprint(region) != patch.guard.expected_region_fingerprint:
        raise StalePatchError("expected region fingerprint does not match target region")
    if text_sha256(region) != patch.guard.original_text_sha256:
        raise StalePatchError("original text hash does not match target region")
    return region


def _replace_text(patch: CorrectionPatch, region: SceneObject) -> SceneObject:
    current = _region_text(region)
    if patch.old_value != current:
        raise StalePatchError("replace-text old_value does not match current diplomatic text")
    if not isinstance(patch.new_value, str):
        raise PatchError("replace-text new_value must be a string")
    payload = dict(region.payload)
    field = "literal_text" if "literal_text" in payload else "text"
    payload[field] = patch.new_value
    return replace(region, payload=payload)


def _replace_geometry(patch: CorrectionPatch, region: SceneObject, page: PageRecord) -> SceneObject:
    if patch.old_value != region.box.to_dict():
        raise StalePatchError("replace-geometry old_value does not match current region box")
    if not isinstance(patch.new_value, Mapping):
        raise PatchError("replace-geometry new_value must be a box object")
    try:
        box = Box.from_dict(dict(patch.new_value))
    except (KeyError, TypeError, ValueError) as error:
        raise PatchError(f"replace-geometry new_value is not a valid box: {error}") from error
    if not page.page_box.contains_box(box):
        raise PatchError("replace-geometry box must stay within the page bounds")
    return replace(region, box=box)


def _replace_reading_order(patch: CorrectionPatch, page: PageRecord) -> PageRecord:
    if patch.old_value != list(page.reading_order):
        raise StalePatchError("reorder-reading old_value does not match current reading order")
    if not isinstance(patch.new_value, list) or not all(
        isinstance(identifier, str) for identifier in patch.new_value
    ):
        raise PatchError("reorder-reading new_value must be an array of region IDs")
    replacement = tuple(patch.new_value)
    if len(replacement) != len(set(replacement)):
        raise PatchError("reorder-reading may not repeat region IDs")
    if set(replacement) != set(page.reading_order):
        raise PatchError("reorder-reading may only permute the existing page reading order")
    return replace(page, reading_order=replacement)


def _replace_style(
    patch: CorrectionPatch, region: SceneObject, styles: StylesRecord
) -> SceneObject:
    if patch.old_value != region.style_id:
        raise StalePatchError("replace-style old_value does not match current style ID")
    if not isinstance(patch.new_value, str):
        raise PatchError("replace-style new_value must be a style ID")
    if patch.new_value not in {token.id for token in styles.tokens}:
        raise PatchError("replace-style new_value does not name a known style token")
    return replace(region, style_id=patch.new_value)


def _replace_region(page: PageRecord, replacement: SceneObject) -> PageRecord:
    objects = tuple(replacement if item.id == replacement.id else item for item in page.objects)
    return replace(page, objects=objects)


def _region_text(region: SceneObject) -> str:
    text = region.payload.get("literal_text", region.payload.get("text"))
    if not isinstance(text, str):
        raise PatchError("target region does not contain a patchable diplomatic text field")
    return text


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise PatchSchemaError(f"{field} must be a string")
    return value
