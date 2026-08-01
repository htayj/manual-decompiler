"""Read-only PDF inventory and deliberately conservative page classification."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .fingerprint import SourceVerification, fingerprint_source, verify_source


class IngestError(RuntimeError):
    """Base exception for ingestion failures with a stable disposition code."""

    code = "ingest-error"


class OptionalPdfDependencyError(IngestError):
    """No usable PDF reader is available in this environment."""

    code = "pdf-backend-unavailable"


class PdfInspectionError(IngestError):
    """The source could not be inspected as a readable PDF."""

    code = "pdf-inspection-failed"


@dataclass(frozen=True, slots=True)
class DocumentInspection:
    """JSON-compatible Stage 1 evidence, never a claim of semantic content."""

    value: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        # Round-trip through JSON gives callers an owned, JSON-only value rather
        # than a mutable reference to our cached nested data.
        return cast(
            dict[str, Any],
            json.loads(json.dumps(self.value, sort_keys=True, separators=(",", ":"))),
        )

    def to_json(self) -> str:
        return json.dumps(self.value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def inspect_pdf(
    path: str | Path, *, collection_root: str | Path | None = None
) -> DocumentInspection:
    """Inspect a PDF without modifying it, then prove its hash still matches.

    PyMuPDF is preferred when installed because it exposes page resources and
    geometry directly.  The command-line fallback uses read-only Poppler/qpdf
    tools.  If neither route is available, callers receive an explicit optional
    dependency error rather than a made-up classification.
    """

    source_path = Path(path)
    before = fingerprint_source(source_path)
    collection_path = _collection_path(source_path, collection_root)
    warnings: list[str] = []
    try:
        raw, backend_warnings = _inspect_with_pymupdf(source_path)
        backend = {"name": "pymupdf", "mode": "python-library"}
        warnings.extend(backend_warnings)
    except ModuleNotFoundError:
        try:
            raw, backend_warnings = _inspect_with_pypdf(source_path)
            backend = {"name": "pypdf", "mode": "python-library"}
            warnings.append("PyMuPDF is unavailable; used pypdf inspection backend.")
            warnings.extend(backend_warnings)
        except ModuleNotFoundError:
            try:
                raw, backend_warnings = _inspect_with_commands(source_path)
            except OptionalPdfDependencyError:
                raise OptionalPdfDependencyError(
                    "PDF inspection needs optional PyMuPDF/pypdf "
                    "(install the project dependencies) or read-only "
                    "qpdf/pdfinfo/pdftotext/pdfimages commands"
                ) from None
            backend = {"name": "poppler-qpdf", "mode": "command-fallback"}
            warnings.append("PyMuPDF and pypdf are unavailable; used read-only command fallback.")
            warnings.extend(backend_warnings)
    except Exception as error:  # pragma: no cover - backend-specific failures
        # An installed but broken Python reader should not silently become a
        # different evidence source; surface the error to the operator.
        raise PdfInspectionError(f"PyMuPDF could not inspect {source_path}: {error}") from error

    after: SourceVerification = verify_source(source_path, before)
    if not after.matches:
        raise PdfInspectionError(
            "source changed while being inspected: "
            f"{source_path}; discard this inspection and retry"
        )
    pages = [_normalise_page(page, index) for index, page in enumerate(raw["pages"], start=1)]
    document_classification = _classify_document(pages)
    result: dict[str, Any] = {
        "backend": backend,
        "classification": document_classification,
        "collection_path": collection_path,
        "disposition": raw.get("disposition", "inspectable"),
        "metadata": _sorted_json_object(raw.get("metadata", {})),
        "page_count": len(pages),
        "pages": pages,
        "schema_version": "lispmdoc-ingest-1",
        "source": before.to_dict(),
        "source_verification": after.to_dict(),
        "status": raw.get("status", "ok"),
        "warnings": sorted(set(warnings)),
    }
    return DocumentInspection(result)


def _collection_path(source: Path, root: str | Path | None) -> str:
    if root is None:
        return source.name
    root_path = Path(root)
    try:
        return source.relative_to(root_path).as_posix()
    except ValueError:
        raise ValueError(f"source {source} is not below collection root {root_path}") from None


def _inspect_with_pymupdf(path: Path) -> tuple[dict[str, Any], list[str]]:
    import fitz  # type: ignore[import-not-found]

    warnings: list[str] = []
    try:
        document = fitz.open(path)
    except Exception as error:
        raise PdfInspectionError(f"unreadable or corrupt PDF {path}: {error}") from error
    try:
        if document.is_encrypted and document.needs_pass:
            return {
                "pages": [],
                "metadata": {},
                "status": "unreadable",
                "disposition": "encrypted",
            }, warnings
        pages: list[dict[str, Any]] = []
        for number, page in enumerate(document, start=1):
            media = page.mediabox
            crop = page.cropbox
            page_area = max(float(page.rect.width * page.rect.height), 1.0)
            text = page.get_text("text")
            image_records: list[dict[str, Any]] = []
            total_image_area = 0.0
            for image in page.get_images(full=True):
                xref, _smask, width, height, bpc, colorspace, _alternate, name, filter_name, *_ = (
                    image
                )
                rectangles = page.get_image_rects(xref)
                area = sum(max(0.0, rect.width * rect.height) for rect in rectangles)
                total_image_area += area
                image_records.append(
                    {
                        "bits_per_component": int(bpc or 0),
                        "colorspace": str(colorspace or "unknown"),
                        "height_px": int(height or 0),
                        "name": str(name or ""),
                        "object_id": int(xref),
                        "placement_count": len(rectangles),
                        "placement_coverage": _number(min(area / page_area, 1.0)),
                        "width_px": int(width or 0),
                        "filter": str(filter_name or "unknown"),
                    }
                )
            try:
                vector_count = len(page.get_drawings())
            except Exception:
                vector_count = None
                warnings.append(f"page {number}: vector operator inventory unavailable")
            annotations = list(page.annots() or [])
            pages.append(
                {
                    "annotations": len(annotations),
                    "crop_box": _box(crop),
                    "embedded_text": _text_evidence(text),
                    "fonts": sorted(
                        {str(font[3]) for font in page.get_fonts(full=True) if len(font) > 3}
                    ),
                    "images": sorted(
                        image_records, key=lambda value: (value["object_id"], value["name"])
                    ),
                    "media_box": _box(media),
                    "page_number": number,
                    "rotation_degrees": int(page.rotation),
                    "vector_operator_count": vector_count,
                }
            )
        metadata = {
            key: value
            for key, value in (document.metadata or {}).items()
            if value not in (None, "")
        }
        return {"pages": pages, "metadata": metadata}, warnings
    finally:
        document.close()


def _inspect_with_pypdf(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Inspect via the project's small pure-Python dependency.

    pypdf deliberately does not render.  Its content-stream inspection gives a
    cheap inventory, while coverage remains ``None`` if the stream cannot be
    interpreted safely; this is preferable to guessing an image's placement.
    """

    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path), strict=False)
    except Exception as error:
        raise PdfInspectionError(f"unreadable or corrupt PDF {path}: {error}") from error
    metadata = {
        str(key).lstrip("/").lower(): str(value)
        for key, value in (reader.metadata or {}).items()
        if value not in (None, "")
    }
    if reader.is_encrypted:
        return {
            "pages": [],
            "metadata": metadata,
            "status": "unreadable",
            "disposition": "encrypted",
        }, []
    warnings: list[str] = []
    pages: list[dict[str, Any]] = []
    try:
        for number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as error:
                text = ""
                warnings.append(
                    f"page {number}: embedded text extraction failed: {type(error).__name__}"
                )
            images, vector_count, stream_warning = _pypdf_page_graphics(page)
            if stream_warning:
                warnings.append(f"page {number}: {stream_warning}")
            resources = _pypdf_object(page.get("/Resources"))
            fonts = _pypdf_fonts(resources)
            annotations = page.get("/Annots")
            pages.append(
                {
                    "annotations": len(annotations) if annotations is not None else 0,
                    "crop_box": _pypdf_box(page.cropbox),
                    "embedded_text": _text_evidence(text),
                    "fonts": fonts,
                    "images": images,
                    "media_box": _pypdf_box(page.mediabox),
                    "page_number": number,
                    "rotation_degrees": int(page.get("/Rotate", 0) or 0),
                    "vector_operator_count": vector_count,
                }
            )
    except Exception as error:
        raise PdfInspectionError(f"pypdf could not inventory {path}: {error}") from error
    return {"pages": pages, "metadata": metadata}, warnings


