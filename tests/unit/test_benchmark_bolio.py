from __future__ import annotations

import pytest

from lispmdoc.benchmark.bolio import (
    BolioSyntaxError,
    MissingCrossReferenceError,
    extract_bolio,
    normalize_bolio_line,
    parse_manual_vars,
    render_bolio_interval,
)
from lispmdoc.benchmark.bolio_formatting import semantic_spans

VARS = """\
(DEFPROP LOCATIVE |section 13, page 170| JUST-VALUE)
(DEFPROP FUNCTION-CHAPTER /10 JUST-VALUE)
(DEFPROP SYMBOL-PLIST-SECTION |section 6.3, page 88| JUST-VALUE)
(DEFPROP |A B| |page 1| JUST-VALUE)
(DEFPROP |A\x1aB| |page 2| JUST-VALUE)
"""


def test_manual_vars_accepts_barred_multiword_and_slash_numeric_values() -> None:
    values = parse_manual_vars(VARS)

    assert dict(values) == {
        "FUNCTION-CHAPTER": "10",
        "LOCATIVE": "section 13, page 170",
        "SYMBOL-PLIST-SECTION": "section 6.3, page 88",
        "A B": "page 1",
        "A\x1aB": "page 2",
    }


def test_extracts_function_section_body_and_code_with_resolved_cross_references() -> None:
    source = """\
.defun function-cell-location sym
\x063function-cell-location\x06* returns a locative pointer.  See \x16(locative).
.lisp
(locf (fsymeval \x062sym\x06*))
.end_lisp
.end_defun

Refer to chapter \x16(function-chapter) for details.

.section "The Property List"
.setq symbol-plist-section section-page
Every symbol has an associated property list.  See \x16(symbol-plist-section).
"""

    extraction = extract_bolio(source, VARS)

    assert [(block.kind, block.text) for block in extraction.blocks] == [
        ("function", "function-cell-location sym"),
        ("body", "function-cell-location returns a locative pointer.  See section 13, page 170."),
        ("code", "(locf (fsymeval sym))"),
        ("body", "Refer to chapter 10 for details."),
        ("section", "The Property List"),
        ("body", "Every symbol has an associated property list.  See section 6.3, page 88."),
    ]
    section = extraction.blocks[4]
    assert section.section_number == "6.3"
    assert extraction.blocks[1].span.to_dict() == {"start_line": 2, "end_line": 2}
    assert extraction.blocks[2].span.to_dict() == {"start_line": 4, "end_line": 4}
    assert extraction.issues == ()


def test_reflows_editorial_prose_wraps_but_preserves_code_and_block_boundaries() -> None:
    source = """\
.defun sample argument
This paragraph is wrapped in the
source editor, but has  two spaces after a sentence.
.lisp
(first-line)
  (second-line)
.end_lisp
This is a separate structural paragraph.
.end_defun
"""

    extraction = extract_bolio(source, VARS)

    assert [(block.kind, block.text, block.line_break_policy) for block in extraction.blocks] == [
        ("function", "sample argument", "structural"),
        (
            "body",
            "This paragraph is wrapped in the source editor, but has  two spaces after a sentence.",
            "reflow-editorial",
        ),
        ("code", "(first-line)\n  (second-line)", "preserve"),
        ("body", "This is a separate structural paragraph.", "reflow-editorial"),
    ]
    assert extraction.blocks[1].span.to_dict() == {"start_line": 2, "end_line": 3}
    assert extraction.blocks[2].span.to_dict() == {"start_line": 5, "end_line": 6}


def test_prose_indent_is_structural_and_trailing_editor_space_is_not_visible() -> None:
    extraction = extract_bolio("\tIndented paragraph line \ncontinuation", VARS)

    assert extraction.blocks[0].text == "Indented paragraph line continuation"
    assert extraction.blocks[0].paragraph_indent


def test_cross_reference_resolution_is_strict() -> None:
    with pytest.raises(MissingCrossReferenceError, match="absent from manual.vars"):
        extract_bolio("See \x16(not-present).", VARS)


