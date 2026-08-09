"""Digest-locked whole-prefix section counters for recovered Bolio sources.

This deliberately consumes only the recovered source order, literal heading
directives, ``.setq … chapter-number`` anchors, and ``manual.vars``.  It does
not accept OCR or a reviewed replica as an input.
"""

from __future__ import annotations

import glob
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .bolio import BolioError, parse_manual_vars


class BolioCounterError(ValueError):
    """The source-order or counter evidence cannot prove a number."""


_HERE_DOCUMENT = re.compile(r"done\)<<EOF\n(?P<body>.*?)\nEOF\n", re.DOTALL)
_HEADING = re.compile(r"^\.(?P<kind>chapter|section|subsection)(?:\s+(?P<title>.*))?$", re.I)
_SETQ = re.compile(r"^\.setq\s+(?P<name>[^\s]+)\s+(?P<value>[^\s]+)\s*$", re.I)
_SETQ_START = re.compile(r"^\.setq\b", re.I)
_SECTION_PAGE_VALUE = re.compile(
    r"^(?P<kind>chapter|section)\s+(?P<number>[1-9][0-9]*(?:\.[0-9]+)*)\s*,\s*page\s+[1-9][0-9]*$",
    re.I,
)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise BolioCounterError(f"{label} must be a regular non-symlink file: {path}")
    try:
        return path.read_bytes()
    except OSError as error:
        raise BolioCounterError(f"cannot read {label}: {path}") from error


@dataclass(frozen=True, slots=True)
class OrderedBolioSource:
    """One digest-bound source selected by the ti-4ed input order."""

    path: str
    sha256: str
    order_index: int


@dataclass(frozen=True, slots=True)
class _SourceInput:
    """A selected source and its one verified byte read."""

    identity: OrderedBolioSource
    content: bytes


@dataclass(frozen=True, slots=True)
class SectionNumberProof:
    """A source-only proof for one literal heading directive."""

    source_path: str
    source_sha256: str
    line: int
    directive: str
    title: str
    number: str
    chapter_anchor_name: str
    chapter_anchor_value: str
    order_sha256: str
    manual_vars_sha256: str

    def matches(self, *, source_path: str, source_sha256: str, line: int) -> bool:
        """Whether a final-manifest citation names this exact proof target."""

        return (
            self.source_path == source_path
            and self.source_sha256 == source_sha256
            and self.line == line
        )

    def to_dict(self) -> dict[str, object]:
        """Stable, complete identity used by the counter receipt and evaluator."""

        return {
            "chapter_anchor_name": self.chapter_anchor_name,
            "chapter_anchor_value": self.chapter_anchor_value,
            "directive": self.directive,
            "line": self.line,
            "manual_vars_sha256": self.manual_vars_sha256,
            "number": self.number,
            "order_sha256": self.order_sha256,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "title": self.title,
        }


