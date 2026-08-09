"""Evidence-only native-PDF object proposals for Wave-2 inventory pages.

These proposals are deliberately not benchmark truth.  They preserve two raw
text-extraction witnesses, a source-page raster, and mechanical warnings for a
later contained review.  They never invoke OCR or infer table/diagram meaning.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import os
import shutil
import stat
import subprocess
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from xml.etree import ElementTree

from pypdf import PdfReader

from lispmdoc.preprocess.render import (
    RenderBackendUnavailableError,
    _seal_verified_executable,
    render_pdf,
)

from .wave2 import (
    CandidateManual,
    Wave2InventoryError,
    _pdf_page_count,
    _safe_relative_path,
    load_inventory,
    read_contained_regular,
)

NATIVE_PDF_PROPOSAL_VERSION = "lispmdoc-native-pdf-evidence-proposal-1"


class NativePdfProposalError(ValueError):
    """A native-PDF evidence proposal cannot be safely constructed."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_new(path: Path, data: bytes, *, mode: int = 0o644) -> None:
    """Write a new regular file only; proposal workspaces never overwrite."""

    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError as error:
        raise NativePdfProposalError(f"proposal artifact already exists: {path}") from error
    with os.fdopen(descriptor, "wb", closefd=True) as output:
        output.write(data)


def _write_json_new(path: Path, value: object) -> None:
    _write_new(path, _evidence_json_bytes(value) + b"\n")


def _evidence_json_bytes(value: object) -> bytes:
    """Deterministic JSON which preserves renderer numeric evidence unchanged."""

    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _binary_bytes(executable: str) -> tuple[dict[str, object], bytes]:
    resolved = Path(executable).resolve(strict=True)
    try:
        descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise NativePdfProposalError(f"cannot descriptor-read executable: {executable}") from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise NativePdfProposalError(f"executable is not a regular file: {executable}")
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            content = source.read()
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        raise
    return (
        {"sha256": _sha256(content), "byte_size": len(content)},
        content,
    )


def _binary_identity(executable: str) -> dict[str, object]:
    return _binary_bytes(executable)[0]


def _snapshot_tool(workspace: Path, source_executable: str, name: str) -> str:
    """Copy a regular executable before version probing or inference uses it."""

    identity, content = _binary_bytes(source_executable)
    tools = workspace / "tools"
    tools.mkdir(exist_ok=True)
    target = tools / name
    _write_new(target, content, mode=0o500)
    metadata = target.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise NativePdfProposalError("tool snapshot must be a regular non-symlink file")
    if stat.S_IMODE(metadata.st_mode) != 0o500:
        raise NativePdfProposalError("tool snapshot mode was not controlled")
    if _binary_identity(target.as_posix())["sha256"] != identity["sha256"]:
        raise NativePdfProposalError("tool snapshot digest drifted")
    return target.as_posix()


def _tool_record(
    executable: str,
    logical_executable: str,
    execution_fd: int | None = None,
    binary: Mapping[str, object] | None = None,
) -> dict[str, object]:
    try:
        completed = subprocess.run(
            [executable, "-v"],
            text=False,
            capture_output=True,
            check=False,
            pass_fds=(execution_fd,) if execution_fd is not None else (),
        )
    except OSError as error:
        raise NativePdfProposalError(f"cannot run tool version probe: {executable}") from error
    return {
        "binary": dict(binary) if binary is not None else _binary_identity(executable),
        "version_argv": [logical_executable, "-v"],
        "version_returncode": completed.returncode,
        "version_stderr": completed.stderr.decode("utf-8", errors="surrogateescape"),
        "version_stdout": completed.stdout.decode("utf-8", errors="surrogateescape"),
    }


def _sealed_execution(executable: str, expected_sha256: str) -> tuple[int, str]:
    """Use the renderer's descriptor-read, digest-bound sealing primitive."""

    try:
        sealed = _seal_verified_executable(Path(executable), expected_sha256)
    except RenderBackendUnavailableError as error:
        raise NativePdfProposalError(str(error)) from error
    return sealed.descriptor, sealed.execution_path


def _text(value: object) -> str:
    """Preserve pypdf's callback scalar representation without text cleanup."""

    return value if isinstance(value, str) else repr(value)


