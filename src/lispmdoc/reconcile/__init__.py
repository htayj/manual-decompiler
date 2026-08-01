"""Evidence-conditioned literal reconciliation and deterministic review queues."""

from .engine import Reconciler, ReviewQueue, align_lines, align_regions, align_tokens
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
)

__all__ = [
    "Alignment",
    "Calibration",
    "CandidatePage",
    "EvidenceReference",
    "Finding",
    "NormalizationSuggestion",
    "Reconciler",
    "ReconciliationResult",
    "ReviewQueue",
    "SelectedLine",
    "SelectedToken",
    "align_lines",
    "align_regions",
    "align_tokens",
]
