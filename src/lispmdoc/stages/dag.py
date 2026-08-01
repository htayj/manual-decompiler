"""Deterministic page-local stage DAGs with resumable manifests.

The DAG deliberately stores only content digests and declared identities.  It
does not serialize closures, paths, timestamps, or worker counts; therefore a
one-worker run and a many-worker run have identical manifests.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from lispmdoc.model import canonical_json_bytes, sha256_hex


def _sha256(value: str, name: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lower-case SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class StageKey:
    """All inputs whose change invalidates one page-local stage."""

    stage: str
    source_pdf_sha256: str
    source_page_index: int
    configuration_sha256: str
    implementation_sha256: str
    tool_identities: tuple[str, ...]
    upstream_digests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.stage or self.source_page_index < 0:
            raise ValueError("stage and non-negative source_page_index are required")
        values = (
            self.source_pdf_sha256,
            self.configuration_sha256,
            self.implementation_sha256,
            *self.upstream_digests,
        )
        for value in values:
            _sha256(value, "stage key hash")
        if tuple(sorted(self.tool_identities)) != self.tool_identities:
            raise ValueError("tool identities must be sorted deterministically")

    @property
    def digest(self) -> str:
        return sha256_hex(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "source_pdf_sha256": self.source_pdf_sha256,
            "source_page_index": self.source_page_index,
            "configuration_sha256": self.configuration_sha256,
            "implementation_sha256": self.implementation_sha256,
            "tool_identities": list(self.tool_identities),
            "upstream_digests": list(self.upstream_digests),
        }


@dataclass(frozen=True, slots=True)
class StageResult:
    key: StageKey
    status: str
    output_digests: tuple[str, ...] = ()
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"complete", "failed"}:
            raise ValueError("stage result status must be complete or failed")
        if self.status == "complete" and self.error is not None:
            raise ValueError("complete stage result cannot carry an error")
        if self.status == "failed" and not self.error:
            raise ValueError("failed stage result requires an error record")
        for digest in self.output_digests:
            _sha256(digest, "stage output digest")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "key": self.key.to_dict(),
            "key_digest": self.key.digest,
            "status": self.status,
            "output_digests": list(self.output_digests),
        }
        if self.error is not None:
            result["error"] = self.error
        return result


StageFunction = Callable[[StageKey], Iterable[str]]


class StageRunner:
    """Persist complete/failure records atomically and resume exact completed jobs."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def run(
        self, keys: Iterable[StageKey], function: StageFunction, *, jobs: int = 1
    ) -> tuple[StageResult, ...]:
        if jobs < 1:
            raise ValueError("jobs must be at least one")
        unique = {key.digest: key for key in keys}
        ordered = tuple(unique[digest] for digest in sorted(unique))
        results: dict[str, StageResult] = {}
        pending: list[StageKey] = []
        for key in ordered:
            cached = self.load(key)
            if cached is None or cached.status == "failed":
                pending.append(key)
            else:
                results[key.digest] = cached
        if jobs == 1:
            for key in pending:
                results[key.digest] = self._execute(key, function)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
                futures = {executor.submit(self._execute, key, function): key for key in pending}
                for future in concurrent.futures.as_completed(futures):
                    key = futures[future]
                    results[key.digest] = future.result()
        return tuple(results[key.digest] for key in ordered)

    def path_for(self, key: StageKey) -> Path:
        return self.root / "stages" / key.stage / key.digest[:2] / f"{key.digest}.json"

    def load(self, key: StageKey) -> StageResult | None:
        path = self.path_for(key)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("key_digest") != key.digest or value.get("key") != key.to_dict():
                return None
            return StageResult(
                key,
                str(value["status"]),
                tuple(str(item) for item in value.get("output_digests", ())),
                str(value["error"]) if "error" in value else None,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError, KeyError):
            return None

    def _execute(self, key: StageKey, function: StageFunction) -> StageResult:
        try:
            result = StageResult(key, "complete", tuple(sorted(function(key))))
        except Exception as error:  # failure record is evidence; do not hide it.
            result = StageResult(key, "failed", error=f"{type(error).__name__}: {error}")
        self._write(result)
        return result

    def _write(self, result: StageResult) -> None:
        target = self.path_for(result.key)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical_json_bytes(result.to_dict()) + b"\n"
        descriptor, temporary_name = tempfile.mkstemp(prefix=".stage-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
