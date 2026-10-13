"""Phase 0: renderer contract, colour tagging and network sandbox."""

from __future__ import annotations

import json
from io import BytesIO
import subprocess
import urllib.request
from pathlib import Path

import imageio_ffmpeg
import pytest
from playwright.async_api import async_playwright

from app.render.renderer import FORMATS, _capture_frame, _ffmpeg_args, _open_page, _seek, render_stills, render_video
from app.render.server import serve_directory

MINI = Path(__file__).parent / "fixtures" / "mini"


def _probe(path: Path) -> str:
    out = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    return out.stderr


def test_ffmpeg_args_tag_bt709_in_filter_and_container():
    args = _ffmpeg_args(Path("x.mp4"), None, 1.0)
    vf = args[args.index("-vf") + 1]
    assert "out_color_matrix=bt709" in vf
    assert "setparams=color_primaries=bt709:color_trc=iec61966-2-1:colorspace=bt709" in vf
    assert args[args.index("-colorspace") + 1] == "bt709"


def test_server_sends_csp_and_stays_in_root(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello")
    with serve_directory(tmp_path) as base:
        with urllib.request.urlopen(f"{base}/a.txt") as resp:
            assert resp.read() == b"hello"
            assert "connect-src 'none'" in resp.headers["Content-Security-Policy"]
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{base}/../../etc/passwd")


@pytest.fixture
async def browser():
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        yield b
        await b.close()


async def test_sandbox_blocks_external_requests(browser):
    with serve_directory(MINI) as base:
        page, diag = await _open_page(browser, base, "index.html", FORMATS["feed"])
        await page.wait_for_function("() => window.__leaked !== null")
        assert await page.evaluate("() => window.__leaked") is False
        assert await page.evaluate("() => document.getElementById('ext').naturalWidth") == 0
        await page.context.close()


async def test_stills_follow_seek(browser, tmp_path: Path):
    from PIL import Image

    with serve_directory(MINI) as base:
        paths, _ = await render_stills(browser, base, "index.html", FORMATS["feed"], [0.0, 0.5], tmp_path)
    first, second = (Image.open(p).convert("RGB") for p in paths)
    assert first.getpixel((100, 200)) == (0xEA, 0x58, 0x37)  # box at x=0
    assert second.getpixel((100, 200)) != (0xEA, 0x58, 0x37)  # box moved to x=300
    assert second.getpixel((400, 200)) == (0xEA, 0x58, 0x37)


@pytest.mark.parametrize("name", ["feed", "story"])
async def test_fast_capture_matches_screenshot_after_seeks(browser, tmp_path: Path, name: str):
    from PIL import Image
    from app.harness.build import build_site

    site = build_site(Path(__file__).parents[1] / "examples/they", tmp_path / "site")
    with serve_directory(site) as base:
        page, _ = await _open_page(browser, base, "index.html", FORMATS[name])
        try:
            session = await page.context.new_cdp_session(page)
            for t in [0, 1 / 30, 0.5, 3.99, 4, 8, 12, 22.5, 29.9, 0.5]:
                await _seek(page, t)
                fast = Image.open(BytesIO(await _capture_frame(session))).convert("RGB")
                reference = Image.open(BytesIO(await page.screenshot(type="png"))).convert("RGB")
                assert fast.size == reference.size == (FORMATS[name].width, FORMATS[name].height)
                assert fast.tobytes() == reference.tobytes(), f"{name} at {t}s"
        finally:
            await page.context.close()


async def test_render_video_duration_size_and_tags(browser, tmp_path: Path):
    from PIL import Image

    out = tmp_path / "mini.mp4"
    with serve_directory(MINI) as base:
        await render_video(browser, base, "index.html", FORMATS["story"], out)
    info = _probe(out)
    assert "Duration: 00:00:01.00" in info
    assert "1080x1920" in info
    assert "bt709/bt709/iec61966-2-1" in info
    frame = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(out), "-vf", "select=eq(n\\,15)",
         "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, check=True,
    ).stdout
    pixels = Image.open(BytesIO(frame)).convert("RGB")
    # Frame 15 must show the box at t=0.5, not the previous compositor frame.
    assert pixels.getpixel((400, 200))[0] > 200
    assert pixels.getpixel((100, 200))[0] < 50


def test_server_survives_request_bursts(tmp_path: Path):
    from concurrent.futures import ThreadPoolExecutor

    for i in range(40):
        (tmp_path / f"f{i}.txt").write_text(str(i))
    with serve_directory(tmp_path) as base:
        with ThreadPoolExecutor(40) as pool:
            bodies = list(pool.map(lambda i: urllib.request.urlopen(f"{base}/f{i % 40}.txt").read(), range(200)))
    assert len(bodies) == 200
