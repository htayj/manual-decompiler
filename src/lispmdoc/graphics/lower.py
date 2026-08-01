"""Bounded deterministic lowering of supplied PDF graphics operations."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from fractions import Fraction
from hashlib import sha256
from typing import Any, Literal, cast

from lispmdoc.model import AffineTransform, Rational

from .operators import (
    AnnotationEvidence,
    GraphicsResources,
    PDFOperator,
    Type3CharProcedure,
)
from .types import (
    AnnotationDisposition,
    ClipProposal,
    Color,
    ExactPoint,
    GraphicsStyle,
    LoweringResult,
    OperatorDisposition,
    PathSegment,
    VectorPath,
    VectorSceneProposal,
    exact_point,
)

ColorSpace = Literal["DeviceGray", "DeviceRGB", "DeviceCMYK"]


@dataclass(frozen=True, slots=True)
class LoweringLimits:
    max_operators: int = 100_000
    max_xobject_depth: int = 16
    max_path_segments: int = 100_000

    def __post_init__(self) -> None:
        if min(self.max_operators, self.max_xobject_depth, self.max_path_segments) < 1:
            raise ValueError("graphics lowering limits must be positive")


@dataclass(frozen=True, slots=True)
class Type3LoweringResult:
    font_id: str
    glyph_name: str
    evidence_sha256: str
    metrics: tuple[Rational, ...]
    lowering: LoweringResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_sha256": self.evidence_sha256,
            "font_id": self.font_id,
            "glyph_name": self.glyph_name,
            "lowering": self.lowering.to_dict(),
            "metrics": [metric.to_dict() for metric in self.metrics],
        }


@dataclass(frozen=True, slots=True)
class _GraphicsState:
    ctm: AffineTransform
    style: GraphicsStyle
    clip_ids: tuple[str, ...] = ()
    stroke_space: ColorSpace = "DeviceGray"
    fill_space: ColorSpace = "DeviceGray"


@dataclass(slots=True)
class _PathBuilder:
    segments: list[PathSegment]
    construction_transforms: list[AffineTransform]
    current_point: ExactPoint | None = None
    subpath_start: ExactPoint | None = None

    @classmethod
    def empty(cls) -> _PathBuilder:
        return cls([], [])

    def add(self, segment: PathSegment, transform: AffineTransform) -> None:
        self.segments.append(segment)
        self.construction_transforms.append(transform)
        if segment.command == "move":
            self.current_point = segment.points[0]
            self.subpath_start = segment.points[0]
        elif segment.command in {"line", "curve"}:
            self.current_point = segment.points[-1]
        elif segment.command == "close" and self.subpath_start is not None:
            self.current_point = self.subpath_start

    def close(self, transform: AffineTransform) -> bool:
        if self.current_point is None or self.subpath_start is None:
            return False
        self.add(PathSegment("close"), transform)
        return True

    def clear(self) -> None:
        self.segments.clear()
        self.construction_transforms.clear()
        self.current_point = None
        self.subpath_start = None


class _Lowerer:
    def __init__(self, limits: LoweringLimits) -> None:
        self.limits = limits
        self.remaining_operators = limits.max_operators
        self.proposals: list[VectorSceneProposal] = []
        self.clips: list[ClipProposal] = []
        self.dispositions: list[OperatorDisposition] = []
        self.annotation_dispositions: list[AnnotationDisposition] = []
        self.z_index = 0
        self.type3_metrics: tuple[Rational, ...] = ()

    def process(
        self,
        operators: tuple[PDFOperator, ...],
        resources: GraphicsResources,
        state: _GraphicsState,
        scope: tuple[str, ...],
        *,
        active_forms: tuple[int, ...] = (),
        depth: int = 0,
        type3: bool = False,
    ) -> None:
        stack: list[_GraphicsState] = []
        path = _PathBuilder.empty()
        pending_clip: Literal["nonzero", "evenodd"] | None = None
        for index, operation in enumerate(operators):
            if self.remaining_operators <= 0:
                self._dispose(index, operation, "limit", "operator budget exhausted", scope)
                continue
            self.remaining_operators -= 1
            name = operation.name
            operands = operation.operands
            try:
                if name == "q":
                    self._arity(operands, 0)
                    stack.append(state)
                    self._consume(index, operation, scope)
                elif name == "Q":
                    self._arity(operands, 0)
                    if not stack:
                        raise _Malformed("graphics-state restore underflow")
                    state = stack.pop()
                    self._consume(index, operation, scope)
                elif name == "cm":
                    values = self._numbers(operands, 6)
                    matrix = AffineTransform(*values)
                    state = replace(state, ctm=_compose(state.ctm, matrix))
                    self._consume(index, operation, scope)
                elif name in {"m", "l"}:
                    values = self._numbers(operands, 2)
                    point = exact_point(state.ctm, values[0], values[1])
                    if name == "l" and path.current_point is None:
                        raise _Malformed("line requires a current point")
                    self._require_path_capacity(path, 1)
                    path.add(PathSegment("move" if name == "m" else "line", (point,)), state.ctm)
                    self._consume(index, operation, scope)
                elif name == "c":
                    values = self._numbers(operands, 6)
                    if path.current_point is None:
                        raise _Malformed("curve requires a current point")
                    points = tuple(
                        exact_point(state.ctm, values[offset], values[offset + 1])
                        for offset in range(0, 6, 2)
                    )
                    self._require_path_capacity(path, 1)
                    path.add(PathSegment("curve", points), state.ctm)
                    self._consume(index, operation, scope)
                elif name in {"v", "y"}:
                    values = self._numbers(operands, 4)
                    if path.current_point is None:
                        raise _Malformed("curve shorthand requires a current point")
                    first = (
                        path.current_point
                        if name == "v"
                        else exact_point(state.ctm, values[0], values[1])
                    )
                    end = exact_point(state.ctm, values[2], values[3])
                    second = exact_point(state.ctm, values[0], values[1]) if name == "v" else end
                    self._require_path_capacity(path, 1)
                    path.add(PathSegment("curve", (first, second, end)), state.ctm)
                    self._consume(index, operation, scope)
                elif name == "h":
                    self._arity(operands, 0)
                    self._require_path_capacity(path, 1)
                    if not path.close(state.ctm):
                        raise _Malformed("close-path requires a current subpath")
                    self._consume(index, operation, scope)
                elif name == "re":
                    values = self._numbers(operands, 4)
                    self._require_path_capacity(path, 5)
                    self._rectangle(path, state.ctm, *values)
                    self._consume(index, operation, scope)
                elif name in {"W", "W*"}:
                    self._arity(operands, 0)
                    if not path.segments:
                        raise _Malformed("clip requires a current path")
                    pending_clip = "evenodd" if name == "W*" else "nonzero"
                    self._consume(index, operation, scope)
                elif name in {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}:
                    self._arity(operands, 0)
                    close = name in {"s", "b", "b*"}
                    if not path.segments:
                        if close:
                            raise _Malformed("close-and-paint requires a current path")
                        self._consume(index, operation, scope)
                        pending_clip = None
                        continue
                    if close:
                        self._require_path_capacity(path, 1)
                        path.close(state.ctm)
                    if pending_clip is not None:
                        clip = self._make_clip(path, pending_clip, state.clip_ids, scope)
                        self.clips.append(clip)
                        state = replace(state, clip_ids=(*state.clip_ids, clip.id))
                    if name != "n":
                        self._paint(path, name, state, scope, index)
                    path.clear()
                    pending_clip = None
                    self._consume(index, operation, scope)
                elif name == "w":
                    value = self._numbers(operands, 1)[0]
                    if value.fraction < 0:
                        raise _Malformed("line width cannot be negative")
                    state = replace(state, style=replace(state.style, line_width=value))
                    self._consume(index, operation, scope)
                elif name == "d":
                    if len(operands) != 2 or not isinstance(operands[0], (list, tuple)):
                        raise _Malformed("dash requires array and phase")
                    dash = tuple(_number(item) for item in operands[0])
                    phase = _number(operands[1])
                    if any(item.fraction < 0 for item in dash) or phase.fraction < 0:
                        raise _Malformed("dash values cannot be negative")
                    state = replace(
                        state,
                        style=replace(state.style, dash_array=dash, dash_phase=phase),
                    )
                    self._consume(index, operation, scope)
                elif name in {"j", "J"}:
                    line_style_value = self._integer_operand(operands)
                    if line_style_value not in {0, 1, 2}:
                        raise _Malformed("line join/cap must be 0, 1, or 2")
                    style = (
                        replace(state.style, line_join=line_style_value)
                        if name == "j"
                        else replace(state.style, line_cap=line_style_value)
                    )
                    state = replace(state, style=style)
                    self._consume(index, operation, scope)
                elif name in {"G", "g", "RG", "rg", "K", "k"}:
                    state = self._direct_color(state, name, operands)
                    self._consume(index, operation, scope)
                elif name in {"CS", "cs"}:
                    self._arity(operands, 1)
                    color_space = _name(operands[0])
                    if color_space not in {"DeviceGray", "DeviceRGB", "DeviceCMYK"}:
                        self._dispose(
                            index,
                            operation,
                            "unsupported",
                            f"unsupported color space {color_space}",
                            scope,
                        )
                        continue
                    device_color_space = cast(ColorSpace, color_space)
                    state = (
                        replace(state, stroke_space=device_color_space)
                        if name == "CS"
                        else replace(state, fill_space=device_color_space)
                    )
                    self._consume(index, operation, scope)
                elif name in {"SC", "SCN", "sc", "scn"}:
                    state = self._selected_color(state, name, operands)
                    self._consume(index, operation, scope)
                elif name == "gs":
                    state, reason = self._ext_gstate(state, operands, resources)
                    if reason is not None:
                        self._dispose(index, operation, "unsupported", reason, scope)
                    else:
                        self._consume(index, operation, scope)
                elif name == "Do":
                    self._form(
                        index,
                        operation,
                        resources,
                        state,
                        scope,
                        active_forms,
                        depth,
                        type3,
                    )
                elif type3 and name in {"d0", "d1"}:
                    expected = 2 if name == "d0" else 6
                    self.type3_metrics = self._numbers(operands, expected)
                    self._consume(index, operation, scope)
                elif name in {"sh"}:
                    self._dispose(
                        index, operation, "unsupported", "shading requires bounded fallback", scope
                    )
                elif name in {"BI", "ID", "EI"}:
                    self._dispose(
                        index,
                        operation,
                        "unsupported",
                        "inline image requires separate bounded raster extraction",
                        scope,
                    )
                else:
                    self._dispose(
                        index,
                        operation,
                        "unsupported",
                        "operator is outside the vector lowering contract",
                        scope,
                    )
            except _PathLimit as error:
                path.clear()
                pending_clip = None
                self._dispose(index, operation, "limit", str(error), scope)
            except (_Malformed, TypeError, ValueError, ZeroDivisionError) as error:
                self._dispose(index, operation, "malformed", str(error), scope)

    def annotations(
        self,
        annotations: tuple[AnnotationEvidence, ...],
        transform: AffineTransform,
    ) -> None:
        for index, annotation in enumerate(annotations):
            try:
                values = tuple(_number(value) for value in annotation.rect)
                if len(values) != 4:
                    raise _Malformed("annotation rectangle requires four coordinates")
                x0, y0, x1, y1 = values
                if x1.fraction < x0.fraction or y1.fraction < y0.fraction:
                    raise _Malformed("annotation rectangle is inverted")
                if annotation.subtype not in {"Link", "Text", "FreeText"}:
                    self.annotation_dispositions.append(
                        AnnotationDisposition(
                            index,
                            annotation.subtype,
                            "unsupported",
                            "annotation subtype requires bounded review",
                        )
                    )
                    continue
                lower_left = exact_point(transform, x0, y0)
                upper_right = exact_point(transform, x1, y1)
                payload = {
                    **annotation.properties,
                    "action_uri": annotation.action_uri,
                    "contents": annotation.contents,
                    "rect": [lower_left.to_dict(), upper_right.to_dict()],
                    "subtype": annotation.subtype,
                }
                self.proposals.append(
                    VectorSceneProposal(
                        _stable_id("annotation", index, annotation.subtype, payload),
                        "annotation",
                        self.z_index,
                        ("annotations",),
                        AffineTransform.identity(),
                        payload=payload,
                    )
                )
                self.z_index += 1
                self.annotation_dispositions.append(
                    AnnotationDisposition(
                        index, annotation.subtype, "consumed", "annotation retained"
                    )
                )
            except (_Malformed, TypeError, ValueError) as error:
                self.annotation_dispositions.append(
                    AnnotationDisposition(index, annotation.subtype, "malformed", str(error))
                )

    def _rectangle(
        self,
        path: _PathBuilder,
        transform: AffineTransform,
        x: Rational,
        y: Rational,
        width: Rational,
        height: Rational,
    ) -> None:
        points = (
            exact_point(transform, x, y),
            exact_point(transform, _add(x, width), y),
            exact_point(transform, _add(x, width), _add(y, height)),
            exact_point(transform, x, _add(y, height)),
        )
        path.add(PathSegment("move", (points[0],)), transform)
        for point in points[1:]:
            path.add(PathSegment("line", (point,)), transform)
        path.close(transform)

    def _paint(
        self,
        path: _PathBuilder,
        operator: str,
        state: _GraphicsState,
        scope: tuple[str, ...],
        operator_index: int,
    ) -> None:
        paint: Literal["stroke", "fill", "fill-stroke"]
        if operator in {"S", "s"}:
            paint = "stroke"
        elif operator in {"f", "F", "f*"}:
            paint = "fill"
        else:
            paint = "fill-stroke"
        fill_rule: Literal["nonzero", "evenodd"] = (
            "evenodd" if operator in {"f*", "B*", "b*"} else "nonzero"
        )
        vector_path = VectorPath(tuple(path.segments))
        payload = {
            "construction_transforms": [
                transform.to_dict() for transform in path.construction_transforms
            ],
            "coordinates": "page-space-exact",
            "paint_ctm": state.ctm.to_dict(),
        }
        self.proposals.append(
            VectorSceneProposal(
                _stable_id("path", scope, operator_index, self.z_index, vector_path.to_dict()),
                "path",
                self.z_index,
                scope,
                AffineTransform.identity(),
                state.clip_ids,
                vector_path,
                paint,
                fill_rule,
                state.style,
                payload,
            )
        )
        self.z_index += 1

    def _make_clip(
        self,
        path: _PathBuilder,
        fill_rule: Literal["nonzero", "evenodd"],
        parents: tuple[str, ...],
        scope: tuple[str, ...],
    ) -> ClipProposal:
        vector_path = VectorPath(tuple(path.segments))
        return ClipProposal(
            _stable_id("clip", scope, len(self.clips), vector_path.to_dict()),
            vector_path,
            fill_rule,
            parents,
            scope,
        )

    def _direct_color(
        self, state: _GraphicsState, operator: str, operands: tuple[Any, ...]
    ) -> _GraphicsState:
        space: ColorSpace = (
            "DeviceGray"
            if operator in {"G", "g"}
            else "DeviceRGB"
            if operator in {"RG", "rg"}
            else "DeviceCMYK"
        )
        expected = {"DeviceGray": 1, "DeviceRGB": 3, "DeviceCMYK": 4}[space]
        color = Color(space, self._numbers(operands, expected))
        if operator[0].isupper():
            return replace(
                state,
                style=replace(state.style, stroke_color=color),
                stroke_space=space,
            )
        return replace(
            state,
            style=replace(state.style, fill_color=color),
            fill_space=space,
        )

    def _selected_color(
        self, state: _GraphicsState, operator: str, operands: tuple[Any, ...]
    ) -> _GraphicsState:
        stroke = operator[0].isupper()
        space = state.stroke_space if stroke else state.fill_space
        if space not in {"DeviceGray", "DeviceRGB", "DeviceCMYK"}:
            raise _Malformed(f"unsupported selected color space {space}")
        expected = {"DeviceGray": 1, "DeviceRGB": 3, "DeviceCMYK": 4}[space]
        color = Color(space, self._numbers(operands, expected))
        if stroke:
            return replace(state, style=replace(state.style, stroke_color=color))
        return replace(state, style=replace(state.style, fill_color=color))

    def _ext_gstate(
        self,
        state: _GraphicsState,
        operands: tuple[Any, ...],
        resources: GraphicsResources,
    ) -> tuple[_GraphicsState, str | None]:
        self._arity(operands, 1)
        name = _name(operands[0])
        value = _resource(resources.ext_gstates, name)
        if value is None:
            raise _Malformed(f"missing ExtGState {name}")
        normalized = {str(key).lstrip("/"): item for key, item in value.items()}
        unsupported = sorted(key for key in normalized if key not in {"ca", "CA", "BM"})
        if unsupported:
            return state, f"unsupported transparency features: {unsupported!r}"
        blend = str(normalized.get("BM", "Normal")).lstrip("/")
        if blend != "Normal":
            return state, f"unsupported blend mode {blend}"
        fill_opacity = _number(normalized.get("ca", state.style.fill_opacity))
        stroke_opacity = _number(normalized.get("CA", state.style.stroke_opacity))
        if not 0 <= fill_opacity.fraction <= 1 or not 0 <= stroke_opacity.fraction <= 1:
            raise _Malformed("opacity must be in 0..1")
        return (
            replace(
                state,
                style=replace(
                    state.style,
                    blend_mode=blend,
                    fill_opacity=fill_opacity,
                    stroke_opacity=stroke_opacity,
                ),
            ),
            None,
        )

    def _form(
        self,
        index: int,
        operation: PDFOperator,
        resources: GraphicsResources,
        state: _GraphicsState,
        scope: tuple[str, ...],
        active_forms: tuple[int, ...],
        depth: int,
        type3: bool,
    ) -> None:
        self._arity(operation.operands, 1)
        name = _name(operation.operands[0])
        form = _resource(resources.forms, name)
        if form is None:
            self._dispose(index, operation, "unsupported", f"missing Form XObject {name}", scope)
            return
        identity = id(form)
        if identity in active_forms:
            self._dispose(index, operation, "limit", f"cyclic Form XObject {name}", scope)
            return
        if depth >= self.limits.max_xobject_depth:
            self._dispose(index, operation, "limit", "Form XObject recursion limit reached", scope)
            return
        nested_state = replace(state, ctm=_compose(state.ctm, form.matrix))
        nested_scope = (*scope, f"form:{name}@{index}")
        self.process(
            form.operators,
            form.resources or resources,
            nested_state,
            nested_scope,
            active_forms=(*active_forms, identity),
            depth=depth + 1,
            type3=type3,
        )
        self._consume(index, operation, scope)

    def _require_path_capacity(self, path: _PathBuilder, additions: int) -> None:
        if len(path.segments) + additions > self.limits.max_path_segments:
            raise _PathLimit("path segment limit exceeded")

    def _numbers(self, operands: tuple[Any, ...], count: int) -> tuple[Rational, ...]:
        self._arity(operands, count)
        return tuple(_number(value) for value in operands)

    def _integer_operand(self, operands: tuple[Any, ...]) -> int:
        self._arity(operands, 1)
        value = operands[0]
        if isinstance(value, bool) or not isinstance(value, int):
            raise _Malformed("operand must be an integer")
        return value

    def _arity(self, operands: tuple[Any, ...], count: int) -> None:
        if len(operands) != count:
            raise _Malformed(f"expected {count} operands, got {len(operands)}")

    def _consume(self, index: int, operation: PDFOperator, scope: tuple[str, ...]) -> None:
        self.dispositions.append(
            OperatorDisposition(index, operation.name, "consumed", "lowered", scope)
        )

    def _dispose(
        self,
        index: int,
        operation: PDFOperator,
        status: Literal["unsupported", "malformed", "limit"],
        reason: str,
        scope: tuple[str, ...],
    ) -> None:
        self.dispositions.append(OperatorDisposition(index, operation.name, status, reason, scope))


class _Malformed(ValueError):
    pass


class _PathLimit(ValueError):
    pass


def lower_pdf_graphics(
    operators: tuple[PDFOperator, ...],
    *,
    resources: GraphicsResources | None = None,
    annotations: tuple[AnnotationEvidence, ...] = (),
    initial_transform: AffineTransform | None = None,
    limits: LoweringLimits | None = None,
) -> LoweringResult:
    """Lower one supplied page stream; unsupported content remains bounded."""

    lowerer = _Lowerer(limits or LoweringLimits())
    transform = initial_transform or AffineTransform.identity()
    state = _GraphicsState(transform, GraphicsStyle())
    lowerer.process(operators, resources or GraphicsResources(), state, ("page",))
    lowerer.annotations(annotations, transform)
    return LoweringResult(
        tuple(lowerer.proposals),
        tuple(lowerer.clips),
        tuple(lowerer.dispositions),
        tuple(lowerer.annotation_dispositions),
    )


def lower_type3_char_procedure(
    procedure: Type3CharProcedure,
    *,
    initial_transform: AffineTransform | None = None,
    limits: LoweringLimits | None = None,
) -> Type3LoweringResult:
    """Lower a Type3 CharProc while retaining its immutable procedure evidence."""

    lowerer = _Lowerer(limits or LoweringLimits())
    transform = initial_transform or AffineTransform.identity()
    scope = ("type3", procedure.font_id, procedure.glyph_name)
    lowerer.process(
        procedure.operators,
        procedure.resources,
        _GraphicsState(transform, GraphicsStyle()),
        scope,
        type3=True,
    )
    result = LoweringResult(
        tuple(
            replace(
                proposal,
                payload={
                    **proposal.payload,
                    "type3_evidence_sha256": procedure.evidence_sha256,
                    "type3_font_id": procedure.font_id,
                    "type3_glyph_name": procedure.glyph_name,
                },
            )
            for proposal in lowerer.proposals
        ),
        tuple(lowerer.clips),
        tuple(lowerer.dispositions),
        (),
    )
    return Type3LoweringResult(
        procedure.font_id,
        procedure.glyph_name,
        procedure.evidence_sha256,
        lowerer.type3_metrics,
        result,
    )


def _number(value: Any) -> Rational:
    if isinstance(value, Rational):
        return value
    if isinstance(value, Fraction):
        return Rational(value.numerator, value.denominator)
    if isinstance(value, bool):
        raise _Malformed("boolean is not a PDF number")
    if isinstance(value, int):
        return Rational(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _Malformed("PDF number must be finite")
        fraction = Fraction(str(value))
        return Rational(fraction.numerator, fraction.denominator)
    raise _Malformed(f"unsupported PDF numeric operand {type(value).__name__}")


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise _Malformed("PDF name operand must be a non-empty string")
    return value.lstrip("/")


def _add(left: Rational, right: Rational) -> Rational:
    value = left.fraction + right.fraction
    return Rational(value.numerator, value.denominator)


def _compose(outer: AffineTransform, inner: AffineTransform) -> AffineTransform:
    oa, ob, oc, od, oe, of = (
        outer.a.fraction,
        outer.b.fraction,
        outer.c.fraction,
        outer.d.fraction,
        outer.e.fraction,
        outer.f.fraction,
    )
    ia, ib, ic, id_, ie, if_ = (
        inner.a.fraction,
        inner.b.fraction,
        inner.c.fraction,
        inner.d.fraction,
        inner.e.fraction,
        inner.f.fraction,
    )
    return AffineTransform(
        Rational.from_value(oa * ia + oc * ib),
        Rational.from_value(ob * ia + od * ib),
        Rational.from_value(oa * ic + oc * id_),
        Rational.from_value(ob * ic + od * id_),
        Rational.from_value(oa * ie + oc * if_ + oe),
        Rational.from_value(ob * ie + od * if_ + of),
    )


def _stable_id(kind: str, *parts: Any) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"{kind}-{sha256(payload).hexdigest()[:20]}"


def _resource(resources: dict[str, Any], name: str) -> Any | None:
    if name in resources:
        return resources[name]
    return resources.get(f"/{name}")
