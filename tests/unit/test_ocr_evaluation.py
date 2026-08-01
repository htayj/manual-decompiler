from __future__ import annotations

import pytest

from lispmdoc.ocr import GroundTruthRegion, evaluate_ground_truth


def test_ground_truth_reports_literal_text_errors_and_omissions() -> None:
    truth = (
        GroundTruthRegion("prose", "Hello, World!"),
        GroundTruthRegion("code", "(DEFUN FOO (X))", kind="code"),
        GroundTruthRegion("missing", "Must remain", required=True),
    )
    report = evaluate_ground_truth(
        truth, {"prose": "hello World!", "code": "(DEFUN FOO (x))", "extra": "x"}
    )

    assert report.cer.errors > 0
    assert report.wer.errors > 0
    assert report.punctuation.reference_units == 6
    assert report.case.substitutions >= 2
    assert report.code_tokens.reference_tokens == 3
    assert report.code_tokens.exact_tokens == 2
    assert report.omissions.omitted_region_ids == ("missing",)
    assert report.omissions.omitted_characters == len("Must remain")
    assert report.omissions.extra_region_ids == ("extra",)


def test_exact_code_token_metric_accounts_for_omitted_and_extra_tokens() -> None:
    report = evaluate_ground_truth(
        (GroundTruthRegion("code", "LOAD A B", kind="code"),), {"code": "LOAD X"}
    )

    assert report.code_tokens.reference_tokens == 3
    assert report.code_tokens.exact_tokens == 1
    assert report.code_tokens.omitted_tokens == 1
    assert report.code_tokens.extra_tokens == 0
    assert report.code_tokens.accuracy == pytest.approx(1 / 3)


def test_duplicate_ground_truth_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        evaluate_ground_truth(
            (GroundTruthRegion("same", "one"), GroundTruthRegion("same", "two")), {}
        )
