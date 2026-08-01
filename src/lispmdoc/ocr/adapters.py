"""OCR adapter contract and literal, engine-specific implementations."""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .types import (
    BBox,
    EngineEvidence,
    OCRLine,
    OCRPage,
    OCRRegion,
    OCRRequest,
    OCRSpan,
    OCRToken,
    PDFTextRun,
    make_id,
)


class OCRUnavailable(RuntimeError):
    """Raised when an optional OCR engine cannot perform a requested run."""


@dataclass(frozen=True, slots=True)
class Capability:
    engine: str
    available: bool
    status: str
    detail: str | None = None
    version: str | None = None
    supports: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "engine": self.engine,
            "available": self.available,
            "status": self.status,
            "supports": list(self.supports),
        }
        if self.detail is not None:
            result["detail"] = self.detail
        if self.version is not None:
            result["version"] = self.version
        return result


class OCRAdapter(Protocol):
    """A literal-text adapter; implementations must retain native evidence."""

    name: str

    def probe(self) -> Capability: ...

    def recognize(self, request: OCRRequest) -> OCRPage: ...


def _mean_confidence(values: Sequence[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return sum(known) / len(known) if known else None


def _runs_to_page(
    request: OCRRequest,
    runs: Sequence[PDFTextRun],
    version: str | None,
    *,
    geometry_method: str,
    native_output: bytes,
    configuration: dict[str, object],
) -> OCRPage:
    """Make one literal line/region per supplied embedded text run."""
    regions: list[OCRRegion] = []
    for index, run in enumerate(runs):
        # Empty runs are extractor artifacts and carry no text evidence.
        if not run.text:
            continue
        token = OCRToken(
            id=make_id("token", request.page_id, "pdf-text", index, run.text),
            text=run.text,
            bbox=run.bbox,
            confidence=run.confidence,
            native_id=run.native_id,
        )
        span = OCRSpan(
            id=make_id("span", request.page_id, "pdf-text", index, run.text),
            text=run.text,
            bbox=run.bbox,
            tokens=(token,),
            confidence=run.confidence,
            native_id=run.native_id,
        )
        line = OCRLine(
            id=make_id("line", request.page_id, "pdf-text", index, run.text),
            text=run.text,
            bbox=run.bbox,
            spans=(span,),
            confidence=run.confidence,
            reading_order=len(regions),
            native_id=run.native_id,
        )
        regions.append(
            OCRRegion(
                id=make_id("region", request.page_id, "pdf-text", index, run.text),
                kind="text",
                bbox=run.bbox,
                lines=(line,),
                confidence=run.confidence,
                reading_order=len(regions),
                language=request.language,
                native_id=run.native_id,
            )
        )
    return OCRPage(
        page_id=request.page_id,
        width=request.width,
        height=request.height,
        engine="pdf-text",
        regions=tuple(regions),
        language=request.language,
        evidence=EngineEvidence(
            engine="pdf-text",
            engine_version=version,
            data={
                "geometry_method": geometry_method,
                "literal": True,
                "run_count": len(runs),
                "configuration": configuration,
            },
        ),
        native_output=native_output,
        native_output_media_type="application/json",
    )


def _runs_native_bytes(runs: Sequence[PDFTextRun]) -> bytes:
    """Stable bytes for the extractor's literal input, without text rewriting."""
    value = [
        {
            "text": run.text,
            "bbox": run.bbox.to_dict() if run.bbox is not None else None,
            "confidence": run.confidence,
            "native_id": run.native_id,
        }
        for run in runs
    ]
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


class PDFTextAdapter:
    """Extract existing PDF text without treating it as OCR.

    Supplied ``embedded_text_runs`` are preferred so an upstream PDF inspector
    can preserve more exact glyph geometry.  Otherwise this adapter uses an
    optional pypdf extraction pass and records its literal chunks.
    """

    name = "pdf-text"

    def probe(self) -> Capability:
        pypdf = importlib.util.find_spec("pypdf")
        return Capability(
            engine=self.name,
            available=True,
            status="available",
            detail="uses supplied embedded runs" if pypdf is None else "pypdf extraction available",
            supports=("embedded-text", "geometry", "literal-text"),
        )

    def recognize(self, request: OCRRequest) -> OCRPage:
        if request.embedded_text_runs:
            return _runs_to_page(
                request,
                request.embedded_text_runs,
                version=None,
                geometry_method="supplied-upstream",
                native_output=_runs_native_bytes(request.embedded_text_runs),
                configuration={"source": "supplied-upstream", "language": request.language},
            )
        if request.pdf_path is None:
            raise OCRUnavailable("pdf-text needs embedded_text_runs or pdf_path")
        try:
            import pypdf
        except ImportError as exc:
            raise OCRUnavailable("pypdf is required to extract text from pdf_path") from exc

        reader = pypdf.PdfReader(request.pdf_path)
        try:
            page = reader.pages[request.pdf_page_number]
        except IndexError as exc:
            raise ValueError(f"PDF page {request.pdf_page_number} does not exist") from exc
        crop_box = page.cropbox
        left = float(crop_box.left)
        bottom = float(crop_box.bottom)
        right = float(crop_box.right)
        top = float(crop_box.top)
        source_width = right - left
        source_height = top - bottom
        if source_width <= 0 or source_height <= 0:
            raise ValueError("PDF page has invalid crop box")
        rotation = int(page.get("/Rotate", 0) or 0) % 360
        if rotation not in {0, 90, 180, 270}:
            raise OCRUnavailable(f"unsupported PDF page rotation for text geometry: {rotation}")
        runs: list[PDFTextRun] = []

        def visitor(text: str, cm: object, tm: object, _font: object, font_size: object) -> None:
            if not text:
                return
            size = float(font_size) if isinstance(font_size, (int, float)) else 0.0
            text_matrix = _pdf_matrix(tm)
            current_matrix = _pdf_matrix(cm)
            if text_matrix is None or current_matrix is None:
                bbox = None
            else:
                run_width = max(size * 0.5 * max(len(text.rstrip()), 1), 0.01)
                run_height = max(size, 0.01)
                source_points = [
                    _apply_pdf_matrix(
                        current_matrix,
                        *_apply_pdf_matrix(text_matrix, local_x, local_y),
                    )
                    for local_x, local_y in (
                        (0.0, 0.0),
                        (run_width, 0.0),
                        (0.0, run_height),
                        (run_width, run_height),
                    )
                ]
                canonical = [
                    _pdf_point_to_canonical(
                        x,
                        y,
                        left=left,
                        bottom=bottom,
                        right=right,
                        top=top,
                        rotation=rotation,
                        width=request.width,
                        height=request.height,
                    )
                    for x, y in source_points
                ]
                bbox = _bounded_box(canonical, request.width, request.height)
            runs.append(PDFTextRun(text=text, bbox=bbox, native_id=str(len(runs))))

        page.extract_text(visitor_text=visitor)
        return _runs_to_page(
            request,
            runs,
            getattr(pypdf, "__version__", None),
            geometry_method="estimated-from-pdf-text-matrix",
            native_output=_runs_native_bytes(runs),
            configuration={
                "page_number": request.pdf_page_number,
                "pypdf_version": getattr(pypdf, "__version__", None),
                "visitor": "text-matrix-estimated-v1",
            },
        )


def _pdf_matrix(value: object) -> tuple[float, float, float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 6:
        return None
    return tuple(float(value[index]) for index in range(6))  # type: ignore[return-value]


def _apply_pdf_matrix(
    matrix: tuple[float, float, float, float, float, float], x: float, y: float
) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def _pdf_point_to_canonical(
    x: float,
    y: float,
    *,
    left: float,
    bottom: float,
    right: float,
    top: float,
    rotation: int,
    width: int,
    height: int,
) -> tuple[int, int]:
    source_width = right - left
    source_height = top - bottom
    if rotation == 0:
        canonical_x = (x - left) / source_width * width
        canonical_y = (top - y) / source_height * height
    elif rotation == 90:
        canonical_x = (top - y) / source_height * width
        canonical_y = (right - x) / source_width * height
    elif rotation == 180:
        canonical_x = (right - x) / source_width * width
        canonical_y = (y - bottom) / source_height * height
    else:
        canonical_x = (y - bottom) / source_height * width
        canonical_y = (x - left) / source_width * height
    return round(canonical_x), round(canonical_y)


def _bounded_box(points: Sequence[tuple[int, int]], width: int, height: int) -> BBox:
    x0 = max(0, min(point[0] for point in points))
    y0 = max(0, min(point[1] for point in points))
    x1 = min(width, max(point[0] for point in points))
    y1 = min(height, max(point[1] for point in points))
    x0 = min(x0, width - 1)
    y0 = min(y0, height - 1)
    return BBox(x0, y0, max(x0 + 1, x1), max(y0 + 1, y1))


def _tesseract_version(binary: str) -> str | None:
    try:
        output = subprocess.run(
            [binary, "--version"], check=False, capture_output=True, text=True, timeout=10
        ).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return output[0].strip() if output else None


@dataclass(frozen=True, slots=True)
class _TsvWord:
    block: int
    paragraph: int
    line: int
    word: int
    text: str
    confidence: float | None
    bbox: BBox


class TesseractAdapter:
    """Tesseract TSV adapter retaining token geometry and confidence."""

    name = "tesseract"

    def __init__(self, executable: str = "tesseract") -> None:
        self.executable = executable

    def probe(self) -> Capability:
        binary = shutil.which(self.executable)
        if binary is None:
            return Capability(
                engine=self.name,
                available=False,
                status="unavailable",
                detail=f"executable not found: {self.executable}",
                supports=("image", "tsv", "token-geometry"),
            )
        return Capability(
            engine=self.name,
            available=True,
            status="available",
            version=_tesseract_version(binary),
            supports=("image", "tsv", "token-geometry", "confidence"),
        )

    def recognize(self, request: OCRRequest) -> OCRPage:
        if request.image_path is None:
            raise OCRUnavailable("tesseract needs OCRRequest.image_path")
        binary = shutil.which(self.executable)
        if binary is None:
            raise OCRUnavailable(f"tesseract executable not found: {self.executable}")
        image = Path(request.image_path)
        if not image.is_file():
            raise FileNotFoundError(image)
        command = [binary, str(image), "stdout"]
        if request.language:
            command.extend(["-l", request.language])
        for key, value in sorted(request.options.items()):
            command.extend(["-c", f"{key}={value}"])
        command.append("tsv")
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        completed = subprocess.run(command, check=False, capture_output=True, env=environment)
        if completed.returncode != 0:
            raise OCRUnavailable(
                completed.stderr.decode("utf-8", errors="replace").strip()
                or "tesseract failed without diagnostic output"
            )
        try:
            tsv = completed.stdout.decode("utf-8")
        except UnicodeDecodeError as error:
            raise OCRUnavailable("tesseract TSV was not valid UTF-8") from error
        words = self._parse_tsv(tsv, request)
        regions = self._build_regions(words, request)
        return OCRPage(
            page_id=request.page_id,
            width=request.width,
            height=request.height,
            engine=self.name,
            regions=tuple(regions),
            language=request.language,
            evidence=EngineEvidence(
                engine=self.name,
                engine_version=_tesseract_version(binary),
                data={
                    "format": "tsv",
                    "configuration": {
                        "command": command[2:],
                        "executable": binary,
                        "language": request.language,
                        "options": dict(sorted(request.options.items())),
                    },
                },
            ),
            native_output=completed.stdout,
            native_output_media_type="text/tab-separated-values; charset=utf-8",
        )

    @staticmethod
    def _parse_tsv(tsv: str, request: OCRRequest) -> list[_TsvWord]:
        rows = csv.DictReader(tsv.splitlines(), delimiter="\t")
        words: list[_TsvWord] = []
        for row in rows:
            if row.get("level") != "5" or not row.get("text", "").strip():
                continue
            try:
                left, top, width, height = (
                    int(row[key]) for key in ("left", "top", "width", "height")
                )
                block, paragraph, line, word = (
                    int(row[key]) for key in ("block_num", "par_num", "line_num", "word_num")
                )
            except (KeyError, ValueError) as exc:
                raise ValueError("malformed tesseract TSV word row") from exc
            confidence: float | None
            try:
                raw_confidence = float(row.get("conf", "-1"))
                confidence = raw_confidence / 100.0 if raw_confidence >= 0 else None
            except ValueError:
                confidence = None
            words.append(
                _TsvWord(
                    block,
                    paragraph,
                    line,
                    word,
                    row["text"],
                    confidence,
                    TesseractAdapter._pixel_box(left, top, width, height, request),
                )
            )
        return words

    @staticmethod
    def _pixel_box(left: int, top: int, width: int, height: int, request: OCRRequest) -> BBox:
        if request.image_width_px is None or request.image_height_px is None:
            raise OCRUnavailable(
                "tesseract needs image_width_px and image_height_px for canonical geometry"
            )
        image_width, image_height = request.image_width_px, request.image_height_px
        return BBox(
            round(left / image_width * request.width),
            round(top / image_height * request.height),
            round((left + width) / image_width * request.width),
            round((top + height) / image_height * request.height),
        )

    @staticmethod
    def _build_regions(words: Sequence[_TsvWord], request: OCRRequest) -> list[OCRRegion]:
        by_line: dict[tuple[int, int, int], list[_TsvWord]] = defaultdict(list)
        for word in words:
            by_line[(word.block, word.paragraph, word.line)].append(word)
        lines_by_block: dict[int, list[OCRLine]] = defaultdict(list)
        for line_index, key in enumerate(sorted(by_line)):
            line_words = sorted(by_line[key], key=lambda item: item.word)
            tokens = tuple(
                OCRToken(
                    id=make_id("token", request.page_id, "tesseract", key, item.word, item.text),
                    text=item.text,
                    bbox=item.bbox,
                    confidence=item.confidence,
                    native_id=f"{key[0]}:{key[1]}:{key[2]}:{item.word}",
                )
                for item in line_words
            )
            text = " ".join(token.text for token in tokens)
            bbox = BBox.union([token.bbox for token in tokens if token.bbox is not None])
            confidence = _mean_confidence([token.confidence for token in tokens])
            span = OCRSpan(
                id=make_id("span", request.page_id, "tesseract", key, text),
                text=text,
                bbox=bbox,
                tokens=tokens,
                confidence=confidence,
                native_id=f"{key[0]}:{key[1]}:{key[2]}",
            )
            line = OCRLine(
                id=make_id("line", request.page_id, "tesseract", key, text),
                text=text,
                bbox=bbox,
                spans=(span,),
                confidence=confidence,
                reading_order=line_index,
                native_id=f"{key[0]}:{key[1]}:{key[2]}",
            )
            lines_by_block[key[0]].append(line)
        regions: list[OCRRegion] = []
        for region_index, block in enumerate(sorted(lines_by_block)):
            lines = tuple(lines_by_block[block])
            bbox = BBox.union([line.bbox for line in lines if line.bbox is not None])
            regions.append(
                OCRRegion(
                    id=make_id("region", request.page_id, "tesseract", block),
                    kind="text",
                    bbox=bbox,
                    lines=lines,
                    confidence=_mean_confidence([line.confidence for line in lines]),
                    reading_order=region_index,
                    language=request.language,
                    native_id=str(block),
                )
            )
        return regions


class PlaceholderAdapter:
    """Honest contract for a feasibility candidate without a verified adapter."""

    def __init__(self, name: str, module: str, *, install_contract: str | None = None) -> None:
        self.name = name
        self._module = module
        self._install_contract = (
            install_contract or f"install Python module '{module}' and pin its models"
        )

    def probe(self) -> Capability:
        installed = importlib.util.find_spec(self._module) is not None
        detail = (
            "package detected but its output/model contract has not passed local feasibility review"
            if installed
            else f"optional package not installed; {self._install_contract}"
        )
        return Capability(
            engine=self.name,
            available=False,
            status="unavailable",
            detail=detail,
            supports=("capability-reporting", "installation-contract"),
        )

    def recognize(self, request: OCRRequest) -> OCRPage:
        del request
        raise OCRUnavailable(
            f"{self.name} is an explicit placeholder; no normalized adapter has been approved"
        )


class SuryaAdapter(PlaceholderAdapter):
    def __init__(self) -> None:
        super().__init__(
            "surya",
            "surya",
            install_contract=(
                "install a pinned Surya OCR 2 package and local model weights; "
                "record model revision, "
                "device, and preprocessing configuration before enabling"
            ),
        )


class PaddleOCRAdapter(PlaceholderAdapter):
    def __init__(self) -> None:
        super().__init__(
            "paddleocr",
            "paddleocr",
            install_contract=(
                "install pinned paddlepaddle and PaddleOCR/PP-Structure weights locally; "
                "record model "
                "revisions, device, and license review before enabling"
            ),
        )


class YomiTokuAdapter(PlaceholderAdapter):
    def __init__(self) -> None:
        super().__init__("yomitoku", "yomitoku")


def default_adapters() -> tuple[OCRAdapter, ...]:
    """Return the deterministic baseline adapter set without selecting a default."""
    return (
        PDFTextAdapter(),
        TesseractAdapter(),
        SuryaAdapter(),
        PaddleOCRAdapter(),
        YomiTokuAdapter(),
    )


def capability_report(adapters: Sequence[OCRAdapter] | None = None) -> tuple[Capability, ...]:
    """Report optional-engine state in a stable engine-name order."""
    selected = default_adapters() if adapters is None else tuple(adapters)
    return tuple(adapter.probe() for adapter in sorted(selected, key=lambda adapter: adapter.name))


def recognize_subset(adapter: OCRAdapter, requests: Sequence[OCRRequest]) -> tuple[OCRPage, ...]:
    """Run an explicit page subset in caller-supplied physical-page order.

    This is intentionally not a document-wide convenience call: callers must
    construct only the source pages they selected, keeping each native result
    isolated and independently retainable.
    """
    page_ids = tuple(request.page_id for request in requests)
    if len(page_ids) != len(set(page_ids)):
        raise ValueError("OCR subset must not contain duplicate page IDs")
    return tuple(adapter.recognize(request) for request in requests)
