from __future__ import annotations

import hashlib

import pytest

from lispmdoc.evidence import ArtifactCorruptError, ArtifactStore, EvidenceRecord


def test_artifact_store_is_content_addressed_and_rejects_corruption(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = ArtifactStore(tmp_path / "evidence")
    artifact = store.put_bytes(
        b"native OCR output", media_type="application/xml", role="native-ocr"
    )
    assert artifact.sha256 == hashlib.sha256(b"native OCR output").hexdigest()
    assert store.get_bytes(artifact) == b"native OCR output"
    store.path_for(artifact.sha256).write_bytes(b"corrupt")
    with pytest.raises(ArtifactCorruptError, match="corrupt"):
        store.get_bytes(artifact)
    store.recover(artifact, b"native OCR output")
    assert store.get_bytes(artifact) == b"native OCR output"


def test_evidence_record_requires_retained_artifact() -> None:
    with pytest.raises(ValueError, match="at least one"):
        EvidenceRecord("e", "p", "engine", "1", "a" * 64, ())
