"""Exact geometry primitives used by the top-left-origin LMDOC coordinate space."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, TypeAlias

MicroPoint: TypeAlias = int


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer number of micropoints")
    return value


@dataclass(frozen=True, slots=True, order=True)
class Point:
    """A point in integer micropoints (one thousandth of a PDF point)."""

    x: MicroPoint
    y: MicroPoint

    def __post_init__(self) -> None:
        _integer(self.x, "x")
        _integer(self.y, "y")

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Point:
        return cls(x=value["x"], y=value["y"])


@dataclass(frozen=True, slots=True)
class Box:
    """A non-empty, half-open rectangle ``[x0, y0, x1, y1]``.

    The right and bottom edges are excluded. Adjacent boxes therefore do not
    overlap, which is important for cells, clipping, and hit testing.
    """

    x0: MicroPoint
    y0: MicroPoint
    x1: MicroPoint
    y1: MicroPoint

    def __post_init__(self) -> None:
        for name in ("x0", "y0", "x1", "y1"):
            _integer(getattr(self, name), name)
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("half-open boxes must have positive width and height")

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def area(self) -> int:
        return self.width * self.height

    def contains_point(self, point: Point) -> bool:
        return self.x0 <= point.x < self.x1 and self.y0 <= point.y < self.y1

    def contains_box(self, other: Box) -> bool:
        return (
            self.x0 <= other.x0
            and self.y0 <= other.y0
            and other.x1 <= self.x1
            and other.y1 <= self.y1
        )

    def intersects(self, other: Box) -> bool:
        return (
            self.x0 < other.x1 and other.x0 < self.x1 and self.y0 < other.y1 and other.y0 < self.y1
        )

    def intersection(self, other: Box) -> Box | None:
        if not self.intersects(other):
            return None
        return Box(
            max(self.x0, other.x0),
            max(self.y0, other.y0),
            min(self.x1, other.x1),
            min(self.y1, other.y1),
        )

    def to_dict(self) -> dict[str, int]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Box:
        return cls(**{name: value[name] for name in ("x0", "y0", "x1", "y1")})


@dataclass(frozen=True, slots=True)
class Rational:
    """A normalized exact scalar for source-coordinate affine transforms."""

    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        _integer(self.numerator, "numerator")
        _integer(self.denominator, "denominator")
        if self.denominator == 0:
            raise ValueError("rational denominator cannot be zero")
        fraction = Fraction(self.numerator, self.denominator)
        object.__setattr__(self, "numerator", fraction.numerator)
        object.__setattr__(self, "denominator", fraction.denominator)

    @property
    def fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    def to_dict(self) -> dict[str, int]:
        return {"numerator": self.numerator, "denominator": self.denominator}

    @classmethod
    def from_value(cls, value: Rational | int | Fraction | dict[str, Any]) -> Rational:
        if isinstance(value, cls):
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return cls(value)
        if isinstance(value, Fraction):
            return cls(value.numerator, value.denominator)
        if isinstance(value, dict):
            return cls(value["numerator"], value.get("denominator", 1))
        raise TypeError("rational coefficients must be integers or Rational records")


@dataclass(frozen=True, slots=True)
class AffineTransform:
    """An exact 2D affine transform: ``(x, y) -> (ax + cy + e, bx + dy + f)``.

    Coefficients use rational records rather than JSON floats so a transform
    from PDF points or pixels can always be recorded exactly.
    """

    a: Rational
    b: Rational
    c: Rational
    d: Rational
    e: Rational
    f: Rational

    def __post_init__(self) -> None:
        for name in ("a", "b", "c", "d", "e", "f"):
            object.__setattr__(self, name, Rational.from_value(getattr(self, name)))
        if self.determinant == 0:
            raise ValueError("affine transform must be invertible")

    @classmethod
    def identity(cls) -> AffineTransform:
        return cls(Rational(1), Rational(0), Rational(0), Rational(1), Rational(0), Rational(0))

    @property
    def determinant(self) -> Fraction:
        return self.a.fraction * self.d.fraction - self.b.fraction * self.c.fraction

    def apply_exact(self, x: int | Rational, y: int | Rational) -> tuple[Fraction, Fraction]:
        source_x = Rational.from_value(x).fraction
        source_y = Rational.from_value(y).fraction
        return (
            self.a.fraction * source_x + self.c.fraction * source_y + self.e.fraction,
            self.b.fraction * source_x + self.d.fraction * source_y + self.f.fraction,
        )

    def apply(self, x: int | Rational, y: int | Rational) -> Point:
        transformed_x, transformed_y = self.apply_exact(x, y)
        if transformed_x.denominator != 1 or transformed_y.denominator != 1:
            raise ValueError("transform result is not an integer micropoint")
        return Point(transformed_x.numerator, transformed_y.numerator)

    def inverse(self) -> AffineTransform:
        determinant = self.determinant
        return AffineTransform(
            Rational.from_value(self.d.fraction / determinant),
            Rational.from_value(-self.b.fraction / determinant),
            Rational.from_value(-self.c.fraction / determinant),
            Rational.from_value(self.a.fraction / determinant),
            Rational.from_value(
                (self.c.fraction * self.f.fraction - self.d.fraction * self.e.fraction)
                / determinant
            ),
            Rational.from_value(
                (self.b.fraction * self.e.fraction - self.a.fraction * self.f.fraction)
                / determinant
            ),
        )

    def to_dict(self) -> dict[str, dict[str, int]]:
        return {name: getattr(self, name).to_dict() for name in ("a", "b", "c", "d", "e", "f")}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AffineTransform:
        return cls(
            **{name: Rational.from_value(value[name]) for name in ("a", "b", "c", "d", "e", "f")}
        )
