#!/usr/bin/env python3
"""Verify downloaded PaddleOCR runtime model files against the repository lock."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("lock", type=Path)
    parser.add_argument("model_root", type=Path)
    args = parser.parse_args()

    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    evidence: dict[str, object] = {"lock": str(args.lock), "models": {}}
    for model_name, model in lock["models"].items():
        files: dict[str, object] = {}
        for relative, expected in model["files"].items():
            path = args.model_root / model_name / relative
            actual = sha256(path)
            if actual != expected:
                raise SystemExit(
                    f"digest mismatch for {model_name}/{relative}: "
                    f"expected {expected}, got {actual}"
                )
            files[relative] = {"sha256": actual, "size": path.stat().st_size}
        evidence["models"][model_name] = {
            "revision": model["revision"],
            "files": files,
        }
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
