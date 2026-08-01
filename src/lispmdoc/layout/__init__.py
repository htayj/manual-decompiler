"""Wave 4 physical-layout reconstruction proposals and evaluation."""

from .metrics import (
    DetectionMetrics,
    ReadingOrderMetrics,
    TableMetrics,
    geometric_region_metrics,
    iou_milli,
    reading_order_pair_accuracy,
    table_cell_span_metrics,
)
from .reconstruct import (
    account_coverage,
    build_reading_order_graph,
    reconstruct_layout,
    regions_from_ocr_page,
)
from .types import (
    CoverageReport,
    LayoutEdge,
    LayoutEvidenceRegion,
    LayoutFinding,
    LayoutPageInput,
    LayoutResult,
    ReadingOrderGraph,
    RegionProposal,
)

__all__ = [
    "CoverageReport",
    "DetectionMetrics",
    "LayoutEdge",
    "LayoutEvidenceRegion",
    "LayoutFinding",
    "LayoutPageInput",
    "LayoutResult",
    "ReadingOrderGraph",
    "ReadingOrderMetrics",
    "RegionProposal",
    "TableMetrics",
    "account_coverage",
    "build_reading_order_graph",
    "geometric_region_metrics",
    "iou_milli",
    "reading_order_pair_accuracy",
    "reconstruct_layout",
    "regions_from_ocr_page",
    "table_cell_span_metrics",
]
