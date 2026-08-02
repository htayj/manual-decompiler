from __future__ import annotations

from lispmdoc.benchmark.bolio import extract_bolio
from lispmdoc.benchmark.source_reference import render_reference_artifact


def test_reference_artifact_has_exact_block_selectors_and_section_number() -> None:
    source = (
        ".defun sample arg\nBody line one.\nBody line two.\n.end_defun\n\n"
        ".section Title\n.setq title-section section-page\n"
    )
    variables = "(DEFPROP TITLE-SECTION |section 2.4, page 9| JUST-VALUE)\n"

    artifact = render_reference_artifact(extract_bolio(source, variables))

    assert artifact.text == "sample arg\n\nBody line one. Body line two.\n\n2.4 Title\n"
    selected = [
        region.source_span.select(artifact.text).rstrip("\n")
        for region in artifact.regions
    ]
    assert selected == [
        "sample arg",
        "Body line one. Body line two.",
        "2.4 Title",
    ]
    assert artifact.issue_count == 0
    assert len(artifact.sha256) == 64
    assert [region.line_break_policy for region in artifact.regions] == [
        "structural",
        "reflow-editorial",
        "structural",
    ]


def test_reference_artifact_preserves_only_explicit_code_line_breaks() -> None:
    source = """\
First editorial line
second editorial line.

.lisp
(one)

  (two)
.end_lisp
"""

    artifact = render_reference_artifact(extract_bolio(source, ""))

    assert artifact.text == "First editorial line second editorial line.\n\n(one)\n\n  (two)\n"
    assert [region.source_span.select(artifact.text) for region in artifact.regions] == [
        "First editorial line second editorial line.\n",
        "(one)\n\n  (two)\n",
    ]
    assert [region.line_break_policy for region in artifact.regions] == [
        "reflow-editorial",
        "preserve",
    ]
