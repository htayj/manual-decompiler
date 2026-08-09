"""Local-only, sealed static server for native-PDF evidence review projects."""

from __future__ import annotations

import functools
import http.server
import stat
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


class ReviewServerError(ValueError):
    """The requested review project is not a sealed regular-file tree."""


def sealed_review_root(workspace: Path) -> Path:
    """Return a review directory only if it has no links or nonregular assets."""

    if workspace.is_symlink() or not workspace.is_dir():
        raise ReviewServerError("workspace must be a non-symlink directory")
    review = workspace / "review"
    if review.is_symlink() or not review.is_dir():
        raise ReviewServerError("workspace lacks a non-symlink review directory")
    for item in (review, *review.rglob("*")):
        metadata = item.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ReviewServerError(f"review tree contains a symlink: {item.relative_to(review)}")
        if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise ReviewServerError(
                f"review tree contains a nonregular file: {item.relative_to(review)}"
            )
    index = review / "index.html"
    if not index.is_file() or index.is_symlink():
        raise ReviewServerError("review tree lacks a regular index.html")
    return review.resolve()


class _ReviewOnlyHandler(http.server.SimpleHTTPRequestHandler):
    """Serve only regular files inside the sealed review root, never listings."""

    def list_directory(self, path: str) -> None:  # type: ignore[override]
        self.send_error(404)
        return None

    def _request_path_is_safe(self) -> bool:
        decoded = unquote(urlsplit(self.path).path)
        candidate = PurePosixPath(decoded)
        if candidate.is_absolute():
            candidate = PurePosixPath(*candidate.parts[1:])
        if any(part in {"", ".", ".."} for part in candidate.parts):
            return False
        root = Path(self.directory or ".").resolve()
        target = root.joinpath(*candidate.parts)
        try:
            target.relative_to(root)
            metadata = target.lstat()
        except (OSError, ValueError):
            return False
        return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler interface
        if not self._request_path_is_safe():
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler interface
        if not self._request_path_is_safe():
            self.send_error(404)
            return
        super().do_HEAD()


def review_handler(review_root: Path) -> type[http.server.BaseHTTPRequestHandler]:
    """Build a handler bound to one prevalidated review directory."""

    return functools.partial(_ReviewOnlyHandler, directory=review_root.as_posix())  # type: ignore[return-value]
