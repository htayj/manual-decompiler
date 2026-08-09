"""Read-only, reproducible page rasterization with exact recorded transforms."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import Image

from lispmdoc.hashing import sha256_bytes, sha256_file
from lispmdoc.ingest.pdf_extract import extract_simple_page_image
from lispmdoc.model.geometry import AffineTransform, Rational

from .operations import PreprocessSettings, canonical_settings_digest, preprocess_image

_PAGE_TOKEN = re.compile(r"^(?P<start>\d+)(?:-(?P<end>\d+)?)?$")
_SCHEMA_VERSION = "lispmdoc-preprocess-2"


class PreprocessError(RuntimeError):
    """Base class for rendering/preprocessing failures."""


class PageSubsetError(PreprocessError, ValueError):
    """A requested 1-based page subset is malformed or outside the document."""


class RenderBackendUnavailableError(PreprocessError):
    """No locally available capability-probed renderer can produce PNG pages."""


class SourceChangedDuringRenderError(PreprocessError):
    """The immutable source fingerprint changed during a render operation."""


class UnsafeOutputRootError(PreprocessError, ValueError):
    """A generated output root could overlap immutable source material."""


@dataclass(slots=True)
class _SealedExecutable:
    """One owned Linux execution descriptor containing verified tool bytes."""

    descriptor: int
    sha256: str
    byte_size: int

    @property
    def execution_path(self) -> str:
        return f"/proc/self/fd/{self.descriptor}"

    def close(self) -> None:
        with suppress(OSError):
            os.close(self.descriptor)


@dataclass(frozen=True, slots=True)
class RenderManifest:
    """Stable JSON-only evidence for one rendered source subset."""

    value: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        decoded: object = json.loads(self.to_json())
        if not isinstance(decoded, dict):  # defensive: ``value`` is a mapping
            raise ValueError("render manifest did not encode as a JSON object")
        return {str(key): item for key, item in decoded.items()}

    def to_json(self) -> str:
        return (
            json.dumps(self.value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        )


@dataclass(frozen=True, slots=True)
class RenderResult:
    """Runtime locations paired with a path-independent :class:`RenderManifest`."""

    manifest: RenderManifest
    artifact_directory: Path
    manifest_path: Path
    cache_reused: bool


def parse_page_subset(
    specification: str | Sequence[int] | None, page_count: int
) -> tuple[int, ...]:
    """Parse 1-based page syntax such as ``1,3-5,8-`` into sorted unique pages."""

    if page_count < 1:
        raise PageSubsetError("a PDF must have at least one page")
    if specification is None or specification == "":
        return tuple(range(1, page_count + 1))
    if not isinstance(specification, str):
        pages = tuple(sorted(set(specification)))
        if any(isinstance(page, bool) or not isinstance(page, int) for page in pages):
            raise PageSubsetError("page subset sequence must contain only integers")
        _validate_page_numbers(pages, page_count)
        return pages
    result: set[int] = set()
    for raw_token in specification.split(","):
        token = raw_token.strip()
        match = _PAGE_TOKEN.fullmatch(token)
        if match is None:
            raise PageSubsetError(f"invalid page subset token: {raw_token!r}")
        start = int(match.group("start"))
        end_text = match.group("end")
        end = page_count if token.endswith("-") else int(end_text) if end_text else start
        if start > end:
            raise PageSubsetError(f"page range start exceeds end: {token!r}")
        result.update(range(start, end + 1))
    pages = tuple(sorted(result))
    _validate_page_numbers(pages, page_count)
    return pages


def probe_render_backend() -> dict[str, str]:
    """Return a versioned, render-capable local backend or fail explicitly.

    Poppler is preferred over optional PyMuPDF because it is available in the
    normal command-line runtime and writes PNG directly without a Python image
    conversion step.  The renderer itself is capability-probed, not inferred
    from a package declaration.
    """

    for name in ("pdftoppm", "pdftocairo"):
        executable = shutil.which(name)
        if executable is None:
            continue
        completed = _run([executable, "-v"])
        if completed.returncode == 0 or completed.stderr or completed.stdout:
            version_line = (completed.stderr or completed.stdout).splitlines()
            return {
                "executable": executable,
                "name": name,
                "version": version_line[0].strip() if version_line else "unknown",
            }
    try:
        import fitz  # type: ignore[import-not-found]
    except ModuleNotFoundError as error:
        raise RenderBackendUnavailableError(
            "no PNG PDF renderer is available; install Poppler (pdftoppm/pdftocairo) "
            "or optional PyMuPDF"
        ) from error
    return {"executable": "python", "name": "pymupdf", "version": str(fitz.VersionBind)}


def render_pdf(
    source: str | Path,
    output_root: str | Path,
    *,
    dpi: int = 300,
    pages: str | Sequence[int] | None = None,
    preprocess_settings: PreprocessSettings | None = None,
    backend_override: Mapping[str, str] | None = None,
) -> RenderResult:
    """Render selected PDF pages into a safe generated root without editing source.

    The cache key is the source SHA-256 plus DPI, page subset, and renderer
    identity. Existing artifacts are reused only after their recorded hashes and
    Pillow-inspected dimensions validate. No deskew, crop, illumination, or
    dewarp adjustment is attempted in this foundation stage.
    """

    if isinstance(dpi, bool) or not isinstance(dpi, int) or dpi <= 0:
        raise ValueError("dpi must be a positive integer")
    source_path = Path(source)
    if not source_path.is_file():
        raise FileNotFoundError(f"PDF source is not a regular file: {source_path}")
    source_fingerprint = _source_fingerprint(source_path)
    settings = preprocess_settings or PreprocessSettings()
    settings_digest = canonical_settings_digest(settings)
    sealed: _SealedExecutable | None = None
    if backend_override:
        backend, sealed = _validated_backend_override(backend_override)
    else:
        backend = probe_render_backend()
    try:
        backend_evidence = _backend_evidence(backend)
        page_infos = _read_pdf_pages(source_path)
        selected = parse_page_subset(pages, len(page_infos))
        root = _safe_output_root(Path(output_root), source_path)
        cache_key = sha256_bytes(
            _canonical_json(
                {
                    "backend": backend_evidence,
                    "dpi": dpi,
                    "pages": list(selected),
                    "preprocess_settings_sha256": settings_digest,
                    "source_sha256": source_fingerprint["sha256"],
                }
            )
        )
        artifact_directory = _safe_child(root, source_fingerprint["sha256"], cache_key)
        page_directory = _safe_child(artifact_directory, "pages")
        manifest_path = _safe_child(artifact_directory, "render-manifest.json")
        if manifest_path.is_file():
            cached = _read_valid_cache(
                manifest_path,
                artifact_directory,
                source_fingerprint,
                backend_evidence,
                dpi,
                selected,
                settings_digest,
            )
            if cached is not None:
                _assert_source_unchanged(source_path, source_fingerprint)
                return RenderResult(cached, artifact_directory, manifest_path, cache_reused=True)

        page_directory.mkdir(parents=True, exist_ok=True)
        helper_directory = _safe_child(artifact_directory, "ocr-helper")
        overlay_directory = _safe_child(artifact_directory, "debug")
        extraction_directory = _safe_child(artifact_directory, "extracted")
        records: list[dict[str, Any]] = []
        for number in selected:
            page_info = page_infos[number - 1]
            filename = f"p{number:06d}.png"
            image_path = _safe_child(page_directory, filename)
            _render_one(source_path, number, dpi, image_path, backend)
            image_evidence = _inspect_png(image_path)
            record = _page_record(
                number=number,
                source_page=page_info,
                dpi=dpi,
                image_relative_path=f"pages/{filename}",
                image_evidence=image_evidence,
            )
            source_to_canonical = AffineTransform.from_dict(
                record["normalization"]["pixel_to_canonical"]
            )
            helper_path = _safe_child(helper_directory, filename)
            overlay_path = _safe_child(overlay_directory, f"p{number:06d}-overlay.png")
            preprocessing = preprocess_image(
                image_path,
                helper_path,
                overlay_path,
                source_to_canonical,
                settings=settings,
            )
            preprocessing["ocr_helper_render"]["path"] = f"ocr-helper/{filename}"
            preprocessing["debug_overlay"]["path"] = f"debug/p{number:06d}-overlay.png"
            extraction = extract_simple_page_image(
                source_path, number, extraction_directory
            ).to_dict()
            if extraction["status"] == "extracted":
                extraction["path"] = f"extracted/{extraction['path']}"
            record["source_render"] = dict(record["image"])
            record["ocr_helper_render"] = preprocessing["ocr_helper_render"]
            record["lossless_page_image"] = extraction
            record["preprocessing"] = preprocessing
            record["normalization"]["helper_pixels_to_source_pixels"] = preprocessing[
                "helper_pixels_to_source_pixels"
            ]
            record["normalization"]["helper_pixels_to_canonical"] = preprocessing[
                "helper_pixels_to_canonical"
            ]
            records.append(record)
        _assert_source_unchanged(source_path, source_fingerprint)
        manifest = RenderManifest(
            {
                "backend": backend_evidence,
                "dpi": dpi,
                "normalization": {
                    "applied": any(record["preprocessing"]["applied"] for record in records),
                    "source_and_ocr_helper_renders_are_separate": True,
                },
                "pages": records,
                "preprocess_settings": settings.to_dict(),
                "preprocess_settings_sha256": settings_digest,
                "schema_version": _SCHEMA_VERSION,
                "selected_pages": list(selected),
                "source": source_fingerprint,
            }
        )
        _write_manifest(manifest_path, manifest)
        return RenderResult(manifest, artifact_directory, manifest_path, cache_reused=False)
    finally:
        if sealed is not None:
            sealed.close()


def _validated_backend_override(
    backend: Mapping[str, str],
) -> tuple[dict[str, Any], _SealedExecutable]:
    """Accept only a fully specified Poppler backend chosen by a caller.

    This is intentionally narrower than capability probing: provenance callers
    can supply a copied, digest-bound executable and know that render commands
    will invoke that exact pathname. The default probe remains unchanged.
    """

    legacy_required = {"executable", "name", "version"}
    required = legacy_required | {"executable_sha256", "identity_executable"}
    if set(backend) not in (legacy_required, required) or any(
        not isinstance(backend[key], str) for key in backend
    ):
        raise RenderBackendUnavailableError(
            "renderer backend override must contain executable/executable_sha256/"
            "identity_executable/name/version"
        )
    name = backend["name"]
    executable = Path(backend["executable"])
    if name not in {"pdftoppm", "pdftocairo"}:
        raise RenderBackendUnavailableError("renderer backend override must be a Poppler renderer")
    if executable.is_symlink() or not executable.is_file():
        raise RenderBackendUnavailableError(
            "renderer backend override must name a regular non-symlink executable"
        )
    claimed_digest = backend.get("executable_sha256")
    if claimed_digest is not None and not re.fullmatch(r"[0-9a-f]{64}", claimed_digest):
        raise RenderBackendUnavailableError(
            "renderer backend override executable_sha256 is invalid"
        )
    sealed = _seal_verified_executable(executable, claimed_digest)
    result = {
        "executable": sealed.execution_path,
        "executable_sha256": sealed.sha256,
        "identity_executable": backend.get("identity_executable", backend["executable"]),
        "name": backend["name"],
        "version": backend["version"],
        "_execution_fd": sealed.descriptor,
    }
    return result, sealed


def _seal_verified_executable(
    executable: Path, claimed_sha256: str | None = None
) -> _SealedExecutable:
    """Copy descriptor-read bytes to a write-sealed Linux memfd for execution.

    The file is opened with ``O_NOFOLLOW`` and never executed by pathname.  The
    returned descriptor is owned by the caller and must be closed.
    """

    try:
        descriptor = os.open(executable, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise RenderBackendUnavailableError(
            "cannot descriptor-read renderer backend override"
        ) from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise RenderBackendUnavailableError("renderer backend override is not a regular file")
        chunks = bytearray()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.extend(chunk)
    finally:
        with suppress(OSError):
            os.close(descriptor)
    content = bytes(chunks)
    digest = sha256_bytes(content)
    if claimed_sha256 is not None and digest != claimed_sha256:
        raise RenderBackendUnavailableError("renderer backend override executable digest drifted")
    try:
        memfd = os.memfd_create(
            "lispmdoc-renderer", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
        )
    except (AttributeError, OSError) as error:
        raise RenderBackendUnavailableError("Linux sealed memfd execution is required") from error
    try:
        written = 0
        while written < len(content):
            count = os.write(memfd, content[written:])
            if count <= 0:
                raise OSError("short write while sealing renderer executable")
            written += count
        fcntl.fcntl(
            memfd,
            fcntl.F_ADD_SEALS,
            fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL,
        )
    except (AttributeError, OSError) as error:
        with suppress(OSError):
            os.close(memfd)
        raise RenderBackendUnavailableError("cannot create sealed renderer executable") from error
    return _SealedExecutable(memfd, digest, len(content))


def _backend_evidence(backend: Mapping[str, Any]) -> dict[str, str]:
    """Remove the machine-local execution path from cache/manifest identity."""

    return {
        "executable": backend.get("identity_executable", backend["executable"]),
        "executable_sha256": backend.get("executable_sha256", "unbound-default-probe"),
        "name": backend["name"],
        "version": backend["version"],
    }


def compose_affine(outer: AffineTransform, inner: AffineTransform) -> AffineTransform:
    """Compose exact transforms as ``outer(inner(x, y))``."""

    oa, ob, oc, od, oe, of = _fractions(outer)
    ia, ib, ic, id_, ie, if_ = _fractions(inner)
    return AffineTransform(
        Rational.from_value(oa * ia + oc * ib),
        Rational.from_value(ob * ia + od * ib),
        Rational.from_value(oa * ic + oc * id_),
        Rational.from_value(ob * ic + od * id_),
        Rational.from_value(oa * ie + oc * if_ + oe),
        Rational.from_value(ob * ie + od * if_ + of),
    )


def _fractions(
    transform: AffineTransform,
) -> tuple[Fraction, Fraction, Fraction, Fraction, Fraction, Fraction]:
    return (
        transform.a.fraction,
        transform.b.fraction,
        transform.c.fraction,
        transform.d.fraction,
        transform.e.fraction,
        transform.f.fraction,
    )


def _validate_page_numbers(pages: Iterable[int], page_count: int) -> None:
    for page in pages:
        if page < 1 or page > page_count:
            raise PageSubsetError(f"page {page} is outside 1..{page_count}")


def _source_fingerprint(path: Path) -> dict[str, Any]:
    return {"byte_size": path.stat().st_size, "sha256": sha256_file(path)}


def _assert_source_unchanged(path: Path, expected: dict[str, Any]) -> None:
    actual = _source_fingerprint(path)
    if actual != expected:
        raise SourceChangedDuringRenderError(
            "source changed during rendering: "
            f"expected {expected['sha256']}, got {actual['sha256']}"
        )


def _safe_output_root(root: Path, source: Path) -> Path:
    source_resolved = source.resolve()
    if root.exists() and root.is_symlink():
        raise UnsafeOutputRootError(f"generated output root must not be a symlink: {root}")
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    try:
        source_resolved.relative_to(resolved_root)
    except ValueError:
        return resolved_root
    raise UnsafeOutputRootError(
        f"generated output root {resolved_root} contains immutable source {source_resolved}"
    )


def _safe_child(root: Path, *parts: str) -> Path:
    if any(not part or Path(part).is_absolute() or ".." in Path(part).parts for part in parts):
        raise UnsafeOutputRootError("generated artifact path must be a non-empty relative path")
    candidate = root.joinpath(*parts)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise UnsafeOutputRootError(
            f"generated artifact path escapes output root: {candidate}"
        ) from None
    return candidate


def _read_pdf_pages(source: Path) -> list[dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as error:  # pragma: no cover - project dependency
        raise RenderBackendUnavailableError(
            "pypdf is required to inventory PDF page geometry"
        ) from error
    reader = PdfReader(str(source), strict=False)
    if reader.is_encrypted:
        raise PreprocessError(
            "cannot render an encrypted PDF without an explicit password workflow"
        )
    pages: list[dict[str, Any]] = []
    for index, page in enumerate(reader.pages):
        crop = page.cropbox
        media = page.mediabox
        contents = page.get_contents()
        page_bytes = contents.get_data() if contents is not None else b""
        crop_box = [float(crop.left), float(crop.bottom), float(crop.right), float(crop.top)]
        media_box = [float(media.left), float(media.bottom), float(media.right), float(media.top)]
        rotation = int(page.get("/Rotate", 0) or 0) % 360
        # The source hash guards every byte. This page-local hash makes a
        # downstream region/cache guard sensitive to the page's content stream,
        # geometry, and position without relying on pypdf's process-local object
        # hashes (which include reader-instance identity).
        page_hash = sha256_bytes(
            _canonical_json(
                {
                    "content_sha256": sha256_bytes(page_bytes),
                    "crop_box": crop_box,
                    "media_box": media_box,
                    "page_index": index,
                    "rotation": rotation,
                }
            )
        )
        pages.append(
            {
                "crop_box": crop_box,
                "media_box": media_box,
                "rotation_degrees": rotation,
                "source_page_index": index,
                "source_page_sha256": page_hash,
            }
        )
    return pages


def _render_one(
    source: Path, page_number: int, dpi: int, target: Path, backend: Mapping[str, Any]
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    name = backend["name"]
    if name in {"pdftoppm", "pdftocairo"}:
        command = [
            backend["executable"],
            "-png",
            "-singlefile",
            "-r",
            str(dpi),
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            str(source),
            str(target.with_suffix("")),
        ]
        raw_fd = backend.get("_execution_fd")
        fd = raw_fd if isinstance(raw_fd, int) else -1
        result = _run(command, pass_fds=(fd,) if fd >= 0 else ())
    elif name == "pymupdf":  # pragma: no cover - Poppler available in CI image
        import fitz

        document = fitz.open(source)
        try:
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            pixmap.save(str(target))
        finally:
            document.close()
        result = subprocess.CompletedProcess([], 0, "", "")
    else:  # defensive: a caller could monkeypatch a malformed probe result
        raise RenderBackendUnavailableError(f"unsupported renderer backend: {name}")
    if result.returncode != 0 or not target.is_file():
        message = (result.stderr or result.stdout or "renderer did not create a PNG").strip()
        raise PreprocessError(f"failed to render page {page_number}: {message}")


def _inspect_png(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            return {
                "format": image.format,
                "height_px": height,
                "mode": image.mode,
                "sha256": sha256_file(path),
                "width_px": width,
            }
    except Exception as error:
        raise PreprocessError(f"renderer produced an invalid PNG {path}: {error}") from error


def _page_record(
    *,
    number: int,
    source_page: dict[str, Any],
    dpi: int,
    image_relative_path: str,
    image_evidence: dict[str, Any],
) -> dict[str, Any]:
    crop_left, crop_bottom, crop_right, crop_top = (
        Fraction(str(value)) for value in source_page["crop_box"]
    )
    rotation = source_page["rotation_degrees"]
    source_to_canonical, canonical_width, canonical_height = source_pdf_to_canonical(
        crop_left, crop_bottom, crop_right, crop_top, rotation
    )
    render_to_canonical = AffineTransform(
        Rational(canonical_width, image_evidence["width_px"]),
        Rational(0),
        Rational(0),
        Rational(canonical_height, image_evidence["height_px"]),
        Rational(0),
        Rational(0),
    )
    no_op = AffineTransform.identity()
    composed = compose_affine(render_to_canonical, no_op)
    return {
        "canonical_page_size_micropoints": {
            "height": canonical_height,
            "width": canonical_width,
        },
        "dpi": dpi,
        "image": {"path": image_relative_path, **image_evidence},
        "normalization": {
            "input_pixels_to_normalized_pixels": no_op.to_dict(),
            "normalized_pixels_to_canonical": render_to_canonical.to_dict(),
            "pixel_to_canonical": composed.to_dict(),
        },
        "page_number": number,
        "source_crop_box_points": source_page["crop_box"],
        "source_media_box_points": source_page["media_box"],
        "source_page_index": source_page["source_page_index"],
        "source_page_rotation_degrees": rotation,
        "source_page_sha256": source_page["source_page_sha256"],
        "source_pdf_to_canonical": source_to_canonical.to_dict(),
    }


def source_pdf_to_canonical(
    left: Fraction,
    bottom: Fraction,
    right: Fraction,
    top: Fraction,
    rotation: int,
) -> tuple[AffineTransform, int, int]:
    """Map a PDF crop box into top-left-origin canonical micropoints."""
    if rotation not in {0, 90, 180, 270}:
        # PDFs permit arbitrary values, but rendering engines normalize only
        # right angles predictably. Preserve the source page rather than claim
        # an invented transform.
        raise PreprocessError(f"unsupported non-right-angle PDF page rotation: {rotation}")
    width = right - left
    height = top - bottom
    if width <= 0 or height <= 0:
        raise PreprocessError("PDF crop box must have positive dimensions")
    scale = Fraction(1000)
    zero = Fraction(0)
    transform: tuple[Fraction, Fraction, Fraction, Fraction, Fraction, Fraction]
    if rotation == 0:
        transform = (scale, zero, zero, -scale, -left * scale, top * scale)
        canonical_width, canonical_height = width * scale, height * scale
    elif rotation == 90:
        transform = (zero, -scale, -scale, zero, top * scale, right * scale)
        canonical_width, canonical_height = height * scale, width * scale
    elif rotation == 180:
        transform = (-scale, zero, zero, scale, right * scale, -bottom * scale)
        canonical_width, canonical_height = width * scale, height * scale
    else:
        transform = (zero, scale, scale, zero, -bottom * scale, -left * scale)
        canonical_width, canonical_height = height * scale, width * scale
    if canonical_width.denominator != 1 or canonical_height.denominator != 1:
        raise PreprocessError("PDF crop box cannot be represented in integer micropoints")
    return (
        AffineTransform(*(Rational.from_value(value) for value in transform)),
        canonical_width.numerator,
        canonical_height.numerator,
    )


def _read_valid_cache(
    manifest_path: Path,
    artifact_directory: Path,
    source: dict[str, Any],
    backend: dict[str, str],
    dpi: int,
    selected: tuple[int, ...],
    settings_digest: str,
) -> RenderManifest | None:
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None
        if (
            value.get("schema_version") != _SCHEMA_VERSION
            or value.get("source") != source
            or value.get("backend") != backend
            or value.get("dpi") != dpi
            or value.get("selected_pages") != list(selected)
            or value.get("preprocess_settings_sha256") != settings_digest
        ):
            return None
        pages = value.get("pages")
        if not isinstance(pages, list) or len(pages) != len(selected):
            return None
        for page in pages:
            if not isinstance(page, dict):
                return None
            for record_name in ("image", "source_render", "ocr_helper_render"):
                image = page.get(record_name)
                if not _valid_image_record(artifact_directory, image):
                    return None
            preprocessing = page.get("preprocessing")
            overlay = (
                preprocessing.get("debug_overlay") if isinstance(preprocessing, dict) else None
            )
            if not _valid_image_record(artifact_directory, overlay):
                return None
            extraction = page.get("lossless_page_image")
            if isinstance(extraction, dict) and extraction.get("status") == "extracted":
                path = extraction.get("path")
                if not isinstance(path, str):
                    return None
                artifact = _safe_child(artifact_directory, *Path(path).parts)
                if not artifact.is_file() or sha256_file(artifact) != extraction.get("sha256"):
                    return None
    except (OSError, ValueError, json.JSONDecodeError, PreprocessError, KeyError):
        return None
    return RenderManifest(value)


def _valid_image_record(artifact_directory: Path, image: Any) -> bool:
    if not isinstance(image, dict) or not isinstance(image.get("path"), str):
        return False
    artifact = _safe_child(artifact_directory, *Path(image["path"]).parts)
    return artifact.is_file() and _inspect_png(artifact) == {
        key: image[key] for key in ("format", "height_px", "mode", "sha256", "width_px")
    }


def _write_manifest(path: Path, manifest: RenderManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(manifest.to_json(), encoding="utf-8")
    temporary.replace(path)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _run(command: list[str], *, pass_fds: tuple[int, ...] = ()) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
        pass_fds=pass_fds,
    )
