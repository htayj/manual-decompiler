"""Canonical JSON and content-addressed identifiers for LMDOC v1.

The canonical IR deliberately has no timestamps or implementation-specific
objects.  This module is the one place where JSON bytes are defined so IDs,
stage fingerprints, and package writers agree exactly.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from enum import Enum
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol, TypeVar, runtime_checkable

FORMAT_VERSION = "1.0"
_ID_KIND_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented in canonical LMDOC JSON."""


@runtime_checkable
class JsonSerializable(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


T = TypeVar("T")


def _canonical_value(value: Any) -> Any:
    """Return only JSON values, rejecting lossy or non-deterministic inputs."""
    if isinstance(value, JsonSerializable):
        return _canonical_value(value.to_dict())
    if dataclasses.is_dataclass(value):
        return _canonical_value(dataclasses.asdict(value))  # type: ignore[arg-type]
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, Fraction):
        return {"numerator": value.numerator, "denominator": value.denominator}
    if isinstance(value, Path):
        return value.as_posix()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        raise CanonicalizationError("floating-point values are not permitted in LMDOC JSON")
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("JSON object keys must be strings")
            output[key] = _canonical_value(item)
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    raise CanonicalizationError(f"unsupported canonical JSON value: {type(value)!r}")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode *value* as the single deterministic JSON representation.

    UTF-8, sorted keys, compact separators, and no ASCII escaping make the
    byte sequence portable. A trailing newline is intentionally omitted: the
    bytes are also the input to content hashes and ZIP entries.
    """
    try:
        text = json.dumps(
            _canonical_value(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise CanonicalizationError(str(error)) from error
    return text.encode("utf-8")


def canonical_json_text(value: Any) -> str:
    """Return canonical JSON text, primarily for human-readable package files."""
    return canonical_json_bytes(value).decode("utf-8")


def sha256_hex(value: Any) -> str:
    """Return the SHA-256 of canonical JSON bytes (or exact bytes)."""
    payload = value if isinstance(value, bytes) else canonical_json_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def content_id(kind: str, content: Any, *, length: int = 24) -> str:
    """Create a stable, content-derived LMDOC identifier.

    ``kind`` is part of the digest input as well as the readable prefix, which
    prevents equal payloads for different record types from sharing an ID.
    """
    if not kind or not set(kind).issubset(_ID_KIND_CHARACTERS):
        raise ValueError("ID kinds must be lower-case ASCII words separated by hyphens")
    if not 12 <= length <= 64:
        raise ValueError("ID digest length must be between 12 and 64")
    digest = sha256_hex({"kind": kind, "content": content})
    return f"{kind}-{digest[:length]}"


def stable_id(kind: str, content: Any, *, length: int = 24) -> str:
    """Compatibility-friendly name for :func:`content_id`."""
    return content_id(kind, content, length=length)
