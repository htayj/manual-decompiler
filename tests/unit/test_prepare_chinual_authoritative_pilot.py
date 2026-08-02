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
        ("function-cell-location", "code"),
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
        ("setplist", "code"),
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

    assert layout.font_size >= 34
    assert len(layout.lines) == 2
    assert layout.lines[0].runs[0].x == 438
    assert layout.lines[1].runs[0].x == 288
    assert layout.generated_bbox[0] == 288
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
