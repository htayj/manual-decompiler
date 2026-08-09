"""Fail-closed human authority receipts for native-PDF evidence.

The native proposal builder deliberately makes no truth claims.  This module
turns one *reviewed* proposal page into an authority receipt only after a
human has saved guarded Vite annotations.  It never accepts text supplied by a
reviewer: every selected token is an exact Poppler word index from the bound
raw XML witness.
"""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .native_pdf_proposals import NATIVE_PDF_PROPOSAL_VERSION
from .wave2 import Wave2Inventory, Wave2InventoryError, _safe_relative_path, read_contained_regular

NATIVE_PDF_AUTHORITY_VERSION = "lispmdoc-native-pdf-authority-receipt-1"
_HEX = frozenset("0123456789abcdef")
_ROLES = frozenset(
    {"chapter-label", "heading", "prose", "citation", "notice", "signature", "running-matter"}
)
_FINDING_DISPOSITIONS = frozenset({"accepted", "not-applicable", "needs-follow-up"})


class NativePdfAuthorityError(ValueError):
    """A proposed native-PDF authority receipt is incomplete or unsafe."""


def validate_ligature_witness(poppler_words: object, visitor_calls: object) -> None:
    """Validate the known corresponding Poppler/pypdf ligature witness."""
    if not isinstance(poppler_words, list) or len(poppler_words) <= 45:
        raise NativePdfAuthorityError("Poppler witness cannot bind the specification token")
    words = [_object(poppler_words[index], "Poppler word").get("text") for index in range(40, 46)]
    pypdf = (
        _object(visitor_calls[9], "pypdf visitor").get("text")
        if isinstance(visitor_calls, list) and len(visitor_calls) > 9
        else None
    )
    if (
        words != ["a", "full", "hardware", "specification", "of", "the"]
        or pypdf != "For a full hardware speciﬁcation of the processor, consult the"
    ):
        raise NativePdfAuthorityError(
            "expected corresponding Poppler/pypdf specification ligature disagreement is absent"
        )


def validate_inventory_binding(
    inventory: Any, source: Mapping[str, Any], page: Mapping[str, Any]
) -> None:
    """Verify the proposal's K Machine identity against parsed tracked inventory."""
    manual = next((item for item in inventory.manuals if item.manual_id == "k-machine"), None)
    if (
        manual is None
        or manual.source_sha256 != source.get("sha256")
        or manual.source_byte_size != source.get("byte_size")
    ):
        raise NativePdfAuthorityError(
            "proposal source snapshot is not the tracked K Machine source"
        )
    candidate = next((item for item in manual.pages if item.page_index == 2), None)
    if (
        candidate is None
        or page.get("source_page_index") != candidate.page_index
        or page.get("page_class") != candidate.page_class
        or page.get("composition_tags") != list(candidate.tags)
    ):
        raise NativePdfAuthorityError(
            "proposal page class/tags are not the tracked K Machine candidate"
        )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in _HEX for c in value):
        raise NativePdfAuthorityError(f"{label} must be a lower-case SHA-256")
    return value


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise NativePdfAuthorityError(f"{label} must be an object")
    return value


def _only(value: Mapping[str, Any], names: frozenset[str], label: str) -> None:
    unknown = sorted(set(value).difference(names))
    if unknown:
        raise NativePdfAuthorityError(f"{label} contains unknown fields: {unknown}")


def _contained(root: Path, relative: object, label: str) -> bytes:
    try:
        name = _safe_relative_path(relative, label)
        return read_contained_regular(root, name, label)
    except Wave2InventoryError as error:
        raise NativePdfAuthorityError(str(error)) from error


def _tracked_inventory(root: Path) -> tuple[Wave2Inventory, bytes]:
    content = _contained(
        root, "config/benchmarks/wave2-representative-candidates.json", "tracked inventory"
    )
    try:
        return Wave2Inventory.from_bytes(content), content
    except Wave2InventoryError as error:
        raise NativePdfAuthorityError(str(error)) from error


def _proposal_page(proposal: Mapping[str, Any]) -> Mapping[str, Any]:
    pages = proposal.get("pages")
    if not isinstance(pages, list):
        raise NativePdfAuthorityError("proposal pages must be an array")
    matches = [
        p
        for p in pages
        if isinstance(p, dict)
        and p.get("manual_id") == "k-machine"
        and p.get("source_page_index") == 2
    ]
    if len(matches) != 1:
        raise NativePdfAuthorityError("proposal must contain exactly K Machine source_page_index 2")
    page = matches[0]
    poppler = _object(page.get("poppler"), "proposal page poppler")
    if not isinstance(poppler.get("words"), list) or not poppler["words"]:
        raise NativePdfAuthorityError("proposal page has no Poppler word witness")
    return page


