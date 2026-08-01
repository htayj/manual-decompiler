"""Deterministic LMDOC package creation."""

from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path, PurePosixPath

from .model import (
    Manifest,
    PageRecord,
    StructureRecord,
    StylesRecord,
    canonical_json_bytes,
)

MIMETYPE = "application/vnd.lispmdoc+zip"
PACKAGE_FORMAT_VERSION = "lispmdoc-package-2"
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_FILE_MODE = 0o100644 << 16


class PackageError(ValueError):
    """Raised when a package source violates the packaging contract."""


def _write_canonical_record(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def write_authoring_tree(
    destination: Path,
    *,
    manifest: Manifest,
    pages: tuple[PageRecord, ...],
    structure: StructureRecord,
    styles: StylesRecord,
) -> None:
    """Write a complete canonical authoring tree from validated model records."""

    if destination.exists() and any(destination.iterdir()):
        raise PackageError(f"authoring destination is not empty: {destination}")
    if structure.document_id != manifest.document_id or styles.document_id != manifest.document_id:
        raise PackageError("manifest, structure, and styles must name the same document")
    page_by_id = {page.id: page for page in pages}
    if len(page_by_id) != len(pages):
        raise PackageError("page records contain duplicate IDs")
    if tuple(reference.id for reference in manifest.pages) != tuple(page.id for page in pages):
        raise PackageError("page records must exactly match manifest order")
    for reference, page in zip(manifest.pages, pages, strict=True):
        if (
            reference.sequence != page.sequence
            or reference.source_page_index != page.source_page_index
        ):
            raise PackageError(f"page metadata disagrees with manifest: {reference.id}")

    destination.mkdir(parents=True, exist_ok=True)
    _write_canonical_record(destination / "manifest.json", manifest)
    _write_canonical_record(destination / "structure.json", structure)
    _write_canonical_record(destination / "styles.json", styles)
    for reference, page in zip(manifest.pages, pages, strict=True):
        _write_canonical_record(destination / reference.path, page)


def _archive_name(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    name = PurePosixPath(*relative.parts).as_posix()
    if name.startswith("/") or ".." in PurePosixPath(name).parts:
        raise PackageError(f"unsafe package entry: {name}")
    return name


def _zip_info(name: str, *, compressed: bool) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
    info.create_system = 3
    info.external_attr = _FILE_MODE
    info.compress_type = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
    return info


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def pack_directory(source: Path, output: Path) -> None:
    """Pack *source* into a byte-reproducible `.lmdoc` ZIP.

    `mimetype` is synthesized as the first uncompressed entry. Source trees must
    not provide their own copy, symlinks, or non-files.
    """

    source = source.resolve(strict=True)
    output = output.resolve()
    if not source.is_dir():
        raise PackageError(f"package source is not a directory: {source}")
    if output == source or source in output.parents:
        raise PackageError("package output cannot be inside its source tree")

    discovered = list(source.rglob("*"))
    symlinks = [path for path in discovered if path.is_symlink()]
    if symlinks:
        raise PackageError(f"package source contains unsupported entry: {symlinks[0]}")
    entries = sorted(
        (path for path in discovered if path.is_file() and path.name != "mimetype"),
        key=lambda path: _archive_name(path, source),
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            archive.writestr(_zip_info("mimetype", compressed=False), MIMETYPE.encode("ascii"))
            for path in entries:
                name = _archive_name(path, source)
                archive.writestr(_zip_info(name, compressed=True), path.read_bytes())
        if output.exists():
            if not output.is_file() or _sha256(output) != _sha256(temporary):
                raise PackageError(f"refusing to replace different package: {output}")
            return
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def inspect_package(path: Path) -> list[str]:
    """Return ordered entry names after validating the package envelope."""

    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if not entries or entries[0].filename != "mimetype":
            raise PackageError("first package entry must be mimetype")
        if entries[0].compress_type != zipfile.ZIP_STORED:
            raise PackageError("mimetype entry must be stored without compression")
        if archive.read(entries[0]) != MIMETYPE.encode("ascii"):
            raise PackageError("unexpected package mimetype")
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise PackageError("package contains duplicate entries")
        return names
