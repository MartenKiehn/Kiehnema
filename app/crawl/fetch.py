"""Guarded HTTP downloads (robots.txt, images, fonts, font CSS) with per-hop redirect checks."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from app.crawl.guard import BlockedURL, UrlGuard

USER_AGENT = "Kiehnema/0.1 (local brand-video tool)"
MAX_REDIRECTS = 5


class FetchError(RuntimeError):
    pass


@dataclass
class Fetched:
    url: str  # final URL after redirects
    status: int
    content_type: str
    body: bytes


def make_client(timeout: float = 20.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "de,en;q=0.8"},
    )


async def fetch(client: httpx.AsyncClient, url: str, guard: UrlGuard, *, max_bytes: int = 10_000_000) -> Fetched:
    """GET ``url`` following at most MAX_REDIRECTS redirects, each hop checked by ``guard``."""
    for _ in range(MAX_REDIRECTS + 1):
        import asyncio
        address, _ = await asyncio.to_thread(guard.pinned, url)
        target = httpx.URL(url)
        async with client.stream("GET", target.copy_with(host=address), headers={'Host': target.netloc.decode()},
                                 extensions={'sni_hostname': target.raw_host.decode('ascii')}) as resp:
            if resp.is_redirect and "location" in resp.headers:
                url = urljoin(url, resp.headers["location"])
                continue
            chunks, size = [], 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError(f"{url}: larger than {max_bytes} bytes")
                chunks.append(chunk)
            return Fetched(url, resp.status_code, resp.headers.get("content-type", ""), b"".join(chunks))
    raise FetchError(f"{url}: too many redirects")


async def fetch_ok(client: httpx.AsyncClient, url: str, guard: UrlGuard, **kw) -> Fetched | None:
    """Like fetch() but returns None for blocked URLs, network errors and non-2xx responses."""
    try:
        res = await fetch(client, url, guard, **kw)
    except (BlockedURL, FetchError, httpx.HTTPError):
        return None
    return res if 200 <= res.status < 300 else None
