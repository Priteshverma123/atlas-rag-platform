"""Safe outbound HTTP for user-supplied URLs (SSRF protection).

Blocks non-http(s) schemes, private/loopback/link-local/metadata addresses (checked after DNS
resolution and on every redirect hop), and caps response size and time.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

from atlas.common.errors import InvalidInput

MAX_BYTES = 50 * 1024 * 1024


async def _assert_public(host: str) -> None:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise InvalidInput(f"cannot resolve host {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast \
                or ip.is_unspecified:
            raise InvalidInput(f"URL resolves to a non-public address ({ip})")


async def safe_fetch(url: str, *, timeout_s: float = 30.0, max_redirects: int = 5) -> tuple[bytes, str, str]:
    """Returns (body, content_type, final_url)."""
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False,
                                 headers={"User-Agent": "atlas-ingest/0.1 (+https://github.com/)"}) as client:
        for _ in range(max_redirects + 1):
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise InvalidInput("only http(s) URLs are allowed")
            await _assert_public(parsed.hostname)
            async with client.stream("GET", url) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers.get("location", ""))
                    continue
                r.raise_for_status()
                chunks, size = [], 0
                async for part in r.aiter_bytes():
                    size += len(part)
                    if size > MAX_BYTES:
                        raise InvalidInput("remote document too large")
                    chunks.append(part)
                return b"".join(chunks), r.headers.get("content-type", ""), url
    raise InvalidInput("too many redirects")
