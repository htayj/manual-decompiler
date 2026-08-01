"""Deterministic ALTO XML interchange for normalized literal OCR records."""

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

_NS = "http://www.loc.gov/standards/alto/ns-v4#"
ET.register_namespace("", _NS)


def _tag(name: str) -> str:
    return f"{{{_NS}}}{name}"


def _local(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _box_attributes(box: BBox | None) -> dict[str, str]:
    if box is None:
        return {}
    return {
        "HPOS": str(box.x0),
        "VPOS": str(box.y0),
        "WIDTH": str(box.x1 - box.x0),
        "HEIGHT": str(box.y1 - box.y0),
    }


def _box(element: ET.Element) -> BBox | None:
    try:
        x0, y0 = int(element.attrib["HPOS"]), int(element.attrib["VPOS"])
        width, height = int(element.attrib["WIDTH"]), int(element.attrib["HEIGHT"])
    except (KeyError, ValueError):
        return None
    if width < 0 or height < 0:
        raise ValueError("ALTO geometry cannot have negative extent")
    return BBox(x0, y0, x0 + width, y0 + height)


def export_alto(page: OCRPage) -> bytes:
    """Serialize a normalized page without changing literal text."""
    root = ET.Element(_tag("alto"))
    description = ET.SubElement(root, _tag("Description"))
    ET.SubElement(description, _tag("Processing"), {"ID": page.engine})
    layout = ET.SubElement(root, _tag("Layout"))
    xml_page = ET.SubElement(
        layout,
        _tag("Page"),
        {"ID": page.page_id, "WIDTH": str(page.width), "HEIGHT": str(page.height)},
    )
    print_space = ET.SubElement(
        xml_page, _tag("PrintSpace"), _box_attributes(BBox(0, 0, page.width, page.height))
    )
    for region in page.regions:
        block = ET.SubElement(
            print_space, _tag("TextBlock"), {"ID": region.id, **_box_attributes(region.bbox)}
        )
        for line in region.lines:
            xml_line = ET.SubElement(
                block, _tag("TextLine"), {"ID": line.id, **_box_attributes(line.bbox)}
            )
            tokens = tuple(token for span in line.spans for token in span.tokens)
            if not tokens:
                tokens = (OCRToken(make_id("token", line.id, line.text), line.text, line.bbox),)
            for index, token in enumerate(tokens):
                if index:
                    ET.SubElement(xml_line, _tag("SP"))
                attributes = {"ID": token.id, "CONTENT": token.text, **_box_attributes(token.bbox)}
                if token.confidence is not None:
                    attributes["WC"] = format(token.confidence, ".12g")
                ET.SubElement(xml_line, _tag("String"), attributes)
    return cast(
        bytes, ET.tostring(root, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    )


def import_alto(data: bytes) -> OCRPage:
    """Parse a safe, local ALTO page into literal normalized OCR evidence."""
    if b"<!DOCTYPE" in data.upper():
        raise ValueError("ALTO documents with DTD declarations are not accepted")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError(f"invalid ALTO XML: {error}") from error
    page_element = next((item for item in root.iter() if _local(item) == "Page"), None)
    if page_element is None:
        raise ValueError("ALTO document contains no Page")
    try:
        width, height = int(page_element.attrib["WIDTH"]), int(page_element.attrib["HEIGHT"])
    except (KeyError, ValueError) as error:
        raise ValueError("ALTO Page requires integer WIDTH and HEIGHT") from error
    page_id = page_element.attrib.get("ID", make_id("page", "alto", width, height))
    regions: list[OCRRegion] = []
    for region_index, block in enumerate(
        item for item in root.iter() if _local(item) == "TextBlock"
    ):
        lines: list[OCRLine] = []
        for line_index, xml_line in enumerate(item for item in block if _local(item) == "TextLine"):
            tokens: list[OCRToken] = []
            for token_index, string in enumerate(
                item for item in xml_line if _local(item) == "String"
            ):
                text = string.attrib.get("CONTENT")
                if text is None:
                    continue
                raw_confidence = string.attrib.get("WC")
                try:
                    confidence = float(raw_confidence) if raw_confidence is not None else None
                except ValueError:
                    confidence = None
                tokens.append(
                    OCRToken(
                        string.attrib.get(
                            "ID",
                            make_id("token", page_id, region_index, line_index, token_index, text),
                        ),
                        text,
                        _box(string),
                        confidence,
                        native_id=string.attrib.get("ID"),
                    )
                )
            text = " ".join(token.text for token in tokens)
            line_id = xml_line.attrib.get(
                "ID", make_id("line", page_id, region_index, line_index, text)
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
                    reading_order=line_index,
                    native_id=xml_line.attrib.get("ID"),
                )
            )
        region_id = block.attrib.get("ID", make_id("region", page_id, region_index))
        box = _box(block) or BBox.union([line.bbox for line in lines if line.bbox is not None])
        regions.append(
            OCRRegion(
                region_id,
                "text",
                box,
                tuple(lines),
                reading_order=region_index,
                native_id=block.attrib.get("ID"),
            )
        )
    return OCRPage(
        page_id,
        width,
        height,
        "alto",
        tuple(regions),
        EngineEvidence("alto", None, {"format": "ALTO XML", "literal": True}),
        native_output=data,
        native_output_media_type="application/alto+xml",
    )
