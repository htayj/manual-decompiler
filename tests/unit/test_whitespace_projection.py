from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from lispmdoc.benchmark.whitespace_projection import (
    ProjectionSubject,
    WhitespaceProjectionError,
    policy_for_kind,
    projection_matches,
    read_contained_overlay,
    sha256_bytes,
    sha256_text,
    validate_overlay,
)


def test_kind_policies_are_general_and_fail_closed() -> None:
    assert policy_for_kind("body") == "prose-layout-whitespace-projection-v1"
    assert policy_for_kind("code") == "code-leading-indent-projection-v1"
    assert policy_for_kind("table") == "exact-text-projection-v1"
    with pytest.raises(WhitespaceProjectionError, match="unsupported region kind"):
        policy_for_kind("math")


def test_prose_projection_allows_only_whitespace_layout_changes() -> None:
    assert projection_matches("one\n two", "one   two", "body")
    assert not projection_matches("one two", "one three", "body")


def test_code_projection_preserves_literal_lines_internal_text_and_trailing_space() -> None:
    semantic = "\t(foo  bar)  \n    (baz)\n"
    assert projection_matches(semantic, "(foo  bar)  \n(baz)\n", "code")
    assert not projection_matches(semantic, "(foo bar)  \n(baz)\n", "code")
    assert not projection_matches(semantic, "(foo  bar)\n(baz)\n", "code")
    assert not projection_matches(semantic, "(foo  bar)  \r\n(baz)\n", "code")
    assert not projection_matches(semantic, "(foo  bar)  \n(baz)", "code")


def test_exact_projection_never_normalizes_table_whitespace() -> None:
    assert projection_matches("a\tb", "a\tb", "table")
    assert not projection_matches("a\tb", "a b", "table")


def test_overlay_requires_all_subjects_and_digest_bound_kind_policy() -> None:
    subject = ProjectionSubject(1, "block-001", "code", "\t(foo)\n", "(foo)\n")
    overlay = {
        "format_version": "lispmdoc-chinual-whitespace-overlay-1",
        "r33_manifest_sha256": "a" * 64,
        "r33_review_sha256": "b" * 64,
        "entries": [
            {
                "page_number": 1,
                "region_id": "block-001",
                "kind": "code",
                "policy": "code-leading-indent-projection-v1",
                "semantic_sha256": sha256_text(subject.semantic_text),
                "physical_sha256": sha256_text(subject.physical_text),
            }
        ],
    }

    receipts = validate_overlay(
        overlay, [subject], r33_manifest_sha256="a" * 64, r33_review_sha256="b" * 64
    )

    assert receipts[0].semantic_sha256 == sha256_text(subject.semantic_text)
    malformed = copy.deepcopy(overlay)
    malformed["entries"][0]["policy"] = "prose-layout-whitespace-projection-v1"
    with pytest.raises(WhitespaceProjectionError, match="policy/kind drifted"):
        validate_overlay(
            malformed, [subject], r33_manifest_sha256="a" * 64, r33_review_sha256="b" * 64
        )


def test_contained_overlay_reader_hashes_and_parses_exact_regular_file(tmp_path: Path) -> None:
    root = tmp_path / "root"
    path = root / "config/overlay.json"
    path.parent.mkdir(parents=True)
    content = b'{"format_version":"fixture","entries":[]}'
    path.write_bytes(content)

    overlay, bytes_read = read_contained_overlay(root, Path("config/overlay.json"))

    assert overlay == json.loads(content)
    assert sha256_bytes(bytes_read) == sha256_bytes(content)


@pytest.mark.parametrize("relative", [Path("../overlay.json"), Path("config/../overlay.json")])
def test_contained_overlay_reader_rejects_escaping_paths(tmp_path: Path, relative: Path) -> None:
    with pytest.raises(WhitespaceProjectionError, match="must not escape"):
        read_contained_overlay(tmp_path, relative)


def test_contained_overlay_reader_rejects_absolute_and_symlinked_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (root / "overlay.json").symlink_to(outside)

    with pytest.raises(WhitespaceProjectionError, match="relative path"):
        read_contained_overlay(root, outside)
    with pytest.raises(WhitespaceProjectionError, match="contains a symlink"):
        read_contained_overlay(root, Path("overlay.json"))
