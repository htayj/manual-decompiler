"""Fail-closed exact-source selectors for recovered benchmark overlays.

Readable line spans remain useful provenance, but a line can contain more
than one benchmark region.  This module selects either a bounded character
slice of the *freshly rendered* interval or a component of one parsed Bolio
definition directive.  It never accepts stored replica text as selector input.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class SourceSelectorError(ValueError):
    """A selector overlay cannot authoritatively select source text."""


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_DIRECTIVE = re.compile(r"^\.(?P<name>[A-Za-z][A-Za-z0-9_-]*)(?:\s+(?P<args>.*))?$")
_DIRECTIVE_ROLES = {
    "defconst": "Constant",
    "defmac": "Macro",
    "defspec": "Special Form",
    "defun": "Function",
    "defvar": "Variable",
}
_PROSE_PROJECTION_KINDS = frozenset({"body"})


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise SourceSelectorError(f"{label} must be a lower-case SHA-256 digest")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SourceSelectorError(f"{label} must be an integer at least {minimum}")
    return value


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SourceSelectorError(f"{label} must be an object")
    return value


@dataclass(frozen=True, slots=True)
class SourceSelector:
    """One manifest-bound exact selection rule."""

    page_number: int
    region_id: str
    source_path: str
    source_sha256: str
    start_line: int
    end_line: int
    selector: Mapping[str, Any]
    selected_text_sha256: str
    region_kind: str = "body"

    @property
    def key(self) -> tuple[int, str]:
        return self.page_number, self.region_id

    def identity(self) -> dict[str, object]:
        return {
            "end_line": self.end_line,
            "page_number": self.page_number,
            "region_id": self.region_id,
            "region_kind": self.region_kind,
            "selected_text_sha256": self.selected_text_sha256,
            "selector": dict(self.selector),
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "start_line": self.start_line,
        }


@dataclass(frozen=True, slots=True)
class SourceSelectorOverlay:
    """Tracked overlay bound to exactly one final-manifest byte sequence."""

    manifest_sha256: str
    selectors: tuple[SourceSelector, ...]

    def selector_for(self, page_number: int, region_id: str) -> SourceSelector | None:
        matches = [item for item in self.selectors if item.key == (page_number, region_id)]
        if len(matches) > 1:
            raise SourceSelectorError(f"ambiguous selector for page {page_number}/{region_id}")
        return matches[0] if matches else None


def load_source_selector_overlay(
    value: Mapping[str, Any], *, manifest_sha256: str
) -> SourceSelectorOverlay:
    """Parse a strict overlay and bind it to the verified final manifest bytes."""

    if value.get("format_version") != "lispmdoc-chinual-source-selector-overlay-1":
        raise SourceSelectorError("source selector overlay format is unsupported")
    if _digest(value.get("r33_manifest_sha256"), "overlay r33_manifest_sha256") != manifest_sha256:
        raise SourceSelectorError("source selector overlay does not bind final r33 manifest")
    raw_selectors = value.get("selectors")
    if not isinstance(raw_selectors, list):
        raise SourceSelectorError("source selector overlay selectors must be an array")
    selectors: list[SourceSelector] = []
    keys: set[tuple[int, str]] = set()
    for index, raw in enumerate(raw_selectors):
        item = _object(raw, f"source selector {index}")
        page_number = _integer(
            item.get("page_number"), f"source selector {index} page_number", minimum=1
        )
        region_id = item.get("region_id")
        region_kind = item.get("region_kind")
        source_path = item.get("source_path")
        if not isinstance(region_id, str) or not region_id:
            raise SourceSelectorError(f"source selector {index} region_id is malformed")
        if not isinstance(region_kind, str) or not region_kind:
            raise SourceSelectorError(f"source selector {index} region_kind is malformed")
        if not isinstance(source_path, str) or not source_path:
            raise SourceSelectorError(f"source selector {index} source_path is malformed")
        span = item.get("source_span")
        if (
            not isinstance(span, list)
            or len(span) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in span
            )
            or span[0] > span[1]
        ):
            raise SourceSelectorError(f"source selector {index} source_span is malformed")
        selector = _object(item.get("selector"), f"source selector {index} selector")
        kind = selector.get("kind")
        if kind not in {"rendered-character-range", "directive-component"}:
            raise SourceSelectorError(f"source selector {index} kind is unsupported")
        key = (page_number, region_id)
        if key in keys:
            raise SourceSelectorError(f"source selector overlay has duplicate target {key}")
        keys.add(key)
        selectors.append(
            SourceSelector(
                page_number,
                region_id,
                source_path,
                _digest(item.get("source_sha256"), f"source selector {index} source_sha256"),
                span[0],
                span[1],
                selector,
                _digest(
                    item.get("selected_text_sha256"),
                    f"source selector {index} selected_text_sha256",
                ),
                region_kind,
            )
        )
    return SourceSelectorOverlay(manifest_sha256, tuple(selectors))


def _directive_tokens(value: str) -> tuple[str, ...]:
    """Split a directive argument list without cutting parenthesized groups."""

    tokens: list[str] = []
    start: int | None = None
    depth = 0
    for index, character in enumerate(value):
        if character.isspace() and depth == 0:
            if start is not None:
                tokens.append(value[start:index])
                start = None
            continue
        if start is None:
            start = index
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                raise SourceSelectorError("directive component has unbalanced parentheses")
    if depth != 0:
        raise SourceSelectorError("directive component has unbalanced parentheses")
    if start is not None:
        tokens.append(value[start:])
    if not tokens:
        raise SourceSelectorError("directive component has no arguments")
    return tuple(tokens)


def _directive_component(
    selector: Mapping[str, Any], source_text: str, rendered_interval: str, rule: SourceSelector
) -> str:
    lines = source_text.split("\n")
    if rule.start_line > len(lines):
        raise SourceSelectorError("directive component selector line exceeds source")
    match = _DIRECTIVE.fullmatch(lines[rule.start_line - 1].strip())
    if match is None:
        raise SourceSelectorError("directive component selector does not name a directive")
    expected_directive = selector.get("directive")
    directive = match.group("name").lower()
    if not isinstance(expected_directive, str) or expected_directive.lower() != directive:
        raise SourceSelectorError("directive component selector directive does not match source")
    component = selector.get("component")
    if component in {"label-and-following-prose", "label-and-following-prose-character-range"}:
        if directive != "kitem":
            raise SourceSelectorError("label-and-following-prose is valid only for .kitem")
        rendered_components = rendered_interval.split("\n\n")
        if len(rendered_components) != 2 or any(not value for value in rendered_components):
            raise SourceSelectorError(
                ".kitem selector does not have one exact following prose component"
            )
        if rendered_components[0] != (match.group("args") or "").strip():
            raise SourceSelectorError(".kitem label does not match freshly rendered component")
        prose = rendered_components[1]
        if component == "label-and-following-prose-character-range":
            start = _integer(selector.get("start"), "kitem prose character-range start")
            end = _integer(selector.get("end"), "kitem prose character-range end")
            if start >= end or end > len(prose):
                raise SourceSelectorError("kitem prose character-range is outside following prose")
            prose = prose[start:end]
        return f"{rendered_components[0]}  {prose}"
    if rule.start_line != rule.end_line:
        raise SourceSelectorError("directive component selector must name exactly one source line")
    if component == "role":
        try:
            return _DIRECTIVE_ROLES[directive]
        except KeyError as error:
            raise SourceSelectorError(
                f"directive {directive!r} has no generic display role"
            ) from error
    if component != "token-range":
        raise SourceSelectorError("directive component selector is unsupported")
    tokens = _directive_tokens(match.group("args") or "")
    start = _integer(selector.get("start"), "directive token-range start")
    end = _integer(selector.get("end"), "directive token-range end")
    if start >= end or end > len(tokens):
        raise SourceSelectorError("directive token-range is outside parsed directive tokens")
    return " ".join(tokens[start:end])


def select_source_text(
    rule: SourceSelector,
    *,
    source_sha256: str,
    source_text: str,
    rendered_interval: str,
    region_kind: str,
    has_table_semantics: bool = False,
) -> str:
    """Select one exact source component and verify its declared output digest."""

    if source_sha256 != rule.source_sha256:
        raise SourceSelectorError("source selector does not bind current source bytes")
    if region_kind != rule.region_kind:
        raise SourceSelectorError("source selector does not bind manifest region kind")
    kind = rule.selector.get("kind")
    if kind == "rendered-character-range":
        start = _integer(rule.selector.get("start"), "rendered character-range start")
        end = _integer(rule.selector.get("end"), "rendered character-range end")
        if start >= end or end > len(rendered_interval):
            raise SourceSelectorError("rendered character-range is outside fresh interval")
        selected = rendered_interval[start:end]
        projection = rule.selector.get("projection", "preserve")
        if projection == "line-breaks-to-spaces":
            if region_kind not in _PROSE_PROJECTION_KINDS or has_table_semantics:
                raise SourceSelectorError(
                    "line-breaks-to-spaces projection is permitted only for non-table body prose"
                )
            selected = selected.replace("\n", " ")
        elif projection != "preserve":
            raise SourceSelectorError("rendered character-range projection is unsupported")
    elif kind == "directive-component":
        selected = _directive_component(rule.selector, source_text, rendered_interval, rule)
    else:
        raise SourceSelectorError("source selector kind is unsupported")
    if not selected:
        raise SourceSelectorError("source selector produced empty text")
    if _sha(selected.encode("utf-8")) != rule.selected_text_sha256:
        raise SourceSelectorError("source selector output digest does not match overlay")
    return selected


def selector_overlay_sha256(content: bytes) -> str:
    """Expose the exact tracked overlay identity to reports."""

    return _sha(content)


__all__ = [
    "SourceSelector",
    "SourceSelectorError",
    "SourceSelectorOverlay",
    "load_source_selector_overlay",
    "select_source_text",
    "selector_overlay_sha256",
]
