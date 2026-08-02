"""Consume digest-bound human review annotations into authoritative truth.

The browser UI is only a collection surface.  This module is the fail-closed
boundary that proves which exact project the reviewer saw, validates every
page/region identifier and source string, and derives the package review state.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .authoritative import (
    AuthoritativeRegionTruth,
    AuthoritativeReviewEvidence,
    AuthoritativeTruthError,
    AuthoritativeTruthPackage,
    MappingEvidence,
)

_DISPOSITIONS = frozenset({"accept", "reject", "needs-fix"})


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AuthoritativeTruthError(f"{name} must be an object")
    return value


def _array(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise AuthoritativeTruthError(f"{name} must be an array")
    return value


def _json_object(raw: bytes, name: str) -> Mapping[str, Any]:
    if not isinstance(raw, bytes) or not raw:
        raise AuthoritativeTruthError(f"{name} bytes must be non-empty")
    try:
        return _object(json.loads(raw.decode("utf-8", errors="strict")), name)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuthoritativeTruthError(f"{name} must be valid UTF-8 JSON") from error


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _disposition(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _DISPOSITIONS:
        raise AuthoritativeTruthError(f"{name} has an invalid disposition")
    return value


def _review_page(
    package: AuthoritativeTruthPackage,
    project: Mapping[str, Any],
    asset_bytes: Mapping[str, bytes],
) -> Mapping[str, Any]:
    if project.get("format_version") != "1.0":
        raise AuthoritativeTruthError("review project format_version must be 1.0")
    assets = _object(project.get("assets"), "review project assets")
    if set(asset_bytes) != set(assets):
        raise AuthoritativeTruthError(
            "review asset bytes must exactly match the project asset inventory"
        )
    for asset_id, raw_definition in assets.items():
        definition = _object(raw_definition, f"review asset {asset_id}")
        expected_digest = definition.get("sha256")
        content = asset_bytes[asset_id]
        if not isinstance(content, bytes) or not content:
            raise AuthoritativeTruthError("review asset bytes must be non-empty")
        if expected_digest != _digest(content):
            raise AuthoritativeTruthError(
                f"review asset {asset_id} bytes do not match the declared SHA-256"
            )
    pages = _array(project.get("pages"), "review project pages")
    page_by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw_page in enumerate(pages):
        page = _object(raw_page, f"review project pages[{index}]")
        page_id = page.get("id")
        if not isinstance(page_id, str) or not page_id:
            raise AuthoritativeTruthError("review project page IDs must be non-empty strings")
        if page_id in page_by_id:
            raise AuthoritativeTruthError("review project page IDs must be unique")
        page_by_id[page_id] = page
    try:
        page = page_by_id[package.queue_page.id]
    except KeyError as error:
        raise AuthoritativeTruthError(
            "review project does not contain the authoritative QueuePage"
        ) from error

    reference_id = page.get("reference_asset_id")
    generated_id = page.get("generated_asset_id")
    if not isinstance(reference_id, str) or not isinstance(generated_id, str):
        raise AuthoritativeTruthError("review page must declare reference and generated assets")
    reference = _object(assets.get(reference_id), "review reference asset")
    generated = _object(assets.get(generated_id), "review generated asset")
    if reference.get("sha256") != package.queue_page.render_sha256:
        raise AuthoritativeTruthError(
            "review reference asset does not match the QueuePage render digest"
        )
    generated_sha = generated.get("sha256")
    if (
        not isinstance(generated_sha, str)
        or len(generated_sha) != 64
        or any(character not in "0123456789abcdef" for character in generated_sha)
    ):
        raise AuthoritativeTruthError("review generated asset needs a lower-case SHA-256")

    project_regions = _array(page.get("regions"), "review project page regions")
    truth_by_id = {region.geometry.region_id: region for region in package.regions}
    seen: set[str] = set()
    for index, raw_region in enumerate(project_regions):
        region = _object(raw_region, f"review project region[{index}]")
        region_id = region.get("id")
        if not isinstance(region_id, str) or region_id not in truth_by_id or region_id in seen:
            raise AuthoritativeTruthError(
                "review project regions must exactly and uniquely match truth regions"
            )
        seen.add(region_id)
        truth = truth_by_id[region_id]
        if region.get("source_text") != truth.literal_text:
            raise AuthoritativeTruthError(
                "review project source text does not match authoritative truth"
            )
        if region.get("canonical_text") != truth.literal_text:
            raise AuthoritativeTruthError(
                "review project canonical text does not match authoritative truth"
            )
    if seen != set(truth_by_id):
        raise AuthoritativeTruthError("review project omits authoritative truth regions")
    return page


def apply_review_annotations(
    package: AuthoritativeTruthPackage,
    *,
    project_bytes: bytes,
    annotations_bytes: bytes,
    asset_bytes: Mapping[str, bytes],
) -> AuthoritativeTruthPackage:
    """Return the package state derived solely from exact saved review bytes."""

    project = _json_object(project_bytes, "review project")
    page = _review_page(package, project, asset_bytes)
    annotations_record = _json_object(annotations_bytes, "review annotations")
    if annotations_record.get("format_version") != "1.0":
        raise AuthoritativeTruthError("review annotations format_version must be 1.0")
    project_sha256 = _digest(project_bytes)
    if annotations_record.get("project_sha256") != project_sha256:
        raise AuthoritativeTruthError(
            "review annotations are not bound to the exact review project bytes"
        )
    reviewer = annotations_record.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip() or reviewer != reviewer.strip():
        raise AuthoritativeTruthError("review annotations require a trimmed reviewer name")

    annotations = _object(annotations_record.get("annotations"), "annotations")
    annotated_pages = _object(annotations.get("pages"), "annotations.pages")
    project_page_ids = {
        candidate.get("id")
        for candidate in _array(project.get("pages"), "review project pages")
        if isinstance(candidate, Mapping)
    }
    if any(page_id not in project_page_ids for page_id in annotated_pages):
        raise AuthoritativeTruthError("review annotations contain an unknown page")
    raw_page_annotation = annotated_pages.get(package.queue_page.id, {})
    page_annotation = _object(raw_page_annotation, "page annotation")
    page_disposition = _disposition(
        page_annotation.get("disposition"), "page annotation"
    )
    mapping_state = (
        "verified" if page_disposition == "accept" else "human-mapping-review-required"
    )

    raw_region_annotations = _object(
        page_annotation.get("regions", {}), "page annotation regions"
    )
    truth_ids = {region.geometry.region_id for region in package.regions}
    if any(region_id not in truth_ids for region_id in raw_region_annotations):
        raise AuthoritativeTruthError("review annotations contain an unknown region")

    reviewed_regions: list[AuthoritativeRegionTruth] = []
    for region in package.regions:
        region_id = region.geometry.region_id
        annotation = _object(
            raw_region_annotations.get(region_id, {}), f"region annotation {region_id}"
        )
        disposition = _disposition(annotation.get("disposition"), f"region {region_id}")
        canonical = annotation.get("canonical_text")
        if canonical is not None and not isinstance(canonical, str):
            raise AuthoritativeTruthError(f"region {region_id} canonical_text must be a string")
        if disposition == "reject" or (
            canonical is not None and canonical != region.literal_text
        ):
            layout_state = "discrepancy"
        elif disposition == "accept":
            layout_state = "verified"
        else:
            layout_state = "human-review-required"
        reviewed_regions.append(
            replace(region, layout_verification_state=layout_state)
        )

    # The page object was validated above; retaining this assertion makes it
    # explicit that the reviewed page is the one used for the state transition.
    assert page.get("id") == package.queue_page.id
    return replace(
        package,
        mapping_evidence=MappingEvidence(package.mapping_evidence.anchors, mapping_state),
        regions=tuple(reviewed_regions),
        review_evidence=AuthoritativeReviewEvidence(
            reviewer,
            project_sha256,
            _digest(annotations_bytes),
        ),
    )
