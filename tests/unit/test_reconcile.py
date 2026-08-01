from __future__ import annotations

from lispmdoc.ocr import BBox, EngineEvidence, OCRLine, OCRPage, OCRRegion, OCRSpan, OCRToken
from lispmdoc.ocr.evaluation import GroundTruthRegion, evaluate_ground_truth
from lispmdoc.reconcile import (
    Calibration,
    CandidatePage,
    Reconciler,
    ReviewQueue,
    align_lines,
    align_regions,
    align_tokens,
)


def _page(engine: str, text: str, *, confidence: float = 0.9, second: str | None = None) -> OCRPage:
    lines = [_line(engine, 0, text, confidence)]
    if second is not None:
        lines.append(_line(engine, 1, second, confidence))
    region = OCRRegion(f"{engine}-region", "text", BBox(0, 0, 1000, 1000), tuple(lines))
    return OCRPage("page", 1000, 1000, engine, (region,), EngineEvidence(engine, "1"))


def _line(engine: str, number: int, text: str, confidence: float) -> OCRLine:
    box = BBox(0, number * 100, 800, number * 100 + 80)
    token = OCRToken(
        f"{engine}-token-{number}", text, box, confidence, native_id=f"native-{number}"
    )
    span = OCRSpan(f"{engine}-span-{number}", text, box, (token,), confidence)
    return OCRLine(f"{engine}-line-{number}", text, box, (span,), confidence, reading_order=number)


def _candidate(page: OCRPage, char: str) -> CandidatePage:
    return CandidatePage(page, char * 64)


def test_alignment_and_selection_are_deterministic_and_retain_alternatives() -> None:
    routed = _candidate(_page("pdf-text", "(DEFUN FOO)", confidence=0.8), "a")
    agreeing = _candidate(_page("tesseract", "(DEFUN FOO)", confidence=0.95), "b")
    disagreeing = _candidate(_page("other", "(DEFUN F00)", confidence=0.99), "c")
    result = Reconciler((Calibration("tesseract", 1000),)).reconcile(
        (disagreeing, routed, agreeing), routed_engine="pdf-text"
    )
    assert result.diplomatic_text == "(DEFUN FOO)"
    assert result.lines[0].source_engine == "tesseract"
    assert result.lines[0].tokens[0].selected.evidence_sha256 == "a" * 64
    assert {finding.code for finding in result.findings} >= {"ENGINE_DISAGREEMENT", "LISP_TOKEN"}
    assert align_lines(routed, agreeing)[0].iou_milli == 1000
    assert align_regions(routed, agreeing)[0].level == "region"
    assert align_tokens(routed, agreeing)[0].level == "token"


def test_reconciliation_never_worsens_cer_or_omissions_than_routed_candidate() -> None:
    routed = _candidate(_page("routed", "correct literal"), "a")
    bad = _candidate(_page("bad", "wrong literal"), "b")
    result = Reconciler().reconcile((bad, routed), routed_engine="routed")
    truth = (GroundTruthRegion("routed-line-0", "correct literal"),)
    baseline = evaluate_ground_truth(truth, routed.page)
    selected = {line.source_line_id: line.text for line in result.lines}
    reconciled = evaluate_ground_truth(truth, selected)
    assert reconciled.cer.error_rate <= baseline.cer.error_rate
    assert (
        reconciled.omissions.silently_omitted_regions <= baseline.omissions.silently_omitted_regions
    )


def test_high_risk_findings_and_queue_are_queryable_and_suggestions_are_non_authoritative() -> None:
    routed = _candidate(
        _page("route", "key=:FOO 42 = (X)\u00a0-\ufffd", confidence=0.2, second="repeat"), "a"
    )
    omitted = _candidate(_page("other", "key=:FOO 42 = (X)", second="repeat"), "b")
    result = Reconciler(low_confidence_milli=700).reconcile(
        (routed, omitted), routed_engine="route"
    )
    codes = {finding.code for finding in result.findings}
    assert {
        "LOW_CONFIDENCE",
        "IDENTIFIER",
        "NUMBER",
        "KEY_NAME",
        "EQUATION",
        "SYMBOL",
        "LISP_TOKEN",
        "UNRESOLVED_GLYPH",
        "UNEXPECTED_UNICODE",
    } <= codes
    assert result.suggestions[0].diplomatic_text == result.lines[0].text
    assert result.suggestions[0].suggested_text != result.lines[0].text
    queue = ReviewQueue(result.findings)
    assert queue.query(code="LOW_CONFIDENCE")
    assert all(finding.severity == "high" for finding in queue.query(minimum_severity="high"))


def test_duplicate_and_hyphenation_findings_are_retained() -> None:
    candidate = _candidate(_page("route", "word-", second="word-"), "a")
    result = Reconciler().reconcile((candidate,), routed_engine="route")
    assert {finding.code for finding in result.findings} >= {
        "DUPLICATE_LINE",
        "HYPHENATION_AMBIGUITY",
    }


def test_missing_engine_line_is_an_explicit_omission() -> None:
    routed = _candidate(_page("route", "first", second="second"), "a")
    partial = _candidate(_page("partial", "first"), "b")
    result = Reconciler().reconcile((routed, partial), routed_engine="route")
    assert any(
        finding.code == "OMISSION" and finding.subject_id == "route-line-1"
        for finding in result.findings
    )