def _evidence(
    root: Path, proposal_root: Path, inventory_bytes: bytes | None = None
) -> tuple[Mapping[str, Any], Mapping[str, Any], dict[str, str]]:
    if proposal_root.is_symlink() or not proposal_root.is_dir():
        raise NativePdfAuthorityError("proposal workspace must be a non-symlink directory")
    # Descriptor reads provide a single concrete byte witness for every bound
    # file; nothing is parsed by a second pathname read.
    proposal_bytes = _contained(proposal_root, "proposal.json", "proposal")
    try:
        proposal = _object(json.loads(proposal_bytes), "proposal")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NativePdfAuthorityError("proposal is not UTF-8 JSON") from error
    if (
        proposal.get("schema_version") != NATIVE_PDF_PROPOSAL_VERSION
        or proposal.get("disposition") != "evidence-only-no-ocr"
    ):
        raise NativePdfAuthorityError("proposal is not evidence-only native-PDF evidence")
    page = _proposal_page(proposal)
    pypdf = _object(page.get("pypdf"), "proposal pypdf")
    poppler_words = _object(page.get("poppler"), "proposal poppler").get("words")
    if not isinstance(poppler_words, list) or len(poppler_words) <= 45:
        raise NativePdfAuthorityError("Poppler witness cannot bind the specification token")
    exact_poppler = [
        _object(poppler_words[index], "Poppler word").get("text") for index in range(40, 46)
    ]
    trace = _object(pypdf.get("trace"), "pypdf trace")
    trace_bytes = _contained(proposal_root, trace.get("path"), "pypdf trace")
    if _digest(trace.get("sha256"), "pypdf trace digest") != _sha(trace_bytes):
        raise NativePdfAuthorityError("pypdf trace drifted")
    visitor_calls = _object(json.loads(trace_bytes), "pypdf trace").get("visitor_calls")
    exact_pypdf = (
        _object(visitor_calls[9], "pypdf visitor").get("text")
        if isinstance(visitor_calls, list) and len(visitor_calls) > 9
        else None
    )
    if (
        exact_poppler != ["a", "full", "hardware", "specification", "of", "the"]
        or exact_pypdf != "For a full hardware speciﬁcation of the processor, consult the"
    ):
        raise NativePdfAuthorityError(
            "expected corresponding Poppler/pypdf specification ligature disagreement is absent"
        )
    validate_ligature_witness(poppler_words, visitor_calls)
    plan = _contained(proposal_root, "plan.json", "proposal plan")
    raw_inventory = _contained(proposal_root, "raw-inventory.json", "proposal raw inventory")
    try:
        plan_record = _object(json.loads(plan), "proposal plan")
        raw_record = _object(json.loads(raw_inventory), "proposal raw inventory")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NativePdfAuthorityError("proposal plan/raw inventory must be UTF-8 JSON") from error
    if plan_record.get("inventory_sha256") != proposal.get("inventory_sha256"):
        raise NativePdfAuthorityError("plan and proposal inventory identities disagree")
    listed = raw_record.get("files")
    if not isinstance(listed, list):
        raise NativePdfAuthorityError("raw inventory has no files list")
    recorded: dict[str, tuple[str, int]] = {}
    for item in listed:
        row = _object(item, "raw inventory file")
        path, digest = row.get("path"), row.get("sha256")
        if not isinstance(path, str) or path in recorded:
            raise NativePdfAuthorityError("raw inventory path is invalid")
        byte_size = row.get("byte_size")
        if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size < 0:
            raise NativePdfAuthorityError("raw inventory byte size is invalid")
        recorded[path] = (_digest(digest, "raw inventory digest"), byte_size)
    actual_paths: set[str] = set()
    for artifact in proposal_root.rglob("*"):
        relative = artifact.relative_to(proposal_root).as_posix()
        metadata = artifact.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise NativePdfAuthorityError("proposal workspace contains a symlink")
        if stat.S_ISREG(metadata.st_mode) and relative != "raw-inventory.json":
            actual_paths.add(relative)
            content = _contained(proposal_root, relative, "raw inventory artifact")
            if recorded.get(relative) != (_sha(content), len(content)):
                raise NativePdfAuthorityError("raw inventory is incomplete or has drifted")
    if actual_paths != set(recorded):
        raise NativePdfAuthorityError("raw inventory does not exactly enumerate workspace files")
    raw = _object(_object(page["poppler"], "poppler").get("raw"), "Poppler raw")
    render = _object(_object(page.get("render"), "render").get("source_render"), "source render")
    source = next(
        (
            item
            for item in proposal.get("source_snapshots", [])
            if isinstance(item, dict) and item.get("manual_id") == "k-machine"
        ),
        None,
    )
    if source is None:
        raise NativePdfAuthorityError("proposal lacks K Machine source snapshot")
    source_record = _object(source, "source snapshot")
    files = {
        "proposal_sha256": _sha(proposal_bytes),
        "plan_sha256": _sha(plan),
        "raw_inventory_sha256": _sha(raw_inventory),
        "source_snapshot_sha256": _sha(
            _contained(proposal_root, source_record.get("path"), "source snapshot")
        ),
        "render_sha256": _sha(_contained(proposal_root, render.get("path"), "render")),
        "poppler_raw_sha256": _sha(_contained(proposal_root, raw.get("path"), "Poppler raw")),
    }
    for path, actual in (
        ("proposal.json", files["proposal_sha256"]),
        ("plan.json", files["plan_sha256"]),
        ("pypdf/unused", ""),
    ):
        if path != "pypdf/unused" and recorded.get(path, (None, None))[0] != actual:
            raise NativePdfAuthorityError("raw inventory does not bind proposal artifact")
    for artifact_relative, actual in [
        (source_record.get("path"), files["source_snapshot_sha256"]),
        (render.get("path"), files["render_sha256"]),
        (raw.get("path"), files["poppler_raw_sha256"]),
    ]:
        if (
            not isinstance(artifact_relative, str)
            or recorded.get(artifact_relative, (None, None))[0] != actual
        ):
            raise NativePdfAuthorityError("raw inventory does not bind required native evidence")
    for declared, actual in (
        (source_record.get("sha256"), files["source_snapshot_sha256"]),
        (render.get("sha256"), files["render_sha256"]),
        (raw.get("sha256"), files["poppler_raw_sha256"]),
    ):
        if _digest(declared, "declared proposal digest") != actual:
            raise NativePdfAuthorityError("proposal artifact digest drifted")
    if inventory_bytes is None:
        inventory, _ = _tracked_inventory(root)
    else:
        try:
            inventory = Wave2Inventory.from_bytes(inventory_bytes)
        except Wave2InventoryError as error:
            raise NativePdfAuthorityError(str(error)) from error
    manual = next((item for item in inventory.manuals if item.manual_id == "k-machine"), None)
    if (
        manual is None
        or manual.source_sha256 != source.get("sha256")
        or manual.source_byte_size != source.get("byte_size")
    ):
        raise NativePdfAuthorityError(
            "proposal source snapshot is not the tracked K Machine source"
        )
    candidate = next((item for item in manual.pages if item.page_index == 2), None)
    if (
        candidate is None
        or page.get("source_page_index") != candidate.page_index
        or page.get("page_class") != candidate.page_class
        or page.get("composition_tags") != list(candidate.tags)
    ):
        raise NativePdfAuthorityError(
            "proposal page class/tags are not the tracked K Machine candidate"
        )
    validate_inventory_binding(inventory, source_record, page)
    return proposal, page, files


