"""Render a built site end to end: read the runtime registry, synthesise music, encode all formats."""

from __future__ import annotations

from app.security.workspace import browser_environment

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Browser, async_playwright

from app.harness.build import Manifest
from app.music.synth import MusicPlan, synthesize, write_wav
from app.render.renderer import FORMATS, ProgressFn, _open_page, render_video
from app.render.server import serve_directory

log = logging.getLogger(__name__)


@dataclass
class RenderResult:
    videos: dict[str, Path]
    music: Path
    music_warnings: list[str] = field(default_factory=list)
    page_errors: dict[str, list[str]] = field(default_factory=dict)


async def read_registry(browser: Browser, base_url: str, fmt_name: str = "feed") -> dict:
    """Scenes, cues and manifest as registered by the composition at build time."""
    page, _ = await _open_page(browser, base_url, "index.html", FORMATS[fmt_name])
    try:
        return await page.evaluate("() => JSON.parse(JSON.stringify(window.__mg))")
    finally:
        await page.context.close()


def music_plan(manifest: Manifest, registry: dict) -> MusicPlan:
    return MusicPlan.from_registry(manifest.duration, manifest.music, registry["scenes"], registry["cues"])


async def render_site(
    site: Path,
    out_dir: Path,
    formats: tuple[str, ...] = ("feed", "story"),
    progress: ProgressFn | None = None,
) -> RenderResult:
    manifest = Manifest.model_validate_json((site / "composition" / "manifest.json").read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    with serve_directory(site) as base:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(env=browser_environment(), chromium_sandbox=True)
            try:
                plan = music_plan(manifest, await read_registry(browser, base))
                music = out_dir / "music.wav"
                samples = await asyncio.to_thread(synthesize, plan)
                write_wav(samples, music)
                result = RenderResult(videos={}, music=music, music_warnings=plan.warnings)
                for name in formats:
                    fmt = FORMATS[name]
                    out = out_dir / f"{name}-{fmt.width}x{fmt.height}.mp4"
                    diag = await render_video(browser, base, "index.html", fmt, out, music, progress)
                    result.videos[name] = out
                    result.page_errors[name] = diag.errors + [f"blocked: {u}" for u in diag.blocked_requests]
                return result
            finally:
                await browser.close()


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    site_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    fmts = tuple(sys.argv[3].split(",")) if len(sys.argv) > 3 else ("feed", "story")
    res = asyncio.run(render_site(site_dir, out, fmts, progress=lambda n, i, t: print(f"\r{n}: {i}/{t}", end="", flush=True)))
    print(f"\n{res}")
