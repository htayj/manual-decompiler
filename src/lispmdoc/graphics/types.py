"""Typed exact vector proposals and exhaustive PDF operator dispositions."""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Literal

from lispmdoc.model import AffineTransform, Rational


@dataclass(frozen=True, slots=True)
class ExactPoint:
    x: Rational
    y: Rational

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", Rational.from_value(self.x))
        object.__setattr__(self, "y", Rational.from_value(self.y))

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x.to_dict(), "y": self.y.to_dict()}


@dataclass(frozen=True, slots=True)
class PathSegment:
    command: Literal["move", "line", "curve", "close"]
    points: tuple[ExactPoint, ...] = ()

    def __post_init__(self) -> None:
        expected = {"move": 1, "line": 1, "curve": 3, "close": 0}[self.command]
        if len(self.points) != expected:
            raise ValueError(f"{self.command} segment requires {expected} points")

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "points": [point.to_dict() for point in self.points],
        }


@dataclass(frozen=True, slots=True)
class VectorPath:
    segments: tuple[PathSegment, ...]

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError("vector path requires at least one segment")

    def to_dict(self) -> dict[str, Any]:
        return {"segments": [segment.to_dict() for segment in self.segments]}


@dataclass(frozen=True, slots=True)
class Color:
    space: Literal["DeviceGray", "DeviceRGB", "DeviceCMYK"]
    components: tuple[Rational, ...]

    def __post_init__(self) -> None:
        expected = {"DeviceGray": 1, "DeviceRGB": 3, "DeviceCMYK": 4}[self.space]
        if len(self.components) != expected:
            raise ValueError(f"{self.space} requires {expected} color components")
        for component in self.components:
            value = Rational.from_value(component).fraction
            if not 0 <= value <= 1:
                raise ValueError("color components must be in 0..1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "components": [component.to_dict() for component in self.components],
            "space": self.space,
        }


@dataclass(frozen=True, slots=True)
class GraphicsStyle:
    line_width: Rational = Rational(1)
    dash_array: tuple[Rational, ...] = ()
    dash_phase: Rational = Rational(0)
    line_join: int = 0
    line_cap: int = 0
    stroke_color: Color = Color("DeviceGray", (Rational(0),))
    fill_color: Color = Color("DeviceGray", (Rational(0),))
    stroke_opacity: Rational = Rational(1)
    fill_opacity: Rational = Rational(1)
    blend_mode: str = "Normal"

    def __post_init__(self) -> None:
        if self.line_width.fraction < 0:
            raise ValueError("line width cannot be negative")
        if any(item.fraction < 0 for item in self.dash_array):
            raise ValueError("dash entries cannot be negative")
        if self.line_join not in {0, 1, 2} or self.line_cap not in {0, 1, 2}:
            raise ValueError("line join and cap must be 0, 1, or 2")

    def to_dict(self) -> dict[str, Any]:
        return {
            "blend_mode": self.blend_mode,
            "dash_array": [item.to_dict() for item in self.dash_array],
            "dash_phase": self.dash_phase.to_dict(),
            "fill_color": self.fill_color.to_dict(),
            "fill_opacity": self.fill_opacity.to_dict(),
            "line_cap": self.line_cap,
            "line_join": self.line_join,
            "line_width": self.line_width.to_dict(),
            "stroke_color": self.stroke_color.to_dict(),
            "stroke_opacity": self.stroke_opacity.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ClipProposal:
    id: str
    path: VectorPath
    fill_rule: Literal["nonzero", "evenodd"]
    parent_clip_ids: tuple[str, ...]
    scope: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fill_rule": self.fill_rule,
            "id": self.id,
            "parent_clip_ids": list(self.parent_clip_ids),
            "path": self.path.to_dict(),
            "scope": list(self.scope),
        }


@dataclass(frozen=True, slots=True)
class VectorSceneProposal:
    id: str
    kind: Literal["path", "annotation"]
    z_index: int
    scope: tuple[str, ...]
    transform: AffineTransform
    clip_ids: tuple[str, ...] = ()
    path: VectorPath | None = None
    paint: Literal["stroke", "fill", "fill-stroke"] | None = None
    fill_rule: Literal["nonzero", "evenodd"] | None = None
    style: GraphicsStyle | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "clip_ids": list(self.clip_ids),
            "id": self.id,
            "kind": self.kind,
            "payload": _json_value(self.payload),
            "scope": list(self.scope),
            "transform": self.transform.to_dict(),
            "z_index": self.z_index,
        }
        if self.path is not None:
            result["path"] = self.path.to_dict()
        if self.paint is not None:
            result["paint"] = self.paint
        if self.fill_rule is not None:
            result["fill_rule"] = self.fill_rule
        if self.style is not None:
            result["style"] = self.style.to_dict()
        return result


@dataclass(frozen=True, slots=True)
class OperatorDisposition:
    operator_index: int
    operator: str
    status: Literal["consumed", "unsupported", "malformed", "limit"]
    reason: str
    scope: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator": self.operator,
            "operator_index": self.operator_index,
            "reason": self.reason,
            "scope": list(self.scope),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class AnnotationDisposition:
    annotation_index: int
    subtype: str
    status: Literal["consumed", "unsupported", "malformed"]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "annotation_index": self.annotation_index,
            "reason": self.reason,
            "status": self.status,
            "subtype": self.subtype,
        }


@dataclass(frozen=True, slots=True)
class LoweringResult:
    proposals: tuple[VectorSceneProposal, ...]
    clips: tuple[ClipProposal, ...]
    operator_dispositions: tuple[OperatorDisposition, ...]
    annotation_dispositions: tuple[AnnotationDisposition, ...]
    whole_page_fallback: bool = False
    fidelity_status: Literal["unmeasured"] = "unmeasured"

    @property
    def all_operators_accounted(self) -> bool:
        keys = {(item.scope, item.operator_index) for item in self.operator_dispositions}
        return len(keys) == len(self.operator_dispositions)

    @property
    def unresolved_count(self) -> int:
        return sum(item.status != "consumed" for item in self.operator_dispositions) + sum(
            item.status != "consumed" for item in self.annotation_dispositions
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_operators_accounted": self.all_operators_accounted,
            "annotation_dispositions": [
                disposition.to_dict() for disposition in self.annotation_dispositions
            ],
            "clips": [clip.to_dict() for clip in self.clips],
            "fidelity_status": self.fidelity_status,
            "operator_dispositions": [
                disposition.to_dict() for disposition in self.operator_dispositions
            ],
            "proposals": [proposal.to_dict() for proposal in self.proposals],
            "unresolved_count": self.unresolved_count,
            "whole_page_fallback": self.whole_page_fallback,
        }


def exact_point(transform: AffineTransform, x: Rational, y: Rational) -> ExactPoint:
    transformed_x, transformed_y = transform.apply_exact(x, y)
    return ExactPoint(
        Rational(transformed_x.numerator, transformed_x.denominator),
        Rational(transformed_y.numerator, transformed_y.denominator),
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Rational):
        return value.to_dict()
    if isinstance(value, Fraction):
        return {"denominator": value.denominator, "numerator": value.numerator}
    if isinstance(value, dict):
        return {str(key): _json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value