def _pypdf_object(value: Any) -> Any:
    return value.get_object() if hasattr(value, "get_object") else value


def _pypdf_box(box: Any) -> list[float]:
    return [
        _number(float(box.left)),
        _number(float(box.bottom)),
        _number(float(box.right)),
        _number(float(box.top)),
    ]


def _pypdf_fonts(resources: Any) -> list[str]:
    if not isinstance(resources, dict):
        return []
    fonts = _pypdf_object(resources.get("/Font", {}))
    if not isinstance(fonts, dict):
        return []
    values: set[str] = set()
    for font in fonts.values():
        dictionary = _pypdf_object(font)
        if isinstance(dictionary, dict):
            values.add(str(dictionary.get("/BaseFont") or dictionary.get("/Subtype") or "unknown"))
    return sorted(values)


def _pypdf_page_graphics(page: Any) -> tuple[list[dict[str, Any]], int | None, str | None]:
    resources = _pypdf_object(page.get("/Resources"))
    if not isinstance(resources, dict):
        return [], 0, None
    xobjects = _pypdf_object(resources.get("/XObject", {}))
    image_by_name: dict[str, dict[str, Any]] = {}
    if isinstance(xobjects, dict):
        for name, reference in xobjects.items():
            image = _pypdf_object(reference)
            if not isinstance(image, dict) or str(image.get("/Subtype")) != "/Image":
                continue
            filters = _pypdf_object(image.get("/Filter", "unknown"))
            filter_name = ",".join(map(str, filters)) if isinstance(filters, list) else str(filters)
            indirect = getattr(reference, "idnum", None)
            image_by_name[str(name)] = {
                "bits_per_component": int(image.get("/BitsPerComponent", 0) or 0),
                "colorspace": str(_pypdf_object(image.get("/ColorSpace", "unknown"))),
                "height_px": int(image.get("/Height", 0) or 0),
                "name": str(name),
                "object_id": int(indirect) if indirect is not None else None,
                "placement_count": 0,
                "placement_coverage": 0.0,
                "width_px": int(image.get("/Width", 0) or 0),
                "filter": filter_name,
            }
    try:
        operations = page.get_contents().operations if page.get_contents() is not None else []
    except Exception as error:
        return (
            sorted(image_by_name.values(), key=lambda value: value["name"]),
            None,
            f"content-stream inventory unavailable: {type(error).__name__}",
        )
    ctm_stack: list[tuple[float, float, float, float, float, float]] = []
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    vector_operators = {
        b"m",
        b"l",
        b"c",
        b"v",
        b"y",
        b"re",
        b"S",
        b"s",
        b"f",
        b"F",
        b"f*",
        b"B",
        b"B*",
        b"b",
        b"b*",
        b"n",
    }
    vector_count = 0
    for operands, operator in operations:
        if operator == b"q":
            ctm_stack.append(ctm)
        elif operator == b"Q":
            ctm = ctm_stack.pop() if ctm_stack else ctm
        elif operator == b"cm" and len(operands) == 6:
            matrix = (
                float(operands[0]),
                float(operands[1]),
                float(operands[2]),
                float(operands[3]),
                float(operands[4]),
                float(operands[5]),
            )
            ctm = _matrix_multiply(ctm, matrix)
        elif operator == b"Do" and operands:
            image = image_by_name.get(str(operands[0]))
            if image is not None:
                image["placement_count"] += 1
                image["_area_pt2"] = image.get("_area_pt2", 0.0) + abs(
                    ctm[0] * ctm[3] - ctm[1] * ctm[2]
                )
        elif operator in vector_operators:
            vector_count += 1
    page_area = abs(float(page.cropbox.width) * float(page.cropbox.height))
    records: list[dict[str, Any]] = []
    for image in image_by_name.values():
        area = float(image.pop("_area_pt2", 0.0))
        image["placement_coverage"] = _number(min(area / page_area, 1.0)) if page_area else None
        records.append(image)
    return (
        sorted(records, key=lambda value: (str(value["object_id"]), value["name"])),
        vector_count,
        None,
    )


