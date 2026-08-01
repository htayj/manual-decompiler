#!/usr/bin/env python3
"""Run the locked English PP-OCRv5 pipeline on one rendered page."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from paddleocr import PaddleOCR


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    ocr = PaddleOCR(
        lang="en",
        ocr_version="PP-OCRv5",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="gpu",
    )
    results = list(ocr.predict(str(args.input), return_word_box=True))
    if len(results) != 1:
        raise SystemExit(f"expected one page result, got {len(results)}")
    payload = results[0].json
    if not isinstance(payload, dict):
        payload = json.loads(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
