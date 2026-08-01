"""Content identities and mutation guards for source material."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SourceChangedError(RuntimeError):
    """Raised when a source no longer has the fingerprint used for inspection."""


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    """The durable identity of an input's exact bytes.

    ``sha256`` and ``byte_size`` are intentionally sufficient to persist in an
    artifact.  Filesystem timestamps are not identity: they are omitted so a
    copied source has the same document identity.
    """

    sha256: str
    byte_size: int
    algorithm: str = "sha256"

    @property
    def document_id(self) -> str:
        return f"sha256:{self.sha256}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "byte_size": self.byte_size,
            "document_id": self.document_id,
            "sha256": self.sha256,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SourceFingerprint:
        algorithm = str(value.get("algorithm", "sha256"))
        if algorithm != "sha256":
            raise ValueError(f"unsupported source fingerprint algorithm: {algorithm!r}")
        digest = value.get("sha256")
        size = value.get("byte_size")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("source fingerprint sha256 must be a 64-character string")
        if not isinstance(size, int) or size < 0:
            raise ValueError("source fingerprint byte_size must be a non-negative integer")
        return cls(sha256=digest.lower(), byte_size=size)


@dataclass(frozen=True, slots=True)
class SourceVerification:
    """The result of checking a source against a previously recorded digest."""

    expected: SourceFingerprint
    actual: SourceFingerprint

    @property
    def matches(self) -> bool:
        return self.expected == self.actual

    def to_dict(self) -> dict[str, Any]:
        return {
            "actual": self.actual.to_dict(),
            "expected": self.expected.to_dict(),
            "matches": self.matches,
        }


def fingerprint_source(path: str | Path, *, chunk_size: int = 1024 * 1024) -> SourceFingerprint:
    """Hash ``path`` without changing it.

    The descriptor is opened read-only.  A size check after reading catches the
    common case of an input being replaced while it is being fingerprinted.
    """

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    source = Path(path)
    before = source.stat()
    if not source.is_file():
        raise ValueError(f"source is not a regular file: {source}")
    digest = hashlib.sha256()
    with source.open("rb") as input_file:
        while chunk := input_file.read(chunk_size):
            digest.update(chunk)
    after = source.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        # The digest may still coincidentally be correct, but treating a racing
        # source as immutable would make the recorded evidence untrustworthy.
        raise SourceChangedError(f"source changed while fingerprinting: {source}")
    return SourceFingerprint(sha256=digest.hexdigest(), byte_size=after.st_size)


def verify_source(
    path: str | Path,
    expected: SourceFingerprint | Mapping[str, Any],
    *,
    raise_on_change: bool = False,
) -> SourceVerification:
    """Rehash a source and compare it with a saved immutable fingerprint."""

    expected_fingerprint = (
        expected
        if isinstance(expected, SourceFingerprint)
        else SourceFingerprint.from_mapping(expected)
    )
    result = SourceVerification(expected=expected_fingerprint, actual=fingerprint_source(path))
    if raise_on_change and not result.matches:
        raise SourceChangedError(
            f"source fingerprint mismatch for {Path(path)}: expected "
            f"{expected_fingerprint.sha256}, got {result.actual.sha256}"
        )
    return result
