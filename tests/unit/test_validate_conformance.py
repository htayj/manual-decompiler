from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from PIL import Image

from lispmdoc.model import (
    AffineTransform,
    Box,
    Manifest,
    PageRecord,
    PageReference,
    SceneObject,
    SourceRecord,
    StructureNode,
    StructureRecord,
    StylesRecord,
    StyleToken,
    canonical_json_bytes,
    content_id,
)
from lispmdoc.package import pack_directory
from lispmdoc.validate import validate_package, validate_tree

SOURCE_SHA = "a" * 64
CONFIG_SHA = "b" * 64


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), (20, 40, 60)).save(output, "PNG")
    return output.getvalue()


def _write_json(root: Path, name: str, value: object) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _write_valid_tree(root: Path, *, conformance: str = "review-required") -> dict[str, str]:
    page_id = PageRecord.derive_id(SOURCE_SHA, 0)
    object_id = content_id("region", {"source": SOURCE_SHA, "index": 0})
    style_id = content_id("style", {"name": "body"})
    document_id = content_id("document", {"source_sha256": SOURCE_SHA})
    page_ref = PageReference(page_id, 1, "pages/p000001.json", 0)
    manifest = Manifest.for_source(
        SourceRecord(SOURCE_SHA, 1),
        (page_ref,),
        "default",
        CONFIG_SHA,
        conformance_level=conformance,  # type: ignore[arg-type]
    )
    page = PageRecord(
        page_id,
        1,
        0,
        Box(0, 0, 1000, 1000),
        "born-digital",
        AffineTransform(1000, 0, 0, 1000, 0, 0),
        AffineTransform(1000, 0, 0, 1000, 0, 0),
        SOURCE_SHA,
        (SceneObject(object_id, "text", Box(0, 0, 100, 100), style_id, {"text": "CAR"}),),
        (object_id,),
    )
    root_id = content_id("structure", {"document": SOURCE_SHA})
    structure = StructureRecord(
        document_id,
        root_id,
        (
            StructureNode(root_id, "document", (content_id("structure", {"node": "p"}),)),
            StructureNode(
                content_id("structure", {"node": "p"}),
                "paragraph",
                region_ids=(object_id,),
                text="CAR",
            ),
        ),
    )
    styles = StylesRecord(document_id, (StyleToken(style_id, "body", "Times", 10000),))
    _write_json(root, "manifest.json", manifest)
    _write_json(root, "pages/p000001.json", page)
    _write_json(root, "structure.json", structure)
    _write_json(root, "styles.json", styles)
    return {"page": page_id, "object": object_id, "style": style_id}


def test_tree_validation_reports_only_structural_evidence(tmp_path: Path) -> None:
    _write_valid_tree(tmp_path)

    report = validate_tree(tmp_path)

    assert report.is_structurally_valid
    assert report.claimed_conformance == "review-required"
    assert report.effective_conformance == "review-required"
    assert [finding.code for finding in report.findings] == ["SOURCE_NOT_PRESENT"]
    assert report.to_dict()["structurally_valid"] is True


def test_tree_validation_checks_page_references_geometry_and_style_links(tmp_path: Path) -> None:
    _write_valid_tree(tmp_path)
    page_path = tmp_path / "pages/p000001.json"
    page = json.loads(page_path.read_text(encoding="utf-8"))
    page["sequence"] = 2
    page["objects"][0]["box"] = {"x0": 0, "y0": 0, "x1": 2000, "y1": 2000}
    page["objects"][0]["style_id"] = content_id("style", {"unknown": True})
    _write_json(tmp_path, "pages/p000001.json", page)

    report = validate_tree(tmp_path)
    codes = {finding.code for finding in report.errors}

    assert not report.is_structurally_valid
    assert {"PAGE_REFERENCE_MISMATCH", "GEOMETRY_OUT_OF_BOUNDS", "STYLE_REFERENCE"} <= codes