def default_regions() -> list[dict[str, object]]:
    """Vision-first grouping only; it is deliberately pending human approval."""
    spans = (
        ("chapter-label", 0, 1),
        ("heading", 2, 2),
        ("prose", 3, 38),
        ("citation", 39, 62),
        ("notice", 63, 85),
        ("signature", 86, 91),
        ("running-matter", 92, 92),
    )
    return [
        {
            "id": f"r-{role}-{start:03d}",
            "role": role,
            "word_ids": [f"word-{i:03d}" for i in range(start, end + 1)],
        }
        for role, start, end in spans
    ]


def _review_regions(words: list[Any], page_bounds: list[Any]) -> list[dict[str, Any]]:
    """Attach exact read-only word evidence and a union highlight per region."""
    width = float(page_bounds[2]) - float(page_bounds[0])
    height = float(page_bounds[3]) - float(page_bounds[1])
    regions = default_regions()
    for region in regions:
        indices = [int(str(word)[5:]) for word in cast(list[object], region["word_ids"])]
        selected = [_object(words[index], "Poppler word") for index in indices]
        x0 = min(float(word["x_min"]) for word in selected)
        y0 = min(float(word["y_min"]) for word in selected)
        x1 = max(float(word["x_max"]) for word in selected)
        y1 = max(float(word["y_max"]) for word in selected)
        region["reference_box"] = [
            x0 / width,
            y0 / height,
            (x1 - x0) / width,
            (y1 - y0) / height,
        ]
        region["kind"] = region["role"]
        region["label"] = str(region["role"]).replace("-", " ")
        region["word_text"] = [str(word.get("text", "")) for word in selected]
    return regions


