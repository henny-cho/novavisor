"""Static UI file resolution as a pure function.

The HTTP layer hands a request target in and gets status plus body out,
so path containment and MIME choice are unit-tested without a socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

# The workbench serves its own few file kinds; an explicit table beats
# host-dependent mimetypes state.
_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".mjs": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}


@dataclass(frozen=True)
class StaticReply:
    status: int
    reason: str
    content_type: str
    body: bytes


def _error(status: int, reason: str) -> StaticReply:
    return StaticReply(status, reason, "text/plain; charset=utf-8", f"{reason}\n".encode())


def resolve(root: Path, target: str) -> StaticReply:
    path = unquote(urlsplit(target).path)
    if path.endswith("/"):
        path += "index.html"
    candidate = (root / path.lstrip("/")).resolve()
    if not candidate.is_relative_to(root.resolve()):
        return _error(403, "Forbidden")
    if not candidate.is_file():
        return _error(404, "Not Found")
    content_type = _CONTENT_TYPES.get(candidate.suffix, "application/octet-stream")
    return StaticReply(200, "OK", content_type, candidate.read_bytes())
