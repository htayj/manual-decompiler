"""Configuration loading with stable normalization for cache keys."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .hashing import sha256_bytes


class ConfigurationError(ValueError):
    """Raised when a configuration file violates the supported contract."""


@dataclass(frozen=True)
class Config:
    profile: str = "english-manual"
    source_root: Path = Path("source-material")
    work_root: Path = Path("work")
    output_root: Path = Path("decompiled")
    render_dpi: int = 300
    ocr_engine: str = "auto"
    jobs: int = 1
    extra: Mapping[str, Any] = field(default_factory=dict)

    def canonical_mapping(self) -> dict[str, Any]:
        return {
            "extra": _normalize(dict(self.extra)),
            "jobs": self.jobs,
            "ocr_engine": self.ocr_engine,
            "output_root": self.output_root.as_posix(),
            "profile": self.profile,
            "render_dpi": self.render_dpi,
            "source_root": self.source_root.as_posix(),
            "work_root": self.work_root.as_posix(),
        }

    def canonical_bytes(self) -> bytes:
        return (
            json.dumps(
                self.canonical_mapping(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()

    @property
    def digest(self) -> str:
        return sha256_bytes(self.canonical_bytes())


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ConfigurationError(f"unsupported configuration value: {type(value).__name__}")


def load_config(path: Path | None = None, *, overrides: Mapping[str, Any] | None = None) -> Config:
    values: dict[str, Any] = {}
    if path is not None:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ConfigurationError("configuration root must be a mapping")
        values.update(loaded)
    if overrides:
        values.update({key: value for key, value in overrides.items() if value is not None})

    known = {
        "profile",
        "source_root",
        "work_root",
        "output_root",
        "render_dpi",
        "ocr_engine",
        "jobs",
    }
    extra = {key: value for key, value in values.items() if key not in known}
    config = Config(
        profile=str(values.get("profile", "english-manual")),
        source_root=Path(values.get("source_root", "source-material")),
        work_root=Path(values.get("work_root", "work")),
        output_root=Path(values.get("output_root", "decompiled")),
        render_dpi=int(values.get("render_dpi", 300)),
        ocr_engine=str(values.get("ocr_engine", "auto")),
        jobs=int(values.get("jobs", 1)),
        extra=extra,
    )
    if config.render_dpi <= 0:
        raise ConfigurationError("render_dpi must be positive")
    if config.jobs <= 0:
        raise ConfigurationError("jobs must be positive")
    return config
