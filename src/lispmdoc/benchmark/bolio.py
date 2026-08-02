"""Deterministic extraction of literal text from recovered MIT Bolio sources.

This is deliberately a small, fail-visible reader rather than an attempt to
re-typeset Bolio.  Its output is source-derived comparison material.  Physical
newlines in ordinary prose are editorial wrapping, so they become a single
space in canonical text.  Newlines in ``.lisp`` blocks are semantic and remain
literal.  Blank lines and directives remain structural boundaries between
blocks.  It never consults OCR output.

Supported inline controls are the ones used by the recovered fourth-edition
Lisp Machine Manual sources:

* ``^F<font>`` (ASCII 0x06 plus one selector) changes font and is omitted;
* ``^Q<character>`` (0x11) quotes the following visible character;
* ``^V(name)`` (0x16) is a cross-reference expanded from ``manual.vars``;
* ``^X`` and ``^Y`` (0x18 and 0x19) end and begin bold, respectively.

Any other control byte, or any directive outside the deliberately narrow
allowlist, is retained or omitted only where Bolio makes it non-visible and is
reported in :attr:`BolioExtraction.issues`.  Callers can therefore route the
case to review instead of accidentally accepting a silently damaged truth
artifact.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


class BolioError(ValueError):
    """Raised when recovered Bolio source cannot produce literal truth."""


class MissingCrossReferenceError(BolioError):
    """Raised when a ``^V(name)`` reference has no exact ``manual.vars`` entry."""


class BolioSyntaxError(BolioError):
    """Raised for malformed constructs whose visible text would be ambiguous."""


_DEFPROP = re.compile(
    r"^\s*\(DEFPROP\s+(?P<name>\|[^|]*\||[^\s()]+)\s+(?P<value>\|[^|]*\||/[^\s()]+)"
    r"\s+JUST-VALUE\s*\)\s*$",
    re.IGNORECASE,
)
_DIRECTIVE = re.compile(r"^\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:[ \t]+(?P<argument>.*))?$")
_SECTION_NUMBER = re.compile(r"\bsection\s+([0-9]+(?:\.[0-9]+)*)\b", re.IGNORECASE)
_CONTROL_NAME = re.compile(r"[A-Za-z0-9:+*/%<>=?_-]+$")
_SETQ = re.compile(r"^(?P<name>[^\s]+)\s+(?P<value>[^\s]+)\s*$")

_SUPPORTED_DIRECTIVES = frozenset(
    {
        "c",
        "chapter",
        "cindex",
        "defun",
        "defun1",
        "defspec",
        "end_defspec",
        "end_defun",
        "end_lisp",
        "end_table",
        "exdent",
        "item",
        "kitem",
        "lisp",
        "nopara",
        "section",
        "setq",
        "sp",
        "subsection",
        "table",
    }
)
_LINE_BREAK_POLICIES = frozenset({"structural", "reflow-editorial", "preserve"})
_VISIBLE_SAIL_CHARACTERS = {"\x1a": "≠", "\x1c": "≤", "\x1d": "≥"}


@dataclass(frozen=True, slots=True)
class BolioSourceSpan:
    """Inclusive source line range, retained with every extracted block."""

    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        if self.start_line < 1 or self.end_line < self.start_line:
            raise BolioError("Bolio source span must be a non-empty positive line range")

    def to_dict(self) -> dict[str, int]:
        return {"end_line": self.end_line, "start_line": self.start_line}


@dataclass(frozen=True, slots=True)
class BolioIssue:
    """A source feature the narrow extractor intentionally did not interpret."""

    kind: str
    line: int
    detail: str

    def to_dict(self) -> dict[str, int | str]:
        return {"detail": self.detail, "kind": self.kind, "line": self.line}


@dataclass(frozen=True, slots=True)
class BolioBlock:
    """One visible, source-derived block of manual content."""

    kind: str
    text: str
    span: BolioSourceSpan
    name: str | None = None
    section_number: str | None = None
    line_break_policy: str = "structural"
    paragraph_indent: bool = False

    def __post_init__(self) -> None:
        if self.kind not in {"function", "section", "list-item", "body", "code"}:
            raise BolioError(f"unsupported Bolio block kind: {self.kind!r}")
        if not self.text:
            raise BolioError("Bolio block text must not be empty")
        if self.kind != "section" and self.section_number is not None:
            raise BolioError("only section blocks may carry a section number")
        if self.paragraph_indent and self.kind != "body":
            raise BolioError("only body blocks may carry a paragraph indent")
        if self.line_break_policy not in _LINE_BREAK_POLICIES:
            raise BolioError("Bolio block has an unsupported line-break policy")
        expected_policy = {
            "body": "reflow-editorial",
            "code": "preserve",
            "function": "structural",
            "list-item": "structural",
            "section": "structural",
        }[self.kind]
        if self.line_break_policy != expected_policy:
            raise BolioError("Bolio block line-break policy does not match its semantic kind")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "line_break_policy": self.line_break_policy,
            "name": self.name,
            "paragraph_indent": self.paragraph_indent,
            "section_number": self.section_number,
            "source_span": self.span.to_dict(),
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class BolioExtraction:
    """Stable source-derived blocks and all non-fatal extraction findings."""

    blocks: tuple[BolioBlock, ...]
    issues: tuple[BolioIssue, ...]
    variables: Mapping[str, str]

    def __post_init__(self) -> None:
        # Freeze and sort the mapping so callers cannot mutate the provenance
        # material after construction and serialisation remains deterministic.
        object.__setattr__(
            self,
            "variables",
            MappingProxyType(dict(sorted(self.variables.items()))),
        )

    @property
    def visible_text(self) -> str:
        """All blocks in source order, separated exactly at semantic boundaries."""
        return "\n\n".join(block.text for block in self.blocks)

    def to_dict(self) -> dict[str, object]:
        return {
            "blocks": [block.to_dict() for block in self.blocks],
            "issues": [issue.to_dict() for issue in self.issues],
            "variables": dict(self.variables),
        }


def _canonical_name(name: str) -> str:
    return name.upper()


def _physical_lines(text: str) -> tuple[str, ...]:
    """Split only on physical LF, never on an embedded Bolio control byte."""
    lines = text.split("\n")
    if lines and not lines[-1]:
        lines.pop()
    return tuple(line[:-1] if line.endswith("\r") else line for line in lines)


def parse_manual_vars(text: str) -> Mapping[str, str]:
    """Parse the recoverable ``(DEFPROP NAME VALUE JUST-VALUE)`` subset.

    A value wrapped in vertical bars is returned without those delimiters,
    preserving all internal spaces.  A slash-prefixed numeric value is returned
    as its printed number (for example ``/13`` becomes ``"13"``).  Duplicate
    names with distinct values are rejected rather than selected arbitrarily.
    """
    values: dict[str, str] = {}
    for line_number, line in enumerate(_physical_lines(text), start=1):
        match = _DEFPROP.fullmatch(line)
        if match is None:
            continue
        raw_name = match.group("name")
        name = _canonical_name(raw_name[1:-1] if raw_name.startswith("|") else raw_name)
        raw_value = match.group("value")
        value = raw_value[1:-1] if raw_value.startswith("|") else raw_value[1:]
        if not value:
            raise BolioSyntaxError(f"manual.vars line {line_number} has an empty DEFPROP value")
        previous = values.get(name)
        if previous is not None and previous != value:
            raise BolioSyntaxError(
                f"manual.vars line {line_number} redefines {name!r} with a distinct value"
            )
        values[name] = value
    return MappingProxyType(dict(sorted(values.items())))


def _resolve_reference(name: str, variables: Mapping[str, str], line: int) -> str:
    if not _CONTROL_NAME.fullmatch(name):
        raise BolioSyntaxError(f"Bolio line {line} has an invalid cross-reference name: {name!r}")
    key = _canonical_name(name)
    try:
        return variables[key]
    except KeyError as error:
        raise MissingCrossReferenceError(
            f"Bolio line {line} references {name!r}, which is absent from manual.vars"
        ) from error


def _normalize_visible_line(
    line: str, variables: Mapping[str, str], line_number: int, issues: list[BolioIssue]
) -> str:
    """Remove known non-visible markup while preserving all visible characters."""
    output: list[str] = []
    index = 0
    while index < len(line):
        character = line[index]
        code = ord(character)
        if character == "\x06":  # ^F: consume the mandatory font selector.
            if index + 1 >= len(line):
                issues.append(
                    BolioIssue("malformed-control", line_number, "^F lacks a font selector")
                )
                output.append(character)
                index += 1
            else:
                index += 2
            continue
        if character == "\x11":  # ^Q: quote the following character literally.
            if index + 1 >= len(line):
                issues.append(
                    BolioIssue("malformed-control", line_number, "^Q lacks a quoted character")
                )
                output.append(character)
                index += 1
            else:
                output.append(_VISIBLE_SAIL_CHARACTERS.get(line[index + 1], line[index + 1]))
                index += 2
            continue
        if character == "\x16":  # ^V(name): typesetter variable/cross-reference.
            if index + 1 >= len(line) or line[index + 1] != "(":
                issues.append(BolioIssue("malformed-control", line_number, "^V lacks '(name)'"))
                output.append(character)
                index += 1
                continue
            closing = line.find(")", index + 2)
            if closing == -1:
                raise BolioSyntaxError(f"Bolio line {line_number} has an unterminated ^V(name)")
            output.append(_resolve_reference(line[index + 2 : closing], variables, line_number))
            index = closing + 1
            continue
        if character in {"\x18", "\x19"}:  # ^X/^Y: end/begin bold.
            index += 1
            continue
        replacement = _VISIBLE_SAIL_CHARACTERS.get(character)
        if replacement is not None:
            output.append(replacement)
            index += 1
            continue
        if (code < 32 and character != "\t") or code == 127:
            issues.append(
                BolioIssue(
                    "unsupported-control", line_number, f"unsupported control byte 0x{code:02x}"
                )
            )
        output.append(character)
        index += 1
    return "".join(output)


def _reflow_editorial_prose(lines: list[str]) -> str:
    """Make only source-editorial wrap boundaries non-semantic.

    A non-empty run outside an explicit structural environment is one Bolio
    paragraph.  Its physical source newlines are not printed line breaks.  We
    use one separating space while retaining every interior character
    (including meaningful double sentence spaces) of each line. Leading
    indentation is classified before this function and retained on the block;
    trailing editor whitespace is non-printing. Code and directives do not call
    this function.
    """
    if not lines:
        raise BolioError("cannot reflow an empty Bolio prose block")
    if any(not line for line in lines):
        raise BolioError("Bolio prose block contains an unclassified blank line")
    return " ".join(lines)


def normalize_bolio_line(
    line: str, variables: Mapping[str, str], line_number: int = 1
) -> tuple[str, tuple[BolioIssue, ...]]:
    """Normalize one literal source line, exposing every non-fatal finding."""
    issues: list[BolioIssue] = []
    normalized = _normalize_visible_line(line, variables, line_number, issues)
    return normalized, tuple(issues)


def render_bolio_interval(
    extraction: BolioExtraction,
    source_text: str,
    *,
    start_line: int,
    end_line: int,
) -> str:
    """Render exact source-derived text intersecting an inclusive line interval.

    The extraction supplies semantic boundaries while the original source
    supplies partial-block text. OCR output is never consulted.
    """

    physical_lines = _physical_lines(source_text)
    if start_line < 1 or end_line < start_line or end_line > len(physical_lines):
        raise BolioError("Bolio render interval exceeds source text")
    parts: list[str] = []
    issues: list[BolioIssue] = []
    for block in extraction.blocks:
        overlap_start = max(start_line, block.span.start_line)
        overlap_end = min(end_line, block.span.end_line)
        if overlap_end < overlap_start:
            continue
        if overlap_start == block.span.start_line and overlap_end == block.span.end_line:
            text = (
                f"{block.section_number} {block.text}"
                if block.kind == "section" and block.section_number
                else block.text
            )
        elif block.kind in {"function", "section", "list-item"}:
            text = block.text
        else:
            visible_lines: list[str] = []
            for line_number in range(overlap_start, overlap_end + 1):
                raw_line = physical_lines[line_number - 1]
                directive = _DIRECTIVE.fullmatch(raw_line)
                if directive and directive.group("name").lower() == "exdent":
                    raw_line = re.sub(r"^[0-9]+(?:\s+|$)", "", directive.group("argument") or "")
                elif directive:
                    continue
                visible = _normalize_visible_line(
                    raw_line, extraction.variables, line_number, issues
                ).strip(" \t")
                if visible:
                    visible_lines.append(visible)
            text = ("\n" if block.kind == "code" else " ").join(visible_lines)
        if text:
            parts.append(text)
    if issues:
        raise BolioError("Bolio render interval contains unresolved controls")
    return "\n\n".join(parts)


def _parse_section_title(argument: str, line: int) -> str:
    title = argument.strip()
    if len(title) >= 2 and title[0] == title[-1] == '"':
        title = title[1:-1]
    if not title:
        raise BolioSyntaxError(f"Bolio line {line} has an empty .section title")
    return title


def _parse_function(argument: str, line: int) -> tuple[str, str]:
    signature = argument.strip()
    if not signature:
        raise BolioSyntaxError(f"Bolio line {line} has an empty .defun signature")
    return signature.split(None, 1)[0], signature


def _section_number(variable_name: str, variables: Mapping[str, str]) -> str | None:
    value = variables.get(_canonical_name(variable_name))
    if value is None:
        return None
    match = _SECTION_NUMBER.search(value)
    return match.group(1) if match else None


def extract_bolio(
    source_text: str,
    manual_vars_text: str | Mapping[str, str],
    *,
    start_line: int = 1,
    end_line: int | None = None,
) -> BolioExtraction:
    """Extract a selected inclusive source span into visible semantic blocks.

    ``start_line`` and ``end_line`` identify lines in *source_text*, not a
    separately sliced string.  This makes the recorded source spans directly
    usable as authoritative provenance.  Unknown directives become findings;
    malformed nesting is an error because its visible structure is uncertain.
    """
    all_lines = _physical_lines(source_text)
    if start_line < 1:
        raise BolioError("start_line must be positive")
    final_line = len(all_lines) if end_line is None else end_line
    if final_line < start_line or final_line > len(all_lines):
        raise BolioError("selected Bolio source span exceeds source text")
    raw_variables = (
        parse_manual_vars(manual_vars_text)
        if isinstance(manual_vars_text, str)
        else MappingProxyType(
            dict(sorted((_canonical_name(k), v) for k, v in manual_vars_text.items()))
        )
    )
    for key, value in raw_variables.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            raise BolioError("manual.vars mappings must contain non-empty strings")

    blocks: list[BolioBlock] = []
    issues: list[BolioIssue] = []
    buffered_lines: list[str] = []
    buffer_start: int | None = None
    buffer_indent = False
    code_lines: list[str] = []
    code_start: int | None = None
    in_lisp = False
    definition_kind: str | None = None
    in_table = False
    pending_section: tuple[str, int] | None = None

    def flush_body() -> None:
        nonlocal buffer_indent, buffer_start
        if not buffered_lines:
            return
        assert buffer_start is not None
        blocks.append(
            BolioBlock(
                "body",
                _reflow_editorial_prose(buffered_lines),
                BolioSourceSpan(buffer_start, buffer_start + len(buffered_lines) - 1),
                line_break_policy="reflow-editorial",
                paragraph_indent=buffer_indent,
            )
        )
        buffered_lines.clear()
        buffer_start = None
        buffer_indent = False

    def flush_code(end: int) -> None:
        nonlocal code_start
        if not code_lines:
            return
        assert code_start is not None
        blocks.append(
            BolioBlock(
                "code",
                "\n".join(code_lines),
                BolioSourceSpan(code_start, end),
                line_break_policy="preserve",
            )
        )
        code_lines.clear()
        code_start = None

    def emit_pending_section(section_number: str | None = None) -> None:
        nonlocal pending_section
        if pending_section is None:
            return
        title, line = pending_section
        blocks.append(
            BolioBlock("section", title, BolioSourceSpan(line, line), section_number=section_number)
        )
        pending_section = None

    for line_number in range(start_line, final_line + 1):
        raw_line = all_lines[line_number - 1]
        if raw_line.lower().startswith("'cindex"):
            continue
        directive = _DIRECTIVE.fullmatch(raw_line)
        if directive is None:
            emit_pending_section()
            normalized = _normalize_visible_line(raw_line, raw_variables, line_number, issues)
            if in_lisp:
                if code_start is None:
                    code_start = line_number
                code_lines.append(normalized)
            else:
                stripped = normalized.strip(" \t")
                leading_indent = bool(normalized) and normalized[0] in " \t"
                if leading_indent and buffered_lines:
                    flush_body()
                if not stripped:
                    flush_body()
                    continue
                if buffer_start is None:
                    buffer_start = line_number
                    buffer_indent = leading_indent
                buffered_lines.append(stripped)
            continue

        name = directive.group("name").lower()
        argument = directive.group("argument") or ""
        if name not in _SUPPORTED_DIRECTIVES:
            emit_pending_section()
            flush_body()
            issues.append(BolioIssue("unsupported-directive", line_number, f".{name}"))
            continue
        if in_lisp:
            if name == "end_lisp":
                flush_code(line_number - 1)
                in_lisp = False
                continue
            if name == "exdent":
                visible = re.sub(r"^[0-9]+(?:\s+|$)", "", argument)
                normalized = _normalize_visible_line(visible, raw_variables, line_number, issues)
                if normalized:
                    if code_start is None:
                        code_start = line_number
                    code_lines.append(normalized)
                continue
            if name in {"c", "cindex"}:
                continue
            raise BolioSyntaxError(f"Bolio line {line_number} starts .{name} inside .lisp")
        if name in {"c", "cindex", "nopara", "sp"}:
            continue
        if name in {"chapter", "section", "subsection"}:
            if in_lisp:
                raise BolioSyntaxError(f"Bolio line {line_number} starts .{name} inside .lisp")
            flush_body()
            emit_pending_section()
            pending_section = (_parse_section_title(argument, line_number), line_number)
            continue
        if name == "setq":
            assignment = _SETQ.fullmatch(argument)
            if assignment is None:
                raise BolioSyntaxError(f"Bolio line {line_number} has malformed .setq")
            if pending_section is not None and assignment.group("value").lower() == "section-page":
                emit_pending_section(_section_number(assignment.group("name"), raw_variables))
            else:
                emit_pending_section()
            continue
        emit_pending_section()
        if name in {"defun", "defun1", "defspec"}:
            if name == "defun1" and definition_kind != "defun":
                raise BolioSyntaxError(f"Bolio line {line_number} has .defun1 outside .defun")
            if name != "defun1" and definition_kind is not None:
                raise BolioSyntaxError(f"Bolio line {line_number} starts nested .{name}")
            flush_body()
            visible_argument = _normalize_visible_line(argument, raw_variables, line_number, issues)
            function_name, signature = _parse_function(visible_argument, line_number)
            blocks.append(
                BolioBlock(
                    "function", signature, BolioSourceSpan(line_number, line_number), function_name
                )
            )
            if name != "defun1":
                definition_kind = name
            continue
        if name in {"end_defun", "end_defspec"}:
            expected = name.removeprefix("end_")
            if definition_kind != expected:
                raise BolioSyntaxError(f"Bolio line {line_number} closes an absent .{expected}")
            flush_body()
            definition_kind = None
            continue
        if name == "lisp":
            flush_body()
            in_lisp = True
            continue
        if name == "table":
            if in_table:
                raise BolioSyntaxError(f"Bolio line {line_number} starts nested .table")
            flush_body()
            in_table = True
            continue
        if name == "end_table":
            if not in_table:
                raise BolioSyntaxError(f"Bolio line {line_number} closes an absent .table")
            flush_body()
            in_table = False
            continue
        if name in {"item", "kitem"}:
            if not in_table:
                raise BolioSyntaxError(f"Bolio line {line_number} has .{name} outside .table")
            flush_body()
            normalized = _normalize_visible_line(argument, raw_variables, line_number, issues)
            if not normalized:
                raise BolioSyntaxError(f"Bolio line {line_number} has an empty .{name}")
            blocks.append(
                BolioBlock("list-item", normalized, BolioSourceSpan(line_number, line_number))
            )
            continue
        if name == "exdent":
            flush_body()
            visible = re.sub(r"^[0-9]+(?:\s+|$)", "", argument)
            normalized = _normalize_visible_line(visible, raw_variables, line_number, issues)
            if normalized:
                blocks.append(
                    BolioBlock("list-item", normalized, BolioSourceSpan(line_number, line_number))
                )
            continue
        raise AssertionError(f"handled directive unexpectedly escaped: {name}")

    emit_pending_section()
    if in_lisp:
        raise BolioSyntaxError("selected Bolio source span ends inside .lisp")
    if definition_kind is not None:
        raise BolioSyntaxError(f"selected Bolio source span ends inside .{definition_kind}")
    if in_table:
        raise BolioSyntaxError("selected Bolio source span ends inside .table")
    flush_body()
    return BolioExtraction(tuple(blocks), tuple(issues), raw_variables)


__all__ = [
    "BolioBlock",
    "BolioError",
    "BolioExtraction",
    "BolioIssue",
    "BolioSourceSpan",
    "BolioSyntaxError",
    "MissingCrossReferenceError",
    "extract_bolio",
    "normalize_bolio_line",
    "parse_manual_vars",
    "render_bolio_interval",
]
