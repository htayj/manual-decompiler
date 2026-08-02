from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

from lispmdoc.benchmark import extract_bolio
from lispmdoc.typography import CHINUAL_4E_LAYOUT


def _replica_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "prepare-chinual-slice-replicas"
    loader = importlib.machinery.SourceFileLoader("chinual_replica_test_module", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def test_exdented_lisp_after_table_keeps_three_source_anchors() -> None:
    replica = _replica_module()
    source = """.table
.item g
x > 0
.end_table
.lisp
.exdent 96 Examples:
(signp ge 12) => t
(signp g 'foo) => nil
.end_lisp
"""
    extraction = extract_bolio(source, {})

    layouts = replica._mixed_table_code_layouts(
        extraction,
        source,
        2,
        8,
        (536, 439, 1227, 762),
        CHINUAL_4E_LAYOUT,
    )

    assert len(layouts) == 4
    table_label, table_body, prose_label, code = layouts
    assert "".join(span.text for span in table_label.spans) == "g"
    assert "".join(span.text for span in table_body.spans) == "x > 0"
    assert "".join(span.text for span in prose_label.spans) == "Examples:"
    assert "".join(span.text for span in code.spans) == (
        "(signp ge 12) => t\n(signp g 'foo) => nil"
    )
    assert prose_label.origin[0] < table_label.origin[0]
    assert code.origin[0] == pytest.approx(table_label.origin[0], abs=1)
    assert table_label.origin[0] - prose_label.origin[0] == pytest.approx(154)
    assert all(span.style == "display-code" for span in code.spans)


def test_mixed_layout_is_not_guessed_without_source_exdent() -> None:
    replica = _replica_module()
    source = """.table
.item g
x > 0
.end_table
.lisp
(signp g 'foo) => nil
.end_lisp
"""
    extraction = extract_bolio(source, {})

    assert (
        replica._mixed_table_code_layouts(
            extraction,
            source,
            2,
            6,
            (536, 439, 1227, 762),
            CHINUAL_4E_LAYOUT,
        )
        == []
    )
