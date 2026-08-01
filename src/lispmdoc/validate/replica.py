"""Fail-closed replica-ready validation and deterministic attestation records.

Metric-producing renderers are intentionally outside this module.  This module
only ingests their pinned, region-aware measurements and refuses promotion when
any required evidence is absent.
"""

from __future__ import annotations

import hashlib
import json
import math
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from lispmdoc.model import sha256_hex
from lispmdoc.review import PageApproval, ReviewProject

REPLICA_GATE_VERSION = "replica-gates-1"
PROSE_CER_MAX = 0.0025
SCAN_SIZE_RATIO_MAX = 0.60
BORN_DIGITAL_SSIM_MIN = 0.995
SCAN_SSIM_MIN = 0.985
EDGE_RECALL_MIN = 0.99
EDGE_DISPLACEMENT_P95_MAX = 1.5
CONTINUOUS_TONE_SSIM_MIN = 0.99


def _digest(value: str, name: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lower-case SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class TextAudit:
    total_characters: int
    sampled_characters: int
    character_errors: int
    sampled_code_exact: bool
    sampled_identifiers_exact: bool
    sampled_math_exact: bool

    def __post_init__(self) -> None:
        if self.total_characters < 0 or self.sampled_characters < 0:
            raise ValueError("text audit character counts cannot be negative")
        if not 0 <= self.character_errors <= self.sampled_characters:
            raise ValueError("text audit errors must be within sampled characters")

    @property
    def cer(self) -> float:
        return self.character_errors / self.sampled_characters if self.sampled_characters else 1.0

    @property
    def required_characters(self) -> int:
        return max(10_000, math.ceil(self.total_characters * 0.05))

    @property
    def wilson_upper_95(self) -> float:
        """95% Wilson upper confidence bound for observed character error rate."""
        n = self.sampled_characters
        if n <= 0:
            return 1.0
        p, z = self.cer, 1.959963984540054
        denominator = 1 + z * z / n
        center = (p + z * z / (2 * n)) / denominator
        radius = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator
        return min(1.0, center + radius)

    @property
    def passes(self) -> bool:
        return (
            self.sampled_characters >= self.required_characters
            and self.cer <= PROSE_CER_MAX
            and self.sampled_code_exact
            and self.sampled_identifiers_exact
            and self.sampled_math_exact
        )


@dataclass(frozen=True, slots=True)
class LayoutEvidence:
    all_regions_treated: bool
    severe_reading_order_findings: int
    severe_structural_findings: int
    tables_reviewed: bool

    @property
    def passes(self) -> bool:
        return (
            self.all_regions_treated
            and self.severe_reading_order_findings == 0
            and self.severe_structural_findings == 0
            and self.tables_reviewed
        )


@dataclass(frozen=True, slots=True)
class VisualEvidence:
    page_class: Literal["born-digital", "scan", "hybrid"]
    ssim: float
    edge_recall: float
    edge_displacement_p95: float
    continuous_tone_ssim: float | None
    undisposed_components: int
    page_id: str | None = None

    def __post_init__(self) -> None:
        values = (
            self.ssim,
            self.edge_recall,
            self.edge_displacement_p95,
            self.continuous_tone_ssim,
        )
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("visual metrics must be finite")
        if (
            not 0 <= self.ssim <= 1
            or not 0 <= self.edge_recall <= 1
            or self.undisposed_components < 0
        ):
            raise ValueError("visual metric values are outside valid ranges")
        if self.page_id is not None and (
            not isinstance(self.page_id, str) or not self.page_id
        ):
            raise ValueError("visual evidence page_id must be a non-empty string when supplied")

    @property
    def passes(self) -> bool:
        ssim_threshold = (
            BORN_DIGITAL_SSIM_MIN if self.page_class == "born-digital" else SCAN_SSIM_MIN
        )
        continuous = (
            self.continuous_tone_ssim is None
            or self.continuous_tone_ssim >= CONTINUOUS_TONE_SSIM_MIN
        )
        return (
            self.ssim >= ssim_threshold
            and self.edge_recall >= EDGE_RECALL_MIN
            and self.edge_displacement_p95 <= EDGE_DISPLACEMENT_P95_MAX
            and continuous
            and self.undisposed_components == 0
        )


@dataclass(frozen=True, slots=True)
class AccessibilityEvidence:
    semantic_html_valid: bool
    authoritative_text_identical: bool
    critical_or_serious_violations: int

    @property
    def passes(self) -> bool:
        return (
            self.semantic_html_valid
            and self.authoritative_text_identical
            and self.critical_or_serious_violations == 0
        )


@dataclass(frozen=True, slots=True)
class PolicyEvidence:
    full_page_rasters: int
    placeholders: int
    unsupported_objects: int
    raster_policy_valid: bool
    assets_hash_complete: bool
    fonts_hash_complete: bool
    rights_complete: bool

    @property
    def passes(self) -> bool:
        return (
            self.full_page_rasters == 0
            and self.placeholders == 0
            and self.unsupported_objects == 0
            and self.raster_policy_valid
            and self.assets_hash_complete
            and self.fonts_hash_complete
            and self.rights_complete
        )


@dataclass(frozen=True, slots=True)
class ReproducibilityEvidence:
    build_one_sha256: str
    build_two_sha256: str
    distinct_roots: bool
    allowed_job_counts: bool

    @property
    def passes(self) -> bool:
        return (
            self.distinct_roots
            and self.allowed_job_counts
            and self.build_one_sha256 == self.build_two_sha256
        )


@dataclass(frozen=True, slots=True)
class SizeEvidence:
    kind: Literal["scan-dominant", "hybrid", "born-digital"]
    source_bytes: int
    package_bytes: int
    born_digital_size_not_applicable: bool = False

    @property
    def passes(self) -> bool:
        if self.source_bytes <= 0 or self.package_bytes < 0:
            return False
        if self.kind == "scan-dominant":
            return self.package_bytes <= self.source_bytes * SCAN_SIZE_RATIO_MAX
        if self.kind == "hybrid":
            return self.package_bytes < self.source_bytes
        return self.born_digital_size_not_applicable or self.package_bytes < self.source_bytes


@dataclass(frozen=True, slots=True)
class ReplicaEvidence:
    package_sha256: str
    source_sha256: str
    benchmark_sha256: str
    renderer_sha256: str
    review_set_sha256: str
    structural_valid: bool
    source_identity_exact: bool
    benchmark_passes_all_page_classes: bool
    high_risk_and_omissions_resolved: bool
    every_page_approved: bool
    text: TextAudit
    layout: LayoutEvidence
    visual: tuple[VisualEvidence, ...]
    accessibility: AccessibilityEvidence
    policy: PolicyEvidence
    reproducibility: ReproducibilityEvidence
    size: SizeEvidence

    def __post_init__(self) -> None:
        for name in (
            "package_sha256",
            "source_sha256",
            "benchmark_sha256",
            "renderer_sha256",
            "review_set_sha256",
        ):
            _digest(getattr(self, name), name)
        _digest(self.reproducibility.build_one_sha256, "build_one_sha256")
        _digest(self.reproducibility.build_two_sha256, "build_two_sha256")


@dataclass(frozen=True, slots=True)
class ReplicaReport:
    gate_version: str
    ready: bool
    failures: tuple[str, ...]
    text_wilson_upper_95: float

    def to_dict(self) -> dict[str, object]:
        return {
            "gate_version": self.gate_version,
            "ready": self.ready,
            "failures": list(self.failures),
            "text_wilson_upper_95": self.text_wilson_upper_95,
        }


@dataclass(frozen=True, slots=True)
class ReplicaAttestation:
    gate_version: str
    package_sha256: str
    source_sha256: str
    benchmark_sha256: str
    renderer_sha256: str
    review_set_sha256: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "package_sha256",
            "source_sha256",
            "benchmark_sha256",
            "renderer_sha256",
            "review_set_sha256",
            "evidence_sha256",
        ):
            _digest(getattr(self, name), name)

    @property
    def sha256(self) -> str:
        return sha256_hex(self.to_dict())

    def to_dict(self) -> dict[str, str]:
        return {
            "gate_version": self.gate_version,
            "package_sha256": self.package_sha256,
            "source_sha256": self.source_sha256,
            "benchmark_sha256": self.benchmark_sha256,
            "renderer_sha256": self.renderer_sha256,
            "review_set_sha256": self.review_set_sha256,
            "evidence_sha256": self.evidence_sha256,
        }


