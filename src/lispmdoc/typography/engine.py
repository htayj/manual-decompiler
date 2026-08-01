"""Deterministic substitute ranking, capability contracts, and distribution gates."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

from lispmdoc.licenses import FontLicenseDecision, require_distributable

from .types import FontResource, FontSubsetPlan, SubstituteCandidate


@dataclass(frozen=True, slots=True)
class TypographyCapabilities:
    fonttools: bool
    woff2: bool
    harfbuzz: bool

    @property
    def can_subset(self) -> bool:
        return self.fonttools and self.woff2

    @property
    def can_shape(self) -> bool:
        return self.harfbuzz

    def contracts(self) -> tuple[str, ...]:
        contracts: list[str] = []
        if not self.fonttools:
            contracts.append("FontTools unavailable: do not inspect or subset fonts")
        if not self.woff2:
            contracts.append("WOFF2 support unavailable: do not emit WOFF2 subsets")
        if not self.harfbuzz:
            contracts.append("HarfBuzz unavailable: do not claim shaped-text fidelity")
        return tuple(contracts)


def probe_capabilities() -> TypographyCapabilities:
    return TypographyCapabilities(
        fonttools=importlib.util.find_spec("fontTools") is not None,
        woff2=importlib.util.find_spec("brotli") is not None,
        harfbuzz=importlib.util.find_spec("uharfbuzz") is not None,
    )


def rank_substitutes(
    candidates: tuple[SubstituteCandidate, ...],
) -> tuple[SubstituteCandidate, ...]:
    """Rank measured candidates, never by a guessed family name alone."""
    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.line_width_error_milli,
                item.baseline_error_micropoints,
                -item.glyph_coverage_milli,
                item.resource.sha256,
            ),
        )
    )


def choose_substitute(candidates: tuple[SubstituteCandidate, ...]) -> SubstituteCandidate | None:
    ranked = rank_substitutes(candidates)
    return ranked[0] if ranked and ranked[0].glyph_coverage_milli == 1000 else None


def distributable_subset(
    plan: FontSubsetPlan, decision: FontLicenseDecision | None
) -> FontSubsetPlan:
    approved = require_distributable(decision)
    if not approved.may_subset:
        raise PermissionError("font decision does not approve subsetting")
    return plan


def distributable_font(
    resource: FontResource, decision: FontLicenseDecision | None
) -> FontResource:
    if decision is None or decision.font_sha256 != resource.sha256:
        raise PermissionError("font resource lacks matching human distribution decision")
    require_distributable(decision)
    return resource
