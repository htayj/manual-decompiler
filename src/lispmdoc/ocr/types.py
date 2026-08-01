"""Engine-neutral, literal OCR evidence types.

All geometry is expressed in canonical integer micropoints with a top-left
origin.  The types deliberately retain the evidence supplied by an OCR engine;
they do not attempt to correct, normalize, or rewrite recognized text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lispmdoc.evidence import Artifact, ArtifactStore


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}-{sha256(payload).hexdigest()[:16]}"


def _json_value(value: Any) -> Any:
    """Convert supported evidence values into deterministic JSON values."""
    if isinstance(value, Mapping):
        return {str(key): _json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class BBox:
    """A half-open bounding box: ``[x0, y0, x1, y1]`` in micropoints."""

    x0: int
    y0: int
    x1: int
    y1: int

    def __post_init__(self) -> None:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("bbox must have x1 >= x0 and y1 >= y0")

    def to_dict(self) -> dict[str, int]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}

    @classmethod
    def union(cls, boxes: Sequence[BBox]) -> BBox | None:
        if not boxes:
            return None
        return cls(
            min(box.x0 for box in boxes),
            min(box.y0 for box in boxes),
            max(box.x1 for box in boxes),
            max(box.y1 for box in boxes),
        )


@dataclass(frozen=True, slots=True)
class Alternative:
    """An unselected engine-provided reading for a text unit."""

    text: str
    confidence: float | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"text": self.text}
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.source is not None:
            result["source"] = self.source
        return result


def _validate_confidence(value: float | None) -> None:
    if value is not None and not 0.0 <= value <= 1.0:
        raise ValueError("confidence must be between 0.0 and 1.0")


@dataclass(frozen=True, slots=True)
class OCRToken:
    id: str
    text: str
    bbox: BBox | None
    confidence: float | None = None
    alternatives: tuple[Alternative, ...] = ()
    native_id: str | None = None

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"id": self.id, "text": self.text}
        if self.bbox is not None:
            result["bbox"] = self.bbox.to_dict()
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.alternatives:
            result["alternatives"] = [item.to_dict() for item in self.alternatives]
        if self.native_id is not None:
            result["native_id"] = self.native_id
        return result


@dataclass(frozen=True, slots=True)
class OCRSpan:
    id: str
    text: str
    bbox: BBox | None
    tokens: tuple[OCRToken, ...] = ()
    confidence: float | None = None
    alternatives: tuple[Alternative, ...] = ()
    native_id: str | None = None

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"id": self.id, "text": self.text}
        if self.bbox is not None:
            result["bbox"] = self.bbox.to_dict()
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.alternatives:
            result["alternatives"] = [item.to_dict() for item in self.alternatives]
        if self.native_id is not None:
            result["native_id"] = self.native_id
        result["tokens"] = [token.to_dict() for token in self.tokens]
        return result


@dataclass(frozen=True, slots=True)
class OCRLine:
    id: str
    text: str
    bbox: BBox | None
    spans: tuple[OCRSpan, ...] = ()
    confidence: float | None = None
    alternatives: tuple[Alternative, ...] = ()
    reading_order: int = 0
    native_id: str | None = None

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "id": self.id,
            "text": self.text,
            "reading_order": self.reading_order,
            "spans": [span.to_dict() for span in self.spans],
        }
        if self.bbox is not None:
            result["bbox"] = self.bbox.to_dict()
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.alternatives:
            result["alternatives"] = [item.to_dict() for item in self.alternatives]
        if self.native_id is not None:
            result["native_id"] = self.native_id
        return result


@dataclass(frozen=True, slots=True)
class OCRRegion:
    id: str
    kind: str
    bbox: BBox | None
    lines: tuple[OCRLine, ...] = ()
    confidence: float | None = None
    alternatives: tuple[Alternative, ...] = ()
    reading_order: int = 0
    language: str | None = None
    orientation_degrees: int | None = None
    native_id: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "id": self.id,
            "kind": self.kind,
            "reading_order": self.reading_order,
            "lines": [line.to_dict() for line in self.lines],
        }
        if self.bbox is not None:
            result["bbox"] = self.bbox.to_dict()
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.alternatives:
            result["alternatives"] = [item.to_dict() for item in self.alternatives]
        if self.language is not None:
            result["language"] = self.language
        if self.orientation_degrees is not None:
            result["orientation_degrees"] = self.orientation_degrees
        if self.native_id is not None:
            result["native_id"] = self.native_id
        return result


@dataclass(frozen=True, slots=True)
class EngineEvidence:
    """Lossless native evidence, represented as JSON-compatible data."""

    engine: str
    engine_version: str | None
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"engine": self.engine, "data": _json_value(self.data)}
        if self.engine_version is not None:
            result["engine_version"] = self.engine_version
        return result


@dataclass(frozen=True, slots=True)
class OCRPage:
    """Normalized result for one physical page from one OCR engine."""

    page_id: str
    width: int
    height: int
    engine: str
    regions: tuple[OCRRegion, ...]
    evidence: EngineEvidence
    language: str | None = None
    orientation_degrees: int | None = None
    # Exact bytes supplied by the engine before normalization.  They are kept
    # out of canonical JSON and can be placed in the artifact store on demand.
    native_output: bytes | None = None
    native_output_media_type: str | None = None

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("page width and height must be positive")
        if self.native_output is None and self.native_output_media_type is not None:
            raise ValueError("native_output_media_type requires native_output bytes")
        if self.native_output is not None and not self.native_output_media_type:
            raise ValueError("native output requires a media type")

    @property
    def native_output_sha256(self) -> str | None:
        return sha256(self.native_output).hexdigest() if self.native_output is not None else None

    def store_native_output(self, store: ArtifactStore) -> Artifact | None:
        """Retain exact adapter output in the shared content-addressed store."""
        if self.native_output is None or self.native_output_media_type is None:
            return None
        return store.put_bytes(
            self.native_output, media_type=self.native_output_media_type, role="native-ocr-output"
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "page_id": self.page_id,
            "width": self.width,
            "height": self.height,
            "engine": self.engine,
            "regions": [region.to_dict() for region in self.regions],
            "evidence": self.evidence.to_dict(),
        }
        if self.language is not None:
            result["language"] = self.language
        if self.orientation_degrees is not None:
            result["orientation_degrees"] = self.orientation_degrees
        if self.native_output is not None:
            result["native_output_sha256"] = self.native_output_sha256
            result["native_output_byte_size"] = len(self.native_output)
            result["native_output_media_type"] = self.native_output_media_type
        return result


@dataclass(frozen=True, slots=True)
class PDFTextRun:
    """Literal embedded-PDF text provided by an extractor or test fixture."""

    text: str
    bbox: BBox | None = None
    confidence: float | None = 1.0
    native_id: str | None = None

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence)


@dataclass(frozen=True, slots=True)
class OCRRequest:
    page_id: str
    width: int
    height: int
    pdf_path: str | None = None
    pdf_page_number: int = 0
    image_path: str | None = None
    image_width_px: int | None = None
    image_height_px: int | None = None
    language: str | None = "eng"
    embedded_text_runs: tuple[PDFTextRun, ...] = ()
    options: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("page width and height must be positive")
        if (self.image_width_px is None) != (self.image_height_px is None):
            raise ValueError("image width and height must be supplied together")
        if self.image_width_px is not None and (
            self.image_width_px <= 0 or self.image_height_px is None or self.image_height_px <= 0
        ):
            raise ValueError("image pixel dimensions must be positive")


def make_id(prefix: str, *parts: object) -> str:
    """Create a deterministic evidence identifier for adapter implementations."""
    return _stable_id(prefix, *parts)
