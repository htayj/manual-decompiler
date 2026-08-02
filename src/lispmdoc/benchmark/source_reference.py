"""Deterministic text-authority artifacts derived from recovered source blocks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .authoritative import SourceSpan
from .bolio import BolioBlock, BolioExtraction


@dataclass(frozen=True, slots=True)
class ReferenceRegion:
    """A Bolio block mapped to exact lines in a derived authority artifact."""

    index: int
    kind: str
    text: str
    source_span: SourceSpan
    bolio_start_line: int
    bolio_end_line: int
    line_break_policy: str

    def to_dict(self) -> dict[str, object]:
        return {
            "bolio_source_span": {
                "end_line": self.bolio_end_line,
                "start_line": self.bolio_start_line,
            },
            "index": self.index,
            "kind": self.kind,
            "line_break_policy": self.line_break_policy,
            "source_span": self.source_span.to_dict(),
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class ReferenceArtifact:
    """UTF-8 canonical authority text plus exact selectors and source findings."""

    text: str
    regions: tuple[ReferenceRegion, ...]
    issue_count: int

    @property
    def bytes(self) -> bytes:
        return self.text.encode("utf-8")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.bytes).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "issue_count": self.issue_count,
            "regions": [region.to_dict() for region in self.regions],
            "sha256": self.sha256,
        }


def _printed_block_text(block: BolioBlock) -> str:
    if block.kind == "section" and block.section_number:
        return f"{block.section_number} {block.text}"
    return block.text


def render_reference_artifact(extraction: BolioExtraction) -> ReferenceArtifact:
    """Render canonical blocks while retaining source and line-break provenance.

    ``body`` blocks arrive with editorial source wrapping reflowed by the
    Bolio reader.  ``code`` blocks retain their literal newlines.  The blank
    separator inserted here is an artifact-level structural boundary, never a
    claim that a source physical newline was printed.
    """
    output_lines: list[str] = []
    regions: list[ReferenceRegion] = []
    for index, block in enumerate(extraction.blocks):
        if output_lines:
            output_lines.append("")
        text = _printed_block_text(block)
        block_lines = text.split("\n")
        start_line = len(output_lines) + 1
        output_lines.extend(block_lines)
        end_line = len(output_lines)
        regions.append(
            ReferenceRegion(
                index,
                block.kind,
                text,
                SourceSpan(start_line, end_line),
                block.span.start_line,
                block.span.end_line,
                block.line_break_policy,
            )
        )
    text = "\n".join(output_lines) + ("\n" if output_lines else "")
    return ReferenceArtifact(text, tuple(regions), len(extraction.issues))


__all__ = ["ReferenceArtifact", "ReferenceRegion", "render_reference_artifact"]
