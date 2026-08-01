"""Deterministic review projects, guarded patch sets, and approval coverage."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import ceil
from typing import Literal

from lispmdoc.model import (
    PageRecord,
    StructureRecord,
    StylesRecord,
    canonical_json_bytes,
    sha256_hex,
)

from .patches import AppliedPatch, CorrectionPatch, PatchError, apply_patches


def _digest(value: str, name: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lower-case SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ReviewArtifacts:
    """All synchronized review views are immutable digest references."""

    source_sha256: str
    page_evidence_sha256: str
    ir_sha256: str
    render_sha256: str
    source_view_sha256: str | None = None
    reconstruction_view_sha256: str | None = None
    semantic_view_sha256: str | None = None
    diff_overlay_sha256: str | None = None
    reading_graph_sha256: str | None = None
    engine_alternatives_sha256: str | None = None
    raster_vector_decisions_sha256: str | None = None

    def __post_init__(self) -> None:
        for name, value in self.to_dict().items():
            if value is not None:
                _digest(value, name)

    def to_dict(self) -> dict[str, str | None]:
        return {
            "source_sha256": self.source_sha256,
            "page_evidence_sha256": self.page_evidence_sha256,
            "ir_sha256": self.ir_sha256,
            "render_sha256": self.render_sha256,
            "source_view_sha256": self.source_view_sha256,
            "reconstruction_view_sha256": self.reconstruction_view_sha256,
            "semantic_view_sha256": self.semantic_view_sha256,
            "diff_overlay_sha256": self.diff_overlay_sha256,
            "reading_graph_sha256": self.reading_graph_sha256,
            "engine_alternatives_sha256": self.engine_alternatives_sha256,
            "raster_vector_decisions_sha256": self.raster_vector_decisions_sha256,
        }

    @property
    def promotion_complete(self) -> bool:
        """Promotion needs synchronized source, reconstruction, semantic, and QA views."""

        return all(
            value is not None
            for value in (
                self.source_view_sha256,
                self.reconstruction_view_sha256,
                self.semantic_view_sha256,
                self.diff_overlay_sha256,
                self.reading_graph_sha256,
                self.engine_alternatives_sha256,
                self.raster_vector_decisions_sha256,
            )
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ReviewArtifacts:
        return cls(
            source_sha256=str(value.get("source_sha256", "")),
            page_evidence_sha256=str(value.get("page_evidence_sha256", "")),
            ir_sha256=str(value.get("ir_sha256", "")),
            render_sha256=str(value.get("render_sha256", "")),
            source_view_sha256=_optional_digest(value.get("source_view_sha256")),
            reconstruction_view_sha256=_optional_digest(value.get("reconstruction_view_sha256")),
            semantic_view_sha256=_optional_digest(value.get("semantic_view_sha256")),
            diff_overlay_sha256=_optional_digest(value.get("diff_overlay_sha256")),
            reading_graph_sha256=_optional_digest(value.get("reading_graph_sha256")),
            engine_alternatives_sha256=_optional_digest(value.get("engine_alternatives_sha256")),
            raster_vector_decisions_sha256=_optional_digest(
                value.get("raster_vector_decisions_sha256")
            ),
        )


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    id: str
    page_id: str
    code: str
    severity: Literal["info", "medium", "high", "severe"]
    resolved: bool = False


@dataclass(frozen=True, slots=True)
class ReviewPage:
    page_id: str
    artifacts: ReviewArtifacts
    patch_set_sha256: str

    def __post_init__(self) -> None:
        if not self.page_id:
            raise ValueError("review page ID is required")
        _digest(self.patch_set_sha256, "patch_set_sha256")


@dataclass(frozen=True, slots=True)
class PageApproval:
    """Human approval bound to the exact evidence and derived render reviewed."""

    page_id: str
    reviewer: str
    source_sha256: str
    page_evidence_sha256: str
    ir_sha256: str
    render_sha256: str
    patch_set_sha256: str

    def __post_init__(self) -> None:
        if not self.page_id or not self.reviewer:
            raise ValueError("approval needs page and reviewer")
        for name in (
            "source_sha256",
            "page_evidence_sha256",
            "ir_sha256",
            "render_sha256",
            "patch_set_sha256",
        ):
            _digest(getattr(self, name), name)

    @property
    def sha256(self) -> str:
        return sha256_hex(self.to_dict())

    def to_dict(self) -> dict[str, str]:
        return {
            "page_id": self.page_id,
            "reviewer": self.reviewer,
            "source_sha256": self.source_sha256,
            "page_evidence_sha256": self.page_evidence_sha256,
            "ir_sha256": self.ir_sha256,
            "render_sha256": self.render_sha256,
            "patch_set_sha256": self.patch_set_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> PageApproval:
        return cls(
            str(value.get("page_id", "")),
            str(value.get("reviewer", "")),
            str(value.get("source_sha256", "")),
            str(value.get("page_evidence_sha256", "")),
            str(value.get("ir_sha256", "")),
            str(value.get("render_sha256", "")),
            str(value.get("patch_set_sha256", "")),
        )


@dataclass(frozen=True, slots=True)
class ReviewProject:
    document_id: str
    pages: tuple[ReviewPage, ...]
    findings: tuple[ReviewFinding, ...] = ()
    manifest_page_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.document_id or not self.pages:
            raise ValueError("review project needs document and at least one page")
        if len({page.page_id for page in self.pages}) != len(self.pages):
            raise ValueError("review project page IDs must be unique")
        page_ids = {page.page_id for page in self.pages}
        if any(finding.page_id not in page_ids for finding in self.findings):
            raise ValueError("review finding references an absent page")
        if self.manifest_page_ids:
            if len(self.manifest_page_ids) != len(set(self.manifest_page_ids)):
                raise ValueError("review manifest page IDs must be unique")
            if set(self.manifest_page_ids) != page_ids:
                raise ValueError("review pages must exactly match manifest page IDs")

    def severe_queue(self) -> tuple[ReviewFinding, ...]:
        return tuple(
            sorted(
                (
                    finding
                    for finding in self.findings
                    if finding.severity == "severe" and not finding.resolved
                ),
                key=lambda item: (item.page_id, item.code, item.id),
            )
        )

    def canonical_export(self) -> bytes:
        """Canonical runtime-independent review input, with no wall-clock field."""
        return canonical_json_bytes(
            {
                "document_id": self.document_id,
                "manifest_page_ids": list(self.manifest_page_ids),
                "pages": [
                    {
                        "page_id": page.page_id,
                        "artifacts": page.artifacts.to_dict(),
                        "patch_set_sha256": page.patch_set_sha256,
                    }
                    for page in sorted(self.pages, key=lambda item: item.page_id)
                ],
                "findings": [
                    {
                        "id": finding.id,
                        "page_id": finding.page_id,
                        "code": finding.code,
                        "severity": finding.severity,
                        "resolved": finding.resolved,
                    }
                    for finding in sorted(
                        self.findings, key=lambda item: (item.page_id, item.code, item.id)
                    )
                ],
            }
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ReviewProject:
        pages_value = value.get("pages")
        findings_value = value.get("findings", [])
        manifest_ids = value.get("manifest_page_ids", [])
        if (
            not isinstance(pages_value, list)
            or not isinstance(findings_value, list)
            or not isinstance(manifest_ids, list)
        ):
            raise ValueError("review project arrays are malformed")
        pages: list[ReviewPage] = []
        for item in pages_value:
            if not isinstance(item, Mapping) or not isinstance(item.get("artifacts"), Mapping):
                raise ValueError("review page is malformed")
            pages.append(
                ReviewPage(
                    str(item.get("page_id", "")),
                    ReviewArtifacts.from_dict(item["artifacts"]),
                    str(item.get("patch_set_sha256", "")),
                )
            )
        findings: list[ReviewFinding] = []
        for item in findings_value:
            if not isinstance(item, Mapping):
                raise ValueError("review finding is malformed")
            severity = item.get("severity")
            if severity not in {"info", "medium", "high", "severe"}:
                raise ValueError("review finding severity is invalid")
            findings.append(
                ReviewFinding(
                    str(item.get("id", "")),
                    str(item.get("page_id", "")),
                    str(item.get("code", "")),
                    severity,
                    bool(item.get("resolved", False)),
                )
            )
        return cls(
            str(value.get("document_id", "")),
            tuple(pages),
            tuple(findings),
            tuple(str(item) for item in manifest_ids),
        )

    def required_second_pages(self) -> tuple[str, ...]:
        page_ids = sorted(page.page_id for page in self.pages)
        sample_count = ceil(len(page_ids) / 10)
        sampled = sorted(
            page_ids,
            key=lambda page_id: hashlib.sha256(
                f"{self.document_id}\0{page_id}".encode()
            ).hexdigest(),
        )[:sample_count]
        # A severe page always receives an independent second review, even if
        # the finding has since been resolved by a guarded correction.
        severe = [finding.page_id for finding in self.findings if finding.severity == "severe"]
        return tuple(sorted(set(sampled) | set(severe)))

    def approval_status(self, approvals: Iterable[PageApproval]) -> dict[str, str]:
        accepted: dict[str, list[PageApproval]] = {page.page_id: [] for page in self.pages}
        by_page = {page.page_id: page for page in self.pages}
        for approval in approvals:
            page = by_page.get(approval.page_id)
            if page is None or not _matches(page, approval):
                continue
            accepted[approval.page_id].append(approval)
        required_second = set(self.required_second_pages())
        status: dict[str, str] = {}
        for page_id, values in sorted(accepted.items()):
            reviewers = {item.reviewer for item in values}
            if not reviewers:
                status[page_id] = "missing-first-approval"
            elif page_id in required_second and len(reviewers) < 2:
                status[page_id] = "missing-independent-second-approval"
            else:
                status[page_id] = "approved"
        return status

    def promotion_ready(self, approvals: Iterable[PageApproval]) -> bool:
        return (
            bool(self.manifest_page_ids)
            and all(page.artifacts.promotion_complete for page in self.pages)
            and not self.severe_queue()
            and all(value == "approved" for value in self.approval_status(approvals).values())
        )


def _optional_digest(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _matches(page: ReviewPage, approval: PageApproval) -> bool:
    artifacts = page.artifacts
    return (
        approval.source_sha256 == artifacts.source_sha256
        and approval.page_evidence_sha256 == artifacts.page_evidence_sha256
        and approval.ir_sha256 == artifacts.ir_sha256
        and approval.render_sha256 == artifacts.render_sha256
        and approval.patch_set_sha256 == page.patch_set_sha256
    )


def patch_set_sha256(patches: tuple[CorrectionPatch, ...]) -> str:
    """A sorted patch-set digest used as an approval guard and rebuild input."""
    values = tuple(
        sorted((patch.to_dict() for patch in patches), key=lambda item: sha256_hex(item))
    )
    return sha256_hex({"patches": values})


def apply_guarded_patch_set(
    patches: tuple[CorrectionPatch, ...],
    page: PageRecord,
    structure: StructureRecord,
    styles: StylesRecord,
) -> tuple[PageRecord, StructureRecord, StylesRecord, tuple[AppliedPatch, ...]]:
    """Reject duplicate patches before in-memory atomic patch application."""
    digests = tuple(patch.sha256 for patch in patches)
    if len(digests) != len(set(digests)):
        raise PatchError("partial/duplicate patch set rejected")
    return apply_patches(patches, page, structure, styles)


REVIEW_OPERATION_STATUS: Mapping[str, str] = {
    "replace-text": "supported",
    "replace-geometry": "supported",
    "reorder-reading": "supported",
    "replace-style": "supported",
    "split-region": "unsupported: stable replacement IDs require a versioned IR migration",
    "merge-region": "unsupported: stable replacement IDs require a versioned IR migration",
    "replace-baseline": "unsupported: baseline contract is not in canonical PageRecord",
    "replace-polygon": "unsupported: polygon contract is not in canonical PageRecord",
    "replace-reading-graph": "unsupported: graph contract is not in canonical PageRecord",
    "relabel-semantics": "unsupported: semantic patch contract is not frozen",
    "replace-table-grid": "unsupported: table patch contract is not frozen",
    "replace-font": "unsupported: font policy patch contract is not frozen",
    "raster-vector-decision": "unsupported: graphics decision contract is not frozen",
    "replace-alt-text": "unsupported: alt-text patch contract is not frozen",
    "resolve-glyph": "unsupported: glyph patch contract is not frozen",
    "approve-page": "supported through PageApproval, not a mutable content patch",
}
