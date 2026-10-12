"""Same-site breadth-first crawler (Playwright) with SSRF guard, robots.txt and limits."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from playwright.async_api import Page, async_playwright

from app.crawl.browser import NetLog, guarded_context
from app.crawl.fetch import MAX_REDIRECTS, USER_AGENT, fetch_ok, make_client
from app.crawl.guard import BlockedURL, UrlGuard

log = logging.getLogger(__name__)

EXTRACT_JS = (Path(__file__).parent / "extract.js").read_text()
SKIP_EXT = re.compile(r"\.(pdf|zip|jpe?g|png|gif|webp|svg|mp4|mov|mp3|docx?|xlsx?|pptx?|ics|xml|json|txt)$", re.I)
COOKIE_BUTTONS = ["Alle akzeptieren", "Akzeptieren", "Zustimmen", "Alle zulassen", "Accept all", "Accept", "Allow all", "I agree", "OK"]
MAX_TEXT_CHARS = 40_000

ProgressFn = Callable[[str], Awaitable[None] | None]


@dataclass
class PageData:
    url: str
    depth: int
    data: dict  # output of extract.js
    screenshot: str | None = None  # relative to the crawl dir


@dataclass
class CrawlResult:
    start_url: str
    pages: list[PageData] = field(default_factory=list)
    home_full_screenshot: str | None = None
    skipped_robots: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def home(self) -> PageData:
        return self.pages[0]

    def save(self, out: Path) -> Path:
        path = out / "crawl.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1))
        temporary.replace(path)
        return path

    @classmethod
    def load(cls, out: Path) -> "CrawlResult":
        raw = json.loads((out / "crawl.json").read_text())
        raw["pages"] = [PageData(**p) for p in raw["pages"]]
        return cls(**raw)


def _site_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalize(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlsplit(url)
    query = "&".join(q for q in p.query.split("&") if q and not q.lower().startswith(("utm_", "fbclid", "gclid")))
    path = p.path or "/"
    return urlunsplit((p.scheme.lower(), (p.hostname or "").lower() + (f":{p.port}" if p.port else ""), path, query, ""))


async def _robots(guard: UrlGuard, start: str) -> RobotFileParser:
    rp = RobotFileParser()
    p = urlsplit(start)
    async with make_client(timeout=10) as client:
        res = await fetch_ok(client, f"{p.scheme}://{p.netloc}/robots.txt", guard, max_bytes=500_000)
    rp.parse(res.body.decode("utf-8", "replace").splitlines() if res else [])
    return rp


async def _goto(page: Page, url: str, guard: UrlGuard, net: NetLog) -> str:
    """Navigate, checking every redirect hop; returns the final URL."""
    for _ in range(MAX_REDIRECTS + 1):
        guard.check(url)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            if not net.redirects:
                raise
            src, target = net.redirects.popitem()  # the navigation we just aborted
            url = urljoin(src, target)
            continue
        return page.url
    raise BlockedURL(f"too many redirects from {url}")


async def _settle(page: Page) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=6_000)
    except Exception:
        pass
    for label in COOKIE_BUTTONS:  # best effort: banners would cover screenshots
        try:
            btn = page.get_by_role("button", name=label, exact=True)
            if await btn.count() and await btn.first.is_visible():
                await btn.first.click(timeout=1_500)
                await page.wait_for_timeout(400)
                break
        except Exception:
            continue
    # trigger lazy-loaded images, then return to the top
    await page.evaluate("""async () => {
        const step = innerHeight * 0.8;
        for (let y = 0; y < Math.min(document.documentElement.scrollHeight, 20000); y += step) {
            window.scrollTo(0, y); await new Promise(r => setTimeout(r, 120));
        }
        window.scrollTo(0, 0);
    }""")
    try:
        await page.wait_for_load_state("networkidle", timeout=4_000)
    except Exception:
        pass


async def crawl(
    start_url: str,
    out: Path,
    guard: UrlGuard | None = None,
    *,
    max_pages: int = 20,
    max_depth: int = 2,
    time_budget: float = 240.0,
    progress: ProgressFn | None = None,
) -> CrawlResult:
    guard = guard or UrlGuard()
    start = normalize(guard.check(start_url))
    out.mkdir(parents=True, exist_ok=True)
    (out / "screens").mkdir(exist_ok=True)
    result = CrawlResult(start_url=start)
    robots = await _robots(guard, start)
    site = _site_key(start)
    deadline = time.monotonic() + time_budget

    queue: list[tuple[int, str]] = [(0, start)]
    seen = {start}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            ctx, net = await guarded_context(browser, guard)
            page = await ctx.new_page()
            while queue and len(result.pages) < max_pages and time.monotonic() < deadline:
                depth, url = queue.pop(0)
                if not robots.can_fetch(USER_AGENT, url):
                    result.skipped_robots.append(url)
                    continue
                if progress:
                    r = progress(f"Reading page {len(result.pages) + 1} (limit {max_pages}): {url}")
                    if asyncio.iscoroutine(r):
                        await r
                try:
                    final = await _goto(page, url, guard, net)
                    if _site_key(final) != site:
                        result.errors.append(f"{url}: redirected off-site to {final}")
                        continue
                    await _settle(page)
                    data = await page.evaluate(EXTRACT_JS)
                except BlockedURL as exc:
                    result.blocked.append(f"{url}: {exc}")
                    continue
                except Exception as exc:
                    result.errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:200]}")
                    continue

                n = len(result.pages)
                shot = f"screens/page-{n:02d}.png"
                await page.screenshot(path=str(out / shot))
                if n == 0:
                    result.home_full_screenshot = "screens/home-full.png"
                    height = int(data.get("docHeight") or 900)
                    clip = {"x": 0, "y": 0, "width": 1440, "height": 9000} if height > 9000 else None
                    await page.screenshot(path=str(out / result.home_full_screenshot), full_page=True, clip=clip)
                total = sum(len(b["md"]) for b in data["blocks"])
                if total > MAX_TEXT_CHARS:
                    data["blocks"] = data["blocks"][: max(1, int(len(data["blocks"]) * MAX_TEXT_CHARS / total))]
                result.pages.append(PageData(url=normalize(final), depth=depth, data=data, screenshot=shot))
                result.save(out)
                seen.add(normalize(final))

                if depth < max_depth:
                    # header/nav links first: they usually point to the main sub-pages
                    links = sorted(data["links"], key=lambda l: {"header": 0, "main": 1, "footer": 2}.get(l["area"], 3))
                    for link in links:
                        href = normalize(link["href"])
                        if (href in seen or _site_key(href) != site or SKIP_EXT.search(urlsplit(href).path)
                                or urlsplit(href).scheme not in ("http", "https")):
                            continue
                        seen.add(href)
                        queue.append((depth + 1, href))
            result.blocked.extend(net.blocked)
            result.errors.extend(net.failed[:50])
            await ctx.close()
        finally:
            await browser.close()
    result.save(out)
    return result


def site_markdown(result: CrawlResult, *, max_chars: int = 120_000) -> str:
    """All crawled pages as one markdown document (header/footer boilerplate de-duplicated)."""
    seen_blocks: set[str] = set()
    parts: list[str] = []
    for p in result.pages:
        lines = [f"## PAGE {p.url}", f"title: {p.data.get('title') or ''}"]
        if p.data.get("description"):
            lines.append(f"description: {p.data['description']}")
        for b in p.data["blocks"]:
            if b["area"] != "main":
                if b["md"] in seen_blocks:
                    continue
                seen_blocks.add(b["md"])
            lines.append(b["md"])
        ctas = sorted({c["text"] for c in p.data.get("ctas", [])})
        if ctas:
            lines.append("buttons: " + " | ".join(ctas))
        parts.append("\n".join(lines))
    text = "\n\n".join(parts)
    return text[:max_chars]
