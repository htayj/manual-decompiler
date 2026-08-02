"""Deterministic OCR-assisted alignment of scan pages to recovered source lines.

OCR is used only as a noisy locator.  The returned records are mapping
proposals and cannot become authoritative text without the independent source
and review gates in :mod:`lispmdoc.benchmark.authoritative`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher

from .bolio import BolioError, normalize_bolio_line

_TOKEN = re.compile(r"[a-z0-9]+")
_VISIBLE_DIRECTIVE = re.compile(
    r"^\.(?:chapter|defspec|defun|item|kitem|section|subsection)\s+(?P<text>.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SourceLineAlignment:
    source_path: str
    start_line: int
    end_line: int
    matched_tokens: int
    ocr_tokens: int
    coverage_milli: int
    runner_up_coverage_milli: int

    def __post_init__(self) -> None:
        if not self.source_path or self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("source alignment needs a path and positive line interval")
        if self.matched_tokens < 1 or self.ocr_tokens < self.matched_tokens:
            raise ValueError("source alignment token counts are inconsistent")
        if not 0 <= self.runner_up_coverage_milli <= self.coverage_milli <= 1000:
            raise ValueError("source alignment coverage must be ordered within 0..1000")

    @property
    def mapping_review_required(self) -> bool:
        return True

    def to_dict(self) -> dict[str, int | str | bool]:
        return {
            "coverage_milli": self.coverage_milli,
            "end_line": self.end_line,
            "mapping_review_required": True,
            "matched_tokens": self.matched_tokens,
            "ocr_tokens": self.ocr_tokens,
            "runner_up_coverage_milli": self.runner_up_coverage_milli,
            "source_path": self.source_path,
            "start_line": self.start_line,
        }


@dataclass(frozen=True, slots=True)
class _Candidate:
    source_path: str
    start_line: int
    end_line: int
    matched_tokens: int
    ocr_tokens: int

    @property
    def coverage_milli(self) -> int:
        return round(1000 * self.matched_tokens / self.ocr_tokens)


@dataclass(frozen=True, slots=True)
class _IndexedSource:
    source_path: str
    tokens: tuple[tuple[str, int], ...]
    ngrams: frozenset[tuple[str, ...]]


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(text.lower()))


def _visible_source_tokens(text: str, variables: Mapping[str, str]) -> tuple[tuple[str, int], ...]:
    tokens: list[tuple[str, int]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        directive = _VISIBLE_DIRECTIVE.fullmatch(raw_line)
        visible = (
            directive.group("text") if directive else "" if raw_line.startswith(".") else raw_line
        )
        try:
            normalized, _issues = normalize_bolio_line(visible, variables, line_number)
        except BolioError:
            # Alignment is allowed to be lossy because it never supplies truth.
            # Retain unfamiliar cross-reference names as searchable text when
            # the strict truth extractor correctly rejects their syntax.
            normalized = re.sub(r"\x16\(([^)]*)\)", r"\1", visible)
            normalized = re.sub(r"\x06.", "", normalized)
            normalized = normalized.replace("\x11", "").replace("\x18", "").replace("\x19", "")
        tokens.extend((token, line_number) for token in _tokens(normalized))
    return tuple(tokens)


def _ngrams(tokens: tuple[str, ...], size: int = 3) -> frozenset[tuple[str, ...]]:
    return frozenset(tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1))


def _align_candidate(
    source_path: str,
    source_tokens: tuple[tuple[str, int], ...],
    ocr_tokens: tuple[str, ...],
) -> _Candidate | None:
    matcher = SequenceMatcher(
        None, tuple(token for token, _line in source_tokens), ocr_tokens, autojunk=False
    )
    raw_blocks = [block for block in matcher.get_matching_blocks() if block.size >= 2]
    if not raw_blocks:
        return None
    ordered = sorted(raw_blocks, key=lambda item: source_tokens[item.a][1])
    clusters = [[ordered[0]]]
    previous_end = source_tokens[ordered[0].a + ordered[0].size - 1][1]
    for block in ordered[1:]:
        start = source_tokens[block.a][1]
        if start - previous_end > 20:
            clusters.append([])
        clusters[-1].append(block)
        previous_end = max(previous_end, source_tokens[block.a + block.size - 1][1])
    # Manual pages form a dense local cluster. Repeated examples and running
    # matter elsewhere in the source file form weaker, separated clusters.
    blocks = max(clusters, key=lambda cluster: sum(block.size for block in cluster))
    matched = sum(block.size for block in blocks)
    if not matched:
        return None
    return _Candidate(
        source_path,
        min(source_tokens[block.a][1] for block in blocks),
        max(source_tokens[block.a + block.size - 1][1] for block in blocks),
        matched,
        len(ocr_tokens),
    )


class RecoveredSourceIndex:
    """Reusable token index for aligning many pages from the same source tree."""

    def __init__(self, sources: Mapping[str, str], variables: Mapping[str, str]) -> None:
        indexed: list[_IndexedSource] = []
        for source_path, source_text in sorted(sources.items()):
            source_tokens = _visible_source_tokens(source_text, variables)
            if source_tokens:
                indexed.append(
                    _IndexedSource(
                        source_path,
                        source_tokens,
                        _ngrams(tuple(token for token, _line in source_tokens)),
                    )
                )
        if not indexed:
            raise ValueError("recovered source index cannot be empty")
        self._sources: tuple[_IndexedSource, ...] = tuple(indexed)

    def align(self, ocr_text: str, *, shortlist_size: int = 6) -> SourceLineAlignment:
        """Propose one recovered source and approximate line range for a scan page."""

        ocr_tokens = _tokens(ocr_text)
        if len(ocr_tokens) < 10:
            raise ValueError("OCR page has too few tokens for source alignment")
        ocr_ngrams = _ngrams(ocr_tokens)
        shortlisted = sorted(
            ((len(source.ngrams & ocr_ngrams), source) for source in self._sources),
            key=lambda item: (-item[0], item[1].source_path),
        )[:shortlist_size]
        candidates = [
            candidate
            for _overlap, source in shortlisted
            if (candidate := _align_candidate(source.source_path, source.tokens, ocr_tokens))
            is not None
        ]
        if not candidates:
            raise ValueError("no recovered source aligns with the OCR page")
        candidates.sort(key=lambda item: (-item.coverage_milli, item.source_path))
        winner = candidates[0]
        runner_up = candidates[1].coverage_milli if len(candidates) > 1 else 0
        return SourceLineAlignment(
            winner.source_path,
            winner.start_line,
            winner.end_line,
            winner.matched_tokens,
            winner.ocr_tokens,
            winner.coverage_milli,
            runner_up,
        )


def align_ocr_page_to_sources(
    ocr_text: str,
    sources: Mapping[str, str],
    variables: Mapping[str, str],
    *,
    shortlist_size: int = 6,
) -> SourceLineAlignment:
    return RecoveredSourceIndex(sources, variables).align(ocr_text, shortlist_size=shortlist_size)


__all__ = ["RecoveredSourceIndex", "SourceLineAlignment", "align_ocr_page_to_sources"]
