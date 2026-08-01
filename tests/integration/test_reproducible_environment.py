from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def test_container_lock_uses_platform_digests() -> None:
    lock = json.loads((ROOT / "containers" / "images.lock.json").read_text())

    assert lock["format"] == "lispmdoc-container-lock-1"
    assert {"cuda-smoke", "paddle-base", "surya-vllm"} == set(lock["images"])
    for image in lock["images"].values():
        reference = image["reference"]
        assert image["platform"] == "linux/amd64"
        assert "@sha256:" in reference
        assert SHA256.fullmatch(reference.rsplit("@sha256:", 1)[1])
        assert ":" not in reference.split("@", 1)[0].rsplit("/", 1)[1]


def test_surya_model_lock_binds_revision_weight_and_license() -> None:
    lock = json.loads((ROOT / "config" / "models" / "surya-ocr-2.lock.json").read_text())

    assert lock["format"] == "lispmdoc-model-lock-1"
    assert re.fullmatch(r"[0-9a-f]{40}", lock["model"]["revision"])
    assert SHA256.fullmatch(lock["model"]["primary_weight"]["sha256"])
    assert lock["model"]["primary_weight"]["size"] > 0
    assert lock["license"]["disposition"] == "review-required"


def test_paddle_model_lock_binds_revisions_files_and_license() -> None:
    lock = json.loads((ROOT / "config" / "models" / "paddleocr-ppocrv5-en.lock.json").read_text())

    assert lock["format"] == "lispmdoc-model-bundle-lock-1"
    assert lock["pipeline"]["paddleocr"] == "3.7.0"
    assert set(lock["models"]) == {
        "PP-OCRv5_server_det",
        "en_PP-OCRv5_mobile_rec",
    }
    for model in lock["models"].values():
        assert re.fullmatch(r"[0-9a-f]{40}", model["revision"])
        assert set(model["files"]) == {
            "config.json",
            "inference.json",
            "inference.pdiparams",
            "inference.yml",
        }
        assert all(SHA256.fullmatch(digest) for digest in model["files"].values())
    assert lock["license"]["disposition"] == "review-required"


def test_paddle_requirements_are_version_and_hash_locked() -> None:
    requirements = (ROOT / "containers" / "paddleocr" / "requirements.lock").read_text()

    assert "paddleocr==3.7.0" in requirements
    assert "--hash=sha256:" in requirements


def test_environment_launchers_are_executable_and_parse_as_bash() -> None:
    scripts = (
        ROOT / "scripts" / "dev-shell",
        ROOT / "scripts" / "ocr-env-doctor",
        ROOT / "tools" / "podman-shims" / "docker",
        ROOT / "tools" / "surya" / "pull_backend",
        ROOT / "tools" / "surya" / "run",
        ROOT / "tools" / "paddleocr" / "build",
        ROOT / "tools" / "paddleocr" / "run",
    )

    for script in scripts:
        assert os.access(script, os.X_OK)
        subprocess.run(["bash", "-n", script], check=True)

    for script in (
        ROOT / "tools" / "paddleocr" / "ocr_page.py",
        ROOT / "tools" / "paddleocr" / "verify_models.py",
    ):
        assert os.access(script, os.X_OK)
        ast.parse(script.read_text())
