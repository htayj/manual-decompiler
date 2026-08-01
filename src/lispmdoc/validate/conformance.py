"""Offline structural validation for LMDOC authoring trees and `.lmdoc` ZIPs.

This validator checks only evidence present in the canonical package.  It does
not attempt to infer OCR accuracy, visual fidelity, accessibility, or a
deterministic rebuild from a static tree; those are separate conformance gates.
"""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

from jsonschema import Draft202012Validator

from lispmdoc.model import canonical_json_bytes
from lispmdoc.package import PackageError, inspect_package
from lispmdoc.raster import (
    RASTER_REASON_CODES,
    PixelBox,
    RasterRegion,
    approved_photo_dominant_disposition,
    evaluate_page_raster_policy,
    inspect_encoded_raster,
    validate_raster_mapping,
)

Severity = Literal["error", "warning", "info"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
@dataclass(frozen=True, slots=True, order=True)
class Finding:
    """One deterministic, machine-readable validation observation."""

    severity: Severity
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Result of structural package validation, stable enough to write as JSON."""

    target: str
    claimed_conformance: str | None
    effective_conformance: str | None
    findings: tuple[Finding, ...]

    @property
    def is_structurally_valid(self) -> bool:
        return not any(finding.severity == "error" for finding in self.findings)

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_version": "1.0",
            "target": self.target,
            "claimed_conformance": self.claimed_conformance,
            "effective_conformance": self.effective_conformance,
            "structurally_valid": self.is_structurally_valid,
            "findings": [finding.to_dict() for finding in self.findings],
        }


class _Reader(Protocol):
    target: str

    def names(self) -> tuple[str, ...]: ...

    def read_bytes(self, name: str) -> bytes: ...


class _TreeReader:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.target = root.as_posix()

    def names(self) -> tuple[str, ...]:
        if not self.root.is_dir():
            return ()
        return tuple(
            sorted(
                path.relative_to(self.root).as_posix()
                for path in self.root.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
        )

    def read_bytes(self, name: str) -> bytes:
        return (self.root / PurePosixPath(name)).read_bytes()


class _ZipReader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.target = path.as_posix()
        with zipfile.ZipFile(path) as archive:
            self._names = tuple(
                entry.filename for entry in archive.infolist() if not entry.is_dir()
            )

    def names(self) -> tuple[str, ...]:
        return self._names

    def read_bytes(self, name: str) -> bytes:
        with zipfile.ZipFile(self.path) as archive:
            return archive.read(name)


def default_schema_root() -> Path:
    """Return the repository schema directory without making a network request."""
    return Path(__file__).resolve().parents[3] / "schemas"


def validate_tree(root: Path, *, schema_root: Path | None = None) -> ValidationReport:
    """Validate an unpacked LMDOC authoring tree."""
    return _validate(_TreeReader(root), schema_root or default_schema_root())


def validate_package(path: Path, *, schema_root: Path | None = None) -> ValidationReport:
    """Validate an `.lmdoc` ZIP without extracting it."""
    try:
        inspect_package(path)
        reader = _ZipReader(path)
    except (OSError, PackageError, zipfile.BadZipFile) as error:
        finding = Finding("error", "INVALID_PACKAGE", path.as_posix(), str(error))
        return ValidationReport(path.as_posix(), None, None, (finding,))
    return _validate(reader, schema_root or default_schema_root())


def validate_lmdoc(path: Path, *, schema_root: Path | None = None) -> ValidationReport:
    """Dispatch to tree or ZIP validation based on the resolved target type."""
    return (
        validate_tree(path, schema_root=schema_root)
        if path.is_dir()
        else validate_package(path, schema_root=schema_root)
    )


def _validate(reader: _Reader, schema_root: Path) -> ValidationReport:
    findings: list[Finding] = []
    names = reader.names()
    _validate_names(names, findings)
    manifest = _load_json(reader, "manifest.json", findings)
    if not isinstance(manifest, dict):
        return _report(reader.target, None, None, findings)

    _validate_json_schema(manifest, schema_root / "manifest.schema.json", "manifest.json", findings)
    claimed = _string_or_none(manifest.get("conformance_level"))
    pages = _manifest_pages(manifest, findings)
    page_records = _validate_pages(reader, names, pages, schema_root, findings)
    _validate_document_records(reader, names, manifest, schema_root, page_records, findings)
    _validate_assets(reader, names, page_records, findings)
    _validate_evidence_records(reader, names, page_records, findings)
    _validate_source_if_present(reader, names, manifest, findings)
    effective = _effective_conformance(claimed, findings)
    return _report(reader.target, claimed, effective, findings)


def _report(
    target: str,
    claimed: str | None,
    effective: str | None,
    findings: Iterable[Finding],
) -> ValidationReport:
    order = {"error": 0, "warning": 1, "info": 2}
    sorted_findings = tuple(
        sorted(
            findings, key=lambda item: (order[item.severity], item.code, item.path, item.message)
        )
    )
    return ValidationReport(target, claimed, effective, sorted_findings)


def _validate_names(names: tuple[str, ...], findings: list[Finding]) -> None:
    if len(names) != len(set(names)):
        _error(findings, "DUPLICATE_ENTRY", "<package>", "package contains duplicate file entries")
    for name in names:
        parts = PurePosixPath(name).parts
        if name.startswith("/") or ".." in parts or "" in parts:
            _error(
                findings, "UNSAFE_ENTRY", name, "package entry must be a safe relative POSIX path"
            )


def _load_json(reader: _Reader, name: str, findings: list[Finding]) -> dict[str, Any] | None:
    if name not in reader.names():
        _error(findings, "MISSING_FILE", name, "required canonical file is absent")
        return None
    try:
        value = json.loads(reader.read_bytes(name).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _error(findings, "INVALID_JSON", name, str(error))
        return None
    if not isinstance(value, dict):
        _error(findings, "INVALID_RECORD", name, "canonical JSON record must be an object")
        return None
    return value


def _validate_json_schema(
    instance: Mapping[str, Any], schema_path: Path, instance_name: str, findings: list[Finding]
) -> None:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        _error(findings, "SCHEMA_UNAVAILABLE", schema_path.as_posix(), str(error))
        return
    for schema_error in sorted(
        validator.iter_errors(instance), key=lambda item: list(item.absolute_path)
    ):
        suffix = "/".join(str(part) for part in schema_error.absolute_path)
        location = instance_name if not suffix else f"{instance_name}/{suffix}"
        _error(findings, "SCHEMA_INVALID", location, schema_error.message)


def _manifest_pages(
    manifest: Mapping[str, Any], findings: list[Finding]
) -> tuple[Mapping[str, Any], ...]:
    raw_pages = manifest.get("pages")
    if not isinstance(raw_pages, list):
        return ()
    pages = tuple(item for item in raw_pages if isinstance(item, Mapping))
    sequence = [item.get("sequence") for item in pages]
    if sequence != list(range(1, len(pages) + 1)):
        _error(
            findings,
            "PAGE_SEQUENCE",
            "manifest.json/pages",
            "page sequence must be contiguous from 1",
        )
    ids = [item.get("id") for item in pages]
    string_ids = [item for item in ids if isinstance(item, str)]
    if len(string_ids) != len(set(string_ids)):
        _error(
            findings, "DUPLICATE_PAGE_ID", "manifest.json/pages", "manifest page IDs must be unique"
        )
    paths = [item.get("path") for item in pages]
    string_paths = [item for item in paths if isinstance(item, str)]
    if len(string_paths) != len(set(string_paths)):
        _error(
            findings,
            "DUPLICATE_PAGE_PATH",
            "manifest.json/pages",
            "manifest page paths must be unique",
        )
    return pages


def _validate_pages(
    reader: _Reader,
    names: tuple[str, ...],
    manifest_pages: tuple[Mapping[str, Any], ...],
    schema_root: Path,
    findings: list[Finding],
) -> dict[str, Mapping[str, Any]]:
    page_records: dict[str, Mapping[str, Any]] = {}
    scene_ids: set[str] = set()
    expected_paths: set[str] = set()
    for manifest_page in manifest_pages:
        manifest_path = manifest_page.get("path")
        if isinstance(manifest_path, str):
            expected_paths.add(manifest_path)
    actual_paths = {name for name in names if name.startswith("pages/") and name.endswith(".json")}
    for missing in sorted(expected_paths - actual_paths):
        _error(
            findings, "MISSING_PAGE", missing, "manifest references a page record that is absent"
        )
    for extra in sorted(actual_paths - expected_paths):
        _error(
            findings, "UNLISTED_PAGE", extra, "page record is not present in manifest page order"
        )
    for reference in manifest_pages:
        path = reference.get("path")
        if not isinstance(path, str):
            continue
        page = _load_json(reader, path, findings)
        if page is None:
            continue
        _validate_json_schema(page, schema_root / "document.schema.json", path, findings)
        page_id = page.get("id")
        if isinstance(page_id, str):
            if page_id in page_records:
                _error(
                    findings,
                    "DUPLICATE_PAGE_ID",
                    path,
                    f"page ID {page_id!r} occurs more than once",
                )
            page_records[page_id] = page
        for field in ("id", "sequence", "source_page_index"):
            if page.get(field) != reference.get(field):
                _error(
                    findings,
                    "PAGE_REFERENCE_MISMATCH",
                    path,
                    f"page {field} does not match its manifest reference",
                )
        _validate_page_geometry(page, path, findings)
        _validate_reading_order(page, path, findings)
        _validate_rasters(page, path, findings)
        objects = page.get("objects")
        if isinstance(objects, list):
            for index, object_ in enumerate(objects):
                if not isinstance(object_, Mapping):
                    continue
                object_id = object_.get("id")
                if not isinstance(object_id, str):
                    continue
                if object_id in scene_ids:
                    _error(
                        findings,
                        "DUPLICATE_SCENE_ID",
                        f"{path}/objects/{index}/id",
                        f"scene object ID {object_id!r} occurs on more than one page",
                    )
                scene_ids.add(object_id)
    return page_records


def _validate_page_geometry(page: Mapping[str, Any], path: str, findings: list[Finding]) -> None:
    page_box = _box(page.get("page_box"))
    if page_box is None:
        _error(
            findings,
            "INVALID_PAGE_BOX",
            f"{path}/page_box",
            "page box must be a non-empty integer box",
        )
        return
    _validate_transform(
        page.get("source_pdf_to_canonical"), f"{path}/source_pdf_to_canonical", findings
    )
    _validate_transform(
        page.get("render_pixels_to_canonical"), f"{path}/render_pixels_to_canonical", findings
    )
    objects = page.get("objects")
    if not isinstance(objects, list):
        return
    for index, object_ in enumerate(objects):
        if not isinstance(object_, Mapping):
            continue
        box = _box(object_.get("box"))
        object_path = f"{path}/objects/{index}/box"
        if box is None:
            _error(
                findings,
                "INVALID_OBJECT_BOX",
                object_path,
                "object box must be a non-empty integer box",
            )
        elif not _contains(page_box, box):
            _error(
                findings,
                "GEOMETRY_OUT_OF_BOUNDS",
                object_path,
                "object box is outside its page box",
            )


def _validate_transform(value: object, path: str, findings: list[Finding]) -> None:
    if not isinstance(value, Mapping):
        return
    try:
        a, b, c, d = (_fraction(value[name]) for name in ("a", "b", "c", "d"))
    except (KeyError, TypeError, ValueError):
        _error(
            findings,
            "INVALID_TRANSFORM",
            path,
            "affine transform coefficients must be exact rationals",
        )
        return
    if a * d - b * c == 0:
        _error(findings, "SINGULAR_TRANSFORM", path, "affine transform must be invertible")


def _validate_reading_order(page: Mapping[str, Any], path: str, findings: list[Finding]) -> None:
    objects = page.get("objects")
    order = page.get("reading_order")
    if not isinstance(objects, list) or not isinstance(order, list):
        return
    object_ids = [item.get("id") for item in objects if isinstance(item, Mapping)]
    known_object_ids = {item for item in object_ids if isinstance(item, str)}
    order_ids = [item for item in order if isinstance(item, str)]
    if len(order_ids) != len(set(order_ids)):
        _error(
            findings,
            "READING_ORDER_DUPLICATE",
            f"{path}/reading_order",
            "reading order IDs must be unique",
        )
    missing = sorted(set(order_ids) - known_object_ids)
    if missing:
        _error(
            findings,
            "READING_ORDER_REFERENCE",
            f"{path}/reading_order",
            f"unknown object IDs: {missing!r}",
        )
    omitted = sorted(known_object_ids - set(order_ids))
    if omitted:
        _warning(
            findings,
            "READING_ORDER_INCOMPLETE",
            f"{path}/reading_order",
            f"scene objects absent from linearized reading order: {omitted!r}",
        )


def _validate_rasters(page: Mapping[str, Any], path: str, findings: list[Finding]) -> None:
    page_box = _box(page.get("page_box"))
    objects = page.get("objects")
    if page_box is None or not isinstance(objects, list):
        return
    raster_entries: list[tuple[int, Mapping[str, Any], tuple[int, int, int, int]]] = []
    for index, object_ in enumerate(objects):
        if not isinstance(object_, Mapping) or object_.get("kind") != "raster":
            continue
        object_path = f"{path}/objects/{index}"
        payload = object_.get("payload")
        reason = payload.get("reason") if isinstance(payload, Mapping) else None
        if reason not in RASTER_REASON_CODES:
            _error(
                findings,
                "RASTER_REASON_REQUIRED",
                object_path,
                "raster objects require an allowed payload.reason code",
            )
        object_box = _box(object_.get("box"))
        if object_box == page_box:
            _warning(
                findings,
                "FULL_PAGE_RASTER",
                object_path,
                "full-page raster prevents replacement-profile conformance",
            )
        if object_box is not None and _contains(page_box, object_box):
            raster_entries.append((index, object_, object_box))
    if not raster_entries:
        return
    regions = tuple(
        RasterRegion(
            str(object_.get("id", f"raster-{index}")),
            PixelBox(
                box[0] - page_box[0],
                box[1] - page_box[1],
                box[2] - box[0],
                box[3] - box[1],
            ),
            "continuous-tone",
            "continuous-tone-photo",
            "0" * 64,
        )
        for index, object_, box in raster_entries
    )
    approved = all(
        isinstance(object_.get("payload"), Mapping)
        and approved_photo_dominant_disposition(object_["payload"])
        for _, object_, _ in raster_entries
    )
    decision = evaluate_page_raster_policy(
        page_box[2] - page_box[0],
        page_box[3] - page_box[1],
        regions,
        manual_approval_id="aggregate-approved" if approved else None,
        explicitly_photo_dominant=approved,
        contains_meaningful_text_or_vector=not approved,
    )
    if not decision.replica_ready:
        _error(
            findings,
            "LARGE_RASTER_POLICY",
            path,
            "aggregate raster coverage exceeds 80% without an explicit approved "
            "photo-dominant disposition: " + ", ".join(decision.findings),
        )


def _validate_document_records(
    reader: _Reader,
    names: tuple[str, ...],
    manifest: Mapping[str, Any],
    schema_root: Path,
    page_records: Mapping[str, Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    structure = _load_json(reader, "structure.json", findings)
    styles = _load_json(reader, "styles.json", findings)
    if structure is not None:
        _validate_json_schema(
            structure, schema_root / "document.schema.json", "structure.json", findings
        )
        _validate_structure(structure, manifest, page_records, findings)
    if styles is not None:
        _validate_json_schema(styles, schema_root / "document.schema.json", "styles.json", findings)
        _validate_styles(styles, manifest, page_records, findings)
    unexpected = {name for name in names if name in {"structure.json", "styles.json"}}
    if unexpected != {"structure.json", "styles.json"}:
        # _load_json already produced the precise missing-file finding.
        return


def _validate_structure(
    structure: Mapping[str, Any],
    manifest: Mapping[str, Any],
    page_records: Mapping[str, Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    if structure.get("document_id") != manifest.get("document_id"):
        _error(
            findings,
            "DOCUMENT_ID_MISMATCH",
            "structure.json/document_id",
            "does not match manifest",
        )
    nodes = structure.get("nodes")
    if not isinstance(nodes, list):
        return
    node_ids = [node.get("id") for node in nodes if isinstance(node, Mapping)]
    known_node_ids = {node_id for node_id in node_ids if isinstance(node_id, str)}
    if len(known_node_ids) != len([node_id for node_id in node_ids if isinstance(node_id, str)]):
        _error(
            findings,
            "DUPLICATE_STRUCTURE_ID",
            "structure.json/nodes",
            "structure node IDs must be unique",
        )
    if structure.get("root_id") not in known_node_ids:
        _error(
            findings,
            "STRUCTURE_ROOT_REFERENCE",
            "structure.json/root_id",
            "root_id must reference a structure node",
        )
    region_ids = {
        object_.get("id")
        for page in page_records.values()
        for object_ in page.get("objects", [])
        if isinstance(object_, Mapping) and isinstance(object_.get("id"), str)
    }
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            continue
        for child_id in node.get("child_ids", []):
            if child_id not in known_node_ids:
                _error(
                    findings,
                    "STRUCTURE_CHILD_REFERENCE",
                    f"structure.json/nodes/{index}/child_ids",
                    f"unknown structure node ID {child_id!r}",
                )
        for region_id in node.get("region_ids", []):
            if region_id not in region_ids:
                _error(
                    findings,
                    "REGION_REFERENCE",
                    f"structure.json/nodes/{index}/region_ids",
                    f"unknown scene object ID {region_id!r}",
                )


def _validate_styles(
    styles: Mapping[str, Any],
    manifest: Mapping[str, Any],
    page_records: Mapping[str, Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    if styles.get("document_id") != manifest.get("document_id"):
        _error(
            findings, "DOCUMENT_ID_MISMATCH", "styles.json/document_id", "does not match manifest"
        )
    tokens = styles.get("tokens")
    if not isinstance(tokens, list):
        return
    token_ids = [token.get("id") for token in tokens if isinstance(token, Mapping)]
    string_token_ids = [token_id for token_id in token_ids if isinstance(token_id, str)]
    if len(string_token_ids) != len(set(string_token_ids)):
        _error(
            findings, "DUPLICATE_STYLE_ID", "styles.json/tokens", "style token IDs must be unique"
        )
    styles_by_id = set(string_token_ids)
    for page_id, page in page_records.items():
        for index, object_ in enumerate(page.get("objects", [])):
            if not isinstance(object_, Mapping):
                continue
            style_id = object_.get("style_id")
            if style_id is not None and style_id not in styles_by_id:
                _error(
                    findings,
                    "STYLE_REFERENCE",
                    f"page:{page_id}/objects/{index}/style_id",
                    f"unknown style token ID {style_id!r}",
                )


def _validate_assets(
    reader: _Reader,
    names: tuple[str, ...],
    page_records: Mapping[str, Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    for name in names:
        if not name.startswith("assets/"):
            continue
        digest = name.removeprefix("assets/").split(".", maxsplit=1)[0]
        if not _SHA256.fullmatch(digest):
            _warning(
                findings,
                "ASSET_NAME_NOT_HASHED",
                name,
                "asset filename does not start with SHA-256",
            )
            continue
        actual = _sha256(reader.read_bytes(name))
        if actual != digest:
            _error(
                findings,
                "ASSET_HASH_MISMATCH",
                name,
                "asset bytes do not match content hash filename",
            )
    for page_id, page in page_records.items():
        for index, object_ in enumerate(page.get("objects", [])):
            if not isinstance(object_, Mapping) or object_.get("kind") != "raster":
                continue
            payload = object_.get("payload")
            if not isinstance(payload, Mapping):
                continue
            asset_path, expected_digest = _asset_reference(payload)
            if asset_path is None:
                _warning(
                    findings,
                    "RASTER_ASSET_UNREFERENCED",
                    f"page:{page_id}/objects/{index}",
                    "raster object has no payload asset path to verify",
                )
                continue
            if asset_path not in names:
                _error(
                    findings,
                    "MISSING_ASSET",
                    f"page:{page_id}/objects/{index}",
                    f"referenced asset is absent: {asset_path}",
                )
                continue
            if expected_digest is None or _SHA256.fullmatch(expected_digest) is None:
                _error(
                    findings,
                    "RASTER_ASSET_HASH_REQUIRED",
                    f"page:{page_id}/objects/{index}",
                    "raster asset reference requires an explicit SHA-256",
                )
                continue
            if (
                _sha256(reader.read_bytes(asset_path)) != expected_digest
            ):
                _error(
                    findings,
                    "ASSET_REFERENCE_HASH_MISMATCH",
                    f"page:{page_id}/objects/{index}",
                    "referenced asset bytes do not match declared SHA-256",
                )
                continue
            data = reader.read_bytes(asset_path)
            try:
                info = inspect_encoded_raster(data)
                validate_raster_mapping(payload, info)
            except ValueError as error:
                _error(
                    findings,
                    "INVALID_RASTER_ASSET",
                    f"page:{page_id}/objects/{index}",
                    str(error),
                )


def _validate_evidence_records(
    reader: _Reader,
    names: tuple[str, ...],
    page_records: Mapping[str, Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    """Check digest-bound evidence metadata without requiring external raw bytes.

    Phase 1 retains exact raw evidence in its content-addressed work store.
    Compact packages carry records that name those bytes, and may optionally
    embed them below ``evidence/sha256``.  This validator verifies either
    embedded bytes or the complete external-reference contract; it never claims
    an absent external store was inspected.
    """

    record_names = tuple(sorted(name for name in names if name.startswith("evidence/records/")))
    records_by_subject: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for name in record_names:
        try:
            value = json.loads(reader.read_bytes(name).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            _error(findings, "INVALID_EVIDENCE_RECORD", name, str(error))
            continue
        if not isinstance(value, Mapping):
            _error(findings, "INVALID_EVIDENCE_RECORD", name, "evidence record must be an object")
            continue
        subject_id = value.get("subject_id")
        if not isinstance(subject_id, str):
            _error(findings, "INVALID_EVIDENCE_RECORD", name, "evidence record has no subject_id")
            continue
        records_by_subject.setdefault(subject_id, []).append((name, value))

    declared_by_page: dict[str, set[str]] = {}
    for page_id, page in page_records.items():
        expected_digest = page.get("page_evidence_sha256")
        records = records_by_subject.get(page_id, [])
        if expected_digest is None:
            if records:
                _warning(
                    findings,
                    "UNBOUND_EVIDENCE_RECORD",
                    records[0][0],
                    "page has evidence metadata but no page_evidence_sha256 binding",
                )
            continue
        if not isinstance(expected_digest, str) or not _SHA256.fullmatch(expected_digest):
            _error(
                findings,
                "INVALID_PAGE_EVIDENCE_DIGEST",
                f"page:{page_id}/page_evidence_sha256",
                "page evidence digest must be a lower-case SHA-256",
            )
            continue
        matched = [
            (name, record)
            for name, record in records
            if _sha256(canonical_json_bytes(record)) == expected_digest
        ]
        if len(matched) != 1:
            _error(
                findings,
                "EVIDENCE_RECORD_MISSING",
                f"page:{page_id}/page_evidence_sha256",
                "page evidence digest does not name exactly one retained evidence record",
            )
            continue
        record_name, record = matched[0]
        declared_by_page[page_id] = _validate_evidence_record(
            reader, names, record_name, record, page_id, findings
        )

    for subject_id, records in records_by_subject.items():
        if subject_id not in page_records:
            _error(
                findings,
                "EVIDENCE_SUBJECT_REFERENCE",
                records[0][0],
                f"evidence subject is not a canonical page: {subject_id!r}",
            )
    for page_id, page in page_records.items():
        known = declared_by_page.get(page_id, set())
        for index, object_ in enumerate(page.get("objects", [])):
            if not isinstance(object_, Mapping):
                continue
            refs = object_.get("evidence_refs", [])
            if not isinstance(refs, list):
                continue
            missing = sorted(ref for ref in refs if isinstance(ref, str) and ref not in known)
            if missing:
                _error(
                    findings,
                    "SCENE_EVIDENCE_REFERENCE",
                    f"page:{page_id}/objects/{index}/evidence_refs",
                    f"scene object references undeclared evidence artifacts: {missing!r}",
                )


def _validate_evidence_record(
    reader: _Reader,
    names: tuple[str, ...],
    name: str,
    record: Mapping[str, Any],
    page_id: str,
    findings: list[Finding],
) -> set[str]:
    required_strings = ("id", "subject_id", "producer", "producer_version", "configuration_sha256")
    for field in required_strings:
        value = record.get(field)
        if not isinstance(value, str) or not value:
            _error(findings, "INVALID_EVIDENCE_RECORD", name, f"missing non-empty {field}")
    if record.get("subject_id") != page_id:
        _error(findings, "EVIDENCE_SUBJECT_REFERENCE", name, "record subject does not match page")
    configuration = record.get("configuration_sha256")
    if not isinstance(configuration, str) or not _SHA256.fullmatch(configuration):
        _error(findings, "INVALID_EVIDENCE_RECORD", name, "configuration_sha256 must be SHA-256")
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        _error(findings, "INVALID_EVIDENCE_RECORD", name, "artifacts must be a non-empty array")
        return set()
    digests: set[str] = set()
    for index, artifact in enumerate(artifacts):
        artifact_path = f"{name}/artifacts/{index}"
        if not isinstance(artifact, Mapping):
            _error(findings, "INVALID_EVIDENCE_RECORD", artifact_path, "artifact must be an object")
            continue
        digest = artifact.get("sha256")
        size = artifact.get("byte_size")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            _error(
                findings, "INVALID_EVIDENCE_RECORD", artifact_path, "artifact SHA-256 is invalid"
            )
            continue
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            _error(
                findings, "INVALID_EVIDENCE_RECORD", artifact_path, "artifact byte_size is invalid"
            )
            continue
        if digest in digests:
            _error(
                findings, "DUPLICATE_EVIDENCE_ARTIFACT", artifact_path, "artifact digest repeats"
            )
            continue
        digests.add(digest)
        embedded = f"evidence/sha256/{digest[:2]}/{digest[2:]}"
        if embedded in names:
            payload = reader.read_bytes(embedded)
            if _sha256(payload) != digest or len(payload) != size:
                _error(
                    findings,
                    "EVIDENCE_ARTIFACT_HASH_MISMATCH",
                    embedded,
                    "embedded evidence bytes do not match the retained artifact record",
                )
        else:
            _info(
                findings,
                "EVIDENCE_ARTIFACT_EXTERNAL",
                artifact_path,
                "exact bytes are retained in the external content-addressed evidence store",
            )
    return digests


def _validate_source_if_present(
    reader: _Reader, names: tuple[str, ...], manifest: Mapping[str, Any], findings: list[Finding]
) -> None:
    source_name = next(
        (name for name in ("source.pdf", "source/source.pdf") if name in names), None
    )
    if source_name is None:
        _info(
            findings,
            "SOURCE_NOT_PRESENT",
            "manifest.json/source",
            "source bytes are not included; digest was not rechecked",
        )
        return
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        return
    source_bytes = reader.read_bytes(source_name)
    if source.get("sha256") != _sha256(source_bytes):
        _error(
            findings,
            "SOURCE_HASH_MISMATCH",
            source_name,
            "source bytes do not match manifest SHA-256",
        )
    if source.get("byte_size") != len(source_bytes):
        _error(
            findings,
            "SOURCE_SIZE_MISMATCH",
            source_name,
            "source bytes do not match manifest byte size",
        )


def _effective_conformance(claimed: str | None, findings: list[Finding]) -> str | None:
    if claimed != "replacement-ready":
        return claimed
    if any(finding.code == "FULL_PAGE_RASTER" for finding in findings):
        _error(
            findings,
            "REPLACEMENT_PROFILE_VIOLATION",
            "manifest.json/conformance_level",
            "replacement-ready packages cannot contain full-page raster objects",
        )
        return "review-required"
    _warning(
        findings,
        "REPLACEMENT_GATES_UNVERIFIED",
        "manifest.json/conformance_level",
        "static structural validation cannot establish OCR, visual, accessibility, size, or rebuild gates",
    )
    return "review-required"


def _asset_reference(payload: Mapping[str, Any]) -> tuple[str | None, str | None]:
    asset = payload.get("asset")
    if isinstance(asset, str):
        return asset, _string_or_none(payload.get("asset_sha256"))
    if isinstance(asset, Mapping):
        path = _string_or_none(asset.get("path"))
        return path, _string_or_none(asset.get("sha256"))
    return _string_or_none(payload.get("asset_path")), _string_or_none(payload.get("asset_sha256"))


def _box(value: object) -> tuple[int, int, int, int] | None:
    if not isinstance(value, Mapping):
        return None
    coordinates: list[int] = []
    for name in ("x0", "y0", "x1", "y1"):
        coordinate = value.get(name)
        if isinstance(coordinate, bool) or not isinstance(coordinate, int):
            return None
        coordinates.append(coordinate)
    x0, y0, x1, y1 = coordinates
    if x0 >= x1 or y0 >= y1:
        return None
    return x0, y0, x1, y1


def _contains(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def _fraction(value: object) -> Fraction:
    if not isinstance(value, Mapping):
        raise TypeError("not a rational object")
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if (
        isinstance(numerator, bool)
        or not isinstance(numerator, int)
        or isinstance(denominator, bool)
        or not isinstance(denominator, int)
        or denominator == 0
    ):
        raise ValueError("invalid rational")
    return Fraction(numerator, denominator)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _error(findings: list[Finding], code: str, path: str, message: str) -> None:
    findings.append(Finding("error", code, path, message))


def _warning(findings: list[Finding], code: str, path: str, message: str) -> None:
    findings.append(Finding("warning", code, path, message))


def _info(findings: list[Finding], code: str, path: str, message: str) -> None:
    findings.append(Finding("info", code, path, message))
