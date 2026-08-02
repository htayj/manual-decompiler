"""Lossless, source-scoped formatting spans for recovered MIT Bolio text.

This module records what the typesetter source says.  It deliberately does not
guess visual emphasis from token spelling: every non-default span is anchored
to a directive or control byte in the selected source lines.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .bolio import BolioSyntaxError

_ROLE_BY_SELECTOR = {"1": "body", "2": "font-2-italic", "3": "font-3-inline-lisp"}


@dataclass(frozen=True, slots=True)
class SemanticTextSpan:
    """A visible span with exact source-derived formatting evidence."""

    text: str
    style: str = "body"
    evidence: str = "source-default-font-1"
    bold: bool = False


def _collapse_editorial_newlines(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(normalized.split("\n"))


def _merge_spans(spans: Sequence[SemanticTextSpan]) -> tuple[SemanticTextSpan, ...]:
    merged: list[SemanticTextSpan] = []
    for span in spans:
        if not span.text:
            continue
        if (
            merged
            and merged[-1].style == span.style
            and merged[-1].evidence == span.evidence
            and merged[-1].bold == span.bold
        ):
            previous = merged[-1]
            merged[-1] = SemanticTextSpan(
                previous.text + span.text, previous.style, previous.evidence, previous.bold
            )
        else:
            merged.append(span)
    return tuple(merged)


def _inline_spans(
    raw_lines: Sequence[str], variables: Mapping[str, str], fallback: str
) -> tuple[SemanticTextSpan, ...]:
    spans: list[SemanticTextSpan] = []
    style = "body"
    evidence = "source-default-font-1"
    font_stack: list[tuple[str, str]] = []
    bold = False
    for line_number, source_line in enumerate(raw_lines):
        if line_number:
            spans.append(SemanticTextSpan(" ", style, evidence, bold))
        index = 0
        while index < len(source_line):
            character = source_line[index]
            if character == "\x06":
                if index + 1 >= len(source_line):
                    raise BolioSyntaxError("truncated Bolio font control")
                selector = source_line[index + 1]
                if selector == "*":
                    if not font_stack:
                        raise BolioSyntaxError("Bolio font pop has no matching push")
                    style, evidence = font_stack.pop()
                else:
                    role = _ROLE_BY_SELECTOR.get(selector)
                    if role is None:
                        raise BolioSyntaxError(f"unsupported Bolio font selector {selector!r}")
                    font_stack.append((style, evidence))
                    style = role
                    evidence = f"source-control-font-{selector}"
                index += 2
                continue
            if character == "\x11":
                if index + 1 >= len(source_line):
                    raise BolioSyntaxError("truncated Bolio quoted-character control")
                spans.append(SemanticTextSpan(source_line[index + 1], style, evidence, bold))
                index += 2
                continue
            if (
                character == "\x16"
                and index + 1 < len(source_line)
                and source_line[index + 1] == "("
            ):
                closing = source_line.find(")", index + 2)
                if closing == -1:
                    raise BolioSyntaxError("unterminated Bolio variable reference")
                name = source_line[index + 2 : closing].upper()
                replacement = variables.get(name)
                if replacement is None:
                    raise BolioSyntaxError(f"undefined Bolio variable {name!r}")
                spans.append(SemanticTextSpan(replacement, style, evidence, bold))
                index = closing + 1
                continue
            if character == "\x19":
                if bold:
                    raise BolioSyntaxError("nested Bolio explicit-bold control")
                bold = True
                index += 1
                continue
            if character == "\x18":
                if not bold:
                    raise BolioSyntaxError("Bolio bold end has no matching begin")
                bold = False
                index += 1
                continue
            spans.append(SemanticTextSpan(character, style, evidence, bold))
            index += 1
    if font_stack:
        raise BolioSyntaxError("Bolio font push is not closed by ^F*")
    if bold:
        raise BolioSyntaxError("Bolio explicit-bold span is not closed by ^X")

    merged = _merge_spans(spans)
    rendered_text = _collapse_editorial_newlines("".join(span.text for span in merged))
    if rendered_text != _collapse_editorial_newlines(fallback):
        raise BolioSyntaxError("Bolio formatting spans do not align with canonical source text")
    normalized = (
        SemanticTextSpan(
            _collapse_editorial_newlines(span.text), span.style, span.evidence, span.bold
        )
        for span in merged
    )
    return _merge_spans(tuple(normalized))


def semantic_spans(
    *,
    kind: str,
    reference_text: str,
    raw_lines: Sequence[str],
    variables: Mapping[str, str],
) -> tuple[SemanticTextSpan, ...]:
    """Return visible spans whose formatting is fully explained by source syntax."""

    if kind == "function":
        match = re.fullmatch(r"(\S+)(.*)", reference_text, flags=re.DOTALL)
        if match is None:
            raise BolioSyntaxError("function heading must contain a function name")
        spans = (
            SemanticTextSpan(match.group(1), "definition-name", "directive-.defun-first-atom"),
        ) + tuple(
            SemanticTextSpan(argument, "definition-argument", "directive-.defun-remaining-atoms")
            for argument in re.findall(r"\s+\S+", match.group(2), flags=re.DOTALL)
        )
    elif kind == "code":
        spans = (
            _inline_spans(raw_lines, variables, reference_text)
            if raw_lines
            else (SemanticTextSpan(reference_text, "display-code", "directive-.lisp"),)
        )
    elif kind == "section":
        spans = (SemanticTextSpan(reference_text, "section-title", "directive-.section"),)
    elif kind == "body":
        spans = _inline_spans(raw_lines, variables, reference_text)
    else:
        raise BolioSyntaxError(f"unsupported semantic block kind {kind!r}")

    expected = reference_text if kind == "code" else _collapse_editorial_newlines(reference_text)
    if "".join(span.text for span in spans) != expected:
        raise BolioSyntaxError(
            "semantic spans must concatenate to the source-derived rendered text"
        )
    return spans


__all__ = ["SemanticTextSpan", "semantic_spans"]
