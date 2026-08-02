from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path


def _pilot_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "prepare-chinual-authoritative-pilot"
    loader = importlib.machinery.SourceFileLoader("chinual_pilot_test_module", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def test_semantic_runs_strip_editorial_newlines_but_keep_bolio_font_intent() -> None:
    pilot = _pilot_module()
    runs = pilot.semantic_runs(
        kind="body",
        reference_text="function-cell-location returns a locative pointer to sym's function cell.",
        raw_lines=(
            "\x063function-cell-location\x06* returns a locative pointer to \x062sym\x06*'s",
            "function cell.",
        ),
        variables={},
    )

    assert "".join(run.text for run in runs) == (
        "function-cell-location returns a locative pointer to sym's function cell."
    )
    assert [(run.text, run.style) for run in runs if run.text.strip()] == [
        ("function-cell-location", "bold"),
        (" returns a locative pointer to ", "roman"),
        ("sym", "italic"),
        ("'s function cell.", "roman"),
    ]


def test_semantic_runs_preserve_literal_intraline_double_spaces() -> None:
    pilot = _pilot_module()

    runs = pilot.semantic_runs(
        kind="body",
        reference_text="First sentence.  Second sentence.",
        raw_lines=("First sentence.  Second", "sentence."),
        variables={},
    )

    assert "".join(run.text for run in runs) == "First sentence.  Second sentence."


def test_function_code_and_arguments_receive_distinct_runs() -> None:
    pilot = _pilot_module()
    runs = pilot.semantic_runs(
        kind="function",
        reference_text="setplist sym list",
        raw_lines=(),
        variables={},
    )

    assert [(run.text, run.style) for run in runs] == [
        ("setplist", "function"),
        (" sym", "italic"),
        (" list", "italic"),
    ]


def test_scan_grounded_paragraph_layout_reflows_and_retains_first_line_indent() -> None:
    pilot = _pilot_module()
    layout = pilot.layout_region(
        kind="body",
        runs=(
            pilot.InlineRun(
                "Every symbol has an associated property list. See section 5.8, page 71 for "
                "documentation of property lists. When a symbol is created, its property list "
                "is initially empty."
            ),
        ),
        bbox=(288, 1063, 2188, 1178),
        paragraph_indent=True,
    )

    assert layout.font_size == 44
    assert len(layout.lines) == 2
    assert layout.lines[0].runs[0].x == 393.72
    assert layout.lines[1].runs[0].x == 293.72
    assert layout.generated_bbox[0] == 293.72
    assert layout.generated_bbox[3] <= 1178


def test_multiline_code_preserves_explicit_lines_and_leading_spaces() -> None:
    pilot = _pilot_module()
    runs = pilot.semantic_runs(
        kind="code",
        reference_text="(one)\n  (two)",
        raw_lines=(),
        variables={},
    )

    layout = pilot.layout_region(
        kind="code",
        runs=runs,
        bbox=(100, 200, 900, 400),
        paragraph_indent=False,
    )

    assert len(layout.lines) == 2
    assert [line.runs[0].text for line in layout.lines] == ["(one)", "  (two)"]
    assert layout.lines[1].baseline > layout.lines[0].baseline
    assert layout.font_size == 40


def test_bolio_f3_is_bold_roman_not_monospaced_and_run_text_is_source_identical() -> None:
    pilot = _pilot_module()
    runs = pilot.semantic_runs(
        kind="body",
        reference_text="The names expr and fexpr are historical.",
        raw_lines=("The names \x063expr\x06* and \x063fexpr\x06* are historical.",),
        variables={},
    )

    assert "".join(run.text for run in runs) == "The names expr and fexpr are historical."
    assert [(run.text, run.style) for run in runs] == [
        ("The names ", "roman"),
        ("expr", "bold"),
        (" and ", "roman"),
        ("fexpr", "bold"),
        (" are historical.", "roman"),
    ]
    assert "font-family=\"'Liberation Serif'" in pilot._style_attributes("bold")
    assert "monospace" not in pilot._style_attributes("bold")


def test_r6_body_scale_fills_long_region_with_tighter_leading_and_section_is_taller() -> None:
    pilot = _pilot_module()
    long_runs = (
        pilot.InlineRun(
            "The Lisp language itself does not use a symbol's property list for anything.  "
            "(This was not true in older Lisp implementations, where the print-name, value-cell, "
            "and function-cell of a symbol were kept on its property list.)  However, various "
            "system programs use the property list to associate information with the symbol.  "
            "For instance, the editor uses the property list of a symbol which is the name of a "
            "function to remember where it has the source code for that function, and the compiler "
            "uses the property list of a symbol which is the name of a special form to remember "
            "how "
            "to compile that special form."
        ),
    )
    body = pilot.layout_region(
        kind="body",
        runs=long_runs,
        bbox=(283, 1218, 2183, 1591),
        paragraph_indent=True,
    )
    section = pilot.layout_region(
        kind="section",
        runs=(pilot.InlineRun("6.3 The Property List", "bold"),),
        bbox=(288, 957, 773, 1020),
        paragraph_indent=False,
    )

    assert body.font_size == 44
    assert body.line_height == 52.8
    assert len(body.lines) == 7
    assert body.generated_bbox[3] - body.generated_bbox[1] > 350
    assert section.font_size == 53
    assert "monospace" in pilot._style_attributes("function")
    assert "font-weight=\"700\"" in pilot._style_attributes("function")
