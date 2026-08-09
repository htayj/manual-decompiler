from __future__ import annotations

import http.client
import threading
from pathlib import Path

import pytest

from lispmdoc.benchmark.native_pdf_review_server import (
    ReviewServerError,
    review_handler,
    sealed_review_root,
)


def test_local_review_server_exposes_only_sealed_review_assets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    review = workspace / "review"
    (review / "assets").mkdir(parents=True)
    (review / "index.html").write_text("review", encoding="utf-8")
    (review / "assets/page.png").write_bytes(b"png")
    (workspace / "input").mkdir()
    (workspace / "input/secret.pdf").write_bytes(b"secret")
    root = sealed_review_root(workspace)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), review_handler(root))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        for target, expected in (
            ("/index.html", 200),
            ("/assets/page.png", 200),
            ("/../input/secret.pdf", 404),
            ("/input/secret.pdf", 404),
            ("/%2e%2e/input/secret.pdf", 404),
        ):
            connection.request("GET", target)
            response = connection.getresponse()
            response.read()
            assert response.status == expected
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_review_server_rejects_symlink_assets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    review = workspace / "review"
    review.mkdir(parents=True)
    (review / "index.html").write_text("review", encoding="utf-8")
    outside = workspace / "outside.pdf"
    outside.write_bytes(b"secret")
    (review / "escape.pdf").symlink_to(outside)
    with pytest.raises(ReviewServerError, match="symlink"):
        sealed_review_root(workspace)
