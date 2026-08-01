from __future__ import annotations

from pathlib import Path

import pytest

from lispmdoc.config import ConfigurationError, load_config


def test_config_digest_is_stable_across_mapping_order(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text("render_dpi: 400\nfeature:\n  b: 2\n  a: 1\n", encoding="utf-8")
    second.write_text("feature:\n  a: 1\n  b: 2\nrender_dpi: 400\n", encoding="utf-8")

    assert load_config(first).digest == load_config(second).digest


def test_config_rejects_nonpositive_jobs() -> None:
    with pytest.raises(ConfigurationError, match="jobs must be positive"):
        load_config(overrides={"jobs": 0})
