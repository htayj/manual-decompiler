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


def test_partial_body_interval_does_not_invent_a_new_paragraph_indent() -> None:
    replica = _replica_module()
    source = "First physical line of one paragraph.\nContinuation on the next page.\n"
    extraction = extract_bolio(source, {})

    canonical, spans, kind, paragraph_indent = replica._region_source(
        "example.1",
        extraction,
        source,
        2,
        2,
        "Continuation on the next page.",
    )

    assert canonical == "Continuation on the next page."
    assert "".join(span.text for span in spans) == canonical
    assert kind == "body"
    assert not paragraph_indent


def test_definition_flow_uses_a_persistent_body_inset_for_wrapped_lines() -> None:
    replica = _replica_module()
    source = """.defun less-or-equal x y
less-or-equal compares its arguments from left to right.  If an argument is greater
than the next, it returns nil.  Otherwise the result is t.
.end_defun
"""
    extraction = extract_bolio(source, {})

    layouts = replica._definition_flow_layouts(
        extraction,
        source,
        1,
        3,
        (275, 452, 2173, 900),
        CHINUAL_4E_LAYOUT,
    )

    assert len(layouts) == 2
    heading, body = layouts
    assert body.line_count == 2
    assert body.origin[0] - heading.origin[0] == pytest.approx(154, abs=1)
    assert not any(
        span.evidence == "typesetter-paragraph-first-line-indent" for span in body.spans
    )


def test_table_scope_is_derived_from_source_directives() -> None:
    replica = _replica_module()
    lines = (".table 3", ".kitem :constructor", "body", ".end_table", "after")

    assert replica._inside_table(lines, 3)
    assert not replica._inside_table(lines, 5)
