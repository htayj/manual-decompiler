"""Fetch and verify the exact Surya model snapshot used by lispmdoc."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from huggingface_hub import snapshot_download


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    lock_path = repository_root / "config" / "models" / "surya-ocr-2.lock.json"
    lock: dict[str, Any] = json.loads(lock_path.read_text(encoding="utf-8"))
    model = lock["model"]
    cache_root = Path(
        os.environ.get(
            "DOCKER_HF_CACHE_PATH",
            repository_root / "work" / "models" / "huggingface",
        )
    ).resolve()
    cache_root.mkdir(parents=True, exist_ok=True)

    snapshot_path = Path(
        snapshot_download(
            repo_id=model["repository"],
            revision=model["revision"],
            cache_dir=cache_root,
        )
    ).resolve()
    weight_contract = model["primary_weight"]
    weight_path = snapshot_path / weight_contract["path"]
    actual_size = weight_path.stat().st_size
    actual_sha256 = _sha256(weight_path)
    if actual_size != weight_contract["size"]:
        raise SystemExit(
            f"model weight size mismatch: expected {weight_contract['size']}, got {actual_size}"
        )
    if actual_sha256 != weight_contract["sha256"]:
        raise SystemExit(
            "model weight digest mismatch: "
            f"expected {weight_contract['sha256']}, got {actual_sha256}"
        )

    evidence = {
        "format": "lispmdoc-model-evidence-1",
        "repository": model["repository"],
        "revision": model["revision"],
        "snapshot_path": str(snapshot_path),
        "primary_weight": {
            "path": weight_contract["path"],
            "sha256": actual_sha256,
            "size": actual_size,
        },
        "license": lock["license"],
    }
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
