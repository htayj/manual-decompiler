"""Conservative deterministic semantic proposals over supplied physical regions."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Literal

from .types import (
    PhysicalRegion,
    SemanticFinding,
    SemanticKind,
    SemanticProposal,
    SemanticResult,
    stable_id,
)

_HIERARCHY = re.compile(r"^\s*(part|chapter|section)\s+([A-Za-z0-9.IVXLC]+)\b", re.IGNORECASE)
_LIST = re.compile(r"^\s*(?:[-*•]|\d+[.)]|[A-Za-z][.)])\s+")
_CAPTION = re.compile(r"^\s*(?:fig(?:ure)?|table)\s+\d+", re.IGNORECASE)
_NOTE = re.compile(r"^\s*(?:note|\d+[.)])\s", re.IGNORECASE)
_INDEX = re.compile(r"^.+,\s*\d+(?:\s*,\s*\d+)*\s*$")
_XREF = re.compile(
    r"\b(?:see|chapter|section|appendix|figure|table)\s+[A-Za-z0-9.IVXLC]+", re.IGNORECASE
)
_MATH = re.compile(r"(?:[=≈≠≤≥±×÷]|\b(?:sin|cos|log|lim)\b|\$[^$]+\$)")
_TERMINAL = re.compile(r"^\s*(?:[$>#]|[A-Za-z0-9_.-]+>)\s")


class SemanticInferer:
    """Make reviewable classifications without manufacturing or rewriting text."""

    def infer(self, regions: Iterable[PhysicalRegion]) -> SemanticResult:
        ordered = tuple(
            sorted(regions, key=lambda item: (item.page_sequence, item.reading_order, item.id))
        )
        if len({region.id for region in ordered}) != len(ordered):
            raise ValueError("physical region IDs must be unique")
        proposals: list[SemanticProposal] = []
        findings: list[SemanticFinding] = []
        hierarchy: list[tuple[PhysicalRegion, str]] = []
        for region in ordered:
            kind, ambiguous = self._classify(region)
            proposal = SemanticProposal(
                stable_id("semantic", kind, region.id),
                kind,
                (region.id,),
                properties=(("diplomatic-text", region.text),),
                fallback=("diplomatic", "visual") if kind == "math" else (),
            )
            proposals.append(proposal)
            if kind in {"part", "chapter", "section"}:
                hierarchy.append((region, kind))
            if ambiguous:
                findings.append(_finding("AMBIGUOUS_SEMANTICS", "high", (region.id,), ambiguous))
            if kind in {"table", "caption", "math", "cross-reference"}:
                findings.append(
                    _finding(
                        f"{kind.upper()}_REVIEW",
                        "high",
                        (region.id,),
                        f"{kind} requires human review",
                    )
                )
        proposals.extend(self._paragraphs(ordered))
        proposals.extend(self._running_matter(ordered))
        findings.extend(self._hierarchy_findings(hierarchy))
        result = SemanticResult(
            tuple(
                sorted(proposals, key=lambda item: (item.kind, item.physical_region_ids, item.id))
            ),
            tuple(
                sorted(findings, key=lambda item: (item.code, item.physical_region_ids, item.id))
            ),
        )
        result.assert_references(ordered)
        return result

    @staticmethod
    def _classify(region: PhysicalRegion) -> tuple[SemanticKind, str | None]:
        text = region.text
        hierarchy = _HIERARCHY.match(text)
        if hierarchy:
            hierarchy_kind: dict[str, Literal["part", "chapter", "section"]] = {
                "part": "part",
                "chapter": "chapter",
                "section": "section",
            }
            return hierarchy_kind[hierarchy.group(1).lower()], None
        if _CAPTION.match(text):
            return "caption", "caption/figure association is not inferred automatically"
        if region.kind == "figure" or text.strip().lower().startswith("figure "):
            return "figure", None
        if region.kind == "table" or "\t" in text or ("|" in text and text.count("|") >= 2):
            return "table", "table cells and spans require review"
        if _LIST.match(text):
            return "list-item", None
        if _NOTE.match(text):
            return "note", "note attachment requires review"
        if _TERMINAL.match(text):
            return "terminal", None
        if region.kind == "code" or text.startswith((" ", "\t")) or "(" in text and ")" in text:
            return "code", None
        if _INDEX.match(text):
            return "index", "index term hierarchy requires review"
        if _XREF.search(text):
            return "cross-reference", "cross-reference target requires review"
        if _MATH.search(text):
            return "math", "MathML is not inferred; use diplomatic and visual fallback"
        if text.isupper() and len(text.split()) <= 12:
            return "section", "all-caps heading level is ambiguous"
        return "paragraph", None

    @staticmethod
    def _paragraphs(regions: tuple[PhysicalRegion, ...]) -> tuple[SemanticProposal, ...]:
        """Group adjacent prose evidence; region text remains separate and untouched."""
        groups: list[tuple[PhysicalRegion, ...]] = []
        current: list[PhysicalRegion] = []
        for region in regions:
            prose = not _LIST.match(region.text) and not _HIERARCHY.match(region.text)
            contiguous = (
                current
                and region.page_sequence == current[-1].page_sequence
                and region.reading_order == current[-1].reading_order + 1
            )
            if prose and contiguous:
                current.append(region)
            else:
                if len(current) > 1:
                    groups.append(tuple(current))
                current = [region] if prose else []
        if len(current) > 1:
            groups.append(tuple(current))
        return tuple(
            SemanticProposal(
                stable_id("semantic", "paragraph-group", *(item.id for item in group)),
                "paragraph",
                tuple(item.id for item in group),
                properties=(("join", "logical-only"),),
            )
            for group in groups
        )

    @staticmethod
    def _running_matter(regions: tuple[PhysicalRegion, ...]) -> tuple[SemanticProposal, ...]:
        pages: dict[str, set[int]] = defaultdict(set)
        by_text: dict[str, list[PhysicalRegion]] = defaultdict(list)
        for region in regions:
            normalized = region.text.strip()
            if normalized:
                pages[normalized].add(region.page_sequence)
                by_text[normalized].append(region)
        return tuple(
            SemanticProposal(
                stable_id("semantic", "running", text),
                "running-matter",
                tuple(item.id for item in by_text[text]),
                properties=(("repeated-pages", str(len(page_numbers))),),
            )
            for text, page_numbers in sorted(pages.items())
            if len(page_numbers) >= 2
        )

    @staticmethod
    def _hierarchy_findings(hierarchy: list[tuple[PhysicalRegion, str]]) -> list[SemanticFinding]:
        order = {"part": 0, "chapter": 1, "section": 2}
        findings: list[SemanticFinding] = []
        previous = -1
        for region, kind in hierarchy:
            level = order[kind]
            if previous >= 0 and level > previous + 1:
                findings.append(
                    _finding(
                        "HIERARCHY_GAP",
                        "high",
                        (region.id,),
                        "heading hierarchy has a skipped level",
                    )
                )
            previous = level
        return findings


def _finding(
    code: str, severity: Literal["medium", "high"], regions: tuple[str, ...], message: str
) -> SemanticFinding:
    return SemanticFinding(
        stable_id("semantic-finding", code, regions, message), code, severity, regions, message
    )
