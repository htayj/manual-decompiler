from __future__ import annotations

import hashlib

import pytest

from lispmdoc.benchmark.source_selectors import (
    SourceSelector,
    SourceSelectorError,
    load_source_selector_overlay,
    select_source_text,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _rule(
    selector: dict[str, object], output: str, *, start: int = 1, end: int = 1
) -> SourceSelector:
    return SourceSelector(
        1,
        "block-001",
        "source.1",
        _sha("source"),
        start,
        end,
        selector,
        _sha(output),
    )


def test_selects_exact_rendered_character_range_and_explicit_projection() -> None:
    rule = _rule(
        {
            "kind": "rendered-character-range",
            "start": 1,
            "end": 4,
            "projection": "line-breaks-to-spaces",
        },
        "a b",
    )

    assert (
        select_source_text(
            rule,
            source_sha256=_sha("source"),
            source_text="irrelevant",
            rendered_interval="xa\nbz",
            region_kind="body",
        )
        == "a b"
    )


def test_selects_generic_directive_role_and_balanced_token_components() -> None:
    role = _rule(
        {"kind": "directive-component", "directive": "defspec", "component": "role"},
        "Special Form",
    )
    signature = _rule(
        {
            "kind": "directive-component",
            "directive": "defspec",
            "component": "token-range",
            "start": 0,
            "end": 2,
        },
        "using-resource (variable resource)",
    )
    source = ".defspec using-resource (variable resource) body...\n"

    assert (
        select_source_text(
            role,
            source_sha256=_sha("source"),
            source_text=source,
            rendered_interval="ignored",
            region_kind="body",
        )
        == "Special Form"
    )
    assert (
        select_source_text(
            signature,
            source_sha256=_sha("source"),
            source_text=source,
            rendered_interval="ignored",
            region_kind="body",
        )
        == "using-resource (variable resource)"
    )


def test_selects_a_structural_kitem_prose_range_without_flattening_table_text() -> None:
    rule = _rule(
        {
            "kind": "directive-component",
            "directive": "kitem",
            "component": "label-and-following-prose-character-range",
            "start": 0,
            "end": 5,
        },
        ":item  hello",
        start=1,
        end=2,
    )

    assert (
        select_source_text(
            rule,
            source_sha256=_sha("source"),
            source_text=".kitem :item\nhello world\n",
            rendered_interval=":item\n\nhello world",
            region_kind="body",
            has_table_semantics=True,
        )
        == ":item  hello"
    )


@pytest.mark.parametrize(
    ("selector", "source", "rendered", "message"),
    (
        (
            {"kind": "rendered-character-range", "start": 0, "end": 9},
            "source",
            "short",
            "outside fresh interval",
        ),
        (
            {"kind": "directive-component", "directive": "defun", "component": "role"},
            ".defspec x\n",
            "",
            "does not match source",
        ),
        (
            {
                "kind": "directive-component",
                "directive": "defspec",
                "component": "token-range",
                "start": 0,
                "end": 1,
            },
            ".defspec f (unbalanced\n",
            "",
            "unbalanced parentheses",
        ),
    ),
)
def test_rejects_ambiguous_or_malformed_component_selection(
    selector: dict[str, object], source: str, rendered: str, message: str
) -> None:
    rule = _rule(selector, "unused")
    with pytest.raises(SourceSelectorError, match=message):
        select_source_text(
            rule,
            source_sha256=_sha("source"),
            source_text=source,
            rendered_interval=rendered,
            region_kind="body",
        )


def test_overlay_rejects_manifest_drift_duplicate_targets_and_source_digest_drift() -> None:
    selector = {
        "page_number": 1,
        "region_id": "block-001",
        "region_kind": "body",
        "source_path": "source.1",
        "source_sha256": _sha("source"),
        "source_span": [1, 1],
        "selector": {"kind": "rendered-character-range", "start": 0, "end": 1},
        "selected_text_sha256": _sha("x"),
    }
    overlay = {
        "format_version": "lispmdoc-chinual-source-selector-overlay-1",
        "r33_manifest_sha256": "a" * 64,
        "selectors": [selector, selector],
    }
    with pytest.raises(SourceSelectorError, match="duplicate target"):
        load_source_selector_overlay(overlay, manifest_sha256="a" * 64)
    with pytest.raises(SourceSelectorError, match="does not bind"):
        load_source_selector_overlay(overlay, manifest_sha256="b" * 64)
    rule = _rule({"kind": "rendered-character-range", "start": 0, "end": 1}, "x")
    with pytest.raises(SourceSelectorError, match="does not bind current source"):
        select_source_text(
            rule,
            source_sha256=_sha("changed"),
            source_text="source",
            rendered_interval="x",
            region_kind="body",
        )
