"""Phase 2: SSRF guard, crawler and brand extraction (local fixture site + optional live run)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.brand.extract import extract_brand, parse_color, sanitize_svg
from app.crawl.crawler import crawl, normalize, site_markdown
from app.crawl.guard import BlockedURL, UrlGuard

FIX = Path(__file__).parent / "fixtures" / "site"


# ---------------------------------------------------------------- guard

def fake_resolver(table):
    return lambda host, port: table[host]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/", "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.1/",
    "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "http://[fc00::1]/", "http://0.0.0.0/",
    "http://100.64.0.1/", "http://[::ffff:127.0.0.1]/", "http://2130706433/", "http://224.0.0.1/",
    "file:///etc/passwd", "ftp://example.com/", "http://user:pw@example.com/", "javascript:alert(1)",
    "http://rebind.test/",
])
def test_guard_blocks(url):
    table = {"localhost": ["127.0.0.1"], "2130706433": ["127.0.0.1"], "rebind.test": ["93.184.216.34", "10.0.0.1"],
             "example.com": ["93.184.216.34"]}
    resolver = lambda host, port: table.get(host, [host.strip("[]")])  # noqa: E731
    with pytest.raises(BlockedURL):
        UrlGuard(resolver=resolver).check(url)


def test_guard_allows_public_hosts():
    guard = UrlGuard(resolver=fake_resolver({"www.example.com": ["93.184.216.34", "2606:2800:220:1::1"]}))
    assert guard.check("https://www.example.com/a?b=1") == "https://www.example.com/a?b=1"


def test_normalize_drops_fragments_and_tracking():
    assert normalize("https://Ex.com/a?utm_source=x&id=2#top") == "https://ex.com/a?id=2"


# ---------------------------------------------------------------- fixture site

async def test_crawl_and_brand_fixture_site(site, tmp_path):
    guard = UrlGuard(allow_hosts={"127.0.0.1"})
    result = await crawl(site, tmp_path / "crawl", guard, max_pages=10)
    urls = {p.url for p in result.pages}
    assert {site, site + "about.html", site + "services/"} <= urls
    assert not any("private" in u or "example.com" in u for u in urls)
    assert any("private.html" in u for u in result.skipped_robots)
    blocked = " ".join(result.blocked)
    assert "169.254.169.254" in blocked and "127.0.0.2" in blocked
    md = site_markdown(result)
    assert "# Robots that work." in md and "- Fixed-price projects" in md and "Book a call" in md
    assert (tmp_path / "crawl" / "screens" / "home-full.png").exists()

    kit = await extract_brand(result, tmp_path / "brand", guard)
    assert kit.site_name == "Acme" and kit.language == "en"
    assert kit.logo and kit.logo.kind == "svg" and kit.logo.shapes == 3
    logo_svg = (tmp_path / "brand" / "logo.svg").read_text()
    assert "<script" not in logo_svg and "onload" not in logo_svg and "evil.example" not in logo_svg
    assert {"#0a7d5a", "#f2994a", "#10243e"} <= set(kit.logo.colors) | {c.hex for c in kit.colors[:8]}
    assert kit.theme.colors["dark"] == "#10243e"
    assert kit.fonts[0].site_family == "TestSans" and kit.fonts[0].source == "site"
    assert (tmp_path / "brand" / kit.fonts[0].file).read_bytes()[:4] == b"\x00\x01\x00\x00"
    kinds = [i.kind for i in kit.images]
    assert kinds.count("client-logo") == 5 and kinds.count("photo") == 1
    saved = json.loads((tmp_path / "brand" / "brand.json").read_text())
    assert saved["theme"]["colors"] == kit.theme.colors


def test_sanitize_svg_rejects_non_svg_and_entities():
    assert sanitize_svg("<html><body/></html>") is None
    bomb = '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><svg>&b;</svg>'
    assert sanitize_svg(bomb) is None


def test_parse_color():
    assert parse_color("rgba(22, 70, 86, 0.5)") == ((22, 70, 86), 0.5)
    assert parse_color("#abc") == ((170, 187, 204), 1.0)
    assert parse_color("transparent") is None


live = pytest.mark.skipif(os.environ.get("MG_LIVE_WEB") != "1", reason="set MG_LIVE_WEB=1 to crawl a real site")


@live
async def test_live_they_consulting(tmp_path):
    result = await crawl("https://www.they-consulting.de/", tmp_path / "crawl", max_pages=4)
    kit = await extract_brand(result, tmp_path / "brand")
    assert {"#164656", "#7ec7be"} <= set(kit.theme.colors.values())
    assert kit.fonts[0].site_family.lower() == "chivo-black"
    assert kit.logo and kit.logo.on_light_ok and not kit.logo.on_dark_ok


def test_weak_logo_candidates_are_not_used(tmp_path):
    import asyncio

    from app.brand.extract import _save_logo
    from app.crawl.crawler import CrawlResult, PageData

    home = {"svgs": [], "images": [{"src": "https://x.test/person.png", "alt": "Jane Doe", "desc": "", "area": "main", "link": None,
                                    "y": 6000, "rh": 150, "rw": 150, "w": 280, "h": 280, "siblings": 2}],
            "ogImage": "https://x.test/og.png", "icons": []}
    crawl_result = CrawlResult(start_url="https://x.test/", pages=[PageData(url="https://x.test/", depth=0, data=home)])
    notes: list[str] = []
    logo, img = asyncio.run(_save_logo(None, UrlGuard(), crawl_result, tmp_path, notes))
    assert logo is None and img is None and "wordmark" in notes[0]


def test_data_url_logos_are_decoded_locally():
    from app.brand.extract import decode_data_url

    svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><path d='M0 0h10v10z' fill='%23f7b85a'/></svg>"
    res = decode_data_url("data:image/svg+xml," + svg.replace(" ", "%20"))
    assert res and res.content_type == "image/svg+xml" and b"#f7b85a" in res.body
    import base64 as b64
    res = decode_data_url("data:image/png;base64," + b64.b64encode(b"\x89PNG\r\n").decode())
    assert res and res.body.startswith(b"\x89PNG")
    assert decode_data_url("data:text/html,<script>") is not None  # decoded, but only image types reach the logo path
