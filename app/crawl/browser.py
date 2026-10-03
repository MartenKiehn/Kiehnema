"""Playwright context for crawling untrusted sites with the SSRF guard on every request.

Playwright does not route redirect targets (verified in Phase 2: neither ``route.continue_()``
nor ``fulfill`` of a 3xx response re-enters the route handler). The handler therefore fetches
every request itself with redirects disabled:
- sub-resources: the redirect chain is followed hop by hop, each hop checked by the guard;
- navigations: the request is aborted and the target recorded, so the crawler can check it and
  navigate again (keeps the document URL correct for relative links).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urljoin

from playwright.async_api import Browser, BrowserContext, Route

from app.crawl.fetch import MAX_REDIRECTS, USER_AGENT
from app.crawl.guard import UrlGuard

log = logging.getLogger(__name__)

SKIP_TYPES = {"media", "websocket", "eventsource", "manifest", "texttrack", "ping", "beacon"}


@dataclass
class NetLog:
    blocked: list[str] = field(default_factory=list)
    redirects: dict[str, str] = field(default_factory=dict)  # navigation url -> Location
    failed: list[str] = field(default_factory=list)


async def guarded_context(browser: Browser, guard: UrlGuard, *, width: int = 1440, height: int = 900) -> tuple[BrowserContext, NetLog]:
    ctx = await browser.new_context(
        viewport={"width": width, "height": height},
        user_agent=f"Mozilla/5.0 (Macintosh) Chrome/140 Safari/537.36 {USER_AGENT}",
        service_workers="block",
        accept_downloads=False,
        locale="de-DE",
    )
    net = NetLog()

    async def handler(route: Route) -> None:
        req = route.request
        url = req.url
        if url.startswith(("data:", "blob:")):
            await route.continue_()
            return
        if req.resource_type in SKIP_TYPES or not guard.allowed(url):
            if req.resource_type not in SKIP_TYPES:
                net.blocked.append(url)
            await route.abort("blockedbyclient")
            return
        try:
            resp = await route.fetch(max_redirects=0, timeout=20_000)
            hops = 0
            while 300 <= resp.status < 400 and "location" in resp.headers:
                target = urljoin(url, resp.headers["location"])
                if req.is_navigation_request() and req.frame == req.frame.page.main_frame:
                    net.redirects[url] = target
                    await route.abort("aborted")
                    return
                hops += 1
                if hops > MAX_REDIRECTS or not guard.allowed(target):
                    net.blocked.append(target)
                    await route.abort("blockedbyclient")
                    return
                url = target
                resp = await route.fetch(url=target, max_redirects=0, timeout=20_000)
            await route.fulfill(response=resp)
        except Exception as exc:  # network errors, timeouts, closed page
            net.failed.append(f"{url}: {type(exc).__name__}")
            try:
                await route.abort("failed")
            except Exception:
                pass

    async def no_websockets(ws) -> None:
        await ws.close()

    await ctx.route("**/*", handler)
    await ctx.route_web_socket("**/*", no_websockets)
    return ctx, net
