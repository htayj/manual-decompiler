from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest


def _evaluation_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "evaluate-chinual-slice"
    loader = importlib.machinery.SourceFileLoader("chinual_slice_evaluation_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _annotation(
    revision: str,
    page_disposition: str,
    region_disposition: str | None = None,
) -> dict[str, object]:
    region = (
        {} if region_disposition is None else {"block-001": {"disposition": region_disposition}}
    )
    return {
        "revision": revision,
        "value": {
            "annotations": {
                "pages": {
                    "page-000092": {
                        "disposition": page_disposition,
                        "regions": region,
                    }
                }
            }
        },
    }


def test_acceptance_chain_requires_latest_correction() -> None:
    evaluation = _evaluation_module()

    chain = [
        _annotation("r25", "accept"),
        _annotation("r30", "accept", "needs-fix"),
        _annotation("r33-final", "accept"),
    ]

    assert evaluation._accepted_pages(chain) == {"page-000092": "r33-final"}


def test_final_acceptance_is_bound_to_exact_replica_bytes() -> None:
    evaluation = _evaluation_module()
    accepted = {"page-000092": "r25"}
    final_pages = {92: {"replica_sha256": "final"}}

    with pytest.raises(ValueError, match="differs from final r33 bytes"):
        evaluation._assert_final_replica_acceptance(
            accepted,
            {"r25": {92: {"replica_sha256": "stale"}}},
            final_pages,
        )

    evaluation._assert_final_replica_acceptance(
        {"page-000092": "r33-final"},
        {"r33-final": {92: {"replica_sha256": "final"}}},
        final_pages,
    )


def test_final_acceptance_requires_every_page() -> None:
    evaluation = _evaluation_module()

    with pytest.raises(ValueError, match="page-000093"):
        evaluation._assert_final_replica_acceptance(
            {"page-000092": "r33-final"},
            {"r33-final": {92: {"replica_sha256": "a"}}},
            {92: {"replica_sha256": "a"}, 93: {"replica_sha256": "b"}},
        )


def test_annotations_cannot_claim_pages_or_regions_absent_from_review() -> None:
    evaluation = _evaluation_module()
    review = {
        "pages": [
            {
                "id": "page-000092",
                "regions": [{"id": "block-001"}],
            }
        ]
    }

    with pytest.raises(ValueError, match="pages absent from review"):
        evaluation._assert_annotation_membership(
            review,
            {"annotations": {"pages": {"page-000091": {"disposition": "accept", "regions": {}}}}},
            "r33-final",
        )

    with pytest.raises(ValueError, match="regions absent"):
        evaluation._assert_annotation_membership(
            review,
            {
                "annotations": {
                    "pages": {
                        "page-000092": {
                            "disposition": "accept",
                            "regions": {"block-999": {"disposition": "accept"}},
                        }
                    }
                }
            },
            "r33-final",
        )

    with pytest.raises(ValueError, match="must be an object"):
        evaluation._assert_annotation_membership(
            review,
            {
                "annotations": {
                    "pages": {
                        "page-000092": {
                            "disposition": "accept",
                            "regions": {"block-001": "reject"},
                        }
                    }
                }
            },
            "r33-final",
        )

    with pytest.raises(ValueError, match="invalid disposition"):
        evaluation._assert_annotation_membership(
            review,
            {
                "annotations": {
                    "pages": {
                        "page-000092": {
                            "disposition": "approved-ish",
                            "regions": {},
                        }
                    }
                }
            },
            "r33-final",
        )


def test_evidence_paths_cannot_escape_their_declared_root(tmp_path: Path) -> None:
    evaluation = _evaluation_module()
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (evidence_root / "inside.txt").write_text("inside", encoding="utf-8")
    (evidence_root / "escape").symlink_to(outside)

    assert evaluation._contained_file(evidence_root, "inside.txt", "test").is_file()
    with pytest.raises(ValueError, match="declared root"):
        evaluation._contained_file(evidence_root, "../outside.txt", "test")
    with pytest.raises(ValueError, match="declared root"):
        evaluation._contained_file(evidence_root, "escape", "test")