def _matrix_multiply(
    left: tuple[float, float, float, float, float, float],
    right: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a, b, c, d, e, f = left
    g, h, i, j, k, final_translate = right
    return (
        a * g + b * h,
        a * i + b * j,
        c * g + d * h,
        c * i + d * j,
        e * g + f * h + k,
        e * i + f * j + final_translate,
    )


def _inspect_with_commands(path: Path) -> tuple[dict[str, Any], list[str]]:
    required = ("pdfinfo", "pdftotext", "pdfimages")
    missing = [command for command in required if shutil.which(command) is None]
    if missing:
        raise OptionalPdfDependencyError("missing PDF inspection commands: " + ", ".join(missing))
    warnings: list[str] = []
    check_command = shutil.which("qpdf")
    if check_command is not None:
        checked = _run([check_command, "--check", str(path)])
        if checked.returncode != 0:
            raise PdfInspectionError(
                f"qpdf rejected {path} as corrupt, encrypted, or unreadable: "
                f"{_tool_message(checked)}"
            )
    else:
        warnings.append("qpdf is unavailable; corruption diagnosis is limited to Poppler parsing.")

    info = _run([shutil.which("pdfinfo") or "pdfinfo", str(path)])
    if info.returncode != 0:
        message = _tool_message(info)
        disposition = (
            "encrypted"
            if "encrypted" in message.lower() or "password" in message.lower()
            else "corrupt-or-unreadable"
        )
        raise PdfInspectionError(f"{disposition} PDF {path}: {message}")
    metadata, page_count = _parse_pdfinfo(info.stdout)
    if page_count is None or page_count < 0:
        raise PdfInspectionError(f"pdfinfo did not report a valid page count for {path}")
    text_pages, text_warning = _poppler_text(path, page_count)
    if text_warning:
        warnings.append(text_warning)
    images, image_warning = _poppler_images(path)
    if image_warning:
        warnings.append(image_warning)
    pages: list[dict[str, Any]] = []
    for number in range(1, page_count + 1):
        page_info = _run(
            [
                shutil.which("pdfinfo") or "pdfinfo",
                "-box",
                "-f",
                str(number),
                "-l",
                str(number),
                str(path),
            ]
        )
        if page_info.returncode != 0:
            raise PdfInspectionError(
                f"could not inventory page {number} of {path}: {_tool_message(page_info)}"
            )
        page_metadata, _unused_count = _parse_pdfinfo(page_info.stdout)
        media, crop, rotation = _parse_page_boxes(page_info.stdout)
        page_images = images.get(number, [])
        pages.append(
            {
                "annotations": None,
                "crop_box": crop,
                "embedded_text": _text_evidence(
                    text_pages[number - 1] if number - 1 < len(text_pages) else ""
                ),
                "fonts": [],
                "images": page_images,
                "media_box": media,
                "page_number": number,
                "rotation_degrees": rotation,
                "vector_operator_count": None,
                "page_metadata_evidence": page_metadata,
            }
        )
    return {"pages": pages, "metadata": metadata}, warnings


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, text=True, capture_output=True, check=False, encoding="utf-8", errors="replace"
    )


