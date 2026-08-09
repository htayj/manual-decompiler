from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run-chinual-provenance-ocr"
RUNNER = runpy.run_path(str(SCRIPT), run_name="chinual_provenance_runner_test")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "repo"
    source_pdf = root / "source-material/chinual.pdf"
    source_pdf.parent.mkdir(parents=True, exist_ok=True)
    source_pdf.write_bytes(b"synthetic source PDF")
    page = root / "work/render/pages/p000091.png"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(b"synthetic rendered page")
    _write_json(
        root / "work/render/render-manifest.json",
        {
            "source": {"sha256": _sha256(source_pdf), "byte_size": source_pdf.stat().st_size},
            "pages": [
                {
                    "page_number": 91,
                    "image": {
                        "path": "pages/p000091.png",
                        "sha256": _sha256(page),
                        "width_px": 10,
                        "height_px": 20,
                    },
                }
            ],
        },
    )
    _write_json(
        root / "containers/images.lock.json",
        {"images": {"surya-vllm": {"reference": "example@sha256:1"}}},
    )
    _write_json(
        root / "config/models/surya-ocr-2.lock.json",
        {
            "model": {
                "repository": "example/model",
                "revision": "abc",
                "primary_weight": {"path": "model.safetensors", "sha256": "0", "size": 0},
            }
        },
    )
    _write_json(root / "config/models/paddleocr-ppocrv5-en.lock.json", {"models": {}})
    for relative in (
        "tools/surya/run",
        "tools/surya/pyproject.toml",
        "tools/surya/uv.lock",
        "tools/paddleocr/run",
        "tools/paddleocr/ocr_page.py",
        "tools/podman-shims/docker",
        "containers/paddleocr/Containerfile",
        "containers/paddleocr/requirements.lock",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder\n", encoding="utf-8")
    return root, root / "work/render/render-manifest.json", source_pdf


def _run(
    root: Path, manifest: Path, source_pdf: Path, output: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo-root",
            str(root),
            "--render-manifest",
            str(manifest),
            "--output",
            str(output),
            "--source-pdf",
            str(source_pdf),
            "--engine",
            "both",
            "--pages",
            "91",
            *extra,
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_dry_run_binds_selected_rendered_page_and_never_creates_output(tmp_path: Path) -> None:
    root, manifest, source_pdf = _fixture_repo(tmp_path)
    output = root / "work/ocr-run"

    result = _run(root, manifest, source_pdf, output, "--dry-run")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["dry_run"] is True
    assert report["plan"]["requested"]["pages"] == [91]
    assert report["plan"]["inputs"][0]["sha256"] == _sha256(root / "work/render/pages/p000091.png")
    assert len(report["plan"]["inference_commands"]) == 2
    assert not output.exists()


def test_refuses_to_overwrite_output_even_for_dry_run(tmp_path: Path) -> None:
    root, manifest, source_pdf = _fixture_repo(tmp_path)
    output = root / "work/ocr-run"
    output.mkdir(parents=True)

    result = _run(root, manifest, source_pdf, output, "--dry-run")

    assert result.returncode == 2
    assert "refusing to overwrite existing output" in result.stderr


def test_dry_run_rejects_render_digest_drift(tmp_path: Path) -> None:
    root, manifest, source_pdf = _fixture_repo(tmp_path)
    (root / "work/render/pages/p000091.png").write_bytes(b"changed")

    result = _run(root, manifest, source_pdf, root / "work/ocr-run", "--dry-run")

    assert result.returncode == 2
    assert "rendered image digest mismatch" in result.stderr


def test_dry_run_rejects_source_pdf_digest_drift(tmp_path: Path) -> None:
    root, manifest, source_pdf = _fixture_repo(tmp_path)
    source_pdf.write_bytes(b"tampered source PDF")

    result = _run(root, manifest, source_pdf, root / "work/ocr-run", "--dry-run")

    assert result.returncode == 2
    assert "source PDF digest does not match" in result.stderr


def test_dry_run_rejects_symlinked_source_pdf(tmp_path: Path) -> None:
    root, manifest, source_pdf = _fixture_repo(tmp_path)
    linked_source = root / "source-material/linked.pdf"
    linked_source.symlink_to(source_pdf)

    result = _run(root, manifest, linked_source, root / "work/ocr-run", "--dry-run")

    assert result.returncode == 2
    assert "regular non-symlink" in result.stderr


def test_raw_inventory_is_sorted_and_missing_expected_output_fails(tmp_path: Path) -> None:
    output = tmp_path / "output"
    (output / "surya/raw/nested").mkdir(parents=True)
    (output / "paddleocr/raw").mkdir(parents=True)
    (output / "surya/raw/nested/z.json").write_text("z", encoding="utf-8")
    (output / "paddleocr/raw/a.json").write_text("a", encoding="utf-8")

    inventory = RUNNER["raw_output_inventory"](output)

    assert [entry["path"] for entry in inventory] == [
        "paddleocr/raw/a.json",
        "surya/raw/nested/z.json",
    ]
    with pytest.raises(RuntimeError, match="Surya completed without"):
        RUNNER["validate_expected_raw_output"](
            {"requested": {"engine": "surya"}, "inputs": []}, output
        )


def test_raw_inventory_and_input_verification_reject_symlinks(tmp_path: Path) -> None:
    output = tmp_path / "output"
    input_root = output / "inputs"
    input_root.mkdir(parents=True)
    source = tmp_path / "page.png"
    source.write_bytes(b"page")
    (input_root / "p000091.png").symlink_to(source)
    plan = {
        "inputs": [{"page_number": 91, "byte_size": 4, "sha256": _sha256(source)}],
    }

    with pytest.raises(RuntimeError, match="regular non-symlink"):
        RUNNER["verify_input_snapshots"](plan, output)

    raw_root = output / "surya/raw"
    raw_root.mkdir(parents=True)
    (raw_root / "results.json").symlink_to(source)
    with pytest.raises(RuntimeError, match="contains a symlink"):
        RUNNER["raw_output_inventory"](output)


def test_preflight_fails_closed_on_any_runtime_or_identity_check() -> None:
    successful = {"returncode": 0}
    record = {
        "checks": {
            "podman": successful,
            "nvidia_smi": successful,
            "container_images": {
                "surya": successful,
                "paddleocr": successful,
            },
            "engine_versions": {
                "surya": {"returncode": 127},
                "paddleocr": successful,
            },
        }
    }

    assert RUNNER["preflight_failures"](record) == ["surya-version"]


def test_child_environment_overrides_ambient_sensitive_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LISPMDOC_MODEL_ROOT", "/attacker/models")
    monkeypatch.setenv("DOCKER_HF_CACHE_PATH", "/attacker/cache")
    monkeypatch.setenv("LISPMDOC_PODMAN_BIN", "/attacker/podman")
    monkeypatch.setenv("SURYA_INFERENCE_BACKEND", "attacker")
    monkeypatch.setenv("VLLM_GPU_TYPE", "attacker")
    model_root = tmp_path / "models"
    raw_output = tmp_path / "work/run/paddleocr/raw"

    environment = RUNNER["child_environment"](model_root, Path("/usr/bin/podman"), raw_output)

    assert environment["LISPMDOC_MODEL_ROOT"] == str(model_root)
    assert environment["DOCKER_HF_CACHE_PATH"] == str(model_root / "huggingface")
    assert environment["LISPMDOC_PODMAN_BIN"] == "/usr/bin/podman"
    assert environment["LISPMDOC_PADDLE_RAW_OUTPUT_ROOT"] == str(raw_output)
    assert environment["SURYA_INFERENCE_BACKEND"] == "vllm"
    assert environment["VLLM_GPU_TYPE"] == "4090"


def test_successful_paddle_inspect_with_wrong_id_fails_closed() -> None:
    record = {"returncode": 0, "stdout": '[{"Id": "wrong"}]', "stderr": ""}

    RUNNER["verify_paddle_image_identity"](record, "expected")

    assert record["returncode"] == 1
    assert "does not match lock" in record["stderr"]


def test_execute_rejects_hostile_launcher_mutating_sealed_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, _, _ = _fixture_repo(tmp_path)
    output = root / "work/ocr-run"
    source = root / "work/render/pages/p000091.png"
    plan = {
        "format": "test",
        "inputs": [
            {
                "page_number": 91,
                "path": "work/render/pages/p000091.png",
                "sha256": _sha256(source),
                "byte_size": source.stat().st_size,
            }
        ],
        "requested": {"engine": "paddleocr", "pages": [91]},
        "inference_commands": [["hostile-launcher"]],
    }
    args = SimpleNamespace(
        repo_root=root,
        output=output,
        model_root=root / "work/models",
        podman_bin=Path("/usr/bin/podman"),
    )

    def fake_preflight(_: object, __: object, ___: object) -> dict[str, object]:
        return {
            "checks": {
                "models": {},
                "podman": {"returncode": 0},
                "nvidia_smi": {"returncode": 0},
                "container_images": {},
                "engine_versions": {},
            }
        }

    def hostile_command(_: object, **__: object) -> dict[str, object]:
        (output / "provenance/plan.json").write_text("tampered", encoding="utf-8")
        (output / "paddleocr/raw/p000091.json").write_text("{}", encoding="utf-8")
        return {"returncode": 0, "stdout": "", "stderr": ""}

    execute_globals = RUNNER["execute"].__globals__
    monkeypatch.setitem(execute_globals, "preflight", fake_preflight)
    monkeypatch.setitem(execute_globals, "run_command", hostile_command)

    assert RUNNER["execute"](plan, args) == 1
    record = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert "sealed plan or prior evidence changed" in record["error"]
