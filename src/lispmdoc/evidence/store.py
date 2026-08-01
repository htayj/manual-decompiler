"""Content-addressed, integrity-checked storage for extraction evidence.

Evidence is deliberately separate from an LMDOC package: a package names the
digest of raw engine output, while this store retains the exact bytes which
support that claim.  The implementation never accepts a claimed digest from a
caller and verifies bytes on every read.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lispmdoc.model import canonical_json_bytes


class EvidenceError(RuntimeError):
    """Base error for evidence storage failures."""


class ArtifactCorruptError(EvidenceError):
    """Raised when stored bytes do not match their content-addressed name."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_digest(digest: str) -> str:
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("artifact digest must be a lower-case SHA-256 hex digest")
    return digest


@dataclass(frozen=True, slots=True)
class Artifact:
    """A stable reference to exact evidence bytes."""

    sha256: str
    byte_size: int
    media_type: str
    role: str

    def __post_init__(self) -> None:
        _validate_digest(self.sha256)
        if isinstance(self.byte_size, bool) or self.byte_size < 0:
            raise ValueError("artifact byte_size must be a non-negative integer")
        if not self.media_type or not self.role:
            raise ValueError("artifact media_type and role are required")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "media_type": self.media_type,
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> Artifact:
        byte_size = value["byte_size"]
        if isinstance(byte_size, bool) or not isinstance(byte_size, int):
            raise ValueError("artifact byte_size must be an integer")
        return cls(
            str(value["sha256"]),
            byte_size,
            str(value["media_type"]),
            str(value["role"]),
        )


class ArtifactStore:
    """A portable SHA-256 store with atomic insertion and verified retrieval."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path_for(self, digest: str) -> Path:
        digest = _validate_digest(digest)
        return self.root / "sha256" / digest[:2] / digest[2:]

    def put_bytes(self, data: bytes, *, media_type: str, role: str) -> Artifact:
        digest = _digest(data)
        target = self.path_for(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._verify_path(target, digest)
        else:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{digest[:12]}-", dir=target.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Another worker may win; identical target bytes are accepted.
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    self._verify_path(target, digest)
                finally:
                    temporary.unlink(missing_ok=True)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        return Artifact(digest, len(data), media_type, role)

    def put_json(self, value: object, *, role: str) -> Artifact:
        return self.put_bytes(canonical_json_bytes(value), media_type="application/json", role=role)

    def get_bytes(self, artifact: Artifact | str) -> bytes:
        digest = artifact.sha256 if isinstance(artifact, Artifact) else artifact
        target = self.path_for(digest)
        if not target.is_file():
            raise FileNotFoundError(target)
        data = target.read_bytes()
        actual = _digest(data)
        if actual != digest:
            raise ArtifactCorruptError(
                f"evidence artifact {digest} is corrupt (found SHA-256 {actual})"
            )
        if isinstance(artifact, Artifact) and len(data) != artifact.byte_size:
            raise ArtifactCorruptError(
                f"evidence artifact {digest} has {len(data)} bytes, expected {artifact.byte_size}"
            )
        return data

    def recover(self, artifact: Artifact, data: bytes) -> None:
        """Restore a missing/corrupt artifact only from caller-supplied exact bytes."""
        if _digest(data) != artifact.sha256 or len(data) != artifact.byte_size:
            raise ArtifactCorruptError("recovery bytes do not match the requested artifact")
        target = self.path_for(artifact.sha256)
        target.unlink(missing_ok=True)
        restored = self.put_bytes(data, media_type=artifact.media_type, role=artifact.role)
        if restored != artifact:
            raise ArtifactCorruptError(
                "recovered artifact metadata differs from requested artifact"
            )

    @staticmethod
    def _verify_path(path: Path, expected: str) -> None:
        actual = _digest(path.read_bytes())
        if actual != expected:
            raise ArtifactCorruptError(
                f"cache path {path} has SHA-256 {actual}, expected {expected}"
            )
