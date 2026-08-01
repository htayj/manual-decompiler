"""Conservative lossless extraction of simple page-sized PDF image XObjects."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
from pypdf import PdfReader

from lispmdoc.hashing import sha256_file


@dataclass(frozen=True, slots=True)
class PageImageExtraction:
    """A success or explicit non-applicability disposition."""

    value: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        decoded: object = json.loads(
            json.dumps(self.value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        if not isinstance(decoded, dict):
            raise ValueError("page image extraction did not encode as a JSON object")
        return {str(key): item for key, item in decoded.items()}


def extract_simple_page_image(
    source: str | Path,
    page_number: int,
    output_directory: str | Path,
) -> PageImageExtraction:
    """Extract a single page-sized image without rendering when safely possible.

    A page qualifies only when its content stream contains one image invocation
    plus graphics-state/matrix operators, the image has no masks, and its
    placement covers the crop box within half a source pixel. pypdf's decoded
    image export is pixel-lossless; the original compressed stream is not
    claimed to be preserved.
    """

    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        raise ValueError("page_number must be a positive 1-based integer")
    source_path = Path(source)
    reader = PdfReader(str(source_path), strict=False)
    if reader.is_encrypted:
        return _not_applicable(page_number, "encrypted PDF")
    if page_number > len(reader.pages):
        raise IndexError(f"page {page_number} exceeds PDF page count {len(reader.pages)}")
    page = reader.pages[page_number - 1]
    if page.get("/Annots"):
        return _not_applicable(page_number, "page annotations require separate preservation")
    contents = page.get_contents()
    operations = contents.operations if contents is not None else []
    allowed = {b"q", b"Q", b"cm", b"Do"}
    unsupported = sorted(
        operator.decode("latin-1", errors="replace")
        for _operands, operator in operations
        if operator not in allowed
    )
    if unsupported:
        return _not_applicable(page_number, "page has non-image drawing operators", unsupported)
    invocations = [operands for operands, operator in operations if operator == b"Do"]
    if len(invocations) != 1 or not invocations[0]:
        return _not_applicable(page_number, "page must invoke exactly one XObject")
    name = str(invocations[0][0])
    resources = _object(page.get("/Resources", {}))
    xobjects = _object(resources.get("/XObject", {})) if isinstance(resources, dict) else {}
    if not isinstance(xobjects, dict) or name not in xobjects:
        return _not_applicable(page_number, "invoked XObject is missing from page resources")
    reference = xobjects[name]
    xobject = _object(reference)
    if not isinstance(xobject, dict) or str(xobject.get("/Subtype")) != "/Image":
        return _not_applicable(page_number, "invoked XObject is not an image")
    if xobject.get("/SMask") is not None or xobject.get("/Mask") is not None:
        return _not_applicable(page_number, "masked image extraction requires composition")
    placement = _image_placement(operations, name)
    if placement is None:
        return _not_applicable(page_number, "image placement matrix is ambiguous")
    crop = page.cropbox
    crop_box = (
        float(crop.left),
        float(crop.bottom),
        float(crop.right),
        float(crop.top),
    )
    width_px = int(xobject.get("/Width", 0) or 0)
    height_px = int(xobject.get("/Height", 0) or 0)
    if width_px <= 0 or height_px <= 0:
        return _not_applicable(page_number, "image dimensions are invalid")
    tolerance_x = max(0.5 * (crop_box[2] - crop_box[0]) / width_px, 1e-6)
    tolerance_y = max(0.5 * (crop_box[3] - crop_box[1]) / height_px, 1e-6)
    if not _covers_crop(placement, crop_box, tolerance_x, tolerance_y):
        return _not_applicable(
            page_number, "image does not cover crop box within half a source pixel"
        )
    try:
        extracted = page.images[name]
    except Exception as error:
        return _not_applicable(
            page_number,
            f"pypdf cannot losslessly decode this image: {type(error).__name__}",
        )
    suffix = Path(extracted.name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".jp2", ".tif", ".tiff"}:
        return _not_applicable(
            page_number, f"unsupported extracted image encoding: {suffix or 'none'}"
        )
    output_root = Path(output_directory)
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / f"p{page_number:06d}{suffix}"
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    temporary.write_bytes(extracted.data)
    temporary.replace(output_path)
    try:
        with Image.open(output_path) as image:
            image.verify()
        with Image.open(output_path) as image:
            actual_size = image.size
            mode = image.mode
            image_format = image.format
    except Exception as error:
        output_path.unlink(missing_ok=True)
        return _not_applicable(
            page_number, f"extracted image failed validation: {type(error).__name__}"
        )
    if actual_size != (width_px, height_px):
        output_path.unlink(missing_ok=True)
        return _not_applicable(
            page_number, "decoded image dimensions differ from XObject dimensions"
        )
    return PageImageExtraction(
        {
            "encoded_stream_preserved": False,
            "format": image_format,
            "height_px": height_px,
            "lossless_kind": "decoded-pixel-lossless",
            "mode": mode,
            "object_id": getattr(reference, "idnum", None),
            "page_number": page_number,
            "path": output_path.name,
            "placement_box_pdf_points": list(placement),
            "sha256": sha256_file(output_path),
            "status": "extracted",
            "width_px": width_px,
        }
    )


def _not_applicable(
    page_number: int, reason: str, unsupported_operators: list[str] | None = None
) -> PageImageExtraction:
    return PageImageExtraction(
        {
            "page_number": page_number,
            "reason": reason,
            "status": "not-applicable",
            "unsupported_operators": unsupported_operators or [],
        }
    )


def _object(value: Any) -> Any:
    return value.get_object() if hasattr(value, "get_object") else value


def _image_placement(
    operations: list[tuple[list[Any], bytes]], name: str
) -> tuple[float, float, float, float] | None:
    stack: list[tuple[float, float, float, float, float, float]] = []
    current = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    placement: tuple[float, float, float, float] | None = None
    for operands, operator in operations:
        if operator == b"q":
            stack.append(current)
        elif operator == b"Q":
            if not stack:
                return None
            current = stack.pop()
        elif operator == b"cm":
            if len(operands) != 6:
                return None
            matrix = (
                float(operands[0]),
                float(operands[1]),
                float(operands[2]),
                float(operands[3]),
                float(operands[4]),
                float(operands[5]),
            )
            current = _multiply(current, matrix)
        elif operator == b"Do" and operands and str(operands[0]) == name:
            corners = (
                _apply(current, 0, 0),
                _apply(current, 1, 0),
                _apply(current, 0, 1),
                _apply(current, 1, 1),
            )
            xs = [point[0] for point in corners]
            ys = [point[1] for point in corners]
            placement = (min(xs), min(ys), max(xs), max(ys))
    return placement


def _multiply(
    left: tuple[float, float, float, float, float, float],
    right: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a, b, c, d, e, f = left
    g, h, i, j, k, final = right
    return (
        a * g + b * h,
        a * i + b * j,
        c * g + d * h,
        c * i + d * j,
        e * g + f * h + k,
        e * i + f * j + final,
    )


def _apply(
    transform: tuple[float, float, float, float, float, float], x: float, y: float
) -> tuple[float, float]:
    a, b, c, d, e, f = transform
    return a * x + c * y + e, b * x + d * y + f


def _covers_crop(
    placement: tuple[float, float, float, float],
    crop: tuple[float, float, float, float],
    tolerance_x: float,
    tolerance_y: float,
) -> bool:
    return (
        abs(placement[0] - crop[0]) <= tolerance_x
        and abs(placement[2] - crop[2]) <= tolerance_x
        and abs(placement[1] - crop[1]) <= tolerance_y
        and abs(placement[3] - crop[3]) <= tolerance_y
    )
