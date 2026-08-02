from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path


def _evaluation_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "evaluate-chinual-authoritative-pilot"
    loader = importlib.machinery.SourceFileLoader("chinual_evaluation_test_module", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def test_authoritative_pilot_remains_insufficient_for_corpus_wide_engine_selection() -> None:
    evaluation = _evaluation_module()

    disposition, reason = evaluation._selection_status("authoritative-ready")

    assert disposition == "provisional-single-page-only"
    assert "authoritative-ready" in reason
    assert "one page" in reason


def test_incomplete_review_reports_layout_limitation() -> None:
    evaluation = _evaluation_module()

    disposition, reason = evaluation._selection_status("human-layout-review-required")

    assert disposition == "provisional-text-only"
    assert "layout discrepancies remain" in reason
