from lispmdoc.ingest.inspect import _classify_document


def _page(label: str) -> dict[str, object]:
    return {"classification": {"label": label}}


def test_ambiguous_pages_do_not_turn_born_digital_document_into_hybrid() -> None:
    result = _classify_document([_page("born-digital"), _page("ambiguous"), _page("born-digital")])

    assert result["label"] == "born-digital"
    assert result["confidence"] == "medium"


def test_actual_scan_and_born_digital_mix_is_hybrid() -> None:
    result = _classify_document([_page("born-digital"), _page("scan-bilevel")])

    assert result["label"] == "hybrid"