def _tool_message(result: subprocess.CompletedProcess[str]) -> str:
    return (
        (result.stderr or result.stdout or "unknown PDF tool failure")
        .strip()
        .replace("\n", " ")[:1000]
    )


def _parse_pdfinfo(text: str) -> tuple[dict[str, str], int | None]:
    metadata: dict[str, str] = {}
    page_count: int | None = None
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if key == "Pages":
            if value.isdigit():
                page_count = int(value)
        elif key in {
            "Title",
            "Subject",
            "Keywords",
            "Author",
            "Creator",
            "Producer",
            "CreationDate",
            "ModDate",
            "PDF version",
        }:
            metadata[key.lower().replace(" ", "_")] = value
    return metadata, page_count


def _poppler_text(path: Path, page_count: int) -> tuple[list[str], str | None]:
    result = _run([shutil.which("pdftotext") or "pdftotext", "-enc", "UTF-8", str(path), "-"])
    if result.returncode != 0:
        return [""] * page_count, f"embedded text extraction unavailable: {_tool_message(result)}"
    pages = result.stdout.split("\f")
    if pages and pages[-1] == "":
        pages.pop()
    if len(pages) != page_count:
        return (pages + [""] * max(0, page_count - len(pages)))[:page_count], (
            "embedded text extractor returned "
            f"{len(pages)} page segments for {page_count} PDF pages"
        )
    return pages, None