def test_replacement_claim_is_downgraded_without_claiming_unproven_gates(tmp_path: Path) -> None:
    _write_valid_tree(tmp_path, conformance="replacement-ready")
    page_path = tmp_path / "pages/p000001.json"
    page = json.loads(page_path.read_text(encoding="utf-8"))
    page["objects"].append(
        {
            "id": content_id("region", {"raster": True}),
            "kind": "raster",
            "box": {"x0": 0, "y0": 0, "x1": 1000, "y1": 1000},
            "payload": {"reason": "continuous-tone-photo", "asset_path": "assets/missing.png"},
        }
    )
    _write_json(tmp_path, "pages/p000001.json", page)

    report = validate_tree(tmp_path)

    assert report.claimed_conformance == "replacement-ready"
    assert report.effective_conformance == "review-required"
    assert {finding.code for finding in report.errors} >= {
        "MISSING_ASSET",
        "REPLACEMENT_PROFILE_VIOLATION",
    }
    assert any(finding.code == "FULL_PAGE_RASTER" for finding in report.findings)


def test_package_validation_checks_content_addressed_asset_bytes(tmp_path: Path) -> None:
    _write_valid_tree(tmp_path)
    asset = _png_bytes()
    digest = hashlib.sha256(asset).hexdigest()
    asset_path = tmp_path / "assets" / f"{digest}.png"
    asset_path.parent.mkdir()
    asset_path.write_bytes(asset)
    page_path = tmp_path / "pages/p000001.json"
    page = json.loads(page_path.read_text(encoding="utf-8"))
    page["objects"].append(
        {
            "id": content_id("region", {"asset": digest}),
            "kind": "raster",
            "box": {"x0": 100, "y0": 100, "x1": 200, "y1": 200},
            "payload": {
                "reason": "continuous-tone-photo",
                "asset": {
                    "path": f"assets/{digest}.png",
                    "sha256": digest,
                    "codec": "png",
                    "width_px": 2,
                    "height_px": 2,
                },
                "source_crop": {"x": 0, "y": 0, "width": 2, "height": 2},
            },
        }
    )
    _write_json(tmp_path, "pages/p000001.json", page)
    package = tmp_path.parent / "valid.lmdoc"
    pack_directory(tmp_path, package)

    report = validate_package(package)

    assert report.is_structurally_valid
    assert not {finding.code for finding in report.findings} & {
        "ASSET_HASH_MISMATCH",
        "ASSET_REFERENCE_HASH_MISMATCH",
    }


def test_static_validation_rejects_fake_png_and_split_large_raster(tmp_path: Path) -> None:
    _write_valid_tree(tmp_path)
    fake = b"not a png"
    digest = hashlib.sha256(fake).hexdigest()
    asset_path = tmp_path / "assets" / f"{digest}.png"
    asset_path.parent.mkdir()
    asset_path.write_bytes(fake)
    page_path = tmp_path / "pages/p000001.json"
    page = json.loads(page_path.read_text(encoding="utf-8"))
    asset = {
        "path": f"assets/{digest}.png",
        "sha256": digest,
        "codec": "png",
        "width_px": 2,
        "height_px": 2,
    }
    crop = {"x": 0, "y": 0, "width": 2, "height": 2}
    page["objects"].extend(
        [
            {
                "id": content_id("region", {"split": "left"}),
                "kind": "raster",
                "box": {"x0": 0, "y0": 0, "x1": 450, "y1": 1000},
                "payload": {
                    "reason": "continuous-tone-photo",
                    "asset": asset,
                    "source_crop": crop,
                },
            },
            {
                "id": content_id("region", {"split": "right"}),
                "kind": "raster",
                "box": {"x0": 450, "y0": 0, "x1": 900, "y1": 1000},
                "payload": {
                    "reason": "continuous-tone-photo",
                    "asset": asset,
                    "source_crop": crop,
                },
            },
        ]
    )
    _write_json(tmp_path, "pages/p000001.json", page)

    report = validate_tree(tmp_path)
    codes = {finding.code for finding in report.errors}

    assert {"INVALID_RASTER_ASSET", "LARGE_RASTER_POLICY"} <= codes


def test_package_validation_rejects_missing_lmdoc_envelope(tmp_path: Path) -> None:
    package = tmp_path / "not-lmdoc.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", "{}")

    report = validate_package(package)

    assert not report.is_structurally_valid
    assert [finding.code for finding in report.errors] == ["INVALID_PACKAGE"]
