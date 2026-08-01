"""Deterministic recursive discovery of PDF aliases."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fingerprint import SourceFingerprint, fingerprint_source


@dataclass(frozen=True, slots=True)
class DiscoveredPdf:
    """A source alias discovered under a collection root."""

    path: Path
    collection_path: str
    source: SourceFingerprint | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "collection_path": self.collection_path,
            "path": str(self.path),
        }
        if self.source is not None:
            result["source"] = self.source.to_dict()
        return result


def discover_pdfs(root: str | Path, *, fingerprint: bool = True) -> list[DiscoveredPdf]:
    """Find PDFs recursively, preserving every filename alias in stable order.

    Symlinked directories are not traversed.  A symlink to a regular PDF is a
    useful source alias and is included; its content fingerprint is the target's
    bytes.  Duplicate bytes are therefore retained here and can be grouped by
    :func:`group_by_source` before expensive work starts.
    """

    collection_root = Path(root)
    if collection_root.is_file():
        if collection_root.suffix.lower() != ".pdf":
            return []
        source = fingerprint_source(collection_root) if fingerprint else None
        return [DiscoveredPdf(collection_root, collection_root.name, source)]
    if not collection_root.is_dir():
        raise FileNotFoundError(
            f"PDF discovery root does not exist or is not a directory: {collection_root}"
        )

    candidates = sorted(
        (
            path
            for path in collection_root.rglob("*")
            if path.suffix.lower() == ".pdf" and path.is_file()
        ),
        key=lambda path: path.relative_to(collection_root).as_posix(),
    )
    results: list[DiscoveredPdf] = []
    for path in candidates:
        relative = path.relative_to(collection_root).as_posix()
        results.append(
            DiscoveredPdf(
                path=path,
                collection_path=relative,
                source=fingerprint_source(path) if fingerprint else None,
            )
        )
    return results


def group_by_source(documents: Iterable[DiscoveredPdf]) -> dict[str, list[DiscoveredPdf]]:
    """Group aliases by durable source identity without dropping any alias."""

    groups: dict[str, list[DiscoveredPdf]] = {}
    for document in documents:
        source = document.source or fingerprint_source(document.path)
        groups.setdefault(source.document_id, []).append(document)
    return {key: groups[key] for key in sorted(groups)}
