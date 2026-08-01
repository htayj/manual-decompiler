"""Measured typography gates for reconstructed text."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TypographyMetrics:
    line_break_agreement_milli: int
    p95_baseline_displacement_micropoints: int
    max_code_column_displacement_micropoints: int
    exact_code: bool

    @property
    def passes_replica_gate(self) -> bool:
        return (
            self.line_break_agreement_milli >= 995
            and self.p95_baseline_displacement_micropoints <= 500
            and self.max_code_column_displacement_micropoints <= 500
            and self.exact_code
        )


def measure_line_break_agreement(source: tuple[str, ...], reconstruction: tuple[str, ...]) -> int:
    maximum = max(len(source), len(reconstruction))
    if maximum == 0:
        return 1000
    return (
        sum(left == right for left, right in zip(source, reconstruction, strict=False))
        * 1000
        // maximum
    )


def p95_baseline_displacement(source: tuple[int, ...], reconstruction: tuple[int, ...]) -> int:
    if len(source) != len(reconstruction) or not source:
        raise ValueError("baseline vectors must be non-empty and equally sized")
    errors = sorted(abs(left - right) for left, right in zip(source, reconstruction, strict=True))
    return errors[(len(errors) * 95 + 99) // 100 - 1]


def code_columns_exact(source: tuple[str, ...], reconstruction: tuple[str, ...]) -> bool:
    return source == reconstruction


def measure_typography(
    source_lines: tuple[str, ...],
    reconstruction_lines: tuple[str, ...],
    source_baselines: tuple[int, ...],
    reconstruction_baselines: tuple[int, ...],
    code_source: tuple[str, ...],
    code_reconstruction: tuple[str, ...],
) -> TypographyMetrics:
    exact = code_columns_exact(code_source, code_reconstruction)
    return TypographyMetrics(
        measure_line_break_agreement(source_lines, reconstruction_lines),
        p95_baseline_displacement(source_baselines, reconstruction_baselines),
        0 if exact else 1001,
        exact,
    )
