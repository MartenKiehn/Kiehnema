"""Frame-accurate renderer: drives a composition page via the harness contract and encodes MP4.

Contract (same as the original render.mjs): the page sets ``window.__ready = true`` once fonts and
images are loaded, exposes ``window.__duration`` (seconds) and ``window.__seek(t)`` which puts the
paused GSAP timeline at time ``t``. The page receives ``?render=1&format=<name>``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit

import imageio_ffmpeg
from playwright.async_api import Browser, Page, Route, async_playwright

log = logging.getLogger(__name__)

FPS = 30


@dataclass(frozen=True)
class VideoFormat:
    name: str
    width: int
    height: int


FORMATS: dict[str, VideoFormat] = {
    "feed": VideoFormat("feed", 1080, 1350),  # 4:5  LinkedIn / Instagram feed
    "story": VideoFormat("story", 1080, 1920),  # 9:16 Reels / Stories
}

ProgressFn = Callable[[str, int, int], Awaitable[None] | None]


class RenderError(RuntimeError):
    """The composition could not be loaded or encoded."""


@dataclass
class PageDiagnostics:
    errors: list[str]
    blocked_requests: list[str]


async def _open_page(browser: Browser, base_url: str, page_path: str, fmt: VideoFormat) -> tuple[Page, PageDiagnostics]:
    context = await browser.new_context(viewport={"width": fmt.width, "height": fmt.height}, device_scale_factor=1)
    page = await context.new_page()
    diag = PageDiagnostics(errors=[], blocked_requests=[])
    origin = urlsplit(base_url).netloc

    async def guard(route: Route) -> None:
        # Model-written code must not reach anything but the local asset server.
        if urlsplit(route.request.url).netloc == origin:
            await route.continue_()
        else:
            diag.blocked_requests.append(route.request.url)
            await route.abort()

    await context.route("**/*", guard)
    page.on("pageerror", lambda exc: diag.errors.append(str(exc)))
    page.on("requestfailed", lambda req: diag.errors.append(f"asset failed to load: {req.url} ({req.failure})")
            if urlsplit(req.url).netloc == origin else None)
    page.on("console", lambda msg: diag.errors.append(msg.text) if msg.type == "error" else None)

    await page.goto(f"{base_url}/{page_path.lstrip('/')}?render=1&format={fmt.name}")
    try:
        await page.wait_for_function("() => window.__ready === true || !!window.__mgFailed", timeout=30_000)
    except Exception as exc:
        await page.context.close()
        raise RenderError(f"composition never became ready ({fmt.name}): {diag.errors[:5]}") from exc
    failed = await page.evaluate("() => window.__mgFailed || null")
    if failed:
        await page.context.close()
        raise RenderError(f"composition failed to build ({fmt.name}): {failed}")
    return page, diag


async def _seek(page: Page, t: float) -> None:
    # Wrapper returns nothing: serialising a GSAP timeline back to Python would hang.
    await page.evaluate("t => { window.__seek(t); }", t)


def _ffmpeg_args(out: Path, audio: Path | None, duration: float) -> list[str]:
    args = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "png", "-i", "-",
    ]
    if audio is not None:
        args += ["-i", str(audio), "-map", "0:v", "-map", "1:a"]
    args += [
        "-t", str(duration),
        # Explicit BT.709 conversion + tags (sRGB transfer like the source). Untagged files make
        # QuickTime/Safari/social apps guess the matrix and visibly shift orange and mint.
        # ffmpeg >= 7 takes colour properties from the frames, so set them in the filter chain too.
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
        "setparams=color_primaries=bt709:color_trc=iec61966-2-1:colorspace=bt709:range=tv",
        "-color_primaries", "bt709", "-color_trc", "iec61966-2-1", "-colorspace", "bt709", "-color_range", "tv",
        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-profile:v", "high", "-r", str(FPS),
    ]
    if audio is not None:
        args += ["-c:a", "aac", "-b:a", "192k"]
    args += ["-movflags", "+faststart", str(out)]
    return args


async def render_video(
    browser: Browser,
    base_url: str,
    page_path: str,
    fmt: VideoFormat,
    out: Path,
    audio: Path | None = None,
    progress: ProgressFn | None = None,
) -> PageDiagnostics:
    page, diag = await _open_page(browser, base_url, page_path, fmt)
    try:
        missing = [e for e in diag.errors if e.startswith("asset failed to load")]
        if missing:
            raise RenderError(f"{fmt.name}: {missing[:3]}")
        duration = float(await page.evaluate("() => window.__duration"))
        frames = round(duration * FPS)
        out.parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *_ffmpeg_args(out, audio, duration),
            stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None
        for i in range(frames):
            await _seek(page, i / FPS)
            proc.stdin.write(await page.screenshot(type="png"))
            await proc.stdin.drain()
            if progress and i % FPS == 0:
                result = progress(fmt.name, i, frames)
                if asyncio.iscoroutine(result):
                    await result
        proc.stdin.close()
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RenderError(f"ffmpeg failed ({proc.returncode}): {stderr.decode(errors='replace')[-800:]}")
        if progress:
            result = progress(fmt.name, frames, frames)
            if asyncio.iscoroutine(result):
                await result
        log.info("rendered %s: %d frames -> %s", fmt.name, frames, out)
        return diag
    finally:
        await page.context.close()


async def render_stills(
    browser: Browser, base_url: str, page_path: str, fmt: VideoFormat, times: list[float], out_dir: Path
) -> tuple[list[Path], PageDiagnostics]:
    page, diag = await _open_page(browser, base_url, page_path, fmt)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    try:
        for t in times:
            await _seek(page, t)
            path = out_dir / f"{fmt.name}-{t:06.2f}s.png"
            await page.screenshot(path=str(path))
            paths.append(path)
        return paths, diag
    finally:
        await page.context.close()


async def _cli() -> None:
    import argparse

    from app.render.server import serve_directory

    parser = argparse.ArgumentParser(description="Render a harness-contract composition to MP4 or stills.")
    parser.add_argument("--root", type=Path, required=True, help="directory served as the page origin")
    parser.add_argument("--page", default="index.html")
    parser.add_argument("--format", default="feed,story")
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--out", type=Path, default=Path("out"))
    parser.add_argument("--stills", help="comma-separated seconds; renders PNGs instead of video")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with serve_directory(args.root) as base_url:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                for name in args.format.split(","):
                    fmt = FORMATS[name]
                    if args.stills:
                        times = [float(t) for t in args.stills.split(",")]
                        paths, diag = await render_stills(browser, base_url, args.page, fmt, times, args.out / "stills")
                        print("\n".join(str(p) for p in paths))
                    else:
                        out = args.out / f"{fmt.name}-{fmt.width}x{fmt.height}.mp4"
                        diag = await render_video(
                            browser, base_url, args.page, fmt, out, args.audio,
                            progress=lambda n, i, total: print(f"\r{n}: {i}/{total} frames", end="", flush=True),
                        )
                        print()
                    if diag.errors or diag.blocked_requests:
                        print(f"page errors: {diag.errors[:5]} blocked: {diag.blocked_requests[:5]}")
            finally:
                await browser.close()


if __name__ == "__main__":
    asyncio.run(_cli())
