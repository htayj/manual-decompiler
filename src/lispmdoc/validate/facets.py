"""Validation of explicit conformance facets, separate from tree structure."""

from __future__ import annotations

from dataclasses import dataclass

from lispmdoc.model import ConformanceFacets


@dataclass(frozen=True, slots=True)
class FacetReport:
    facets: ConformanceFacets
    missing: tuple[str, ...]

    @property
    def replacement_ready(self) -> bool:
        return not self.missing


def validate_facets(facets: ConformanceFacets) -> FacetReport:
    """A replacement claim needs every independent facet to pass."""
    missing = tuple(name for name, value in facets.to_dict().items() if value != "pass")
    return FacetReport(facets, missing)
