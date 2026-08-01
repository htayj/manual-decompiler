"""Deterministic, page-local resumable build stages."""

from .dag import StageKey, StageResult, StageRunner

__all__ = ["StageKey", "StageResult", "StageRunner"]
