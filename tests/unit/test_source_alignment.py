from __future__ import annotations

from lispmdoc.benchmark import OcrRegionText, align_ocr_page_to_sources, align_ocr_regions_to_source


def test_ocr_alignment_selects_source_and_retains_approximate_lines() -> None:
    sources = {
        "other.1": "Unrelated words about windows and files.\nAnother unrelated paragraph.",
        "manual.2": (
            ".section Symbols\n"
            "Every symbol has an associated property list.\n"
            "When a symbol is created its property list is initially empty.\n"
            ".defun get symbol indicator\n"
            "This searches the property list for the indicator.\n"
        ),
    }
    ocr = (
        "Lisp Machine Manual Symbols Every symbol has an associated property list. "
        "When a symbol is created its property list is initially empty. "
        "get symbol indicator This searches the property list for the indicater."
    )

    aligned = align_ocr_page_to_sources(ocr, sources, {})

    assert aligned.source_path == "manual.2"
    assert aligned.start_line == 1
    assert aligned.end_line == 5
    assert aligned.coverage_milli > 800
    assert aligned.mapping_review_required


def test_repeated_text_outside_page_cluster_does_not_expand_line_range() -> None:
    padding = "\n".join(f"irrelevant line {index}" for index in range(150))
    sources = {
        "manual.3": (
            "common example words\n"
            + padding
            + "\nfirst distinctive source phrase with enough exact tokens\n"
            + "second distinctive source phrase with still more exact tokens\n"
        )
    }
    ocr = (
        "common example words first distinctive source phrase with enough exact tokens "
        "second distinctive source phrase with still more exact tokens"
    )

    aligned = align_ocr_page_to_sources(ocr, sources, {})

    assert aligned.start_line > 140
    assert aligned.end_line <= 153


def test_ordered_ocr_regions_retain_exact_source_line_evidence() -> None:
    source = """\
.section Property Lists
Every symbol has an associated property list.
When created, that property list is empty.
.defun get symbol indicator
Get searches for the indicator and returns its value.
"""
    regions = (
        OcrRegionText("heading", "Property Lists"),
        OcrRegionText(
            "prose",
            "Every symbol has an associated property list. "
            "When created, that property list is empty.",
        ),
        OcrRegionText("definition", "get symbol indicator"),
        OcrRegionText("explanation", "Get searches for the indicator and returns its value."),
    )

    aligned = align_ocr_regions_to_source(
        regions,
        source,
        {},
        approximate_start_line=1,
        approximate_end_line=5,
    )

    assert [(item.region_id, item.start_line, item.end_line) for item in aligned] == [
        ("heading", 1, 1),
        ("prose", 2, 3),
        ("definition", 4, 4),
        ("explanation", 5, 5),
    ]
    assert all(item.coverage_milli == 1000 for item in aligned)
