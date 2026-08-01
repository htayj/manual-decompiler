"""Command-line entry point for the headless LMDOC pipeline foundation."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from .benchmark import (
    QueuePage,
    candidates_from_inspections,
    load_corpus,
    select_stratified_pages,
    validate_60_page_queue,
)
from .config import load_config
from .hashing import sha256_bytes, sha256_file
from .ingest import discover_pdfs, inspect_pdf
from .model import Manifest, PageRecord, StructureRecord, StylesRecord, canonical_json_bytes
from .ocr.adapters import capability_report
from .ocr.evaluation import GroundTruthRegion, evaluate_ground_truth
from .package import PACKAGE_FORMAT_VERSION, inspect_package, pack_directory
from .pipeline import phase1_orchestrator
from .preprocess import (
    PreprocessSettings,
    analyze_page_shape,
    detect_scanner_border,
    estimate_deskew,
    probe_render_backend,
    render_pdf,
)
from .render import (
    VIEW_FORMAT_VERSION,
    write_view_tree,
)
from .render import (
    probe_capabilities as probe_view_capabilities,
)
from .review import ReviewArtifacts, ReviewPage, ReviewProject, load_patch, patch_set_sha256
from .validate import (
    ReplicaAttestationInputs,
    attest_replica,
    replica_evidence_from_dict,
    validate_lmdoc,
    validate_replica,
)
from .validate.schema import validate_instance, validate_schema


def _write_json(value: Any) -> None:
    sys.stdout.write(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lispmdoc",
        description="Decompile historical manual PDFs into LMDOC.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="recursively inventory PDF inputs")
    discover.add_argument("path", nargs="?", type=Path, default=Path("source-material"))
    discover.add_argument(
        "--no-fingerprint", action="store_true", help="list paths without hashing source bytes"
    )

    inspect = subparsers.add_parser("inspect", help="inspect and classify a PDF without writing it")
    inspect.add_argument("pdf", type=Path)
    inspect.add_argument("--collection-root", type=Path)

    render = subparsers.add_parser("render", help="render immutable PDF pages into the work cache")
    render.add_argument("pdf", type=Path)
    render.add_argument("--work-root", type=Path, default=Path("work/render"))
    render.add_argument("--dpi", type=int, default=300)
    render.add_argument("--pages", help="1-based subset such as 1,3-5,8-")

    decompile = subparsers.add_parser(
        "decompile", help="run the review-required Phase 1 decompiler"
    )
    decompile.add_argument("pdf", type=Path)
    decompile.add_argument("--config", type=Path)
    decompile.add_argument("--work-root", type=Path)
    decompile.add_argument("--output-root", type=Path)
    decompile.add_argument("--ocr-engine")
    decompile.add_argument("--dpi", type=int)

    subparsers.add_parser("ocr-capabilities", help="report optional OCR engine availability")
    subparsers.add_parser("render-capabilities", help="report optional render/export availability")

    preprocess_proposal = subparsers.add_parser(
        "preprocess-proposal",
        help="inspect a rendered page and report reversible preprocessing proposals",
    )
    preprocess_proposal.add_argument("image", type=Path)

    benchmark = subparsers.add_parser(
        "benchmark-ocr", help="score literal predictions against ground truth"
    )
    benchmark.add_argument("ground_truth", type=Path)
    benchmark.add_argument("predictions", type=Path)

    benchmark_check = subparsers.add_parser(
        "benchmark-check", help="validate a manually grounded benchmark manifest"
    )
    benchmark_check.add_argument("corpus", type=Path)

    benchmark_select = subparsers.add_parser(
        "benchmark-select", help="select a deterministic stratified review queue"
    )
    benchmark_select.add_argument("inspections", type=Path)
    benchmark_select.add_argument("tags", type=Path)
    benchmark_select.add_argument("--per-stratum", type=int, default=1)

    queue_check = subparsers.add_parser(
        "benchmark-queue-check", help="validate a Wave 1 60-page selection queue"
    )
    queue_check.add_argument("queue", type=Path)

    render_views = subparsers.add_parser(
        "render-views", help="derive semantic HTML and paged SVG from an authoring tree"
    )
    render_views.add_argument("authoring_tree", type=Path)
    render_views.add_argument(
        "--raster-policy", choices=("placeholder", "error"), default="placeholder"
    )
    render_views.add_argument("--replica-mode", action="store_true")
    render_views.add_argument(
        "--raster-assets",
        type=Path,
        help="JSON object mapping SHA-256 raster digests to local source paths",
    )
    render_views.add_argument("--permitted-font-sha256", action="append", default=[])

    patch_check = subparsers.add_parser(
        "patch-check", help="validate and fingerprint a guarded review patch"
    )
    patch_check.add_argument("patch", type=Path)

    pack = subparsers.add_parser("pack", help="create a deterministic .lmdoc package")
    pack.add_argument("source", type=Path)
    pack.add_argument("output", type=Path)

    inspect_pack = subparsers.add_parser("inspect-package", help="validate a package envelope")
    inspect_pack.add_argument("package", type=Path)

    check_schema = subparsers.add_parser("validate-schema", help="validate a JSON Schema")
    check_schema.add_argument("schema", type=Path)

    check_instance = subparsers.add_parser(
        "validate-instance", help="validate canonical JSON against a schema"
    )
    check_instance.add_argument("instance", type=Path)
    check_instance.add_argument("schema", type=Path)

    validate = subparsers.add_parser("validate", help="validate an LMDOC tree or package offline")
    validate.add_argument("target", type=Path)

    replica_check = subparsers.add_parser(
        "replica-check", help="validate explicit replica-gate evidence without inventing metrics"
    )
    replica_check.add_argument("evidence", type=Path)
    replica_check.add_argument("--attest", action="store_true")
    replica_check.add_argument(
        "--attestation-inputs",
        type=Path,
        help=(
            "JSON object naming resolved package/source/benchmark/renderer/review "
            "artifacts, approvals, visual evidence, and two builds"
        ),
    )

    review_export = subparsers.add_parser(
        "review-export",
        help="export a digest-bound, read-only review project from an authoring tree",
    )
    review_export.add_argument("authoring_tree", type=Path)
    review_export.add_argument("output", type=Path)
    return parser


def _load_benchmark(path: Path) -> list[GroundTruthRegion]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("ground truth must be a JSON array")
    regions: list[GroundTruthRegion] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"ground truth item {index} must be an object")
        regions.append(
            GroundTruthRegion(
                id=str(item["id"]),
                text=str(item["text"]),
                kind=str(item.get("kind", "prose")),
                required=bool(item.get("required", True)),
            )
        )
    return regions


def _load_predictions(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(text, str) for key, text in value.items()
    ):
        raise ValueError("predictions must be a JSON object mapping region IDs to text")
    return value


def _ensure_output_root_does_not_contain_source(root: Path, source: Path) -> None:
    if root.exists() and root.is_symlink():
        raise ValueError(f"generated output root must not be a symlink: {root}")
    source_resolved = source.resolve(strict=True)
    root_resolved = root.resolve()
    if source_resolved.is_relative_to(root_resolved):
        raise ValueError(
            f"generated output root {root_resolved} contains immutable source {source_resolved}"
        )


def _load_authoring_records(
    root: Path,
) -> tuple[Manifest, tuple[PageRecord, ...], StructureRecord, StylesRecord]:
    manifest = Manifest.from_dict(json.loads((root / "manifest.json").read_text(encoding="utf-8")))
    pages = tuple(
        PageRecord.from_dict(json.loads((root / reference.path).read_text(encoding="utf-8")))
        for reference in manifest.pages
    )
    structure = StructureRecord.from_dict(
        json.loads((root / "structure.json").read_text(encoding="utf-8"))
    )
    styles = StylesRecord.from_dict(json.loads((root / "styles.json").read_text(encoding="utf-8")))
    return manifest, pages, structure, styles


def _load_selection_tags(path: Path) -> dict[tuple[str, int], tuple[str, ...]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("selection tags must be a JSON array")
    result: dict[tuple[str, int], tuple[str, ...]] = {}
    for index, record in enumerate(value):
        if not isinstance(record, dict) or not isinstance(record.get("tags"), list):
            raise ValueError(f"selection tag item {index} must contain a tags array")
        key = (str(record["source_sha256"]), int(record["source_page_index"]))
        if key in result:
            raise ValueError(f"duplicate selection tag target: {key[0]}:{key[1]}")
        result[key] = tuple(str(tag) for tag in record["tags"])
    return result


def _load_queue(path: Path) -> tuple[QueuePage, ...]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("benchmark queue must be a JSON array")
    pages: list[QueuePage] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"benchmark queue item {index} must be an object")
        tags = item.get("tags")
        if not isinstance(tags, list):
            raise ValueError(f"benchmark queue item {index} must have tags")
        pages.append(
            QueuePage(
                str(item.get("source_sha256", "")),
                int(item.get("source_page_index", -1)),
                str(item.get("render_sha256", "")),
                str(item.get("page_class", "")),
                tuple(str(tag) for tag in tags),
            )
        )
    return tuple(pages)


def _load_raster_assets(path: Path | None) -> dict[str, Path]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(
        isinstance(digest, str) and isinstance(asset, str) for digest, asset in value.items()
    ):
        raise ValueError("raster assets must be a JSON object mapping digests to paths")
    return {digest: Path(asset) for digest, asset in value.items()}


def _load_attestation_inputs(path: Path) -> ReplicaAttestationInputs:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("attestation inputs must be a JSON object")
    names = (
        "package_path",
        "source_path",
        "benchmark_path",
        "renderer_evidence_path",
        "review_project_path",
        "approvals_path",
        "visual_evidence_path",
        "build_one_path",
        "build_two_path",
    )
    if any(not isinstance(value.get(name), str) for name in names):
        raise ValueError("attestation inputs must provide every artifact path as a string")
    return ReplicaAttestationInputs(**{name: Path(value[name]) for name in names})


def _preprocess_proposal(image_path: Path) -> dict[str, Any]:
    """Return analysis only; this command never creates an OCR helper raster."""

    with Image.open(image_path) as image:
        source = image.convert("RGB")
        return {
            "disposition": "proposal-only",
            "image_sha256": sha256_file(image_path),
            "analysis": analyze_page_shape(source),
            "border": detect_scanner_border(source),
            "deskew": estimate_deskew(source),
            "default_settings": PreprocessSettings().to_dict(),
            "contract": "No pixels, source render, or OCR input were modified.",
        }


def _export_review_project(authoring_tree: Path, output: Path) -> dict[str, Any]:
    manifest, pages, _, _ = _load_authoring_records(authoring_tree)
    review_pages: list[ReviewPage] = []
    for page in pages:
        if page.page_evidence_sha256 is None:
            raise ValueError(f"review export requires retained page evidence: {page.id}")
        svg_path = authoring_tree / "render" / "pages" / f"{page.sequence:04d}.svg"
        if not svg_path.is_file():
            raise ValueError(f"review export requires derived SVG page: {svg_path}")
        review_pages.append(
            ReviewPage(
                page.id,
                ReviewArtifacts(
                    manifest.source.sha256,
                    page.page_evidence_sha256,
                    sha256_bytes(canonical_json_bytes(page)),
                    sha256_file(svg_path),
                ),
                patch_set_sha256(()),
            )
        )
    project = ReviewProject(
        manifest.document_id,
        tuple(review_pages),
        manifest_page_ids=tuple(page.id for page in pages),
    )
    payload = project.canonical_export() + b"\n"
    if output.exists():
        raise ValueError(f"review export destination already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    return {
        "document_id": manifest.document_id,
        "output": str(output),
        "page_count": len(review_pages),
        "sha256": sha256_bytes(payload),
        "disposition": "review-input-only; export does not create approvals or promotion claims",
    }


def run(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "discover":
        discovered = discover_pdfs(args.path, fingerprint=not args.no_fingerprint)
        _write_json([record.to_dict() for record in discovered])
    elif args.command == "inspect":
        _write_json(inspect_pdf(args.pdf, collection_root=args.collection_root).to_dict())
    elif args.command == "render":
        render_result = render_pdf(
            args.pdf,
            args.work_root,
            dpi=args.dpi,
            pages=args.pages,
        )
        _write_json(
            {
                "artifact_directory": str(render_result.artifact_directory),
                "cache_reused": render_result.cache_reused,
                "manifest": render_result.manifest.to_dict(),
                "manifest_path": str(render_result.manifest_path),
            }
        )
    elif args.command == "decompile":
        config = load_config(
            args.config,
            overrides={
                "work_root": args.work_root,
                "output_root": args.output_root,
                "ocr_engine": args.ocr_engine,
                "render_dpi": args.dpi,
            },
        )
        _ensure_output_root_does_not_contain_source(config.output_root, args.pdf)
        inspection = inspect_pdf(args.pdf)
        decompile_result = phase1_orchestrator(config.work_root).run(args.pdf, inspection, config)
        authoring = decompile_result.work_path / "lmdoc"
        views = write_view_tree(
            authoring,
            decompile_result.manifest,
            decompile_result.pages,
            decompile_result.structure,
            decompile_result.styles,
        )
        tree_report = validate_lmdoc(authoring)
        if not tree_report.is_structurally_valid:
            _write_json(tree_report.to_dict())
            return 1
        build_id = sha256_bytes(
            f"{decompile_result.stage_id}\0{VIEW_FORMAT_VERSION}\0{PACKAGE_FORMAT_VERSION}".encode()
        )
        output = config.output_root / f"{args.pdf.stem}-{build_id[:12]}.lmdoc"
        pack_directory(authoring, output)
        package_report = validate_lmdoc(output)
        _write_json(
            {
                "cache_hit": decompile_result.cache_hit,
                "build_id": build_id,
                "output": str(output),
                "stage_id": decompile_result.stage_id,
                "validation": package_report.to_dict(),
                "view_format_version": VIEW_FORMAT_VERSION,
                "package_format_version": PACKAGE_FORMAT_VERSION,
                "view_warnings": list(views.warnings),
                "work_path": str(decompile_result.work_path),
            }
        )
        if not package_report.is_structurally_valid:
            return 1
    elif args.command == "ocr-capabilities":
        _write_json([capability.to_dict() for capability in capability_report()])
    elif args.command == "render-capabilities":
        derived = probe_view_capabilities()
        _write_json(
            {
                "pdf_render": probe_render_backend(),
                "derived_views": {
                    "chromium": derived.chromium,
                    "resvg": derived.resvg,
                    "harfbuzz": derived.harfbuzz,
                    "optional_pdf": derived.optional_pdf,
                    "deterministic_browser_rendering_available": (
                        derived.deterministic_browser_rendering_available
                    ),
                    "contracts": list(derived.contracts()),
                },
            }
        )
    elif args.command == "preprocess-proposal":
        _write_json(_preprocess_proposal(args.image))
    elif args.command == "benchmark-ocr":
        evaluation_report = evaluate_ground_truth(
            _load_benchmark(args.ground_truth),
            _load_predictions(args.predictions),
        )
        _write_json(evaluation_report.to_dict())
    elif args.command == "benchmark-check":
        corpus = load_corpus(args.corpus)
        _write_json(
            {
                "grounded": True,
                "page_count": len(corpus.pages),
                "corpus": corpus.to_dict(),
            }
        )
    elif args.command == "benchmark-select":
        inspections = json.loads(args.inspections.read_text(encoding="utf-8"))
        if not isinstance(inspections, list):
            raise ValueError("inspections must be a JSON array")
        candidates = candidates_from_inspections(
            inspections,
            _load_selection_tags(args.tags),
        )
        selection = select_stratified_pages(candidates, per_stratum=args.per_stratum)
        _write_json(selection.to_dict())
    elif args.command == "benchmark-queue-check":
        queue = validate_60_page_queue(_load_queue(args.queue))
        _write_json(queue.to_dict())
        return 0 if queue.disposition == "selection-ready" else 1
    elif args.command == "render-views":
        authoring_records = _load_authoring_records(args.authoring_tree)
        views = write_view_tree(
            args.authoring_tree,
            *authoring_records,
            raster_policy=args.raster_policy,
            replica_mode=args.replica_mode,
            raster_assets=_load_raster_assets(args.raster_assets),
            permitted_font_sha256=args.permitted_font_sha256,
        )
        _write_json(
            {
                "css": str(views.css_path),
                "html": str(views.html_path),
                "plain_text": str(views.plain_text_path),
                "svg_pages": [str(path) for path in views.svg_paths],
                "warnings": list(views.warnings),
            }
        )
    elif args.command == "patch-check":
        patch = load_patch(args.patch)
        _write_json({"patch": patch.to_dict(), "sha256": patch.sha256, "valid": True})
    elif args.command == "pack":
        pack_directory(args.source, args.output)
        _write_json({"entries": inspect_package(args.output), "output": str(args.output)})
    elif args.command == "inspect-package":
        _write_json({"entries": inspect_package(args.package), "package": str(args.package)})
    elif args.command == "validate-schema":
        validate_schema(args.schema)
        _write_json({"schema": str(args.schema), "valid": True})
    elif args.command == "validate-instance":
        validate_instance(args.instance, args.schema)
        _write_json({"instance": str(args.instance), "schema": str(args.schema), "valid": True})
    elif args.command == "validate":
        validation_report = validate_lmdoc(args.target)
        _write_json(validation_report.to_dict())
        return 0 if validation_report.is_structurally_valid else 1
    elif args.command == "replica-check":
        value = json.loads(args.evidence.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("replica evidence must be a JSON object")
        evidence = replica_evidence_from_dict(value)
        report = validate_replica(evidence)
        result: dict[str, Any] = {"report": report.to_dict()}
        if args.attest and report.ready and args.attestation_inputs is not None:
            result["attestation"] = attest_replica(
                evidence, _load_attestation_inputs(args.attestation_inputs)
            ).to_dict()
        elif args.attest:
            result["attestation_refused"] = (
                list(report.failures)
                if args.attestation_inputs is not None
                else ["ATTESTATION_INPUTS_REQUIRED"]
            )
        _write_json(result)
        return 0 if report.ready else 1
    elif args.command == "review-export":
        _write_json(_export_review_project(args.authoring_tree, args.output))
    else:  # pragma: no cover - argparse enforces a known command
        raise AssertionError(f"unhandled command: {args.command}")
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except (OSError, ValueError) as error:
        print(f"lispmdoc: {error}", file=sys.stderr)
        raise SystemExit(2) from error
