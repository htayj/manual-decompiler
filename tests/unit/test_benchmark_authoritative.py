from __future__ import annotations

import hashlib
import io
import json
import tarfile
from dataclasses import replace

import pytest

from lispmdoc.benchmark.authoritative import (
    AUTHORITATIVE_TRUTH_VERSION,
    AuthoritativeMaterial,
    AuthoritativeRegionTruth,
    AuthoritativeReviewEvidence,
    AuthoritativeTruthError,
    AuthoritativeTruthPackage,
    MappingAnchor,
    MappingEvidence,
    QueuePageBinding,
    SourceSpan,
    SupportingSourceFile,
    TextDerivation,
    TypesetterSourceProvenance,
)
from lispmdoc.benchmark.review_annotations import apply_review_annotations
from lispmdoc.benchmark.wave1 import ExpectedRunIdentity, QueuePage, RegionGeometry


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tar(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for path, content in sorted(members.items()):
            info = tarfile.TarInfo(path)
            info.size = len(content)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


_SOURCE = b"line one\nline two\nline three\n"
_ARCHIVE = _tar({"orig4ed/fd_sym.70": _SOURCE})


def _page() -> QueuePage:
    return QueuePage(
        _sha(b"scan.pdf"),
        99,
        _sha(b"rendered-page-100.png"),
        "scan-gray",
        ("clean-scanned-prose",),
        ("body",),
        ExpectedRunIdentity("fixture", "1", "fixture", "1", "fixture", "1"),
    )


def _provenance(*, derivation: TextDerivation | None = None) -> TypesetterSourceProvenance:
    return TypesetterSourceProvenance(
        _sha(_ARCHIVE),
        _sha(_SOURCE),
        "Lisp Machine Manual, fourth edition (1984-08)",
        "orig4ed/fd_sym.70",
        ("strip-final-newline",),
        derivation or TextDerivation("source-literal", "utf-8"),
    )


def _region(*, span: SourceSpan | None = None) -> AuthoritativeRegionTruth:
    return AuthoritativeRegionTruth(
        RegionGeometry(
            "body", ((0, 0), (100, 0), (100, 30)), ((0, 30), (100, 30)), 0, "prose"
        ),
        "line one\nline two\nline three",
        (8, 17),
        "prose",
        span or SourceSpan(1, 3),
        True,
        "verified",
    )


def _evidence(state: str = "verified") -> MappingEvidence:
    anchors = (
        MappingAnchor("source-footer", "MCL:LMMAN;FD.SYM 70", "MCL:LMMAN;FD.SYM 70"),
        MappingAnchor("heading", "The Property List", "The Property List"),
    )
    return MappingEvidence(anchors, state)


def _package(*, evidence: MappingEvidence | None = None) -> AuthoritativeTruthPackage:
    page = _page()
    return AuthoritativeTruthPackage(
        AUTHORITATIVE_TRUTH_VERSION,
        page,
        QueuePageBinding.from_queue_page(page),
        _provenance(),
        evidence or _evidence(),
        (_region(),),
        AuthoritativeReviewEvidence("reviewer", "d" * 64, "e" * 64),
    )


def _review_bytes(
    package: AuthoritativeTruthPackage,
    *,
    page_disposition: str = "accept",
    region_disposition: str = "accept",
    canonical_text: str | None = None,
) -> tuple[bytes, bytes]:
    project = {
        "assets": {
            "generated": {"sha256": _sha(b"generated")},
            "scan": {"sha256": package.queue_page.render_sha256},
        },
        "format_version": "1.0",
        "pages": [
            {
                "generated_asset_id": "generated",
                "id": package.queue_page.id,
                "reference_asset_id": "scan",
                "regions": [
                    {
                        "canonical_text": region.literal_text,
                        "id": region.geometry.region_id,
                        "source_text": region.literal_text,
                    }
                    for region in package.regions
                ],
            }
        ],
    }
    project_bytes = (json.dumps(project, sort_keys=True) + "\n").encode()
    region = package.regions[0]
    region_annotation: dict[str, str] = {"disposition": region_disposition}
    if canonical_text is not None:
        region_annotation["canonical_text"] = canonical_text
    annotations = {
        "annotations": {
            "pages": {
                package.queue_page.id: {
                    "disposition": page_disposition,
                    "regions": {region.geometry.region_id: region_annotation},
                }
            }
        },
        "format_version": "1.0",
        "project_sha256": _sha(project_bytes),
        "reviewer": "reviewer",
    }
    return project_bytes, (json.dumps(annotations, sort_keys=True) + "\n").encode()


def _review_assets(package: AuthoritativeTruthPackage) -> dict[str, bytes]:
    return {"generated": b"generated", "scan": b"rendered-page-100.png"}


def test_authoritative_package_is_deterministic_round_trippable_and_ready() -> None:
    package = _package()

    encoded = package.to_json()
    restored = AuthoritativeTruthPackage.from_json(encoded)

    assert restored == package
    assert restored.to_json() == encoded
    assert restored.truth_digest() == package.truth_digest()
    assert package.status().disposition == "authoritative-ready"
    assert package.status().truth_sha256 == package.truth_digest()
    package.verify_material(
        source_archive=_ARCHIVE, source_file=_SOURCE
    )


def test_review_state_routes_only_mapping_to_the_one_human() -> None:
    package = _package(evidence=_evidence("human-mapping-review-required"))

    assert package.status().disposition == "human-mapping-review-required"


def test_saved_review_bytes_are_the_only_path_to_reviewed_readiness() -> None:
    pending = replace(
        _package(),
        mapping_evidence=_evidence("human-mapping-review-required"),
        regions=(replace(_region(), layout_verification_state="human-review-required"),),
        review_evidence=None,
    )
    project_bytes, annotations_bytes = _review_bytes(pending)

    reviewed = apply_review_annotations(
        pending,
        project_bytes=project_bytes,
        annotations_bytes=annotations_bytes,
        asset_bytes=_review_assets(pending),
    )

    assert reviewed.ready
    reviewed.verify_material_bundle(
        AuthoritativeMaterial(
            _ARCHIVE,
            _SOURCE,
            review_project=project_bytes,
            review_annotations=annotations_bytes,
            review_assets=tuple(sorted(_review_assets(pending).items())),
        )
    )
    with pytest.raises(AuthoritativeTruthError, match="requires exact project"):
        reviewed.verify_material_bundle(AuthoritativeMaterial(_ARCHIVE, _SOURCE))


def test_review_corrections_and_digest_mismatch_fail_closed() -> None:
    pending = replace(
        _package(),
        mapping_evidence=_evidence("human-mapping-review-required"),
        regions=(replace(_region(), layout_verification_state="human-review-required"),),
        review_evidence=None,
    )
    project_bytes, correction_bytes = _review_bytes(
        pending, region_disposition="needs-fix", canonical_text="human correction"
    )
    corrected = apply_review_annotations(
        pending,
        project_bytes=project_bytes,
        annotations_bytes=correction_bytes,
        asset_bytes=_review_assets(pending),
    )
    assert corrected.status().disposition == "source-scan-discrepancy"
    assert not corrected.ready

    bad = json.loads(correction_bytes)
    bad["project_sha256"] = "0" * 64
    with pytest.raises(AuthoritativeTruthError, match="not bound"):
        apply_review_annotations(
            pending,
            project_bytes=project_bytes,
            annotations_bytes=(json.dumps(bad) + "\n").encode(),
            asset_bytes=_review_assets(pending),
        )


def test_fabricated_review_digest_shapes_do_not_pass_material_gate() -> None:
    package = _package()
    with pytest.raises(AuthoritativeTruthError, match="requires exact project"):
        package.verify_material_bundle(AuthoritativeMaterial(_ARCHIVE, _SOURCE))

    pending = replace(package, review_evidence=None)
    project_bytes, annotations_bytes = _review_bytes(pending)
    with pytest.raises(AuthoritativeTruthError, match="asset generated bytes"):
        apply_review_annotations(
            pending,
            project_bytes=project_bytes,
            annotations_bytes=annotations_bytes,
            asset_bytes={"generated": b"forged", "scan": b"rendered-page-100.png"},
        )


@pytest.mark.parametrize("method", ("ocr", "generated", "manual", "surya"))
def test_rejects_non_typesetter_methods(method: str) -> None:
    with pytest.raises(AuthoritativeTruthError, match="source-literal or converted-text"):
        TextDerivation(method, "utf-8")


def test_converted_text_requires_converter_identity_and_immutable_digests() -> None:
    converter = b"bolio-to-texinfo-v6"
    output = b"converted source text"
    derivation = TextDerivation(
        "converted-text", "utf-8", "ti-4ed.sh @ revision 6", _sha(converter), _sha(output)
    )
    provenance = _provenance(derivation=derivation)

    provenance.verify_material(
        source_archive=_ARCHIVE,
        source_file=_SOURCE,
        converter=converter,
        converted_text=output,
    )
    with pytest.raises(AuthoritativeTruthError, match="converter SHA-256"):
        provenance.verify_material(
            source_archive=_ARCHIVE,
            source_file=_SOURCE,
            converter=b"other",
            converted_text=output,
        )


def test_hash_mismatch_and_unsafe_source_span_fail_closed() -> None:
    package = _package()
    with pytest.raises(AuthoritativeTruthError, match="source archive SHA-256"):
        package.verify_material(
            source_archive=b"other", source_file=_SOURCE
        )

    unsafe = _region(span=SourceSpan(1, 4))
    page = _page()
    out_of_bounds = AuthoritativeTruthPackage(
        AUTHORITATIVE_TRUTH_VERSION,
        page,
        QueuePageBinding.from_queue_page(page),
        _provenance(),
        _evidence(),
        (unsafe,),
    )
    with pytest.raises(AuthoritativeTruthError, match="exceeds selected text-authority artifact"):
        out_of_bounds.verify_material(
            source_archive=_ARCHIVE, source_file=_SOURCE
        )
    with pytest.raises(AuthoritativeTruthError, match="positive line range"):
        SourceSpan(0, 1)

    wrong_member_archive = _tar({"orig4ed/not-fd_sym.70": _SOURCE})
    wrong_member_provenance = replace(
        _provenance(), source_archive_sha256=_sha(wrong_member_archive)
    )
    with pytest.raises(AuthoritativeTruthError, match="exact archive members"):
        wrong_member_provenance.verify_material(
            source_archive=wrong_member_archive, source_file=_SOURCE
        )


def test_queue_binding_and_mapping_evidence_cannot_be_omitted_or_staled() -> None:
    page = _page()
    stale = QueuePage(
        page.source_sha256,
        page.source_page_index,
        page.render_sha256,
        page.page_class,
        page.tags,
        page.inventory_region_ids,
        None,
    )
    with pytest.raises(AuthoritativeTruthError, match="not bound to this exact QueuePage"):
        AuthoritativeTruthPackage(
            AUTHORITATIVE_TRUTH_VERSION,
            stale,
            QueuePageBinding.from_queue_page(page),
            _provenance(),
            _evidence(),
            (_region(),),
        )
    with pytest.raises(AuthoritativeTruthError, match="requires at least one"):
        MappingEvidence((), "human-mapping-review-required")
    with pytest.raises(AuthoritativeTruthError, match="verified mapping needs two"):
        MappingEvidence(
            (MappingAnchor("heading", "The Property List", "The Property List"),), "verified"
        )


def test_source_path_and_region_inventory_are_strict() -> None:
    with pytest.raises(AuthoritativeTruthError, match="safe relative POSIX"):
        TypesetterSourceProvenance(
            _sha(b"archive"),
            _sha(b"source"),
            "edition",
            "../fd_sym.70",
            ("none",),
            TextDerivation("source-literal", "utf-8"),
        )


def test_supporting_conversion_inputs_are_immutable_and_must_be_supplied() -> None:
    variables = b"SYMBOL-PLIST-SECTION = 6.3\n"
    archive = _tar(
        {"orig4ed/fd_sym.70": _SOURCE, "orig4ed/manual.vars": variables}
    )
    provenance = TypesetterSourceProvenance(
        _sha(archive),
        _sha(_SOURCE),
        "Lisp Machine Manual, fourth edition (1984-08)",
        "orig4ed/fd_sym.70",
        ("strip-final-newline",),
        TextDerivation("source-literal", "utf-8"),
        (SupportingSourceFile("orig4ed/manual.vars", _sha(variables)),),
    )

    provenance.verify_material(
        source_archive=archive,
        source_file=_SOURCE,
        supporting_files={"orig4ed/manual.vars": variables},
    )
    with pytest.raises(AuthoritativeTruthError, match="supporting source SHA-256"):
        provenance.verify_material(
            source_archive=archive,
            source_file=_SOURCE,
            supporting_files={"orig4ed/manual.vars": b"different variable file"},
        )
    page = _page()
    wrong_region = AuthoritativeRegionTruth(
        RegionGeometry(
            "invented", ((0, 0), (1, 0), (1, 1)), ((0, 1), (1, 1)), 0, "prose"
        ),
        "text",
        (),
        "prose",
        SourceSpan(1, 1),
    )
    with pytest.raises(AuthoritativeTruthError, match="exactly match"):
        AuthoritativeTruthPackage(
            AUTHORITATIVE_TRUTH_VERSION,
            page,
            QueuePageBinding.from_queue_page(page),
            _provenance(),
            _evidence(),
            (wrong_region,),
        )


def test_rejects_fabricated_literal_text_even_when_all_hashes_match() -> None:
    page = _page()
    fabricated = AuthoritativeRegionTruth(
        _region().geometry,
        "text invented by an OCR engine",
        (),
        "prose",
        SourceSpan(1, 3),
        True,
        "verified",
    )
    package = AuthoritativeTruthPackage(
        AUTHORITATIVE_TRUTH_VERSION,
        page,
        QueuePageBinding.from_queue_page(page),
        _provenance(),
        _evidence(),
        (fabricated,),
    )

    with pytest.raises(AuthoritativeTruthError, match="does not exactly match"):
        package.verify_material(
            source_archive=_ARCHIVE, source_file=_SOURCE
        )


def test_converted_text_spans_select_converted_output_not_raw_source() -> None:
    converter = b"converter"
    converted = b"converted literal\n"
    derivation = TextDerivation(
        "converted-text", "utf-8", "converter v1", _sha(converter), _sha(converted)
    )
    page = _page()
    region = AuthoritativeRegionTruth(
        _region().geometry,
        "converted literal",
        (),
        "prose",
        SourceSpan(1, 1),
        True,
        "verified",
    )
    package = AuthoritativeTruthPackage(
        AUTHORITATIVE_TRUTH_VERSION,
        page,
        QueuePageBinding.from_queue_page(page),
        _provenance(derivation=derivation),
        _evidence(),
        (region,),
    )

    package.verify_material(
        source_archive=_ARCHIVE,
        source_file=_SOURCE,
        converter=converter,
        converted_text=converted,
    )
    wrong_converted = b"wrong converted artifact\n"
    wrong_derivation = TextDerivation(
        "converted-text", "utf-8", "converter v1", _sha(converter), _sha(wrong_converted)
    )
    wrong_package = AuthoritativeTruthPackage(
        AUTHORITATIVE_TRUTH_VERSION,
        page,
        QueuePageBinding.from_queue_page(page),
        _provenance(derivation=wrong_derivation),
        _evidence(),
        (region,),
    )
    with pytest.raises(AuthoritativeTruthError, match="does not exactly match"):
        wrong_package.verify_material(
            source_archive=_ARCHIVE,
            source_file=_SOURCE,
            converter=converter,
            converted_text=wrong_converted,
        )


@pytest.mark.parametrize(
    ("state", "expected"),
    (
        ("human-review-required", "human-layout-review-required"),
        ("discrepancy", "source-scan-discrepancy"),
    ),
)
def test_unverified_or_discrepant_layout_cannot_be_authoritative(state: str, expected: str) -> None:
    page = _page()
    pending = AuthoritativeRegionTruth(
        _region().geometry,
        _region().literal_text,
        _region().line_breaks,
        "prose",
        SourceSpan(1, 3),
        True,
        state,
    )
    package = AuthoritativeTruthPackage(
        AUTHORITATIVE_TRUTH_VERSION,
        page,
        QueuePageBinding.from_queue_page(page),
        _provenance(),
        _evidence(),
        (pending,),
    )

    assert not package.ready
    assert package.status().disposition == expected
