"""Tiny threaded static file server bound to 127.0.0.1, confined to one directory.

Compositions are rendered from here so the browser only ever talks to a local origin
(the renderer additionally blocks every request that does not target this origin).
"""

from __future__ import annotations

import functools
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator


class _QuietHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".svg": "image/svg+xml",
        ".ttf": "font/ttf",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
    }

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass

    def end_headers(self) -> None:
        # Compositions are model-written code: forbid any network access beyond this origin.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'none'; frame-src 'none'; "
            "object-src 'none'; base-uri 'none'; form-action 'none'",
        )
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


class _Server(ThreadingHTTPServer):
    # The default listen backlog (5) resets connections when a page requests many assets at once.
    request_queue_size = 256
    daemon_threads = True


@contextmanager
def serve_directory(root: Path) -> Iterator[str]:
    """Serve ``root`` on a random localhost port; yields the base URL."""
    handler = functools.partial(_QuietHandler, directory=str(root.resolve()))
    server = _Server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
