"""Explicit human-controlled distribution policy for embedded font resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LicenseStatus = Literal[
    "approved-embed", "approved-subset", "external-reference-only", "restricted", "unknown"
]


@dataclass(frozen=True, slots=True)
class FontLicenseDecision:
    font_sha256: str
    status: LicenseStatus
    authority: str
    rationale: str

    def __post_init__(self) -> None:
        if len(self.font_sha256) != 64 or not self.authority or not self.rationale:
            raise ValueError("license decisions require font digest, authority, and rationale")

    @property
    def distributable(self) -> bool:
        return self.status in {"approved-embed", "approved-subset"}

    @property
    def may_subset(self) -> bool:
        return self.status == "approved-subset"


def require_distributable(decision: FontLicenseDecision | None) -> FontLicenseDecision:
    """Fail closed: absent, unknown, or restricted decisions cannot ship a font."""
    if decision is None or not decision.distributable:
        raise PermissionError("font has no approved distribution decision")
    return decision
