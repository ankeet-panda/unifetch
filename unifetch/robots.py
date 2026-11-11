"""Cached, non-fatal robots.txt gate.

A host that fails to serve robots.txt is treated as permissive -- that is what
RFC 9309 prescribes for an unreachable or 4xx robots file. Only an explicit
``Disallow`` match blocks a fetch.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from .config import FetchConfig

if TYPE_CHECKING:
    from urllib.robotparser import RobotFileParser

# urllib.robotparser drags in urllib.request -> ssl/http.client, ~180 ms on a cold
# start. Import it when a robots.txt is actually parsed, not at module load.
_cache: dict[str, RobotFileParser | None] = {}
_locks: dict[str, asyncio.Lock] = {}


def _robots_url(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    return origin, urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


async def allowed(url: str, cfg: FetchConfig) -> bool:
    """True if ``cfg.user_agent`` may fetch ``url``."""
    if not cfg.obey_robots:
        return True

    origin, robots_url = _robots_url(url)
    lock = _locks.setdefault(origin, asyncio.Lock())

    async with lock:
        if origin not in _cache:
            _cache[origin] = await _load(robots_url, cfg)

    parser = _cache[origin]
    if parser is None:  # unreachable or malformed -> permissive
        return True
    return parser.can_fetch(cfg.user_agent, url)


async def _load(robots_url: str, cfg: FetchConfig) -> RobotFileParser | None:
    from urllib.robotparser import RobotFileParser

    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=cfg.robots_timeout,
            follow_redirects=True,
            headers={"User-Agent": cfg.user_agent},
        ) as client:
            response = await client.get(robots_url)
    except Exception:
        return None

    if response.status_code >= 400:
        return None

    parser = RobotFileParser()
    try:
        parser.parse(response.text.splitlines())
    except Exception:
        return None
    return parser


def clear_cache() -> None:
    _cache.clear()
    _locks.clear()