@dataclass(frozen=True, slots=True)
class BolioCounterResult:
    """Counter output with all source-order and variable identities retained."""

    ti_script_sha256: str
    manual_vars_sha256: str
    order_sha256: str
    sources: tuple[OrderedBolioSource, ...]
    proofs: tuple[SectionNumberProof, ...]

    def proof_for(self, source_path: str, line: int) -> SectionNumberProof | None:
        matches = [
            proof
            for proof in self.proofs
            if proof.source_path == source_path and proof.line == line
        ]
        if len(matches) > 1:
            raise BolioCounterError(f"counter proof is ambiguous for {source_path}:{line}")
        return matches[0] if matches else None

    @property
    def proof_inventory_sha256(self) -> str:
        """Digest the complete proof set, rather than only its source roots."""

        return _sha(
            json.dumps(
                [proof.to_dict() for proof in self.proofs],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    @property
    def proof_count(self) -> int:
        return len(self.proofs)


def _title(value: str | None, *, path: str, line: int) -> str:
    title = (value or "").strip()
    if len(title) >= 2 and title[0] == title[-1] == '"':
        title = title[1:-1]
    if not title:
        raise BolioCounterError(f"empty heading at {path}:{line}")
    return title


def _input_patterns(script: bytes) -> tuple[str, ...]:
    try:
        text = script.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BolioCounterError("ti-4ed.sh is not UTF-8") from error
    match = _HERE_DOCUMENT.search(text)
    if match is None:
        raise BolioCounterError("cannot locate ti-4ed.sh source-order here document")
    patterns = tuple(line.strip() for line in match.group("body").splitlines() if line.strip())
    if not patterns or any("/" in pattern or pattern.startswith(".") for pattern in patterns):
        raise BolioCounterError("ti-4ed.sh source-order entries are malformed")
    return patterns


def _ti4ed_ordered_prefix_contents(
    document_root: Path,
    through: str,
    script: bytes,
    source_buffers: Mapping[str, bytes] | None = None,
    allow_unbuffered_sources: bool = False,
) -> tuple[_SourceInput, ...]:
    """Resolve one prefix and retain each selected source's verified bytes.

    Every glob must resolve to exactly one regular file in the accepted prefix.
    This intentionally refuses the ambiguous later wildcard expansions in the
    recovered script instead of picking a revision by pathname or mtime.
    """

    if document_root.is_symlink() or not document_root.is_dir():
        raise BolioCounterError("document root must be a non-symlink directory")
    source_root = document_root / "orig4ed"
    if source_root.is_symlink() or not source_root.is_dir():
        raise BolioCounterError("orig4ed source root must be a non-symlink directory")
    selected: list[_SourceInput] = []
    found = False
    for pattern in _input_patterns(script):
        paths = sorted(Path(value) for value in glob.glob(str(source_root / pattern)))
        if len(paths) != 1:
            raise BolioCounterError(
                f"ti-4ed.sh pattern {pattern!r} is not an unambiguous single source"
            )
        path = paths[0]
        if path.name != pattern and "*" not in pattern:
            raise BolioCounterError(f"ti-4ed.sh literal source is not exact: {pattern}")
        if source_buffers is None:
            content = _read(path, "ti-4ed source")
        else:
            if path.is_symlink() or not path.is_file():
                raise BolioCounterError(f"ti-4ed source must be a regular file: {path}")
            buffered_content = source_buffers.get(path.name)
            if isinstance(buffered_content, bytes):
                content = buffered_content
            else:
                if not allow_unbuffered_sources:
                    raise BolioCounterError(
                        f"ti-4ed source is absent from the verified buffer set: {path.name}"
                    )
                content = _read(path, "ti-4ed non-manifest source")
        selected.append(
            _SourceInput(OrderedBolioSource(path.name, _sha(content), len(selected)), content)
        )
        if path.name == through:
            found = True
            break
    if not found:
        raise BolioCounterError(f"ti-4ed.sh does not order requested endpoint {through!r}")
    return tuple(selected)


def ti4ed_ordered_prefix(document_root: Path, through: str) -> tuple[OrderedBolioSource, ...]:
    """Resolve one unambiguous, inclusive ti-4ed source prefix."""

    script = _read(document_root / "ti-4ed.sh", "ti-4ed.sh")
    return tuple(
        source_input.identity
        for source_input in _ti4ed_ordered_prefix_contents(document_root, through, script)
    )


def _order_sha(sources: tuple[OrderedBolioSource, ...]) -> str:
    return _sha(
        "".join(
            f"{source.order_index}\t{source.path}\t{source.sha256}\n" for source in sources
        ).encode("ascii")
    )


def _section_page_anchor(
    *,
    source: OrderedBolioSource,
    line: int,
    text: str,
    variables: Mapping[str, str],
    current_number: str | None,
    current_kind: str | None,
) -> None:
    """Check one source ``section-page`` reference against manual.vars state."""

    stripped = text.strip()
    if not _SETQ_START.match(stripped):
        return
    setq = _SETQ.fullmatch(stripped)
    if setq is None:
        if "section-page" in stripped.lower():
            raise BolioCounterError(f"malformed section-page anchor at {source.path}:{line}")
        return
    if setq.group("value").lower() != "section-page":
        return
    if current_number is None or current_kind is None:
        raise BolioCounterError(
            f"section-page anchor has no derived heading at {source.path}:{line}"
        )
    manual_value = variables.get(setq.group("name").upper())
    if manual_value is None:
        raise BolioCounterError(
            f"section-page anchor is absent from manual.vars at {source.path}:{line}"
        )
    match = _SECTION_PAGE_VALUE.fullmatch(manual_value)
    if match is None:
        raise BolioCounterError(
            f"section-page anchor has malformed manual.vars value at {source.path}:{line}"
        )
    expected_kind = "chapter" if current_kind == "chapter" else "section"
    expected_number = current_number.rstrip(".")
    if match.group("kind").lower() != expected_kind or match.group("number") != expected_number:
        raise BolioCounterError(
            f"section-page anchor conflicts with derived {expected_kind} "
            f"{expected_number} at {source.path}:{line}"
        )


def _derive_ti4ed_section_numbers(
    *, script: bytes, manual_vars: bytes, source_inputs: tuple[_SourceInput, ...]
) -> BolioCounterResult:
    """Derive proofs entirely from already-read source and manual bytes."""

    try:
        variables = parse_manual_vars(manual_vars.decode("utf-8"))
    except (UnicodeDecodeError, BolioError) as error:
        raise BolioCounterError("cannot parse manual.vars for counter anchors") from error
    sources = tuple(source_input.identity for source_input in source_inputs)
    order_sha256, manual_vars_sha256 = _order_sha(sources), _sha(manual_vars)
    chapter: int | None = None
    section = 0
    subsection = 0
    chapter_anchor_name: str | None = None
    chapter_anchor_value: str | None = None
    proofs: list[SectionNumberProof] = []
    for source_input in source_inputs:
        source, content = source_input.identity, source_input.content
        try:
            lines = content.decode("utf-8").split("\n")
        except UnicodeDecodeError as error:
            raise BolioCounterError(f"ti-4ed source is not UTF-8: {source.path}") from error
        heading_positions = [
            index
            for index, line in enumerate(lines)
            if _HEADING.fullmatch(line.strip()) is not None
        ]
        first_heading = heading_positions[0] if heading_positions else len(lines)
        for setq_index, setq_line in enumerate(lines[:first_heading], start=1):
            _section_page_anchor(
                source=source,
                line=setq_index,
                text=setq_line,
                variables=variables,
                current_number=None,
                current_kind=None,
            )
        for heading_index in heading_positions:
            match = _HEADING.fullmatch(lines[heading_index].strip())
            assert match is not None
            kind, title = (
                match.group("kind").lower(),
                _title(match.group("title"), path=source.path, line=heading_index + 1),
            )
            next_heading = next(
                (index for index in heading_positions if index > heading_index), len(lines)
            )
            if kind == "chapter":
                anchors = [
                    setq
                    for line in lines[heading_index + 1 : next_heading]
                    if (setq := _SETQ.fullmatch(line.strip())) is not None
                    and setq.group("value").lower() == "chapter-number"
                ]
                if not anchors:
                    if chapter is not None:
                        raise BolioCounterError(
                            f"chapter {source.path}:{heading_index + 1} "
                            "lacks a chapter-number anchor"
                        )
                    # The introduction precedes the first numeric chapter and
                    # deliberately has no counter anchor. It cannot yield a
                    # proof, but it also cannot be mistaken for one.
                    for setq_index, setq_line in enumerate(
                        lines[heading_index + 1 : next_heading], start=heading_index + 2
                    ):
                        _section_page_anchor(
                            source=source,
                            line=setq_index,
                            text=setq_line,
                            variables=variables,
                            current_number=None,
                            current_kind=None,
                        )
                    section, subsection = 0, 0
                    continue
                anchor_values = [variables.get(anchor.group("name").upper()) for anchor in anchors]
                if (
                    any(
                        value is None or not re.fullmatch(r"[1-9][0-9]*", value)
                        for value in anchor_values
                    )
                    or len(set(anchor_values)) != 1
                ):
                    raise BolioCounterError(
                        f"chapter {source.path}:{heading_index + 1} "
                        "has conflicting chapter-number anchors"
                    )
                chapter_anchor_name = anchors[0].group("name")
                chapter_anchor_value = anchor_values[0]
                assert chapter_anchor_value is not None
                next_chapter = int(chapter_anchor_value)
                if chapter is not None and next_chapter <= chapter:
                    raise BolioCounterError(
                        f"chapter anchor {chapter_anchor_name!r} is non-increasing in ti-4ed order"
                    )
                chapter, section, subsection = next_chapter, 0, 0
                number = f"{chapter}."
            elif kind == "section":
                if chapter is None or chapter_anchor_name is None or chapter_anchor_value is None:
                    for setq_index, setq_line in enumerate(
                        lines[heading_index + 1 : next_heading], start=heading_index + 2
                    ):
                        _section_page_anchor(
                            source=source,
                            line=setq_index,
                            text=setq_line,
                            variables=variables,
                            current_number=None,
                            current_kind=None,
                        )
                    continue
                section, subsection = section + 1, 0
                number = f"{chapter}.{section}"
            else:
                if (
                    chapter is None
                    or section == 0
                    or chapter_anchor_name is None
                    or chapter_anchor_value is None
                ):
                    for setq_index, setq_line in enumerate(
                        lines[heading_index + 1 : next_heading], start=heading_index + 2
                    ):
                        _section_page_anchor(
                            source=source,
                            line=setq_index,
                            text=setq_line,
                            variables=variables,
                            current_number=None,
                            current_kind=None,
                        )
                    continue
                subsection += 1
                number = f"{chapter}.{section}.{subsection}"
            for setq_index, setq_line in enumerate(
                lines[heading_index + 1 : next_heading], start=heading_index + 2
            ):
                _section_page_anchor(
                    source=source,
                    line=setq_index,
                    text=setq_line,
                    variables=variables,
                    current_number=number,
                    current_kind=kind,
                )
            proofs.append(
                SectionNumberProof(
                    source.path,
                    source.sha256,
                    heading_index + 1,
                    kind,
                    title,
                    number,
                    chapter_anchor_name,
                    chapter_anchor_value,
                    order_sha256,
                    manual_vars_sha256,
                )
            )
    return BolioCounterResult(
        _sha(script), manual_vars_sha256, order_sha256, sources, tuple(proofs)
    )


def derive_ti4ed_section_numbers(document_root: Path, through: str) -> BolioCounterResult:
    """Derive literal heading numbers for one digest-bound unambiguous prefix."""

    script = _read(document_root / "ti-4ed.sh", "ti-4ed.sh")
    source_root = document_root / "orig4ed"
    manual_vars = _read(source_root / "manual.vars", "manual.vars")
    return _derive_ti4ed_section_numbers(
        script=script,
        manual_vars=manual_vars,
        source_inputs=_ti4ed_ordered_prefix_contents(document_root, through, script),
    )


def derive_ti4ed_section_numbers_from_buffers(
    document_root: Path,
    through: str,
    *,
    manual_vars: bytes,
    source_buffers: Mapping[str, bytes],
    allow_unbuffered_sources: bool = False,
) -> BolioCounterResult:
    """Derive proofs without reopening verified manual/source evidence bytes.

    ``allow_unbuffered_sources`` is only for ti-4ed prefix roots absent from
    the final manifest.  Their bytes are read once by the counter and remain
    counter-only; any source cited by the final manifest must be supplied.
    """

    if not isinstance(manual_vars, bytes):
        raise BolioCounterError("manual.vars buffer is malformed")
    script = _read(document_root / "ti-4ed.sh", "ti-4ed.sh")
    return _derive_ti4ed_section_numbers(
        script=script,
        manual_vars=manual_vars,
        source_inputs=_ti4ed_ordered_prefix_contents(
            document_root,
            through,
            script,
            source_buffers,
            allow_unbuffered_sources,
        ),
    )


def verify_ti4ed_counter_receipt(result: BolioCounterResult, receipt: Mapping[str, object]) -> None:
    """Require a tracked receipt for the script, variables, and exact prefix."""

    if receipt.get("format_version") != "lispmdoc-ti4ed-counter-receipt-1":
        raise BolioCounterError("ti-4ed counter receipt format is unsupported")
    expected = {
        "manual_vars_sha256": result.manual_vars_sha256,
        "order_sha256": result.order_sha256,
        "ti_script_sha256": result.ti_script_sha256,
        "through": result.sources[-1].path if result.sources else None,
        "proof_count": result.proof_count,
        "proof_inventory_sha256": result.proof_inventory_sha256,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise BolioCounterError("ti-4ed counter receipt does not bind current roots")
    source_values = receipt.get("sources")
    if not isinstance(source_values, list):
        raise BolioCounterError("ti-4ed counter receipt sources are malformed")
    actual = [
        {"order_index": source.order_index, "path": source.path, "sha256": source.sha256}
        for source in result.sources
    ]
    if source_values != actual:
        raise BolioCounterError("ti-4ed counter receipt source order differs from current roots")


__all__ = [
    "BolioCounterError",
    "BolioCounterResult",
    "OrderedBolioSource",
    "SectionNumberProof",
    "derive_ti4ed_section_numbers",
    "derive_ti4ed_section_numbers_from_buffers",
    "ti4ed_ordered_prefix",
    "verify_ti4ed_counter_receipt",
]