@dataclass(frozen=True, slots=True)
class ReplicaAttestationInputs:
    """Resolved, independently produced artifacts required for attestation."""

    package_path: Path
    source_path: Path
    benchmark_path: Path
    renderer_evidence_path: Path
    review_project_path: Path
    approvals_path: Path
    visual_evidence_path: Path
    build_one_path: Path
    build_two_path: Path
    build_one_root: Path | None = None
    build_two_root: Path | None = None


def validate_replica(evidence: ReplicaEvidence) -> ReplicaReport:
    failures: list[str] = []
    checks = (
        ("STRUCTURAL_INVALID", evidence.structural_valid),
        ("SOURCE_IDENTITY", evidence.source_identity_exact),
        ("TREATMENT_OR_POLICY", evidence.layout.passes and evidence.policy.passes),
        ("BENCHMARK", evidence.benchmark_passes_all_page_classes),
        ("FINDINGS", evidence.high_risk_and_omissions_resolved),
        ("APPROVAL", evidence.every_page_approved),
        ("TEXT_AUDIT", evidence.text.passes),
        ("VISUAL", bool(evidence.visual) and all(item.passes for item in evidence.visual)),
        ("ACCESSIBILITY", evidence.accessibility.passes),
        ("REPRODUCIBILITY", evidence.reproducibility.passes),
        ("SIZE", evidence.size.passes),
    )
    failures.extend(code for code, passed in checks if not passed)
    return ReplicaReport(
        REPLICA_GATE_VERSION, not failures, tuple(failures), evidence.text.wilson_upper_95
    )