def _poppler_overlay_svg(words: list[Any], render: Mapping[str, Any]) -> str:
    width, height = int(render["width_px"]), int(render["height_px"])
    scale = 300 / 72
    shapes: list[str] = []
    for index, word in enumerate(words):
        item = _object(word, "Poppler word")
        x, y0, x1, y1 = (float(item[key]) for key in ("x_min", "y_min", "x_max", "y_max"))
        shapes.append(
            f'<rect x="{x * scale}" y="{y0 * scale}" width="{(x1 - x) * scale}" '
            f'height="{(y1 - y0) * scale}" fill="none" stroke="#167ac6"/>'
            f'<text x="{x * scale}" y="{y0 * scale}" fill="#b21">{index}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">' + "".join(shapes) + "</svg>"
    )


def _expected_review_contract(
    page: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    words = _object(page["poppler"], "poppler")["words"]
    if not isinstance(words, list):
        raise NativePdfAuthorityError("Poppler words must be a list")
    render = _object(_object(page["render"], "render")["source_render"], "source render")
    regions = _review_regions(words, list(page["page_bounds_pt"]))
    findings = [
        {
            "id": f"proposal-{index:03d}-{item.get('code')}",
            "code": item.get("code"),
            "severity": item.get("severity"),
        }
        for index, item in enumerate(page.get("findings", []))
        if isinstance(item, dict)
    ]
    findings.append(
        {
            "id": "pypdf-ligature-specification",
            "code": "pypdf-ligature-specification",
            "severity": "review",
        }
    )
    authority = {
        "word_ids": [f"word-{i:03d}" for i in range(len(words))],
        "default_reading_order": [
            item["id"] for item in regions if item["role"] != "running-matter"
        ],
        "default_excluded_word_ids": ["word-092"],
        "findings": findings,
    }
    return regions, authority, _poppler_overlay_svg(words, render)


def _validate_fixed_decision(
    decision: Mapping[str, Any], regions: list[dict[str, Any]], authority: Mapping[str, Any]
) -> None:
    decision_regions = [
        {"id": region["id"], "role": region["role"], "word_ids": region["word_ids"]}
        for region in regions
    ]
    for name, expected in (
        ("regions", decision_regions),
        ("reading_order", authority["default_reading_order"]),
        ("excluded_word_ids", authority["default_excluded_word_ids"]),
    ):
        if decision.get(name) != expected:
            raise NativePdfAuthorityError("saved decision does not retain fixed review contract")


def _validate_annotation_envelope(
    annotations: Mapping[str, Any], review_sha256: str
) -> Mapping[str, Any]:
    _only(
        annotations,
        frozenset(
            {
                "format_version",
                "project_sha256",
                "document_id",
                "reviewer",
                "saved_at",
                "annotations",
            }
        ),
        "saved annotations",
    )
    if (
        annotations.get("format_version") != "1.0"
        or annotations.get("project_sha256") != review_sha256
        or annotations.get("document_id") != "native-pdf-k-machine-p3"
        or not isinstance(annotations.get("reviewer"), str)
        or not annotations["reviewer"].strip()
        or not isinstance(annotations.get("saved_at"), str)
        or not annotations["saved_at"].strip()
    ):
        raise NativePdfAuthorityError("saved annotation envelope is invalid")
    root = _object(annotations.get("annotations"), "annotation root")
    _only(root, frozenset({"pages"}), "annotation root")
    pages = _object(root.get("pages"), "annotation pages")
    if set(pages) != {"k-machine-p000003"}:
        raise NativePdfAuthorityError("annotation pages must contain exactly K Machine page 3")
    page = _object(pages["k-machine-p000003"], "K Machine page annotations")
    _only(
        page, frozenset({"disposition", "notes", "native_decision"}), "K Machine page annotations"
    )
    if page.get("disposition") != "accept":
        raise NativePdfAuthorityError("page has not been explicitly accepted")
    if "notes" in page and not isinstance(page["notes"], str):
        raise NativePdfAuthorityError("annotation notes must be text")
    if "native_decision" not in page:
        raise NativePdfAuthorityError("saved annotations lack native decision")
    return page


def build_review_project(root: Path, proposal_root: Path, output: Path) -> Path:
    """Create a new, local Vite manifest for user adjudication; never a receipt."""
    _, inventory_bytes = _tracked_inventory(root)
    proposal, page, hashes = _evidence(root, proposal_root, inventory_bytes)
    if output.exists() or output.is_symlink():
        raise NativePdfAuthorityError("review output already exists; refusing to overwrite")
    output.mkdir(parents=True)
    asset_dir = output / "assets"
    asset_dir.mkdir()
    render = _object(_object(page["render"], "render")["source_render"], "source render")
    png = _contained(proposal_root, render["path"], "render")
    target = asset_dir / "k-machine-p000003.png"
    target.write_bytes(png)
    words = _object(page["poppler"], "poppler")["words"]
    if not isinstance(words, list):
        raise NativePdfAuthorityError("Poppler words must be a list")
    regions = _review_regions(words, list(page["page_bounds_pt"]))
    width, height = int(render["width_px"]), int(render["height_px"])
    scale = 300 / 72
    shapes: list[str] = []
    for index, word in enumerate(words):
        item = _object(word, "Poppler word")
        try:
            x, y0, x1, y1 = (float(item[key]) for key in ("x_min", "y_min", "x_max", "y_max"))
        except (KeyError, TypeError, ValueError) as error:
            raise NativePdfAuthorityError("Poppler word has invalid box") from error
        y = y0 * scale
        rectangle = (
            f'<rect x="{x * scale}" y="{y}" width="{(x1 - x) * scale}" '
            f'height="{(y1 - y0) * scale}" fill="none" stroke="#167ac6"/>'
        )
        shapes.append(rectangle + f'<text x="{x * scale}" y="{y}" fill="#b21">{index}</text>')
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">' + "".join(shapes) + "</svg>"
    )
    overlay = asset_dir / "k-machine-p000003-poppler.svg"
    overlay.write_text(svg, encoding="utf-8")
    finding_rows = [
        {
            "id": f"proposal-{index:03d}-{item.get('code')}",
            "code": item.get("code"),
            "severity": item.get("severity"),
        }
        for index, item in enumerate(page.get("findings", []))
        if isinstance(item, dict)
    ]
    # This exact raw-token discrepancy cannot be normalised by the UI.  It is a
    # semantic gate question: Poppler says `specification`, pypdf says the fi
    # ligature `speciﬁcation`.
    finding_rows.append(
        {
            "id": "pypdf-ligature-specification",
            "code": "pypdf-ligature-specification",
            "severity": "review",
        }
    )
    project = {
        "format_version": "1.0",
        "document_id": "native-pdf-k-machine-p3",
        "title": "K Machine p. 3 — native-PDF authority review",
        "review_mode": "native-pdf-authority",
        "assets": {
            "scan": {
                "path": "assets/k-machine-p000003.png",
                "sha256": _sha(png),
                "media_type": "image/png",
            },
            "poppler-words": {
                "path": "assets/k-machine-p000003-poppler.svg",
                "sha256": _sha(overlay.read_bytes()),
                "media_type": "image/svg+xml",
            },
        },
        "pages": [
            {
                "id": "k-machine-p000003",
                "label": "K Machine source page 3",
                "reference_asset_id": "scan",
                "generated_asset_id": "scan",
                "overlay_asset_id": "poppler-words",
                "regions": regions,
                "native_pdf_authority": {
                    "word_ids": [f"word-{i:03d}" for i in range(len(words))],
                    "default_reading_order": [
                        item["id"] for item in regions if item["role"] != "running-matter"
                    ],
                    "default_excluded_word_ids": ["word-092"],
                    "findings": finding_rows,
                },
            }
        ],
        "evidence": hashes,
        "review_instructions": {
            "page": (
                "Accept or reject the fixed Poppler grouping, order, and exclusions. Mark needs "
                "fix with a note to request regeneration. No transcription or editable "
                "text is accepted."
            )
        },
    }
    project_path = output / "native-pdf-authority-review.json"
    project_path.write_text(json.dumps(project, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return project_path


def _validate_decision(decision: Mapping[str, Any], words: set[str], findings: set[str]) -> None:
    _only(
        decision,
        frozenset(
            {
                "regions",
                "reading_order",
                "excluded_word_ids",
                "finding_dispositions",
                "region_dispositions",
                "acceptance",
            }
        ),
        "native decision",
    )
    regions = decision.get("regions")
    if not isinstance(regions, list) or not regions:
        raise NativePdfAuthorityError("native decision requires regions")
    seen: set[str] = set()
    region_ids: set[str] = set()
    for item in regions:
        record = _object(item, "native region")
        _only(record, frozenset({"id", "role", "word_ids"}), "native region")
        ident, role, ids = record.get("id"), record.get("role"), record.get("word_ids")
        if (
            not isinstance(ident, str)
            or not ident
            or ident in region_ids
            or role not in _ROLES
            or not isinstance(ids, list)
            or not ids
        ):
            raise NativePdfAuthorityError(
                "native regions must have unique IDs, known roles, and word IDs"
            )
        region_ids.add(ident)
        for word in ids:
            if not isinstance(word, str) or word not in words or word in seen:
                raise NativePdfAuthorityError(
                    "native region word IDs must be exact, known, and partitioned"
                )
            seen.add(word)
    if seen != words:
        raise NativePdfAuthorityError("native regions must exhaustively partition Poppler word IDs")
    excluded = decision.get("excluded_word_ids")
    if (
        not isinstance(excluded, list)
        or len(excluded) != len(set(excluded))
        or any(word not in words for word in excluded)
    ):
        raise NativePdfAuthorityError("excluded_word_ids must be a duplicate-free Poppler subset")
    # Exclusion is only legitimate for a whole explicitly-labelled running
    # matter region; it cannot silently remove a word from normal reading.
    for item in regions:
        member = set(item["word_ids"])
        if member.intersection(excluded) and (
            item["role"] != "running-matter" or member != set(excluded).intersection(member)
        ):
            raise NativePdfAuthorityError("excluded words must be complete running-matter regions")
    order = decision.get("reading_order")
    expected = [item["id"] for item in regions if not set(item["word_ids"]).intersection(excluded)]
    if not isinstance(order, list) or set(order) != set(expected) or len(order) != len(expected):
        raise NativePdfAuthorityError(
            "reading_order must contain every and only non-excluded region"
        )
    dispositions = _object(decision.get("finding_dispositions"), "finding dispositions")
    if set(dispositions) != findings or any(
        value not in _FINDING_DISPOSITIONS for value in dispositions.values()
    ):
        raise NativePdfAuthorityError("every proposal finding needs an explicit disposition")
    if any(value != "accepted" for value in dispositions.values()):
        raise NativePdfAuthorityError("accepted receipt requires every witnessed finding accepted")
    acceptance = _object(decision.get("acceptance"), "acceptance")
    if set(acceptance) != {"layout", "reading_order", "semantics", "object_extraction"} or any(
        value is not True for value in acceptance.values()
    ):
        raise NativePdfAuthorityError(
            "layout, reading order, semantics, and object extraction need separate acceptance"
        )
    region_dispositions = _object(decision.get("region_dispositions"), "region dispositions")
    if set(region_dispositions) != region_ids or any(
        value != "accept" for value in region_dispositions.values()
    ):
        raise NativePdfAuthorityError(
            "every fixed region must be explicitly accepted before promotion"
        )


@dataclass(frozen=True, slots=True)
class NativePdfAuthorityReceipt:
    status: str
    evidence: Mapping[str, Any] | None

    @classmethod
    def from_bytes(cls, content: bytes) -> NativePdfAuthorityReceipt:
        try:
            raw = _object(json.loads(content), "native authority receipt")
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NativePdfAuthorityError("receipt is not UTF-8 JSON") from error
        _only(raw, frozenset({"schema_version", "status", "evidence"}), "native authority receipt")
        if raw.get("schema_version") != NATIVE_PDF_AUTHORITY_VERSION or raw.get("status") not in {
            "pending",
            "accepted",
        }:
            raise NativePdfAuthorityError("unsupported native authority receipt state")
        if raw["status"] == "pending":
            if raw.get("evidence") is not None:
                raise NativePdfAuthorityError("pending receipt must be empty")
            return cls("pending", None)
        return cls("accepted", _object(raw.get("evidence"), "receipt evidence"))


def verify_receipt(root: Path, proposal_root: Path, receipt_path: str) -> bool:
    receipt = NativePdfAuthorityReceipt.from_bytes(
        _contained(root, receipt_path, "native authority receipt")
    )
    if receipt.status == "pending":
        return False
    _, inventory_bytes = _tracked_inventory(root)
    proposal, page, hashes = _evidence(root, proposal_root, inventory_bytes)
    evidence = _object(receipt.evidence, "receipt evidence")
    _only(
        evidence,
        frozenset(
            {"tracked_inventory_sha256", "proposal", "review_project", "annotations", "decision"}
        ),
        "receipt evidence",
    )
    if _digest(evidence.get("tracked_inventory_sha256"), "tracked inventory digest") != _sha(
        inventory_bytes
    ):
        raise NativePdfAuthorityError("tracked inventory drifted")
    proposal_hashes = _object(evidence.get("proposal"), "receipt proposal")
    if set(proposal_hashes) != set(hashes) or any(
        _digest(proposal_hashes.get(k), k) != v for k, v in hashes.items()
    ):
        raise NativePdfAuthorityError("proposal evidence binding drifted")
    review_binding = _object(evidence.get("review_project"), "receipt review project")
    _only(review_binding, frozenset({"path", "sha256"}), "receipt review project")
    review_bytes = _contained(root, review_binding.get("path"), "review project")
    if _digest(review_binding.get("sha256"), "review project digest") != _sha(review_bytes):
        raise NativePdfAuthorityError("review project drifted")
    try:
        review = _object(json.loads(review_bytes), "review project")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NativePdfAuthorityError("review project is not UTF-8 JSON") from error
    if review.get("review_mode") != "native-pdf-authority" or review.get("evidence") != hashes:
        raise NativePdfAuthorityError("review project does not bind this exact proposal evidence")
    review_pages = review.get("pages")
    if not isinstance(review_pages, list) or len(review_pages) != 1:
        raise NativePdfAuthorityError(
            "review project must contain exactly the K Machine authority page"
        )
    review_page = _object(review_pages[0], "review page")
    regions, authority, overlay_svg = _expected_review_contract(page)
    if (
        review_page.get("id") != "k-machine-p000003"
        or review_page.get("regions") != regions
        or review_page.get("native_pdf_authority") != authority
        or review_page.get("reference_asset_id") != "scan"
        or review_page.get("generated_asset_id") != "scan"
        or review_page.get("overlay_asset_id") != "poppler-words"
    ):
        raise NativePdfAuthorityError("review project fixed native-PDF contract drifted")
    assets = _object(review.get("assets"), "review assets")
    scan = _object(assets.get("scan"), "review scan")
    overlay = _object(assets.get("poppler-words"), "review overlay")
    if scan.get("sha256") != hashes["render_sha256"] or overlay.get("sha256") != _sha(
        overlay_svg.encode()
    ):
        raise NativePdfAuthorityError("review assets do not match exact proposal render/overlay")
    annotations_binding = _object(evidence.get("annotations"), "receipt annotations")
    _only(annotations_binding, frozenset({"path", "sha256"}), "receipt annotations")
    annotation_bytes = _contained(root, annotations_binding.get("path"), "saved annotations")
    if _digest(annotations_binding.get("sha256"), "annotations digest") != _sha(annotation_bytes):
        raise NativePdfAuthorityError("saved annotations drifted")
    try:
        annotations = _object(json.loads(annotation_bytes), "saved annotations")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NativePdfAuthorityError("saved annotations are not UTF-8 JSON") from error
    page_annotations = _validate_annotation_envelope(annotations, _sha(review_bytes))
    decision = _object(evidence.get("decision"), "receipt decision")
    if page_annotations.get("native_decision") != decision:
        raise NativePdfAuthorityError("receipt decision differs from saved review decision")
    _validate_fixed_decision(decision, regions, authority)
    words = {f"word-{i:03d}" for i in range(len(_object(page["poppler"], "poppler")["words"]))}
    findings = {
        f"proposal-{index:03d}-{item['code']}"
        for index, item in enumerate(page.get("findings", []))
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }
    findings.add("pypdf-ligature-specification")
    _validate_decision(decision, words, findings)
    return True
