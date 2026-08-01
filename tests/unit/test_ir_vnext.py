from __future__ import annotations

import pytest

from lispmdoc.model import ConformanceFacets, ReadingEdge, linearize_reading_order
from lispmdoc.validate import validate_facets


def test_reading_graph_has_deterministic_topological_order() -> None:
    assert linearize_reading_order(
        ("c", "a", "b"),
        (ReadingEdge("a", "b", "reading-next"), ReadingEdge("b", "c", "reading-next")),
    ) == ("a", "b", "c")
    with pytest.raises(ValueError, match="acyclic"):
        linearize_reading_order(
            ("a", "b"),
            (ReadingEdge("a", "b", "reading-next"), ReadingEdge("b", "a", "reading-next")),
        )


def test_conformance_facets_are_not_substitutable() -> None:
    report = validate_facets(ConformanceFacets(text="pass", fidelity="pass"))
    assert not report.replacement_ready
    assert set(report.missing) == {
        "structure",
        "accessibility",
        "reproducibility",
        "raster_policy",
        "size",
        "distribution_rights",
    }