def test_unknown_directives_and_controls_are_explicitly_reported_not_dropped() -> None:
    extraction = extract_bolio(".mystery retain-this\nA\x07B", VARS)

    assert extraction.blocks[0].text == "A\x07B"
    assert [(issue.kind, issue.line) for issue in extraction.issues] == [
        ("unsupported-directive", 1),
        ("unsupported-control", 2),
    ]


def test_normalization_is_deterministic_and_control_q_preserves_quoted_punctuation() -> None:
    first = extract_bolio("i.e\x11. \x063name\x06*", VARS)
    second = extract_bolio("i.e\x11. \x063name\x06*", VARS)

    assert first.to_dict() == second.to_dict()
    assert first.blocks[0].text == "i.e. name"
    assert normalize_bolio_line("a\x18b\x19", parse_manual_vars(VARS)) == ("ab", ())


def test_malformed_structure_is_not_treated_as_visible_text() -> None:
    with pytest.raises(BolioSyntaxError, match="inside .lisp"):
        extract_bolio(".lisp\n.section Wrong", VARS)


def test_explicit_bold_is_scoped_by_source_controls_not_token_spelling() -> None:
    spans = semantic_spans(
        kind="body",
        reference_text="same same same",
        raw_lines=("same \x19same\x18 same",),
        variables={},
    )

    assert [(span.text, span.bold) for span in spans] == [
        ("same ", False),
        ("same", True),
        (" same", False),
    ]


def test_unbalanced_explicit_bold_fails_closed() -> None:
    with pytest.raises(BolioSyntaxError, match="not closed"):
        semantic_spans(kind="body", reference_text="same", raw_lines=("\x19same",), variables={})


def test_tables_defspec_defun1_and_sail_comparison_glyphs_are_structural() -> None:
    source = """\
.defspec defresource
.table 3
.kitem :constructor
Makes the resource.
.end_table
.end_defspec
.defun >= x y
.defun1 \x11\x1d x y
Returns \x19t\x18 when x \x1d y.
.lisp
(\x1d x y)
.end_lisp
.end_defun
"""

    extraction = extract_bolio(source, VARS)

    assert [(block.kind, block.text) for block in extraction.blocks] == [
        ("function", "defresource"),
        ("list-item", ":constructor"),
        ("body", "Makes the resource."),
        ("function", ">= x y"),
        ("function", "≥ x y"),
        ("body", "Returns t when x ≥ y."),
        ("code", "(≥ x y)"),
    ]
    assert not extraction.issues


def test_partial_interval_is_rendered_from_source_not_neighboring_blocks() -> None:
    source = """\
First physical source line
continues here.

Second paragraph must be excluded.
"""
    extraction = extract_bolio(source, VARS)

    rendered = render_bolio_interval(extraction, source, start_line=1, end_line=2)

    assert rendered == "First physical source line continues here."


def test_multiline_code_formatting_spans_preserve_literal_newlines() -> None:
    spans = semantic_spans(
        kind="code",
        reference_text="(first)\n  (second sym)",
        raw_lines=("(first)", "  (second \x062sym\x06*)"),
        variables={},
    )

    assert "".join(span.text for span in spans) == "(first)\n  (second sym)"
    assert next(span for span in spans if span.text == "sym").style == "font-2-italic"


def test_apostrophe_cindex_command_is_nonprinting_metadata() -> None:
    extraction = extract_bolio(".section Sorting\n'cindex sorting\nVisible prose.\n", VARS)

    assert [(block.kind, block.text) for block in extraction.blocks] == [
        ("section", "Sorting"),
        ("body", "Visible prose."),
    ]


def test_quoted_sail_not_equal_character_is_preserved_semantically() -> None:
    source = "x \x11\x1a 0\n"

    extraction = extract_bolio(source, VARS)
    spans = semantic_spans(
        kind="body",
        reference_text=extraction.blocks[0].text,
        raw_lines=(source.rstrip("\n"),),
        variables={},
    )

    assert extraction.blocks[0].text == "x ≠ 0"
    assert "".join(span.text for span in spans) == "x ≠ 0"
