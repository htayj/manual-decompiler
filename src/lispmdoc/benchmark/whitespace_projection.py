"""Bind source-semantic text to a separately reviewed physical projection.

The comparison rules here are deliberately selected by region *kind*, never by
page, words, or source paths.  They prove a limited correspondence between two
channels; they do not rewrite either channel or make an unreviewed region
authoritative.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class WhitespaceProjectionError(ValueError):
    """The semantic/physical receipt is malformed or no longer reproducible."""


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_PROSE_KINDS = frozenset({"body", "function", "section"})
_EXACT_KINDS = frozenset({"table", "unknown"})


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def policy_for_kind(kind: str) -> str:
    """Return the only physical-projection policy allowed for ``kind``."""

    if kind == "code":
        return "code-leading-indent-projection-v1"
    if kind in _PROSE_KINDS:
        return "prose-layout-whitespace-projection-v1"
    if kind in _EXACT_KINDS:
        return "exact-text-projection-v1"
    raise WhitespaceProjectionError(f"unsupported region kind for whitespace projection: {kind}")


def _prose_signature(text: str) -> str:
    return " ".join(text.split())


def _code_lines(text: str) -> tuple[tuple[str, str], ...]:
    """Split code without erasing line ending, trailing, or indentation data."""

    lines: list[tuple[str, str]] = []
    offset = 0
    for match in re.finditer(r".*?(\r\n|\n|\r|\Z)", text, flags=re.DOTALL):
        line = match.group(0)
        if not line and match.end() == offset:
            break
        offset = match.end()
        ending = ""
        for candidate in ("\r\n", "\n", "\r"):
            if line.endswith(candidate):
                ending = candidate
                line = line[: -len(candidate)]
                break
        lines.append((line, ending))
        if match.end() == len(text):
            break
    return tuple(lines)


def _code_projection_matches(semantic: str, physical: str) -> bool:
    semantic_lines = _code_lines(semantic)
    physical_lines = _code_lines(physical)
    if len(semantic_lines) != len(physical_lines):
        return False
    for (semantic_line, semantic_ending), (physical_line, physical_ending) in zip(
        semantic_lines, physical_lines, strict=True
    ):
        if semantic_ending != physical_ending:
            return False
        if semantic_line.lstrip(" \t") != physical_line.lstrip(" \t"):
            return False
    return True


def projection_matches(semantic: str, physical: str, kind: str) -> bool:
    """Check the permitted relation while preserving the semantic bytes unchanged."""

    policy = policy_for_kind(kind)
    if policy == "prose-layout-whitespace-projection-v1":
        return _prose_signature(semantic) == _prose_signature(physical)
    if policy == "code-leading-indent-projection-v1":
        return _code_projection_matches(semantic, physical)
    return semantic == physical


@dataclass(frozen=True, slots=True)
class ProjectionSubject:
    page_number: int
    region_id: str
    kind: str
    semantic_text: str
    physical_text: str

    @property
    def key(self) -> tuple[int, str]:
        return self.page_number, self.region_id


@dataclass(frozen=True, slots=True)
class ProjectionReceipt:
    page_number: int
    region_id: str
    kind: str
    policy: str
    semantic_sha256: str
    physical_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "page_number": self.page_number,
            "region_id": self.region_id,
            "kind": self.kind,
            "policy": self.policy,
            "semantic_sha256": self.semantic_sha256,
            "physical_sha256": self.physical_sha256,
        }


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise WhitespaceProjectionError(f"{name} must be an object")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise WhitespaceProjectionError(f"{name} must be a lower-case SHA-256")
    return value


def validate_overlay(
    overlay: Mapping[str, Any],
    subjects: Iterable[ProjectionSubject],
    *,
    r33_manifest_sha256: str,
    r33_review_sha256: str,
) -> tuple[ProjectionReceipt, ...]:
    """Validate a digest-bound overlay against live source and review channels.

    An overlay is only a region-level authority witness when both digests,
    kind-selected policy, and the physical relation still hold.  Missing,
    duplicate, changed, or additional entries fail closed.
    """

    if overlay.get("format_version") != "lispmdoc-chinual-whitespace-overlay-1":
        raise WhitespaceProjectionError("unsupported whitespace overlay format")
    if _digest(overlay.get("r33_manifest_sha256"), "r33_manifest_sha256") != r33_manifest_sha256:
        raise WhitespaceProjectionError("whitespace overlay r33 manifest digest drifted")
    if _digest(overlay.get("r33_review_sha256"), "r33_review_sha256") != r33_review_sha256:
        raise WhitespaceProjectionError("whitespace overlay r33 review digest drifted")
    raw_entries = overlay.get("entries")
    if not isinstance(raw_entries, list):
        raise WhitespaceProjectionError("whitespace overlay entries must be an array")
    source = {subject.key: subject for subject in subjects}
    receipts: list[ProjectionReceipt] = []
    seen: set[tuple[int, str]] = set()
    for index, raw_entry in enumerate(raw_entries):
        entry = _object(raw_entry, f"entries[{index}]")
        page_number = entry.get("page_number")
        region_id = entry.get("region_id")
        if (
            not isinstance(page_number, int)
            or page_number < 1
            or not isinstance(region_id, str)
            or not region_id
        ):
            raise WhitespaceProjectionError(f"entries[{index}] has invalid region key")
        key = page_number, region_id
        if key in seen:
            raise WhitespaceProjectionError(
                f"whitespace overlay duplicates p{page_number}/{region_id}"
            )
        seen.add(key)
        subject = source.get(key)
        if subject is None:
            raise WhitespaceProjectionError(
                f"whitespace overlay references unknown p{page_number}/{region_id}"
            )
        kind = entry.get("kind")
        policy = entry.get("policy")
        if kind != subject.kind or policy != policy_for_kind(subject.kind):
            raise WhitespaceProjectionError(
                f"whitespace overlay policy/kind drifted for p{page_number}/{region_id}"
            )
        semantic_sha256 = _digest(entry.get("semantic_sha256"), "semantic_sha256")
        physical_sha256 = _digest(entry.get("physical_sha256"), "physical_sha256")
        if semantic_sha256 != sha256_text(subject.semantic_text):
            raise WhitespaceProjectionError(
                f"semantic text digest drifted for p{page_number}/{region_id}"
            )
        if physical_sha256 != sha256_text(subject.physical_text):
            raise WhitespaceProjectionError(
                f"physical text digest drifted for p{page_number}/{region_id}"
            )
        if not projection_matches(subject.semantic_text, subject.physical_text, subject.kind):
            raise WhitespaceProjectionError(
                f"physical projection is not permitted for p{page_number}/{region_id}"
            )
        receipts.append(
            ProjectionReceipt(
                page_number, region_id, subject.kind, policy, semantic_sha256, physical_sha256
            )
        )
    if set(source) != seen:
        missing = sorted(set(source) - seen)
        raise WhitespaceProjectionError(f"whitespace overlay omits live subjects: {missing}")
    return tuple(sorted(receipts, key=lambda receipt: (receipt.page_number, receipt.region_id)))


def load_overlay_bytes(value: bytes) -> Mapping[str, Any]:
    try:
        return _object(json.loads(value), "whitespace overlay")
    except json.JSONDecodeError as error:
        raise WhitespaceProjectionError("whitespace overlay is invalid JSON") from error


def read_contained_overlay(root: Path, relative_path: Path) -> tuple[Mapping[str, Any], bytes]:
    """Read one tracked overlay without following links or accepting path escapes."""

    if relative_path.is_absolute() or not relative_path.parts:
        raise WhitespaceProjectionError("whitespace overlay path must be a non-empty relative path")
    if any(part in {"", ".", ".."} for part in relative_path.parts):
        raise WhitespaceProjectionError("whitespace overlay path must not escape its root")
    base = root.resolve()
    candidate = base
    for part in relative_path.parts:
        candidate = candidate / part
        try:
            metadata = candidate.lstat()
        except FileNotFoundError as error:
            raise WhitespaceProjectionError(
                f"whitespace overlay is missing: {relative_path}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise WhitespaceProjectionError(
                f"whitespace overlay path contains a symlink: {relative_path}"
            )
    if not stat.S_ISREG(candidate.lstat().st_mode):
        raise WhitespaceProjectionError(
            f"whitespace overlay is not a regular file: {relative_path}"
        )
    content = candidate.read_bytes()
    return load_overlay_bytes(content), content