def _poppler_images(path: Path) -> tuple[dict[int, list[dict[str, Any]]], str | None]:
    result = _run([shutil.which("pdfimages") or "pdfimages", "-list", str(path)])
    if result.returncode != 0:
        return {}, f"image inventory unavailable: {_tool_message(result)}"
    images: dict[int, list[dict[str, Any]]] = {}
    # The stable prefix of Poppler's table is: page num type width height color
    # comp bpc enc.  Later columns vary by Poppler version and are ignored.
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 9 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        try:
            page, ordinal, width, height, components, bpc = (
                int(fields[0]),
                int(fields[1]),
                int(fields[3]),
                int(fields[4]),
                int(fields[6]),
                int(fields[7]),
            )
        except ValueError:
            continue
        record: dict[str, Any] = {
            "bits_per_component": bpc,
            "colorspace": fields[5],
            "components": components,
            "height_px": height,
            "name": f"pdfimages:{ordinal}",
            "object_id": None,
            "placement_count": None,
            "placement_coverage": None,
            "width_px": width,
            "filter": fields[8],
        }
        # x/y ppi follow object id/generation in current Poppler. They are
        # evidence only: images can be transformed or reused, so never treat
        # this as a precise placement without a library backend.
        if len(fields) >= 14:
            try:
                x_ppi, y_ppi = float(fields[12]), float(fields[13])
                if x_ppi > 0 and y_ppi > 0:
                    record["estimated_display_area_pt2"] = _number(
                        (width * 72 / x_ppi) * (height * 72 / y_ppi)
                    )
            except ValueError:
                pass
        images.setdefault(page, []).append(record)
    return images, None


def _parse_page_boxes(text: str) -> tuple[list[float] | None, list[float] | None, int]:
    boxes: dict[str, list[float]] = {}
    rotation = 0
    for line in text.splitlines():
        # Poppler prints either ``MediaBox:`` or ``Page 12 MediaBox:``
        # depending on whether a page range was requested.
        match = re.match(
            r"^(?:Page\s+\d+\s+)?(MediaBox|CropBox):\s*([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)",
            line,
        )
        if match:
            boxes[match.group(1)] = [_number(float(match.group(index))) for index in range(2, 6)]
        rotation_match = re.match(r"^(?:Page\s+\d+\s+)?rot:\s*([\-\d]+)", line)
        if rotation_match:
            rotation = int(rotation_match.group(1))
    return boxes.get("MediaBox"), boxes.get("CropBox"), rotation


def _normalise_page(page: dict[str, Any], expected_number: int) -> dict[str, Any]:
    images = sorted(
        page.get("images", []),
        key=lambda item: (str(item.get("object_id")), str(item.get("name", ""))),
    )
    embedded = page.get("embedded_text", _text_evidence(""))
    media = page.get("media_box")
    crop = page.get("crop_box") or media
    width, height = _box_dimensions(crop or media)
    page_area = width * height if width and height else None
    known_coverage = [
        image["placement_coverage"]
        for image in images
        if image.get("placement_coverage") is not None
    ]
    if page_area:
        estimated = [
            image.get("estimated_display_area_pt2", 0.0) / page_area
            for image in images
            if image.get("estimated_display_area_pt2")
        ]
        coverage = (
            min(sum(known_coverage), 1.0)
            if known_coverage
            else (min(sum(estimated), 1.0) if estimated else None)
        )
    else:
        coverage = min(sum(known_coverage), 1.0) if known_coverage else None
    evidence = {
        "embedded_text_characters": int(embedded.get("non_whitespace_characters", 0)),
        "font_count": len(page.get("fonts", [])),
        "image_count": len(images),
        "image_coverage": _number(coverage) if coverage is not None else None,
        "vector_operator_count": page.get("vector_operator_count"),
    }
    classification = _classify_page(evidence, images, width, height)
    result = {
        "annotations": page.get("annotations"),
        "classification": classification,
        "crop_box": crop,
        "embedded_text": embedded,
        "fonts": page.get("fonts", []),
        "geometry": {
            "height_pt": _number(height) if height is not None else None,
            "width_pt": _number(width) if width is not None else None,
        },
        "images": images,
        "media_box": media,
        "page_number": int(page.get("page_number", expected_number)),
        "rotation_degrees": int(page.get("rotation_degrees", 0)) % 360,
        "vector_operator_count": page.get("vector_operator_count"),
    }
    if "page_metadata_evidence" in page:
        result["page_metadata_evidence"] = _sorted_json_object(page["page_metadata_evidence"])
    return result


