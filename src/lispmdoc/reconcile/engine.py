"""Literal-only multi-engine alignment, selection, findings, and review queues."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

from lispmdoc.ocr import BBox, OCRLine, OCRPage, OCRToken

from .types import (
    Alignment,
    Calibration,
    CandidatePage,
    EvidenceReference,
    Finding,
    NormalizationSuggestion,
    ReconciliationResult,
    SelectedLine,
    SelectedToken,
    line_tokens,
    stable_id,
)

_IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_-]*\b")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_KEY = re.compile(r"(?::[A-Za-z][A-Za-z0-9-]*\b|\b[A-Za-z_][A-Za-z0-9_-]*=)")
_EQUATION = re.compile(r"(?:=|[≈≠≤≥±×÷])")


def _iou_milli(left: BBox | None, right: BBox | None) -> int:
    if left is None or right is None:
        return 0
    x0, y0, x1, y1 = (
        max(left.x0, right.x0),
        max(left.y0, right.y0),
        min(left.x1, right.x1),
        min(left.y1, right.y1),
    )
    intersection = max(0, x1 - x0) * max(0, y1 - y0)
    union = (
        (left.x1 - left.x0) * (left.y1 - left.y0)
        + (right.x1 - right.x0) * (right.y1 - right.y0)
        - intersection
    )
    return 0 if union == 0 else intersection * 1000 // union


def _edit_distance(left: str, right: str) -> int:
    row = list(range(len(right) + 1))
    for index, character in enumerate(left, 1):
        next_row = [index]
        for right_index, other in enumerate(right, 1):
            next_row.append(
                min(
                    next_row[-1] + 1,
                    row[right_index] + 1,
                    row[right_index - 1] + (character != other),
                )
            )
        row = next_row
    return row[-1]


def _text_similarity_milli(left: str, right: str) -> int:
    maximum = max(len(left), len(right))
    return 1000 if maximum == 0 else (maximum - _edit_distance(left, right)) * 1000 // maximum


def _reference(
    candidate: CandidatePage, normalized_id: str, native_id: str | None
) -> EvidenceReference:
    return EvidenceReference(
        candidate.page.engine, candidate.evidence_sha256, native_id, normalized_id
    )


def _candidate_lines(page: OCRPage) -> tuple[OCRLine, ...]:
    return tuple(line for region in page.regions for line in region.lines)


def _candidate_tokens(page: OCRPage) -> tuple[OCRToken, ...]:
    return tuple(token for line in _candidate_lines(page) for token in line_tokens(line))


class _TextBox(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def text(self) -> str: ...

    @property
    def bbox(self) -> BBox | None: ...


def _align_items(
    level: Literal["region", "line", "token"],
    left: CandidatePage,
    right: CandidatePage,
    left_items: tuple[_TextBox, ...],
    right_items: tuple[_TextBox, ...],
) -> tuple[Alignment, ...]:
    """One-to-one deterministic geometric/text alignment at one OCR level."""
    available = list(right_items)
    alignments: list[Alignment] = []
    for source in left_items:
        scored = [
            (
                _iou_milli(source.bbox, candidate.bbox),
                _text_similarity_milli(source.text, candidate.text),
                candidate,
            )
            for candidate in available
        ]
        if not scored:
            continue
        iou, similarity, target = max(scored, key=lambda item: (item[0], item[1], item[2].id))
        # A text-only match is accepted when geometry is unavailable; unrelated
        # text with zero overlap is deliberately not aligned.
        if iou == 0 and similarity == 0:
            continue
        available.remove(target)
        alignments.append(
            Alignment(
                level, left.page.engine, source.id, right.page.engine, target.id, iou, similarity
            )
        )
    return tuple(alignments)


def align_regions(left: CandidatePage, right: CandidatePage) -> tuple[Alignment, ...]:
    return _align_items("region", left, right, left.page.regions, right.page.regions)


def align_lines(left: CandidatePage, right: CandidatePage) -> tuple[Alignment, ...]:
    return _align_items(
        "line", left, right, _candidate_lines(left.page), _candidate_lines(right.page)
    )


def align_tokens(left: CandidatePage, right: CandidatePage) -> tuple[Alignment, ...]:
    return _align_items(
        "token", left, right, _candidate_tokens(left.page), _candidate_tokens(right.page)
    )


@dataclass(frozen=True, slots=True)
class ReviewQueue:
    findings: tuple[Finding, ...]

    def query(
        self, *, code: str | None = None, minimum_severity: str = "medium"
    ) -> tuple[Finding, ...]:
        rank = {"info": 0, "medium": 1, "high": 2}
        if minimum_severity not in rank:
            raise ValueError("unknown review severity")
        return tuple(
            finding
            for finding in self.findings
            if rank[finding.severity] >= rank[minimum_severity]
            and (code is None or finding.code == code)
        )


class Reconciler:
    """Conservative reconciliation: disagreement never overwrites routed text."""

    def __init__(
        self, calibrations: Iterable[Calibration] = (), *, low_confidence_milli: int = 700
    ) -> None:
        values = {item.engine: item for item in calibrations}
        self._calibrations = values
        if not 0 <= low_confidence_milli <= 1000:
            raise ValueError("low confidence threshold must be 0..1000")
        self._low_confidence_milli = low_confidence_milli

    def reconcile(
        self, candidates: Iterable[CandidatePage], *, routed_engine: str
    ) -> ReconciliationResult:
        pages = tuple(sorted(candidates, key=lambda item: item.page.engine))
        if not pages:
            raise ValueError("at least one OCR candidate is required")
        baseline = next((item for item in pages if item.page.engine == routed_engine), None)
        if baseline is None:
            raise ValueError("routed engine has no candidate page")
        if any(item.page.page_id != baseline.page.page_id for item in pages):
            raise ValueError("all reconciliation candidates must belong to one page")
        alignments = tuple(
            alignment
            for item in pages
            if item is not baseline
            for align in (align_regions, align_lines, align_tokens)
            for alignment in align(baseline, item)
        )
        targets: dict[tuple[str, str], OCRLine] = {}
        for item in pages:
            for line in _candidate_lines(item.page):
                targets[(item.page.engine, line.id)] = line
        by_baseline: dict[str, list[tuple[CandidatePage, OCRLine, Alignment]]] = {}
        by_engine = {item.page.engine: item for item in pages}
        for alignment in (item for item in alignments if item.level == "line"):
            target = targets[(alignment.right_engine, alignment.right_id)]
            by_baseline.setdefault(alignment.left_id, []).append(
                (by_engine[alignment.right_engine], target, alignment)
            )
        selected: list[SelectedLine] = []
        findings: list[Finding] = []
        suggestions: list[NormalizationSuggestion] = []
        for line in _candidate_lines(baseline.page):
            contenders = by_baseline.get(line.id, [])
            selected.append(self._select_line(baseline, line, contenders))
            findings.extend(self._line_findings(baseline, line, contenders, pages))
            suggestions.extend(_suggestions(line))
        findings.extend(_duplicate_findings(pages))
        ordered_findings = tuple(
            sorted(findings, key=lambda item: (item.severity, item.code, item.subject_id, item.id))
        )
        return ReconciliationResult(
            baseline.page.page_id,
            routed_engine,
            tuple(selected),
            alignments,
            ordered_findings,
            tuple(suggestions),
        )

    def _select_line(
        self,
        baseline: CandidatePage,
        line: OCRLine,
        contenders: list[tuple[CandidatePage, OCRLine, Alignment]],
    ) -> SelectedLine:
        base_ref = _reference(baseline, line.id, line.native_id)
        agreeing = [(baseline, line, 1000)]
        agreeing.extend(
            (candidate, other, alignment.text_similarity_milli)
            for candidate, other, alignment in contenders
            if other.text == line.text
        )
        best, best_line, _ = max(
            agreeing,
            key=lambda item: (
                self._calibrated(item[0].page.engine, item[1].confidence),
                item[0].page.engine,
                item[1].id,
            ),
        )
        # The chosen source can change only among exact textual agreement. The
        # actual diplomatic text/tokens remain those of the routed candidate.
        alternatives = tuple(
            sorted(
                (
                    _reference(candidate, other.id, other.native_id)
                    for candidate, other, _ in contenders
                ),
                key=lambda item: (item.engine, item.normalized_id),
            )
        )
        tokens = line_tokens(line)
        selected_tokens = tuple(
            SelectedToken(
                token.text,
                _reference(baseline, token.id, token.native_id),
                (),
                self._calibrated(best.page.engine, best_line.confidence),
            )
            for token in tokens
        )
        return SelectedLine(
            line.text, best.page.engine, line.id, selected_tokens, (base_ref, *alternatives)
        )

    def _line_findings(
        self,
        baseline: CandidatePage,
        line: OCRLine,
        contenders: list[tuple[CandidatePage, OCRLine, Alignment]],
        pages: tuple[CandidatePage, ...],
    ) -> list[Finding]:
        reference = _reference(baseline, line.id, line.native_id)
        findings: list[Finding] = []
        if self._calibrated(baseline.page.engine, line.confidence) < self._low_confidence_milli:
            findings.append(
                _finding(
                    baseline.page.page_id,
                    line.id,
                    "LOW_CONFIDENCE",
                    "high",
                    "low calibrated confidence",
                    (reference,),
                )
            )
        disagreed = [
            (candidate, other) for candidate, other, _ in contenders if other.text != line.text
        ]
        if disagreed:
            evidence = (
                reference,
                *(
                    _reference(candidate, other.id, other.native_id)
                    for candidate, other in disagreed
                ),
            )
            findings.append(
                _finding(
                    baseline.page.page_id,
                    line.id,
                    "ENGINE_DISAGREEMENT",
                    "high",
                    "engines disagree; routed literal retained",
                    evidence,
                )
            )
        aligned_engines = {candidate.page.engine for candidate, _, _ in contenders}
        for candidate in pages:
            if candidate is not baseline and candidate.page.engine not in aligned_engines:
                findings.append(
                    _finding(
                        baseline.page.page_id,
                        line.id,
                        "OMISSION",
                        "high",
                        f"{candidate.page.engine} omitted routed line",
                        (reference,),
                    )
                )
        rules = (
            ("IDENTIFIER", _IDENTIFIER),
            ("NUMBER", _NUMBER),
            ("KEY_NAME", _KEY),
            ("EQUATION", _EQUATION),
        )
        for code, pattern in rules:
            if pattern.search(line.text):
                findings.append(
                    _finding(
                        baseline.page.page_id,
                        line.id,
                        code,
                        "high",
                        f"{code.lower()} requires review",
                        (reference,),
                    )
                )
        if any(unicodedata.category(char).startswith("P") for char in line.text):
            findings.append(
                _finding(
                    baseline.page.page_id,
                    line.id,
                    "PUNCTUATION",
                    "high",
                    "punctuation requires review",
                    (reference,),
                )
            )
        if any(
            not char.isalnum() and not char.isspace() and unicodedata.category(char)[0] != "P"
            for char in line.text
        ):
            findings.append(
                _finding(
                    baseline.page.page_id,
                    line.id,
                    "SYMBOL",
                    "high",
                    "symbol requires review",
                    (reference,),
                )
            )
        if "(" in line.text or ")" in line.text or "'" in line.text:
            findings.append(
                _finding(
                    baseline.page.page_id,
                    line.id,
                    "LISP_TOKEN",
                    "high",
                    "Lisp-like token requires review",
                    (reference,),
                )
            )
        if "\ufffd" in line.text or "□" in line.text:
            findings.append(
                _finding(
                    baseline.page.page_id,
                    line.id,
                    "UNRESOLVED_GLYPH",
                    "high",
                    "unresolved glyph marker",
                    (reference,),
                )
            )
        if any(ord(char) < 32 and char not in "\n\t\r" for char in line.text) or any(
            ord(char) > 127 for char in line.text
        ):
            findings.append(
                _finding(
                    baseline.page.page_id,
                    line.id,
                    "UNEXPECTED_UNICODE",
                    "high",
                    "unexpected Unicode requires review",
                    (reference,),
                )
            )
        if line.text.endswith("-"):
            findings.append(
                _finding(
                    baseline.page.page_id,
                    line.id,
                    "HYPHENATION_AMBIGUITY",
                    "high",
                    "line-final hyphen is ambiguous",
                    (reference,),
                )
            )
        return findings

    def _calibrated(self, engine: str, confidence: float | None) -> int:
        return self._calibrations.get(engine, Calibration(engine)).apply(confidence)


def _finding(
    page_id: str,
    subject_id: str,
    code: str,
    severity: Literal["info", "medium", "high"],
    message: str,
    evidence: tuple[EvidenceReference, ...],
) -> Finding:
    ordered = tuple(sorted(evidence, key=lambda item: (item.engine, item.normalized_id)))
    return Finding(
        stable_id("finding", page_id, subject_id, code, ordered),
        page_id,
        subject_id,
        code,
        severity,
        message,
        ordered,
    )


def _duplicate_findings(pages: tuple[CandidatePage, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for candidate in pages:
        counts = Counter(line.text for line in _candidate_lines(candidate.page) if line.text)
        for text, count in sorted(counts.items()):
            if count > 1:
                line = next(line for line in _candidate_lines(candidate.page) if line.text == text)
                reference = _reference(candidate, line.id, line.native_id)
                findings.append(
                    _finding(
                        candidate.page.page_id,
                        line.id,
                        "DUPLICATE_LINE",
                        "high",
                        f"duplicate literal line appears {count} times",
                        (reference,),
                    )
                )
    return findings


def _suggestions(line: OCRLine) -> list[NormalizationSuggestion]:
    # Suggestions are deliberately separate data.  The reconciler never uses
    # them in selection and never changes the diplomatic line.
    if "\u00a0" in line.text:
        return [
            NormalizationSuggestion(
                line.id, line.text, line.text.replace("\u00a0", " "), "replace-nbsp"
            )
        ]
    return []
