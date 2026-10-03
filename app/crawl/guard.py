"""SSRF guard for user-supplied URLs.

Only http(s) URLs whose host resolves exclusively to *global* unicast addresses are allowed.
This blocks loopback, private (RFC 1918), link-local (incl. the cloud metadata endpoint
169.254.169.254), CGNAT, multicast, reserved, unspecified, ULA and IPv4-mapped variants of those.

Every hop must be checked: browsers and HTTP clients follow redirects on their own, so callers
fetch with redirects disabled and validate each ``Location`` (see app/crawl/fetch.py, crawler.py).
Residual risk: DNS rebinding between this check and the actual connection (acceptable for a
local single-user tool; documented in docs/phase2-crawl.md).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Callable, Iterable
from urllib.parse import urlsplit

Resolver = Callable[[str, int], Iterable[str]]


class BlockedURL(ValueError):
    """The URL must not be fetched."""


def _system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _is_public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


@dataclass
class UrlGuard:
    resolver: Resolver = _system_resolver
    # host names that may resolve to non-public addresses (tests / explicit local use only)
    allow_hosts: set[str] = field(default_factory=set)
    allowed_ports: frozenset[int] | None = None  # None: any port
    _cache: dict[tuple[str, int], bool] = field(default_factory=dict, repr=False)

    def check(self, url: str) -> str:
        """Return the URL if it is safe to fetch, else raise BlockedURL."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise BlockedURL(f"scheme not allowed: {parts.scheme or '(none)'}")
        if parts.username or parts.password:
            raise BlockedURL("credentials in URL are not allowed")
        host = (parts.hostname or "").rstrip(".").lower()
        if not host:
            raise BlockedURL("missing host")
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError as exc:
            raise BlockedURL(f"invalid port: {exc}") from exc
        if self.allowed_ports is not None and port not in self.allowed_ports:
            raise BlockedURL(f"port not allowed: {port}")
        if host in self.allow_hosts:
            return url
        key = (host, port)
        if key not in self._cache:
            try:
                addrs = list(self.resolver(host, port))
            except (socket.gaierror, UnicodeError, OSError) as exc:
                raise BlockedURL(f"cannot resolve {host}: {exc}") from exc
            self._cache[key] = bool(addrs) and all(_is_public(a) for a in addrs)
        if not self._cache[key]:
            raise BlockedURL(f"{host} resolves to a non-public address")
        return url

    def allowed(self, url: str) -> bool:
        try:
            self.check(url)
            return True
        except BlockedURL:
            return False
