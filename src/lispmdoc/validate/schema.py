"""Validate canonical JSON files against the repository's versioned schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class SchemaValidationError(ValueError):
    """Raised when a canonical artifact fails schema validation."""


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_instance(instance_path: Path, schema_path: Path) -> None:
    schema = load_json(schema_path)
    instance = load_json(instance_path)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise SchemaValidationError(f"{instance_path}: {location}: {first.message}")


def validate_schema(schema_path: Path) -> None:
    Draft202012Validator.check_schema(load_json(schema_path))
