"""Deterministic PAGE XML interchange for normalized literal OCR records."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import cast

from lispmdoc.ocr import (
    BBox,
    EngineEvidence,
    OCRLine,
    OCRPage,
    OCRRegion,
    OCRSpan,
    OCRToken,
    make_id,
)

_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"
ET.register_namespace("", _NS)


def _tag(name: str) -> str:
    return f"{{{_NS}}}{name}"


def _local(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _points(box: BBox | None) -> str:
    if box is None:
        return ""
    return f"{box.x0},{box.y0} {box.x1},{box.y0} {box.x1},{box.y1} {box.x0},{box.y1}"


def _box(element: ET.Element) -> BBox | None:
    coords = next((child for child in element if _local(child) == "Coords"), None)
    if coords is None or not coords.attrib.get("points"):
        return None
    points: list[tuple[int, int]] = []
    try:
        for part in coords.attrib["points"].split():
            x, y = part.split(",", 1)
            points.append((int(x), int(y)))
    except ValueError as error:
        raise ValueError("invalid PAGE Coords points") from error
    if len(points) < 2:
        raise ValueError("PAGE Coords needs at least two points")
    return BBox(
        min(x for x, _ in points),
        min(y for _, y in points),
        max(x for x, _ in points),
        max(y for _, y in points),
    )


def _text_equiv(parent: ET.Element, text: str, confidence: float | None) -> None:
    attributes: dict[str, str] = {}
    if confidence is not None:
        attributes["conf"] = format(confidence, ".12g")
    equivalent = ET.SubElement(parent, _tag("TextEquiv"), attributes)
    ET.SubElement(equivalent, _tag("Unicode")).text = text


def export_pagexml(page: OCRPage) -> bytes:
    root = ET.Element(_tag("PcGts"))
    xml_page = ET.SubElement(
        root,
        _tag("Page"),
        {
            "imageFilename": page.page_id,
            "imageWidth": str(page.width),
            "imageHeight": str(page.height),
        },
    )
    for region in page.regions:
        xml_region = ET.SubElement(
            xml_page, _tag("TextRegion"), {"id": region.id, "type": region.kind}
        )
        ET.SubElement(xml_region, _tag("Coords"), {"points": _points(region.bbox)})
        for line in region.lines:
            xml_line = ET.SubElement(xml_region, _tag("TextLine"), {"id": line.id})
            ET.SubElement(xml_line, _tag("Coords"), {"points": _points(line.bbox)})
            for span in line.spans:
                for token in span.tokens:
                    word = ET.SubElement(xml_line, _tag("Word"), {"id": token.id})
                    ET.SubElement(word, _tag("Coords"), {"points": _points(token.bbox)})
                    _text_equiv(word, token.text, token.confidence)
            _text_equiv(xml_line, line.text, line.confidence)
    return cast(
        bytes, ET.tostring(root, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    )


def _unicode(element: ET.Element) -> str:
    value = next((child for child in element.iter() if _local(child) == "Unicode"), None)
    return "" if value is None or value.text is None else value.text


def _confidence(element: ET.Element) -> float | None:
    equivalent = next((child for child in element if _local(child) == "TextEquiv"), None)
    if equivalent is None:
        return None
    try:
        return float(equivalent.attrib["conf"]) if "conf" in equivalent.attrib else None
    except ValueError:
        return None


def import_pagexml(data: bytes) -> OCRPage:
    if b"<!DOCTYPE" in data.upper():
        raise ValueError("PAGE XML documents with DTD declarations are not accepted")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError(f"invalid PAGE XML: {error}") from error
    page = next((item for item in root.iter() if _local(item) == "Page"), None)
    if page is None:
        raise ValueError("PAGE XML contains no Page")
    try:
        width, height = int(page.attrib["imageWidth"]), int(page.attrib["imageHeight"])
    except (KeyError, ValueError) as error:
        raise ValueError("PAGE Page requires integer imageWidth and imageHeight") from error
    page_id = page.attrib.get("imageFilename", make_id("page", "pagexml", width, height))
    regions: list[OCRRegion] = []
    for region_index, xml_region in enumerate(
        item for item in page if _local(item) == "TextRegion"
    ):
        lines: list[OCRLine] = []
        for line_index, xml_line in enumerate(
            item for item in xml_region if _local(item) == "TextLine"
        ):
            tokens: list[OCRToken] = []
            for word_index, word in enumerate(item for item in xml_line if _local(item) == "Word"):
                text = _unicode(word)
                tokens.append(
                    OCRToken(
                        word.attrib.get(
                            "id",
                            make_id("token", page_id, region_index, line_index, word_index, text),
                        ),
                        text,
                        _box(word),
                        _confidence(word),
                        native_id=word.attrib.get("id"),
                    )
                )
            text = _unicode(xml_line) or " ".join(token.text for token in tokens)
            line_id = xml_line.attrib.get(
                "id", make_id("line", page_id, region_index, line_index, text)
            )
            box = _box(xml_line) or BBox.union(
                [token.bbox for token in tokens if token.bbox is not None]
            )
            span = OCRSpan(make_id("span", line_id, text), text, box, tuple(tokens))
            lines.append(
                OCRLine(
                    line_id,
                    text,
                    box,
                    (span,),
                    _confidence(xml_line),
                    reading_order=line_index,
                    native_id=xml_line.attrib.get("id"),
                )
            )
        region_id = xml_region.attrib.get("id", make_id("region", page_id, region_index))
        box = _box(xml_region) or BBox.union([line.bbox for line in lines if line.bbox is not None])
        regions.append(
            OCRRegion(
                region_id,
                xml_region.attrib.get("type", "text"),
                box,
                tuple(lines),
                reading_order=region_index,
                native_id=xml_region.attrib.get("id"),
            )
        )
    return OCRPage(
        page_id,
        width,
        height,
        "pagexml",
        tuple(regions),
        EngineEvidence("pagexml", None, {"format": "PAGE XML", "literal": True}),
        native_output=data,
        native_output_media_type="application/vnd.prima.page+xml",
    )