def _stable_pdf_object(value: object) -> object:
    """Serialize pypdf font evidence without its reader-instance identity."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    idnum = getattr(value, "idnum", None)
    generation = getattr(value, "generation", None)
    if isinstance(idnum, int) and isinstance(generation, int):
        return {"indirect_object": {"generation": generation, "idnum": idnum}}
    if isinstance(value, Mapping):
        return {
            str(key): _stable_pdf_object(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_pdf_object(item) for item in value]
    raise NativePdfProposalError(f"unsupported pypdf font object: {type(value).__name__}")


def _matrix(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [repr(item) for item in value]
    return [repr(value)]


def _number(value: object) -> float | None:
    if not isinstance(value, (str, int, float)):
        return None
    try:
        return float(value)  # Geometry checks are derived, raw strings stay in evidence.
    except (TypeError, ValueError):
        return None


def _box_from_visitor(item: Mapping[str, object]) -> dict[str, object] | None:
    matrix = item["text_matrix"]
    ctm = item["ctm"]
    if not isinstance(matrix, list) or not isinstance(ctm, list) or len(matrix) < 6 or len(ctm) < 6:
        return None
    tm_a, tm_b, tm_c, tm_d, tm_e, tm_f = (_number(value) for value in matrix[:6])
    cm_a, cm_b, cm_c, cm_d, cm_e, cm_f = (_number(value) for value in ctm[:6])
    size = _number(item["font_size"])
    text = item["text"]
    if None in (
        tm_a,
        tm_b,
        tm_c,
        tm_d,
        tm_e,
        tm_f,
        cm_a,
        cm_b,
        cm_c,
        cm_d,
        cm_e,
        cm_f,
        size,
    ) or not isinstance(text, str):
        return None
    assert tm_e is not None and tm_f is not None and cm_a is not None and cm_b is not None
    assert cm_c is not None and cm_d is not None and cm_e is not None and cm_f is not None
    assert size is not None
    # Composition is explicit and derived only for a review overlay; both raw
    # callback matrices remain in the trace and no glyph extent is claimed.
    x = tm_e * cm_a + tm_f * cm_c + cm_e
    y = tm_e * cm_b + tm_f * cm_d + cm_f
    return {
        "geometry_kind": "visitor-baseline-from-raw-ctm-and-text-matrix",
        "height_pt": repr(size),
        "text": text,
        "width_pt": repr(max(size, 0.0) * len(text) * 0.6),
        "x_pt": repr(x),
        "y_pt": repr(y),
    }


def _pypdf_evidence(reader: PdfReader, page_index: int, directory: Path) -> dict[str, object]:
    page = reader.pages[page_index]
    visitors: list[dict[str, object]] = []

    def visitor(text: object, cm: object, tm: object, font_dict: object, font_size: object) -> None:
        visitors.append(
            {
                "ctm": _matrix(cm),
                "font": _stable_pdf_object(font_dict),
                "font_size": repr(font_size),
                "text": _text(text),
                "text_matrix": _matrix(tm),
            }
        )

    try:
        extracted = page.extract_text(visitor_text=visitor)
        extraction_error: str | None = None
    except Exception as error:  # pypdf has document-specific failure types.
        extracted = None
        extraction_error = f"{type(error).__name__}: {error}"
    trace_path = directory / f"p{page_index + 1:06d}-visitor.json"
    _write_json_new(
        trace_path,
        {
            "callback_contract": "pypdf.PageObject.extract_text(visitor_text)",
            "coordinate_space": "pypdf callback CTM/text-matrix values as returned",
            "extraction_error": extraction_error,
            "extracted_text": extracted,
            "visitor_calls": visitors,
        },
    )
    content_path = directory / f"p{page_index + 1:06d}-content-stream.bin"
    try:
        contents = page.get_contents()
        content = b"" if contents is None else contents.get_data()
        content_error: str | None = None
    except Exception as error:
        content = b""
        content_error = f"{type(error).__name__}: {error}"
    _write_new(content_path, content)
    boxes = [box for item in visitors if (box := _box_from_visitor(item)) is not None]
    return {
        "boxes": boxes,
        "content_stream": {
            "error": content_error,
            "path": content_path.relative_to(directory.parent).as_posix(),
            "sha256": _sha256(content),
            "byte_size": len(content),
        },
        "extraction_error": extraction_error,
        "extracted_text": extracted,
        "raw_text_sequence": [item["text"] for item in visitors],
        "trace": {
            "path": trace_path.relative_to(directory.parent).as_posix(),
            "sha256": _sha256(trace_path.read_bytes()),
        },
    }


def _poppler_words(raw: bytes) -> list[dict[str, str]]:
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as error:
        raise NativePdfProposalError("pdftotext bbox output was not XML") from error
    words: list[dict[str, str]] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "word":
            continue
        required = ("xMin", "yMin", "xMax", "yMax")
        if any(attribute not in element.attrib for attribute in required):
            raise NativePdfProposalError("pdftotext bbox word lacks a coordinate")
        words.append(
            {
                "text": element.text or "",
                "x_max": element.attrib["xMax"],
                "x_min": element.attrib["xMin"],
                "y_max": element.attrib["yMax"],
                "y_min": element.attrib["yMin"],
            }
        )
    return words


def _run_poppler(
    executable: str,
    snapshot: Path,
    page_number: int,
    directory: Path,
    workspace: Path,
    execution_fd: int,
) -> dict[str, object]:
    output = directory / f"p{page_number:06d}-bbox-layout.html"
    argv = [
        executable,
        "-bbox-layout",
        "-f",
        str(page_number),
        "-l",
        str(page_number),
        snapshot.as_posix(),
        output.as_posix(),
    ]
    completed = subprocess.run(
        argv, text=False, capture_output=True, check=False, pass_fds=(execution_fd,)
    )
    stderr_path = directory / f"p{page_number:06d}-pdftotext.stderr"
    stdout_path = directory / f"p{page_number:06d}-pdftotext.stdout"
    _write_new(stderr_path, completed.stderr)
    _write_new(stdout_path, completed.stdout)
    if completed.returncode != 0 or not output.is_file():
        raise NativePdfProposalError(f"pdftotext failed for page {page_number}")
    raw = output.read_bytes()
    return {
        "command": {
            "argv": [
                "tools/pdftotext",
                "-bbox-layout",
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                snapshot.relative_to(workspace).as_posix(),
                output.relative_to(workspace).as_posix(),
            ],
            "returncode": completed.returncode,
            "stderr": {
                "path": stderr_path.relative_to(directory.parent).as_posix(),
                "sha256": _sha256(completed.stderr),
            },
            "stdout": {
                "path": stdout_path.relative_to(directory.parent).as_posix(),
                "sha256": _sha256(completed.stdout),
            },
        },
        "raw": {
            "path": output.relative_to(directory.parent).as_posix(),
            "sha256": _sha256(raw),
            "byte_size": len(raw),
        },
        "words": _poppler_words(raw),
    }


def _page_bounds(page: Any) -> tuple[float, float, float, float]:
    box = page.mediabox
    return tuple(float(box[index]) for index in range(4))  # type: ignore[return-value]


def _overlap(
    one: tuple[float, float, float, float], two: tuple[float, float, float, float]
) -> bool:
    return max(one[0], two[0]) < min(one[2], two[2]) and max(one[1], two[1]) < min(one[3], two[3])


def _findings(
    *,
    pypdf: Mapping[str, object],
    poppler: Mapping[str, object],
    bounds: tuple[float, float, float, float],
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    pypdf_sequence = pypdf["raw_text_sequence"]
    poppler_words = poppler["words"]
    assert isinstance(pypdf_sequence, list) and isinstance(poppler_words, list)
    if not any(isinstance(text, str) and text.strip() for text in pypdf_sequence):
        findings.append({"code": "missing-pypdf-text", "severity": "warning"})
    if not any(word["text"].strip() for word in poppler_words if isinstance(word, dict)):
        findings.append({"code": "missing-poppler-text", "severity": "warning"})
    poppler_sequence = [word["text"] for word in poppler_words if isinstance(word, dict)]
    if pypdf_sequence != poppler_sequence:
        findings.append({"code": "raw-reading-order-disagreement", "severity": "review"})
    boxes: list[tuple[float, float, float, float]] = []
    for index, word in enumerate(poppler_words):
        assert isinstance(word, dict)
        values = tuple(_number(word[key]) for key in ("x_min", "y_min", "x_max", "y_max"))
        if any(value is None for value in values):
            findings.append(
                {"code": "unparseable-poppler-box", "index": index, "severity": "warning"}
            )
            continue
        x_min, y_min, x_max, y_max = values
        assert x_min is not None and y_min is not None and x_max is not None and y_max is not None
        box = (x_min, y_min, x_max, y_max)
        if (
            box[0] > box[2]
            or box[1] > box[3]
            or box[0] < bounds[0]
            or box[1] < bounds[1]
            or box[2] > bounds[2]
            or box[3] > bounds[3]
        ):
            findings.append(
                {"code": "poppler-box-out-of-page", "index": index, "severity": "review"}
            )
        if any(_overlap(box, earlier) for earlier in boxes):
            findings.append({"code": "poppler-box-overlap", "index": index, "severity": "review"})
        boxes.append(box)
    pypdf_boxes = pypdf["boxes"]
    assert isinstance(pypdf_boxes, list)
    for index, box in enumerate(pypdf_boxes):
        assert isinstance(box, dict)
        x, y = _number(box["x_pt"]), _number(box["y_pt"])
        if (
            x is None
            or y is None
            or x < bounds[0]
            or x > bounds[2]
            or y < bounds[1]
            or y > bounds[3]
        ):
            findings.append(
                {"code": "pypdf-baseline-out-of-page", "index": index, "severity": "review"}
            )
    return findings


def _render_records(
    snapshot: Path,
    workspace: Path,
    selected_pages: Iterable[int],
    backend_override: Mapping[str, str],
) -> tuple[dict[str, object], dict[int, dict[str, object]]]:
    result = render_pdf(
        snapshot,
        workspace / "render",
        dpi=300,
        pages=tuple(selected_pages),
        backend_override=backend_override,
    )
    manifest = result.manifest.to_dict()
    artifact_prefix = result.artifact_directory.relative_to(workspace).as_posix()
    records: dict[int, dict[str, object]] = {}
    for raw_record in manifest["pages"]:
        assert isinstance(raw_record, dict)
        record = json.loads(json.dumps(raw_record))
        for key in ("image", "source_render", "ocr_helper_render"):
            image = record.get(key)
            if isinstance(image, dict) and isinstance(image.get("path"), str):
                image["path"] = f"{artifact_prefix}/{image['path']}"
        rendered_png = workspace / str(record["source_render"]["path"])
        record["renderer_command"] = {
            "argv": [
                backend_override["identity_executable"],
                "-png",
                "-singlefile",
                "-r",
                "300",
                "-f",
                str(record["page_number"]),
                "-l",
                str(record["page_number"]),
                snapshot.relative_to(workspace).as_posix(),
                rendered_png.with_suffix("").relative_to(workspace).as_posix(),
            ]
        }
        records[int(record["page_number"])] = record
    backend = manifest["backend"]
    assert isinstance(backend, dict)
    renderer = dict(backend)
    renderer["binary"] = {"sha256": backend_override["executable_sha256"]}
    renderer["render_manifest"] = result.manifest_path.relative_to(workspace).as_posix()
    return renderer, records


def _review_svg(
    *,
    boxes: list[Mapping[str, object]],
    render: Mapping[str, object],
    color: str,
    approximate: bool,
) -> str:
    image = render["source_render"]
    assert isinstance(image, dict)
    width, height = int(image["width_px"]), int(image["height_px"])
    scale = 300.0 / 72.0
    shapes: list[str] = []
    for order, box in enumerate(boxes, start=1):
        x = _number(box.get("x_min", box.get("x_pt")))
        y = _number(box.get("y_min", box.get("y_pt")))
        xmax = _number(box.get("x_max"))
        ymax = _number(box.get("y_max"))
        if x is None or y is None:
            continue
        w = (xmax - x) if xmax is not None else _number(box.get("width_pt"))
        h = (ymax - y) if ymax is not None else _number(box.get("height_pt"))
        if w is None or h is None:
            continue
        px, py, pw, ph = x * scale, height - (y + h) * scale, w * scale, h * scale
        dash = ' stroke-dasharray="5 3"' if approximate else ""
        shapes.append(
            f'<rect x="{px!r}" y="{py!r}" width="{pw!r}" height="{ph!r}" '
            f'fill="none" stroke="{color}"{dash}/>'
            f'<text x="{px!r}" y="{py!r}" fill="{color}">{order}</text>'
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        + "".join(shapes)
        + "</svg>"
    )


def _write_review_project(workspace: Path, pages: list[dict[str, object]]) -> None:
    review = workspace / "review"
    review.mkdir()
    assets = review / "assets"
    assets.mkdir()
    rows: list[str] = []
    review_pages: list[dict[str, object]] = []
    for item in pages:
        render = item["render"]
        pypdf = item["pypdf"]
        poppler = item["poppler"]
        assert isinstance(render, dict) and isinstance(pypdf, dict) and isinstance(poppler, dict)
        image = render["source_render"]
        assert isinstance(image, dict)
        basename = f"{item['manual_id']}-p{item['source_page_number']:06d}"
        render_path = image["path"]
        assert isinstance(render_path, str)
        render_bytes = read_contained_regular(workspace, render_path, "render asset")
        if _sha256(render_bytes) != image["sha256"]:
            raise NativePdfProposalError("render asset digest drifted before review copy")
        image_path = f"assets/{basename}.png"
        _write_new(assets / f"{basename}.png", render_bytes)
        poppler_boxes = poppler["words"]
        pypdf_boxes = pypdf["boxes"]
        assert isinstance(poppler_boxes, list) and isinstance(pypdf_boxes, list)
        poppler_svg = _review_svg(
            boxes=[cast(Mapping[str, object], box) for box in poppler_boxes],
            render=render,
            color="#167ac6",
            approximate=False,
        )
        pypdf_svg = _review_svg(
            boxes=[cast(Mapping[str, object], box) for box in pypdf_boxes],
            render=render,
            color="#bc3b28",
            approximate=True,
        )
        _write_new(review / f"{basename}-poppler.svg", poppler_svg.encode())
        _write_new(review / f"{basename}-pypdf.svg", pypdf_svg.encode())
        rows.append(
            "<section><h2>"
            + html.escape(basename)
            + "</h2><p>Blue: Poppler word boxes/order; red dashed: pypdf visitor-baseline "
            + "approximations/order.</p>"
            + f'<div class="grid"><div class="sheet"><img src="{html.escape(image_path)}">'
            + f'<img class="overlay" src="{basename}-poppler.svg"></div>'
            + f'<div class="sheet"><img src="{html.escape(image_path)}">'
            + f'<img class="overlay" src="{basename}-pypdf.svg"></div></div></section>'
        )
        review_pages.append(
            {
                "id": basename,
                "render": image_path,
                "poppler_overlay": f"{basename}-poppler.svg",
                "pypdf_overlay": f"{basename}-pypdf.svg",
            }
        )
    _write_json_new(
        review / "review-project.json",
        {
            "schema_version": NATIVE_PDF_PROPOSAL_VERSION,
            "pages": review_pages,
            "purpose": "localhost evidence review only; no acceptance state",
        },
    )
    document = (
        "<!doctype html><meta charset=utf-8><title>Native PDF evidence proposal</title>"
        "<style>body{font-family:sans-serif}.grid{display:grid;grid-template-columns:1fr 1fr;"
        "gap:1rem}.sheet{position:relative;overflow:auto}.sheet img{max-width:100%;display:block}"
        ".sheet .overlay{position:absolute;left:0;top:0}section{border-top:1px solid #bbb}</style>"
        "<h1>Native-PDF object evidence</h1><p>This project is evidence-only. It does not "
        "accept text, layout, reading order, tables, or diagrams.</p>" + "".join(rows)
    )
    _write_new(review / "index.html", document.encode("utf-8"))


def _inventory_files(workspace: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace).as_posix()
        if relative == "raw-inventory.json":
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise NativePdfProposalError(f"proposal workspace contains a symlink: {relative}")
        if stat.S_ISREG(metadata.st_mode):
            content = path.read_bytes()
            files.append({"path": relative, "sha256": _sha256(content), "byte_size": len(content)})
    return files


def _new_workspace(root: Path, relative_root: str, name: str) -> Path:
    """Create a contained workspace without traversing an existing symlink."""

    current = root
    for component in Path(relative_root).parts:
        current = current / component
        if current.exists() or current.is_symlink():
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise NativePdfProposalError("workspace root contains a symlink or non-directory")
        else:
            current.mkdir()
    workspace = current / name
    if workspace.exists() or workspace.is_symlink():
        raise NativePdfProposalError(
            f"proposal workspace already exists: {workspace.relative_to(root)}"
        )
    workspace.mkdir()
    return workspace


def _verify_snapshot(path: Path, expected_sha256: str, expected_size: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise NativePdfProposalError(f"cannot read verified snapshot: {path}") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise NativePdfProposalError(f"snapshot is not a regular file: {path}")
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            content = source.read()
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        raise
    if len(content) != expected_size or _sha256(content) != expected_sha256:
        raise NativePdfProposalError(f"snapshot identity drifted: {path.name}")
    return content


def _validate_proposal(value: Mapping[str, object]) -> None:
    """Apply the strict root/page contract before binding workspace evidence."""

    expected_root = {
        "schema_version",
        "disposition",
        "inventory_sha256",
        "source_snapshots",
        "pdftotext",
        "pages",
        "review_project",
    }
    if set(value) != expected_root:
        raise NativePdfProposalError("proposal root does not match its strict schema")
    pages = value["pages"]
    if not isinstance(pages, list) or not pages:
        raise NativePdfProposalError("proposal requires pages")
    expected_page = {
        "manual_id",
        "source_page_index",
        "source_page_number",
        "page_class",
        "composition_tags",
        "semantic_assertions",
        "page_bounds_pt",
        "render",
        "renderer_command",
        "renderer",
        "pypdf",
        "poppler",
        "comparison",
        "findings",
        "limitations",
    }
    for page in pages:
        if not isinstance(page, dict) or set(page) != expected_page:
            raise NativePdfProposalError("proposal page does not match its strict schema")
        if page["semantic_assertions"] != []:
            raise NativePdfProposalError("native-PDF proposal must not assert semantics")


@dataclass(frozen=True, slots=True)
class NativePdfProposalResult:
    workspace: Path
    proposal_path: Path
    proposal_sha256: str
    pages: int


def build_native_pdf_proposal(
    root: Path,
    *,
    inventory_path: str = "config/benchmarks/wave2-representative-candidates.json",
    workspace_root: str = "work/wave2-native-pdf-proposals",
) -> NativePdfProposalResult:
    """Build one no-overwrite evidence workspace for native-object inventory pages."""

    if root.is_symlink() or not root.is_dir():
        raise NativePdfProposalError("root must be a non-symlink directory")
    root = root.resolve()
    try:
        inventory, inventory_bytes = load_inventory(root, inventory_path)
    except Wave2InventoryError as error:
        raise NativePdfProposalError(str(error)) from error
    native_manuals = tuple(
        manual for manual in inventory.manuals if manual.truth.origin == "native-pdf-objects"
    )
    if not native_manuals:
        raise NativePdfProposalError("inventory has no native-pdf-objects candidates")
    workspace_relative = _safe_relative_path(workspace_root, "workspace root")
    sources: list[tuple[CandidateManual, bytes]] = []
    for manual in native_manuals:
        content = read_contained_regular(root, manual.source_path, "source PDF")
        if _sha256(content) != manual.source_sha256 or len(content) != manual.source_byte_size:
            raise NativePdfProposalError(f"source PDF identity drifted: {manual.manual_id}")
        if _pdf_page_count(content, manual.source_path) != manual.page_count:
            raise NativePdfProposalError(f"source PDF page count drifted: {manual.manual_id}")
        sources.append((manual, content))
    plan_seed = _evidence_json_bytes(
        {
            "inventory_sha256": _sha256(inventory_bytes),
            "manuals": [
                {
                    "manual_id": manual.manual_id,
                    "source_sha256": manual.source_sha256,
                    "pages": [page.page_index for page in manual.pages],
                }
                for manual, _ in sources
            ],
        }
    )
    workspace = _new_workspace(root, workspace_relative, f"proposal-{_sha256(plan_seed)[:20]}")
    (workspace / "input").mkdir()
    (workspace / "pypdf").mkdir()
    (workspace / "poppler").mkdir()
    ambient_poppler = shutil.which("pdftotext")
    if ambient_poppler is None:
        raise NativePdfProposalError("pdftotext is required for a second native-text witness")
    renderer_name = next(
        (name for name in ("pdftoppm", "pdftocairo") if shutil.which(name) is not None), None
    )
    if renderer_name is None:
        raise NativePdfProposalError("a Poppler PNG renderer is required for native-PDF evidence")
    ambient_renderer = shutil.which(renderer_name)
    assert ambient_renderer is not None
    poppler = _snapshot_tool(workspace, ambient_poppler, "pdftotext")
    renderer_executable = _snapshot_tool(workspace, ambient_renderer, renderer_name)
    poppler_binary = _binary_identity(poppler)
    renderer_binary = _binary_identity(renderer_executable)
    poppler_digest = poppler_binary["sha256"]
    renderer_digest = renderer_binary["sha256"]
    assert isinstance(poppler_digest, str) and isinstance(renderer_digest, str)
    poppler_fd, poppler_exec = _sealed_execution(poppler, poppler_digest)
    try:
        return _build_native_pdf_proposal_workspace(
            workspace=workspace,
            inventory_path=inventory_path,
            inventory_bytes=inventory_bytes,
            sources=sources,
            poppler_binary=poppler_binary,
            poppler_digest=poppler_digest,
            poppler_fd=poppler_fd,
            poppler_exec=poppler_exec,
            renderer_binary=renderer_binary,
            renderer_digest=renderer_digest,
            renderer_executable=renderer_executable,
            renderer_name=renderer_name,
        )
    finally:
        with suppress(OSError):
            os.close(poppler_fd)


def _build_native_pdf_proposal_workspace(
    *,
    workspace: Path,
    inventory_path: str,
    inventory_bytes: bytes,
    sources: list[tuple[CandidateManual, bytes]],
    poppler_binary: Mapping[str, object],
    poppler_digest: str,
    poppler_fd: int,
    poppler_exec: str,
    renderer_binary: Mapping[str, object],
    renderer_digest: str,
    renderer_executable: str,
    renderer_name: str,
) -> NativePdfProposalResult:
    """Finish a workspace while the caller owns the sealed pdftotext FD."""

    poppler_record = _tool_record(
        poppler_exec, "tools/pdftotext", poppler_fd, poppler_binary
    )
    renderer_fd, renderer_exec = _sealed_execution(renderer_executable, renderer_digest)
    try:
        renderer_record = _tool_record(
            renderer_exec,
            f"tools/{renderer_name}",
            renderer_fd,
            renderer_binary,
        )
    finally:
        with suppress(OSError):
            os.close(renderer_fd)
    version_output = renderer_record["version_stderr"] or renderer_record["version_stdout"]
    renderer_evidence = renderer_record["binary"]
    assert isinstance(version_output, str)
    assert isinstance(renderer_evidence, dict) and isinstance(renderer_evidence.get("sha256"), str)
    renderer_override = {
        "executable": renderer_executable,
        "executable_sha256": renderer_evidence["sha256"],
        "identity_executable": f"tools/{renderer_name}",
        "name": renderer_name,
        "version": version_output.splitlines()[0] if version_output.splitlines() else "unknown",
    }
    _write_json_new(
        workspace / "plan.json",
        {
            "schema_version": NATIVE_PDF_PROPOSAL_VERSION,
            "disposition": "evidence-only-no-ocr",
            "inventory_path": inventory_path,
            "inventory_sha256": _sha256(inventory_bytes),
            "native_manuals": [
                {
                    "manual_id": manual.manual_id,
                    "source_path": manual.source_path,
                    "source_sha256": manual.source_sha256,
                    "source_byte_size": manual.source_byte_size,
                    "pages": [page.page_index for page in manual.pages],
                }
                for manual, _ in sources
            ],
            "pdftotext": poppler_record,
            "renderer": renderer_record,
        },
    )
    proposals: list[dict[str, object]] = []
    for manual, content in sources:
        snapshot = workspace / "input" / f"{manual.manual_id}-{manual.source_sha256}.pdf"
        _write_new(snapshot, content)
        snapshot_content = _verify_snapshot(snapshot, manual.source_sha256, manual.source_byte_size)
        selected = [page.page_index + 1 for page in manual.pages]
        renderer, rendered = _render_records(snapshot, workspace, selected, renderer_override)
        _verify_snapshot(snapshot, manual.source_sha256, manual.source_byte_size)
        reader = PdfReader(io.BytesIO(snapshot_content))
        for candidate in manual.pages:
            number = candidate.page_index + 1
            pypdf = _pypdf_evidence(reader, candidate.page_index, workspace / "pypdf")
            poppler_page = _run_poppler(
                poppler_exec, snapshot, number, workspace / "poppler", workspace, poppler_fd
            )
            _verify_snapshot(snapshot, manual.source_sha256, manual.source_byte_size)
            page = reader.pages[candidate.page_index]
            bounds = _page_bounds(page)
            findings = _findings(pypdf=pypdf, poppler=poppler_page, bounds=bounds)
            render = rendered[number]
            proposals.append(
                {
                    "manual_id": manual.manual_id,
                    "source_page_index": candidate.page_index,
                    "source_page_number": number,
                    "page_class": candidate.page_class,
                    "composition_tags": list(candidate.tags),
                    "semantic_assertions": [],
                    "page_bounds_pt": [repr(value) for value in bounds],
                    "render": render,
                    "renderer_command": render["renderer_command"],
                    "renderer": renderer,
                    "pypdf": pypdf,
                    "poppler": poppler_page,
                    "comparison": {
                        "comparison_policy": (
                            "raw callback sequence versus raw XML word sequence; "
                            "no text normalization"
                        ),
                        "pypdf_sequence_sha256": _sha256(
                            _evidence_json_bytes(pypdf["raw_text_sequence"])
                        ),
                        "poppler_sequence_sha256": _sha256(
                            _evidence_json_bytes(
                                [
                                    word["text"]
                                    for word in cast(list[dict[str, str]], poppler_page["words"])
                                ]
                            )
                        ),
                        "raw_sequences_equal": pypdf["raw_text_sequence"]
                        == [
                            word["text"]
                            for word in cast(list[dict[str, str]], poppler_page["words"])
                        ],
                    },
                    "findings": findings,
                    "limitations": [
                        "pypdf visitor callback order and baseline approximations are not "
                        "authoritative visual reading order or glyph boxes",
                        "Poppler bbox words are a separate extraction witness, not accepted "
                        "semantic/layout truth",
                        "No table, diagram, or glyph semantics are inferred.",
                    ],
                }
            )
    _write_review_project(workspace, proposals)
    proposal = {
        "schema_version": NATIVE_PDF_PROPOSAL_VERSION,
        "disposition": "evidence-only-no-ocr",
        "inventory_sha256": _sha256(inventory_bytes),
        "source_snapshots": [
            {
                "manual_id": manual.manual_id,
                "path": f"input/{manual.manual_id}-{manual.source_sha256}.pdf",
                "sha256": manual.source_sha256,
                "byte_size": manual.source_byte_size,
            }
            for manual, _ in sources
        ],
        "pdftotext": poppler_record,
        "pages": sorted(
            proposals,
            key=lambda page: (str(page["manual_id"]), cast(int, page["source_page_index"])),
        ),
        "review_project": "review/review-project.json",
    }
    _validate_proposal(proposal)
    _write_json_new(workspace / "proposal.json", proposal)
    inventory_files = _inventory_files(workspace)
    _write_json_new(
        workspace / "raw-inventory.json",
        {"schema_version": NATIVE_PDF_PROPOSAL_VERSION, "files": inventory_files},
    )
    proposal_bytes = (workspace / "proposal.json").read_bytes()
    return NativePdfProposalResult(
        workspace, workspace / "proposal.json", _sha256(proposal_bytes), len(proposals)
    )
