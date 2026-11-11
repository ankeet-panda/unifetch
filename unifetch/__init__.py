"""unifetch -- one fetch() over httpx, Scrapling, Crawl4AI, CloakBrowser and Agent-Reach.

    >>> from unifetch import fetch
    >>> r = fetch("https://example.com")
    >>> r.tier.label, r.status
    ('http', 200)

The async API is the real one; ``fetch``/``fetch_many`` are thin wrappers. Prefer
``afetch_many`` for batches -- it reuses one browser across URLs, which the sync
wrappers cannot do because they tear down the event loop on every call.
"""

from __future__ import annotations

import asyncio

from .config import DEFAULT_USER_AGENT, PLATFORM_HOSTS, FetchConfig
from .router import BOT_WALL_MARKERS, Router, aclose, default_backends, is_sufficient
from .types import (
    Attempt,
    BackendUnavailable,
    FetchFailed,
    FetchResult,
    RobotsDenied,
    Tier,
    UniFetchError,
)

__version__ = "0.1.0"

__all__ = [
    "fetch",
    "afetch",
    "fetch_many",
    "afetch_many",
    "close",
    "aclose",
    "FetchConfig",
    "FetchResult",
    "Tier",
    "Attempt",
    "Router",
    "UniFetchError",
    "FetchFailed",
    "RobotsDenied",
    "BackendUnavailable",
    "DEFAULT_USER_AGENT",
    "PLATFORM_HOSTS",
    "BOT_WALL_MARKERS",
    "default_backends",
    "is_sufficient",
    "__version__",
]

_router = Router()


async def afetch(url: str, config: FetchConfig | None = None) -> FetchResult:
    """Fetch ``url``, escalating tiers until the result is usable."""
    return await _router.afetch(url, config)


async def afetch_many(
    urls: list[str],
    config: FetchConfig | None = None,
    concurrency: int = 5,
    return_exceptions: bool = True,
) -> list[FetchResult | BaseException]:
    """Fetch many URLs concurrently, reusing browsers across them."""
    semaphore = asyncio.Semaphore(concurrency)

    async def one(target: str) -> FetchResult:
        async with semaphore:
            return await _router.afetch(target, config)

    return await asyncio.gather(
        *(one(u) for u in urls), return_exceptions=return_exceptions
    )


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "fetch() cannot run inside an active event loop -- await afetch() instead"
        )

    async def wrapper():
        try:
            return await coro
        finally:
            await aclose()  # the loop dies with this call; drop the cached browser

    return asyncio.run(wrapper())


def fetch(url: str, config: FetchConfig | None = None) -> FetchResult:
    """Blocking ``afetch``. Tears down any browser afterwards -- see ``afetch_many``."""
    return _run(afetch(url, config))


def fetch_many(
    urls: list[str], config: FetchConfig | None = None, concurrency: int = 5
) -> list[FetchResult | BaseException]:
    """Blocking ``afetch_many``."""
    return _run(afetch_many(urls, config, concurrency))


def close() -> None:
    """Blocking ``aclose``."""
    asyncio.run(aclose())
