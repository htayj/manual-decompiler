"""Canonical evidence records; raw bytes live in :mod:`lispmdoc.evidence.store`."""

from __future__ import annotations

from dataclasses import dataclass

from .store import Artifact


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One auditable claim and the exact artifacts that support it."""

    id: str
    subject_id: str
    producer: str
    producer_version: str
    configuration_sha256: str
    artifacts: tuple[Artifact, ...]
    alternatives: tuple[str, ...] = ()
    unresolved_findings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id or not self.subject_id or not self.producer or not self.producer_version:
            raise ValueError("evidence identity and producer identity are required")
        if len(self.configuration_sha256) != 64:
            raise ValueError("evidence configuration_sha256 must be SHA-256")
        if not self.artifacts:
            raise ValueError("evidence must retain at least one exact artifact")
        if len({item.sha256 for item in self.artifacts}) != len(self.artifacts):
            raise ValueError("evidence artifacts must not duplicate digests")

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "producer": self.producer,
            "producer_version": self.producer_version,
            "configuration_sha256": self.configuration_sha256,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "alternatives": list(self.alternatives),
            "unresolved_findings": list(self.unresolved_findings),
        }
