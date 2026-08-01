"""Deterministic, literal-only reconciliation records."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from lispmdoc.ocr import OCRLine, OCRPage, OCRToken


def stable_id(kind: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{kind}-{sha256(payload).hexdigest()[:20]}"


@dataclass(frozen=True, slots=True)
class CandidatePage:
    """One normalized engine result bound to exact native evidence bytes."""

    page: OCRPage
    evidence_sha256: str

    def __post_init__(self) -> None:
        if len(self.evidence_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.evidence_sha256
        ):
            raise ValueError("candidate evidence_sha256 must be a lower-case SHA-256 digest")


@dataclass(frozen=True, slots=True)
class Calibration:
    """A fixed, benchmark-derived reliability adjustment in thousandths."""

    engine: str
    confidence_scale_milli: int = 1000
    confidence_offset_milli: int = 0

    def __post_init__(self) -> None:
        if not self.engine or not 0 <= self.confidence_scale_milli <= 2000:
            raise ValueError("calibration engine and 0..2000 scale are required")
        if not -1000 <= self.confidence_offset_milli <= 1000:
            raise ValueError("calibration offset must be in -1000..1000")

    def apply(self, confidence: float | None) -> int:
        # Unknown confidence is not evidence of high quality.
        raw = 0 if confidence is None else round(confidence * 1000)
        return max(
            0, min(1000, (raw * self.confidence_scale_milli) // 1000 + self.confidence_offset_milli)
        )


@dataclass(frozen=True, slots=True)
class Alignment:
    level: Literal["region", "line", "token"]
    left_engine: str
    left_id: str
    right_engine: str
    right_id: str
    iou_milli: int
    text_similarity_milli: int


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    engine: str
    evidence_sha256: str
    native_id: str | None
    normalized_id: str


@dataclass(frozen=True, slots=True)
class SelectedToken:
    """Diplomatic selected token plus immutable provenance and alternatives."""

    text: str
    selected: EvidenceReference
    alternatives: tuple[EvidenceReference, ...]
    calibrated_confidence_milli: int


@dataclass(frozen=True, slots=True)
class SelectedLine:
    text: str
    source_engine: str
    source_line_id: str
    tokens: tuple[SelectedToken, ...]
    alternatives: tuple[EvidenceReference, ...]


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    page_id: str
    subject_id: str
    code: str
    severity: Literal["info", "medium", "high"]
    message: str
    evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True, slots=True)
class NormalizationSuggestion:
    """A non-authoritative suggestion; it never changes ``SelectedLine.text``."""

    subject_id: str
    diplomatic_text: str
    suggested_text: str
    rule: str


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    page_id: str
    routed_engine: str
    lines: tuple[SelectedLine, ...]
    alignments: tuple[Alignment, ...]
    findings: tuple[Finding, ...]
    suggestions: tuple[NormalizationSuggestion, ...]

    @property
    def diplomatic_text(self) -> str:
        return "\n".join(line.text for line in self.lines)


def line_tokens(line: OCRLine) -> tuple[OCRToken, ...]:
    return tuple(token for span in line.spans for token in span.tokens)