def authoritative_text_equivalent(expected: str, observed: str) -> bool:
    """Diplomatic text equivalence deliberately preserves whitespace and Unicode."""
    return expected == observed


def accessibility_structure_evidence(
    *,
    semantic_html_valid: bool,
    authoritative_text: str,
    rendered_text: str,
    critical_or_serious_violations: int,
) -> AccessibilityEvidence:
    """Build accessibility evidence without a browser or image dependency."""
    if critical_or_serious_violations < 0:
        raise ValueError("accessibility violation count cannot be negative")
    return AccessibilityEvidence(
        semantic_html_valid,
        authoritative_text_equivalent(authoritative_text, rendered_text),
        critical_or_serious_violations,
    )


def attest_replica(
    evidence: ReplicaEvidence, inputs: ReplicaAttestationInputs | None = None
) -> ReplicaAttestation:
    """Issue an attestation only after resolving independent on-disk evidence.

    ``validate_replica`` remains a pure assessment of supplied measurements.
    It cannot create an attestation; this function requires actual artifacts so
    callers cannot promote a package merely by typing matching digest strings.
    """

    report = validate_replica(evidence)
    if not report.ready:
        raise ValueError(f"replica attestation refused: {', '.join(report.failures)}")
    if inputs is None:
        raise ValueError("replica attestation requires resolved artifact inputs")
    _verify_attestation_inputs(evidence, inputs)
    payload = {
        "gate_version": REPLICA_GATE_VERSION,
        "report": report.to_dict(),
        "evidence": _evidence_dict(evidence),
    }
    evidence_sha256 = hashlib.sha256(
        json.dumps(
            payload, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return ReplicaAttestation(
        REPLICA_GATE_VERSION,
        evidence.package_sha256,
        evidence.source_sha256,
        evidence.benchmark_sha256,
        evidence.renderer_sha256,
        evidence.review_set_sha256,
        evidence_sha256,
    )


def _file_digest(path: Path, name: str) -> str:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{name} must be a regular file: {resolved}")
    return hashlib.sha256(resolved.read_bytes()).hexdigest()


def _package_manifest(path: Path) -> Mapping[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            value = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (
        OSError,
        KeyError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as error:
        raise ValueError(f"cannot load package manifest: {error}") from error
    if not isinstance(value, Mapping):
        raise ValueError("package manifest must be an object")
    return value


def _load_review_project(path: Path) -> ReviewProject:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot load review project: {error}") from error
    if not isinstance(value, Mapping):
        raise ValueError("review project must be an object")
    project = ReviewProject.from_dict(value)
    canonical = project.canonical_export()
    if raw not in {canonical, canonical + b"\n"}:
        raise ValueError("review project is not a canonical export")
    return project


def _load_approvals(path: Path) -> tuple[PageApproval, ...]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot load approvals: {error}") from error
    if not isinstance(value, list):
        raise ValueError("approvals must be a JSON array")
    approvals: list[PageApproval] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("approval must be an object")
        approvals.append(PageApproval.from_dict(item))
    return tuple(approvals)


def _load_visual_records(path: Path) -> tuple[VisualEvidence, ...]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot load visual evidence: {error}") from error
    if not isinstance(value, list):
        raise ValueError("visual evidence must be a JSON array")
    return tuple(_visual_from_dict(item, index) for index, item in enumerate(value))


def _visual_from_dict(value: object, index: int) -> VisualEvidence:
    if not isinstance(value, Mapping):
        raise ValueError(f"visual evidence {index} must be an object")
    parsed = replica_evidence_from_dict(
        {
            "package_sha256": "0" * 64,
            "source_sha256": "0" * 64,
            "benchmark_sha256": "0" * 64,
            "renderer_sha256": "0" * 64,
            "review_set_sha256": "0" * 64,
            "structural_valid": False,
            "source_identity_exact": False,
            "benchmark_passes_all_page_classes": False,
            "high_risk_and_omissions_resolved": False,
            "every_page_approved": False,
            "text": {
                "total_characters": 0,
                "sampled_characters": 0,
                "character_errors": 0,
                "sampled_code_exact": False,
                "sampled_identifiers_exact": False,
                "sampled_math_exact": False,
            },
            "layout": {
                "all_regions_treated": False,
                "severe_reading_order_findings": 1,
                "severe_structural_findings": 1,
                "tables_reviewed": False,
            },
            "visual": [dict(value)],
            "accessibility": {
                "semantic_html_valid": False,
                "authoritative_text_identical": False,
                "critical_or_serious_violations": 1,
            },
            "policy": {
                "full_page_rasters": 1,
                "placeholders": 1,
                "unsupported_objects": 1,
                "raster_policy_valid": False,
                "assets_hash_complete": False,
                "fonts_hash_complete": False,
                "rights_complete": False,
            },
            "reproducibility": {
                "build_one_sha256": "0" * 64,
                "build_two_sha256": "0" * 64,
                "distinct_roots": False,
                "allowed_job_counts": False,
            },
            "size": {
                "kind": "born-digital",
                "source_bytes": 1,
                "package_bytes": 1,
                "born_digital_size_not_applicable": True,
            },
        }
    ).visual
    return parsed[0]


def _resolved_file(path: Path, name: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{name} cannot be resolved: {error}") from error
    if not resolved.is_file():
        raise ValueError(f"{name} must be a regular file: {resolved}")
    return resolved


def _resolved_build_root(path: Path | None, artifact: Path, name: str) -> Path:
    """Resolve a declared build root, or use the artifact's output directory."""

    if path is None:
        return artifact.parent
    try:
        root = path.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{name} cannot be resolved: {error}") from error
    if not root.is_dir():
        raise ValueError(f"{name} must be a directory: {root}")
    if artifact != root and root not in artifact.parents:
        raise ValueError(f"{name} does not contain its declared build artifact")
    return root


def _verify_attestation_inputs(evidence: ReplicaEvidence, inputs: ReplicaAttestationInputs) -> None:
    package_path = _resolved_file(inputs.package_path, "package")
    source_path = _resolved_file(inputs.source_path, "source")
    benchmark_path = _resolved_file(inputs.benchmark_path, "benchmark")
    renderer_evidence_path = _resolved_file(inputs.renderer_evidence_path, "renderer evidence")
    review_project_path = _resolved_file(inputs.review_project_path, "review project")
    approvals_path = _resolved_file(inputs.approvals_path, "approvals")
    visual_evidence_path = _resolved_file(inputs.visual_evidence_path, "visual evidence")
    if _file_digest(package_path, "package") != evidence.package_sha256:
        raise ValueError("resolved package bytes do not match package_sha256")
    source_digest = _file_digest(source_path, "source")
    if source_digest != evidence.source_sha256:
        raise ValueError("resolved source bytes do not match source_sha256")
    if _file_digest(benchmark_path, "benchmark") != evidence.benchmark_sha256:
        raise ValueError("benchmark artifact does not match benchmark_sha256")
    if _file_digest(renderer_evidence_path, "renderer evidence") != evidence.renderer_sha256:
        raise ValueError("renderer evidence does not match renderer_sha256")
    manifest = _package_manifest(package_path)
    from .conformance import validate_package

    if not validate_package(package_path).is_structurally_valid:
        raise ValueError("resolved package fails offline structural validation")
    source = manifest.get("source")
    pages = manifest.get("pages")
    if not isinstance(source, Mapping) or source.get("sha256") != source_digest:
        raise ValueError("package manifest source does not match resolved source")
    if not isinstance(pages, list):
        raise ValueError("package manifest has no pages")
    page_ids = tuple(item.get("id") for item in pages if isinstance(item, Mapping))
    if len(page_ids) != len(pages) or any(not isinstance(item, str) for item in page_ids):
        raise ValueError("package manifest page IDs are invalid")
    project = _load_review_project(review_project_path)
    if sha256_hex(project.canonical_export()) != evidence.review_set_sha256:
        raise ValueError("review project does not match review_set_sha256")
    if (
        project.document_id != manifest.get("document_id")
        or tuple(project.manifest_page_ids) != page_ids
    ):
        raise ValueError("review project does not bind the exact package manifest page set")
    if any(page.artifacts.source_sha256 != source_digest for page in project.pages):
        raise ValueError("review project page artifacts do not bind the resolved source")
    if not all(page.artifacts.promotion_complete for page in project.pages):
        raise ValueError("review project lacks synchronized promotion artifacts")
    approvals = _load_approvals(approvals_path)
    if not project.promotion_ready(approvals):
        raise ValueError("review approvals do not cover the exact manifest page set")
    visual = _load_visual_records(visual_evidence_path)
    visual_page_ids = tuple(item.page_id for item in visual)
    if (
        len(set(page_ids)) != len(page_ids)
        or any(not page_id for page_id in page_ids)
        or visual_page_ids != page_ids
    ):
        raise ValueError("visual evidence does not cover the exact manifest page set")
    if visual != evidence.visual:
        raise ValueError("resolved visual evidence differs from declared replica evidence")
    first = _resolved_file(inputs.build_one_path, "build one")
    second = _resolved_file(inputs.build_two_path, "build two")
    first_root = _resolved_build_root(inputs.build_one_root, first, "build one root")
    second_root = _resolved_build_root(inputs.build_two_root, second, "build two root")
    if (
        len({package_path, first, second}) != 3
        or first_root == second_root
        or first_root in second_root.parents
        or second_root in first_root.parents
    ):
        raise ValueError("reproducibility builds must use independently located artifacts")
    first_digest = _file_digest(first, "build one")
    second_digest = _file_digest(second, "build two")
    if (
        first_digest != evidence.reproducibility.build_one_sha256
        or second_digest != evidence.reproducibility.build_two_sha256
        or first_digest != second_digest
        or first_digest != evidence.package_sha256
    ):
        raise ValueError(
            "resolved reproducibility build bytes do not match declared package hashes"
        )
    if (
        evidence.size.source_bytes != source_path.stat().st_size
        or evidence.size.package_bytes != package_path.stat().st_size
    ):
        raise ValueError("declared size evidence does not match resolved artifacts")


def replica_evidence_from_dict(value: Mapping[str, Any]) -> ReplicaEvidence:
    """Load explicit attestation inputs; no metric is inferred by the CLI."""

    def mapping(name: str) -> Mapping[str, Any]:
        candidate = value.get(name)
        if not isinstance(candidate, Mapping):
            raise ValueError(f"replica evidence {name} must be an object")
        return candidate

    def boolean(record: Mapping[str, Any], name: str) -> bool:
        candidate = record.get(name)
        if not isinstance(candidate, bool):
            raise ValueError(f"replica evidence {name} must be a boolean")
        return candidate

    def integer(record: Mapping[str, Any], name: str) -> int:
        candidate = record.get(name)
        if isinstance(candidate, bool) or not isinstance(candidate, int):
            raise ValueError(f"replica evidence {name} must be an integer")
        return candidate

    def number(record: Mapping[str, Any], name: str) -> float:
        candidate = record.get(name)
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
            raise ValueError(f"replica evidence {name} must be numeric")
        return float(candidate)

    text = mapping("text")
    layout = mapping("layout")
    accessibility = mapping("accessibility")
    policy = mapping("policy")
    reproducibility = mapping("reproducibility")
    size = mapping("size")
    visual_value = value.get("visual")
    if not isinstance(visual_value, list):
        raise ValueError("replica evidence visual must be an array")
    visual: list[VisualEvidence] = []
    for index, item in enumerate(visual_value):
        if not isinstance(item, Mapping):
            raise ValueError(f"replica evidence visual[{index}] must be an object")
        continuous = item.get("continuous_tone_ssim")
        if continuous is not None and (
            isinstance(continuous, bool) or not isinstance(continuous, (int, float))
        ):
            raise ValueError("replica evidence continuous_tone_ssim must be numeric or null")
        page_class = item.get("page_class")
        if page_class not in {"born-digital", "scan", "hybrid"}:
            raise ValueError("replica evidence page_class is invalid")
        page_id = item.get("page_id")
        if page_id is not None and (not isinstance(page_id, str) or not page_id):
            raise ValueError("replica evidence page_id must be a non-empty string when supplied")
        visual.append(
            VisualEvidence(
                cast(Literal["born-digital", "scan", "hybrid"], page_class),
                number(item, "ssim"),
                number(item, "edge_recall"),
                number(item, "edge_displacement_p95"),
                float(continuous) if continuous is not None else None,
                integer(item, "undisposed_components"),
                page_id,
            )
        )
    size_kind = size.get("kind")
    if size_kind not in {"scan-dominant", "hybrid", "born-digital"}:
        raise ValueError("replica evidence size kind is invalid")
    return ReplicaEvidence(
        package_sha256=str(value.get("package_sha256", "")),
        source_sha256=str(value.get("source_sha256", "")),
        benchmark_sha256=str(value.get("benchmark_sha256", "")),
        renderer_sha256=str(value.get("renderer_sha256", "")),
        review_set_sha256=str(value.get("review_set_sha256", "")),
        structural_valid=boolean(value, "structural_valid"),
        source_identity_exact=boolean(value, "source_identity_exact"),
        benchmark_passes_all_page_classes=boolean(value, "benchmark_passes_all_page_classes"),
        high_risk_and_omissions_resolved=boolean(value, "high_risk_and_omissions_resolved"),
        every_page_approved=boolean(value, "every_page_approved"),
        text=TextAudit(
            integer(text, "total_characters"),
            integer(text, "sampled_characters"),
            integer(text, "character_errors"),
            boolean(text, "sampled_code_exact"),
            boolean(text, "sampled_identifiers_exact"),
            boolean(text, "sampled_math_exact"),
        ),
        layout=LayoutEvidence(
            boolean(layout, "all_regions_treated"),
            integer(layout, "severe_reading_order_findings"),
            integer(layout, "severe_structural_findings"),
            boolean(layout, "tables_reviewed"),
        ),
        visual=tuple(visual),
        accessibility=AccessibilityEvidence(
            boolean(accessibility, "semantic_html_valid"),
            boolean(accessibility, "authoritative_text_identical"),
            integer(accessibility, "critical_or_serious_violations"),
        ),
        policy=PolicyEvidence(
            integer(policy, "full_page_rasters"),
            integer(policy, "placeholders"),
            integer(policy, "unsupported_objects"),
            boolean(policy, "raster_policy_valid"),
            boolean(policy, "assets_hash_complete"),
            boolean(policy, "fonts_hash_complete"),
            boolean(policy, "rights_complete"),
        ),
        reproducibility=ReproducibilityEvidence(
            str(reproducibility.get("build_one_sha256", "")),
            str(reproducibility.get("build_two_sha256", "")),
            boolean(reproducibility, "distinct_roots"),
            boolean(reproducibility, "allowed_job_counts"),
        ),
        size=SizeEvidence(
            cast(Literal["scan-dominant", "hybrid", "born-digital"], size_kind),
            integer(size, "source_bytes"),
            integer(size, "package_bytes"),
            boolean(size, "born_digital_size_not_applicable"),
        ),
    )


def _evidence_dict(evidence: ReplicaEvidence) -> dict[str, object]:
    """Stable attestation payload without paths, timestamps, or runtime state."""
    return {
        "package_sha256": evidence.package_sha256,
        "source_sha256": evidence.source_sha256,
        "benchmark_sha256": evidence.benchmark_sha256,
        "renderer_sha256": evidence.renderer_sha256,
        "review_set_sha256": evidence.review_set_sha256,
        "structural_valid": evidence.structural_valid,
        "source_identity_exact": evidence.source_identity_exact,
        "benchmark_passes_all_page_classes": evidence.benchmark_passes_all_page_classes,
        "high_risk_and_omissions_resolved": evidence.high_risk_and_omissions_resolved,
        "every_page_approved": evidence.every_page_approved,
        "text": evidence.text.__dict__
        if hasattr(evidence.text, "__dict__")
        else {
            "total_characters": evidence.text.total_characters,
            "sampled_characters": evidence.text.sampled_characters,
            "character_errors": evidence.text.character_errors,
            "sampled_code_exact": evidence.text.sampled_code_exact,
            "sampled_identifiers_exact": evidence.text.sampled_identifiers_exact,
            "sampled_math_exact": evidence.text.sampled_math_exact,
        },
        "layout": {
            "all_regions_treated": evidence.layout.all_regions_treated,
            "severe_reading_order_findings": evidence.layout.severe_reading_order_findings,
            "severe_structural_findings": evidence.layout.severe_structural_findings,
            "tables_reviewed": evidence.layout.tables_reviewed,
        },
        "visual": [
            {
                "page_class": item.page_class,
                "ssim": item.ssim,
                "edge_recall": item.edge_recall,
                "edge_displacement_p95": item.edge_displacement_p95,
                "continuous_tone_ssim": item.continuous_tone_ssim,
                "undisposed_components": item.undisposed_components,
                "page_id": item.page_id,
            }
            for item in evidence.visual
        ],
        "accessibility": {
            "semantic_html_valid": evidence.accessibility.semantic_html_valid,
            "authoritative_text_identical": evidence.accessibility.authoritative_text_identical,
            "critical_or_serious_violations": evidence.accessibility.critical_or_serious_violations,
        },
        "policy": {
            "full_page_rasters": evidence.policy.full_page_rasters,
            "placeholders": evidence.policy.placeholders,
            "unsupported_objects": evidence.policy.unsupported_objects,
            "raster_policy_valid": evidence.policy.raster_policy_valid,
            "assets_hash_complete": evidence.policy.assets_hash_complete,
            "fonts_hash_complete": evidence.policy.fonts_hash_complete,
            "rights_complete": evidence.policy.rights_complete,
        },
        "reproducibility": {
            "build_one_sha256": evidence.reproducibility.build_one_sha256,
            "build_two_sha256": evidence.reproducibility.build_two_sha256,
            "distinct_roots": evidence.reproducibility.distinct_roots,
            "allowed_job_counts": evidence.reproducibility.allowed_job_counts,
        },
        "size": {
            "kind": evidence.size.kind,
            "source_bytes": evidence.size.source_bytes,
            "package_bytes": evidence.size.package_bytes,
            "born_digital_size_not_applicable": evidence.size.born_digital_size_not_applicable,
        },
    }
