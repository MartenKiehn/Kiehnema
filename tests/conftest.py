"""Shared fixtures: a local multi-page website with SSRF traps (served on 127.0.0.1)."""

from __future__ import annotations

import functools
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

FIX = Path(__file__).parent / "fixtures" / "site"


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/redirect-out":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        if self.path == "/img-redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.2/internal.png")
            self.end_headers()
            return
        super().do_GET()


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    shutil.copytree(FIX, root)
    Image.new("RGB", (1600, 1000), (40, 120, 200)).save(root / "photo.jpg")
    for i in range(1, 6):
        Image.new("RGBA", (200, 80), (20 * i, 30, 30, 255)).save(root / f"c{i}.png")
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
