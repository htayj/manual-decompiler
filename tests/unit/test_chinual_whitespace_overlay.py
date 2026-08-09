from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify-chinual-whitespace-overlay"


def test_live_chinual_whitespace_overlay_receipt_has_all_reviewed_regions() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(ROOT)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["receipt_count"] == 20
    assert len(receipt["whitespace_overlay_sha256"]) == 64
    assert {entry["kind"] for entry in receipt["receipts"]} == {"body", "code"}
    assert sum(entry["kind"] == "code" for entry in receipt["receipts"]) == 7
    assert sum(entry["kind"] == "body" for entry in receipt["receipts"]) == 13


def test_verifier_rejects_absolute_and_escaping_overlay_paths() -> None:
    for overlay in (
        "/tmp/overlay.json",
        "../config/benchmarks/chinual-r33-whitespace-overlay.json",
    ):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(ROOT), "--overlay", overlay],
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 2
        assert "whitespace overlay path" in result.stderr