def _classify_page(
    evidence: dict[str, Any],
    images: Iterable[dict[str, Any]],
    width: float | None,
    height: float | None,
) -> dict[str, Any]:
    image_list = list(images)
    text = evidence["embedded_text_characters"]
    coverage = evidence["image_coverage"]
    image_count = evidence["image_count"]
    vector_count = evidence["vector_operator_count"]
    colorspaces = [str(image.get("colorspace", "")).lower() for image in image_list]
    bpcs = [int(image.get("bits_per_component", 0) or 0) for image in image_list]
    bilevel = bool(image_list) and all(
        bpc == 1 or color in {"mono", "monochrome"}
        for bpc, color in zip(bpcs, colorspaces, strict=True)
    )
    continuous = any(
        bpc > 1 or color in {"rgb", "cmyk", "gray", "grey", "icc", "iccbased"}
        for bpc, color in zip(bpcs, colorspaces, strict=True)
    )
    has_page_sized_image = coverage is not None and coverage >= 0.60
    # A short embedded string is still meaningful when it accompanies a
    # full-page scan (many historical OCR layers are sparse).  The count is
    # evidence rather than a truth claim; downstream reconciliation decides
    # whether it is usable transcription.
    text_present = text >= 8
    landscape = width is not None and height is not None and width > height * 1.15
    reasons: list[str] = []
    alternatives: list[str] = []

    if text_present and (has_page_sized_image or (image_count and coverage is None)):
        label, confidence = "hybrid", "medium" if coverage is None else "high"
        reasons.extend(["embedded-text-present", "page-image-present"])
    elif has_page_sized_image and not text_present:
        if bilevel and landscape:
            label, confidence = "schematic", "medium"
            reasons.extend(["landscape-bilevel-page-image", "no-trustworthy-embedded-text"])
            alternatives.append("scan-bilevel")
        elif bilevel:
            label, confidence = "scan-bilevel", "high"
            reasons.extend(["page-sized-bilevel-image", "no-trustworthy-embedded-text"])
        elif continuous:
            label, confidence = "photo-or-illustration-dominant", "medium"
            reasons.extend(["page-sized-continuous-tone-image", "no-trustworthy-embedded-text"])
            alternatives.extend(["scan-gray", "scan-color"])
        else:
            label, confidence = "ambiguous", "low"
            reasons.append("page-image-has-unrecognized-sampling")
    elif text_present and (
        evidence["font_count"] > 0
        or (vector_count is not None and vector_count > 0)
        or not image_count
    ):
        label, confidence = "born-digital", "high" if not image_count else "medium"
        reasons.append("embedded-text-without-page-sized-image")
        if image_count:
            alternatives.append("hybrid")
    elif image_count and coverage is None:
        label, confidence = "ambiguous", "low"
        reasons.append("images-present-but-placement-coverage-unavailable")
        alternatives.append("hybrid" if text_present else "scan-gray")
    else:
        label, confidence = "ambiguous", "low"
        reasons.append("insufficient-text-image-and-vector-evidence")
    return {
        "alternatives": sorted(set(alternatives)),
        "confidence": confidence,
        "label": label,
        "reasons": sorted(reasons),
    }


def _classify_document(pages: list[dict[str, Any]]) -> dict[str, Any]:
    labels = [page["classification"]["label"] for page in pages]
    counts = {label: labels.count(label) for label in sorted(set(labels))}
    unambiguous = [label for label in labels if label != "ambiguous"]
    if not pages or not unambiguous:
        label, confidence = "ambiguous", "low"
    elif len(set(unambiguous)) == 1 and unambiguous[0] != "hybrid":
        label = unambiguous[0]
        confidence = "high" if len(unambiguous) == len(labels) else "medium"
    elif "hybrid" in unambiguous or (
        "born-digital" in unambiguous
        and any(page_class != "born-digital" for page_class in unambiguous)
    ):
        label, confidence = "hybrid", "medium"
    else:
        label, confidence = "hybrid", "low"
    return {"confidence": confidence, "label": label, "page_class_counts": counts}


def _text_evidence(text: str) -> dict[str, Any]:
    non_whitespace = sum(not character.isspace() for character in text)
    return {
        "extraction_available": True,
        "non_whitespace_characters": non_whitespace,
        "text_sha256": __import__("hashlib").sha256(text.encode("utf-8")).hexdigest(),
    }


def _box(rectangle: Any) -> list[float]:
    return [
        _number(float(rectangle.x0)),
        _number(float(rectangle.y0)),
        _number(float(rectangle.x1)),
        _number(float(rectangle.y1)),
    ]


def _box_dimensions(box: list[float] | None) -> tuple[float | None, float | None]:
    if box is None or len(box) != 4:
        return None, None
    return abs(float(box[2]) - float(box[0])), abs(float(box[3]) - float(box[1]))


def _number(value: float) -> float:
    # PDF backends expose floats; fixed rounding avoids backend-noise creating
    # needless cache misses while retaining sub-micropoint inspection evidence.
    return round(value, 6)


def _sorted_json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): value[key] for key in sorted(value, key=str)}
