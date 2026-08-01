"""Evidence-conditioned physical layout proposals with explicit ambiguity."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any

from lispmdoc.model import Box
from lispmdoc.ocr import OCRPage

from .types import (
    CoverageReport,
    EvidenceSource,
    LayoutEdge,
    LayoutEvidenceRegion,
    LayoutFinding,
    LayoutPageInput,
    LayoutResult,
    ProposalStatus,
    ReadingOrderGraph,
    RegionProposal,
)

_DIRECT_KINDS = {
    "baseline",
    "callout",
    "caption",
    "code",
    "column",
    "equation",
    "figure",
    "footer",
    "header",
    "list",
    "list-item",
    "listing",
    "marginalia",
    "page-number",
    "paragraph",
    "table",
    "table-cell",
    "text",
}


def reconstruct_layout(page: LayoutPageInput) -> LayoutResult:
    """Create physical proposals without promoting labels to semantic truth."""

    evidence, source, selection_findings = _select_evidence(page)
    root_id = _stable_id("region", page.page_id, "page")
    root = RegionProposal(
        root_id,
        "page",
        page.page_box,
        None,
        confidence_milli=1000,
        status="accepted-evidence",
        properties={"page_class": page.page_class},
    )
    proposals: list[RegionProposal] = [root]
    findings = list(selection_findings)
    evidence_by_id = {region.id: region for region in evidence}
    for region in evidence:
        proposal, finding = _leaf_proposal(page, root_id, region)
        proposals.append(proposal)
        if finding is not None:
            findings.append(finding)
    proposals, hierarchy_findings = _nest_proposals(page, proposals, evidence_by_id)
    findings.extend(hierarchy_findings)
    proposals, column_findings = _propose_columns(page, proposals, root_id)
    findings.extend(column_findings)
    graph, graph_findings = build_reading_order_graph(proposals, evidence_by_id)
    findings.extend(graph_findings)
    coverage = account_coverage(evidence, proposals)
    if not coverage.passes:
        findings.append(
            LayoutFinding(
                "incomplete-coverage-accounting",
                (*coverage.missing_regions, *coverage.multiply_accounted_regions),
                "every supplied content-bearing region must have exactly one "
                "treatment or exclusion",
                "high",
            )
        )
    return LayoutResult(
        page.page_id,
        source,
        tuple(sorted(proposals, key=_proposal_sort_key)),
        graph,
        coverage,
        tuple(sorted(findings, key=_finding_sort_key)),
    )


def regions_from_ocr_page(page: OCRPage) -> tuple[LayoutEvidenceRegion, ...]:
    """Adapt normalized OCR/layout evidence without rewriting its labels or text."""

    regions: list[LayoutEvidenceRegion] = []
    for region in page.regions:
        if region.bbox is None:
            continue
        confidence = 0 if region.confidence is None else round(region.confidence * 1000)
        regions.append(
            LayoutEvidenceRegion(
                id=region.id,
                box=Box(
                    region.bbox.x0,
                    region.bbox.y0,
                    region.bbox.x1,
                    region.bbox.y1,
                ),
                kind=region.kind,
                text=region.text,
                confidence_milli=confidence,
                source="ocr-layout",
                reading_order=region.reading_order,
                properties={
                    "language": region.language,
                    "native_id": region.native_id,
                    "orientation_degrees": region.orientation_degrees,
                },
            )
        )
    return tuple(regions)


def account_coverage(
    evidence: tuple[LayoutEvidenceRegion, ...] | list[LayoutEvidenceRegion],
    proposals: tuple[RegionProposal, ...] | list[RegionProposal],
) -> CoverageReport:
    required = {region.id for region in evidence if region.content_bearing}
    counts = {region_id: 0 for region_id in required}
    treated: set[str] = set()
    excluded: set[str] = set()
    for proposal in proposals:
        accounted = (
            proposal.treatment is not None or proposal.intentional_exclusion_reason is not None
        )
        if not accounted:
            continue
        for region_id in proposal.source_region_ids:
            if region_id not in counts:
                continue
            counts[region_id] += 1
            if proposal.intentional_exclusion_reason is not None:
                excluded.add(region_id)
            else:
                treated.add(region_id)
    return CoverageReport(
        len(required),
        tuple(sorted(region_id for region_id in treated if counts[region_id] == 1)),
        tuple(sorted(region_id for region_id in excluded if counts[region_id] == 1)),
        tuple(sorted(region_id for region_id, count in counts.items() if count == 0)),
        tuple(sorted(region_id for region_id, count in counts.items() if count > 1)),
    )


def build_reading_order_graph(
    proposals: list[RegionProposal] | tuple[RegionProposal, ...],
    evidence_by_id: dict[str, LayoutEvidenceRegion],
) -> tuple[ReadingOrderGraph, list[LayoutFinding]]:
    proposal_by_id = {proposal.id: proposal for proposal in proposals}
    leaves = [
        proposal
        for proposal in proposals
        if proposal.source_region_ids
        and proposal.treatment is not None
        and proposal.kind not in {"header", "footer", "page-number"}
    ]
    source_orders = [
        evidence_by_id[proposal.source_region_ids[0]].reading_order for proposal in leaves
    ]
    use_supplied_order = (
        bool(leaves)
        and all(order is not None for order in source_orders)
        and len(set(source_orders)) == len(source_orders)
    )
    if use_supplied_order:
        ordered = sorted(
            leaves,
            key=lambda proposal: (
                evidence_by_id[proposal.source_region_ids[0]].reading_order,
                proposal.id,
            ),
        )
    else:
        ordered = sorted(leaves, key=lambda proposal: _reading_key(proposal, proposal_by_id))
    edges: list[LayoutEdge] = []
    findings: list[LayoutFinding] = []
    for proposal in proposals:
        if proposal.parent_id is not None:
            edges.append(LayoutEdge(proposal.parent_id, proposal.id, "contains", 1000))
        if proposal.kind == "table-cell" and proposal.parent_id is not None:
            edges.append(LayoutEdge(proposal.parent_id, proposal.id, "table-cell", 1000))
        if proposal.kind == "caption" and proposal.parent_id is not None:
            edges.append(LayoutEdge(proposal.id, proposal.parent_id, "caption-for", 900))
    ambiguous_pairs: set[tuple[str, str]] = set()
    for left, right in zip(ordered, ordered[1:], strict=False):
        if _ambiguous_pair(left, right, proposal_by_id):
            pair = (min(left.id, right.id), max(left.id, right.id))
            ambiguous_pairs.add(pair)
            findings.append(
                LayoutFinding(
                    "ambiguous-reading-order",
                    pair,
                    "side-by-side regions lack enough column or supplied-order evidence",
                    "high",
                )
            )
            edges.append(LayoutEdge(left.id, right.id, "adjacent", 400))
        else:
            confidence = 1000 if use_supplied_order else 800
            edges.append(LayoutEdge(left.id, right.id, "reading-next", confidence))
    nodes = tuple(proposal.id for proposal in ordered)
    return (
        ReadingOrderGraph(
            nodes,
            tuple(sorted(edges, key=_edge_sort_key)),
            nodes,
            not ambiguous_pairs,
        ),
        findings,
    )


def _select_evidence(
    page: LayoutPageInput,
) -> tuple[tuple[LayoutEvidenceRegion, ...], EvidenceSource, list[LayoutFinding]]:
    born_digital = page.page_class == "born-digital"
    preferred = page.pdf_regions if born_digital else page.ocr_regions
    fallback = page.ocr_regions if born_digital else page.pdf_regions
    source: EvidenceSource = "pdf-object" if born_digital else "ocr-layout"
    if preferred:
        return preferred, source, []
    if fallback:
        fallback_source: EvidenceSource = "ocr-layout" if born_digital else "pdf-object"
        return (
            fallback,
            fallback_source,
            [
                LayoutFinding(
                    "preferred-layout-evidence-unavailable",
                    (page.page_id,),
                    f"{source} evidence unavailable; used {fallback_source} evidence",
                    "medium",
                )
            ],
        )
    return (
        (),
        source,
        [
            LayoutFinding(
                "layout-evidence-missing",
                (page.page_id,),
                "no physical layout evidence was supplied",
                "high",
            )
        ],
    )


def _leaf_proposal(
    page: LayoutPageInput,
    root_id: str,
    region: LayoutEvidenceRegion,
) -> tuple[RegionProposal, LayoutFinding | None]:
    if region.intentional_exclusion_reason is not None:
        return (
            RegionProposal(
                _stable_id("region", page.page_id, region.id, "intentional-exclusion"),
                "intentional-exclusion",
                region.box,
                root_id,
                (region.id,),
                region.confidence_milli,
                "accepted-evidence",
                intentional_exclusion_reason=region.intentional_exclusion_reason,
                baseline=region.baseline,
                polygon=region.polygon,
                properties={"source_kind": region.kind, **region.properties},
            ),
            None,
        )
    kind, status, confidence, heuristic = _physical_kind(page.page_box, region)
    treatment = _treatment(kind)
    proposal = RegionProposal(
        _stable_id("region", page.page_id, region.id, kind),
        kind,
        region.box,
        root_id,
        (region.id,),
        confidence,
        status,
        treatment,
        baseline=region.baseline,
        polygon=region.polygon,
        properties={
            "source": region.source,
            "source_kind": region.kind,
            "source_reading_order": region.reading_order,
            "text": region.text,
            **region.properties,
        },
    )
    finding = None
    if heuristic or status == "review":
        finding = LayoutFinding(
            "physical-label-review",
            (proposal.id, region.id),
            f"{region.kind!r} produced physical proposal {kind!r}; semantic truth is unresolved",
            "medium",
        )
    return proposal, finding


def _physical_kind(
    page_box: Box, region: LayoutEvidenceRegion
) -> tuple[str, ProposalStatus, int, bool]:
    normalized = region.kind.lower().replace("_", "-")
    aliases = {
        "cell": "table-cell",
        "code-block": "code",
        "formula": "equation",
        "image": "figure",
        "line": "text",
        "text-block": "text",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in _DIRECT_KINDS:
        return normalized, "accepted-evidence", region.confidence_milli, False
    box = region.box
    if normalized in {"unknown", "region"}:
        if box.y0 <= page_box.y0 + page_box.height * 7 // 100:
            return "header", "review", min(700, region.confidence_milli), True
        if box.y1 >= page_box.y1 - page_box.height * 7 // 100:
            if region.text is not None and region.text.strip().isdigit():
                return "page-number", "review", min(750, region.confidence_milli), True
            return "footer", "review", min(700, region.confidence_milli), True
        near_side = (
            box.x0 <= page_box.x0 + page_box.width // 10
            or box.x1 >= page_box.x1 - page_box.width // 10
        )
        if near_side and box.width <= page_box.width // 5:
            return "marginalia", "review", min(650, region.confidence_milli), True
    return "content-region", "review", min(500, region.confidence_milli), True


def _treatment(kind: str) -> str:
    if kind in {"header", "footer", "page-number"}:
        return "preserve-running-matter"
    if kind == "content-region":
        return "preserve-unclassified"
    return f"preserve-{kind}"


def _nest_proposals(
    page: LayoutPageInput,
    proposals: list[RegionProposal],
    evidence_by_id: dict[str, LayoutEvidenceRegion],
) -> tuple[list[RegionProposal], list[LayoutFinding]]:
    containers = [
        proposal
        for proposal in proposals
        if proposal.kind in {"table", "list", "figure", "callout", "column"}
    ]
    output: list[RegionProposal] = []
    findings: list[LayoutFinding] = []
    proposal_by_source = {
        source_id: proposal for proposal in proposals for source_id in proposal.source_region_ids
    }
    for proposal in proposals:
        expected_parent_kind = {
            "caption": "figure",
            "list-item": "list",
            "table-cell": "table",
        }.get(proposal.kind)
        if expected_parent_kind is None:
            output.append(proposal)
            continue
        evidence = evidence_by_id[proposal.source_region_ids[0]]
        explicit_key = {
            "caption": "figure_id",
            "list-item": "list_id",
            "table-cell": "table_id",
        }[proposal.kind]
        explicit_source = evidence.properties.get(explicit_key)
        parent = (
            proposal_by_source.get(explicit_source) if isinstance(explicit_source, str) else None
        )
        if parent is not None and parent.kind != expected_parent_kind:
            parent = None
        candidates = [
            container
            for container in containers
            if container.kind == expected_parent_kind and container.box.contains_box(proposal.box)
        ]
        if parent is None and len(candidates) == 1:
            parent = candidates[0]
        if parent is None:
            findings.append(
                LayoutFinding(
                    f"ambiguous-{proposal.kind}-parent",
                    (proposal.id,),
                    f"{proposal.kind} requires reviewed {expected_parent_kind} association",
                    "high",
                )
            )
            output.append(replace(proposal, status="review"))
        else:
            output.append(replace(proposal, parent_id=parent.id))
    return output, findings


def _propose_columns(
    page: LayoutPageInput,
    proposals: list[RegionProposal],
    root_id: str,
) -> tuple[list[RegionProposal], list[LayoutFinding]]:
    if any(proposal.kind == "column" for proposal in proposals):
        return proposals, []
    candidates = [
        proposal
        for proposal in proposals
        if proposal.source_region_ids
        and proposal.kind
        not in {
            "caption",
            "footer",
            "header",
            "marginalia",
            "page-number",
            "table-cell",
            "list-item",
            "intentional-exclusion",
        }
        and proposal.box.width < page.page_box.width * 45 // 100
    ]
    midpoint = page.page_box.x0 + page.page_box.width // 2
    left = [item for item in candidates if (item.box.x0 + item.box.x1) // 2 < midpoint]
    right = [item for item in candidates if (item.box.x0 + item.box.x1) // 2 > midpoint]
    if len(left) < 2 or len(right) < 2:
        return proposals, []
    shared_top = max(min(item.box.y0 for item in left), min(item.box.y0 for item in right))
    shared_bottom = min(max(item.box.y1 for item in left), max(item.box.y1 for item in right))
    if shared_top >= shared_bottom:
        return proposals, []
    columns: list[RegionProposal] = []
    replacements: dict[str, str] = {}
    for index, group in enumerate((left, right), start=1):
        box = _union(item.box for item in group)
        column_id = _stable_id(
            "region", page.page_id, "column", index, *(item.id for item in group)
        )
        columns.append(
            RegionProposal(
                column_id,
                "column",
                box,
                root_id,
                confidence_milli=800,
                status="proposed",
                properties={"column_index": index},
            )
        )
        replacements.update({item.id: column_id for item in group})
    nested = [
        replace(proposal, parent_id=replacements[proposal.id])
        if proposal.id in replacements and proposal.parent_id == root_id
        else proposal
        for proposal in proposals
    ]
    return (
        [*nested, *columns],
        [
            LayoutFinding(
                "column-structure-proposed",
                tuple(column.id for column in columns),
                "two-column geometry is measured; cross-column reading order remains a proposal",
                "info",
            )
        ],
    )


def _reading_key(
    proposal: RegionProposal, proposal_by_id: dict[str, RegionProposal]
) -> tuple[int, int, int, str]:
    parent = proposal_by_id.get(proposal.parent_id or "")
    column_x = parent.box.x0 if parent is not None and parent.kind == "column" else proposal.box.x0
    return column_x, proposal.box.y0, proposal.box.x0, proposal.id


def _ambiguous_pair(
    left: RegionProposal,
    right: RegionProposal,
    proposal_by_id: dict[str, RegionProposal],
) -> bool:
    left_parent = proposal_by_id.get(left.parent_id or "")
    right_parent = proposal_by_id.get(right.parent_id or "")
    if (
        left_parent is not None
        and right_parent is not None
        and left_parent.kind == right_parent.kind == "column"
    ):
        return False
    overlap = min(left.box.y1, right.box.y1) - max(left.box.y0, right.box.y0)
    minimum_height = min(left.box.height, right.box.height)
    separated = left.box.x1 <= right.box.x0 or right.box.x1 <= left.box.x0
    return separated and overlap > 0 and overlap * 2 >= minimum_height


def _union(boxes: Any) -> Box:
    values = tuple(boxes)
    return Box(
        min(box.x0 for box in values),
        min(box.y0 for box in values),
        max(box.x1 for box in values),
        max(box.y1 for box in values),
    )


def _proposal_sort_key(proposal: RegionProposal) -> tuple[int, int, int, str]:
    rank = 0 if proposal.kind == "page" else 1 if proposal.kind == "column" else 2
    return rank, proposal.box.y0, proposal.box.x0, proposal.id


def _finding_sort_key(finding: LayoutFinding) -> tuple[str, tuple[str, ...], str]:
    return finding.code, finding.subject_ids, finding.message


def _edge_sort_key(edge: LayoutEdge) -> tuple[str, str, str]:
    return edge.relation, edge.source_id, edge.target_id


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode()
    return f"{prefix}-{sha256(payload).hexdigest()[:20]}"
