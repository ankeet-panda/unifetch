"""Tier 3 -- JS rendering and LLM-ready markdown via Crawl4AI.

Crawl4AI drives a real Chromium through Playwright, so launching one per URL
would dominate the runtime. The crawler is created lazily and reused; call
``unifetch.aclose()`` (or ``close()``) to tear it down.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..config import FetchConfig
from ..types import FetchResult, Tier
from .base import Backend, filter_kwargs, module_installed

_crawler: Any = None
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


class Crawl4AIBackend(Backend):
    name = "crawl4ai"
    tier = Tier.RENDER

    def available(self, cfg: FetchConfig) -> tuple[bool, str]:
        if not module_installed("crawl4ai"):
            return False, "crawl4ai not installed"
        return True, ""

    async def fetch(self, url: str, cfg: FetchConfig) -> FetchResult:
        from crawl4ai import CacheMode, CrawlerRunConfig

        crawler = await self._crawler(cfg)

        run_kwargs = filter_kwargs(
            CrawlerRunConfig,
            {
                "cache_mode": CacheMode.BYPASS,
                "page_timeout": int(cfg.timeout * 1000),
                "wait_until": "domcontentloaded",
                "scan_full_page": False,
                "verbose": False,
            },
        )
        result = await crawler.arun(url=url, config=CrawlerRunConfig(**run_kwargs))

        if not getattr(result, "success", False):
            raise RuntimeError(getattr(result, "error_message", None) or "crawl failed")

        return self._result(
            getattr(result, "url", None) or url,
            status=getattr(result, "status_code", None),
            html=getattr(result, "cleaned_html", None) or getattr(result, "html", None),
            markdown=_markdown(result),
            meta={"title": (getattr(result, "metadata", None) or {}).get("title")},
        )

    async def _crawler(self, cfg: FetchConfig) -> Any:
        global _crawler
        async with _get_lock():
            if _crawler is None:
                from crawl4ai import AsyncWebCrawler, BrowserConfig

                browser_kwargs = filter_kwargs(
                    BrowserConfig,
                    {
                        "headless": True,
                        "verbose": False,
                        "user_agent": cfg.user_agent,
                        **({"proxy": cfg.proxy} if cfg.proxy else {}),
                    },
                )
                crawler = AsyncWebCrawler(config=BrowserConfig(**browser_kwargs))
                await crawler.start()
                _crawler = crawler
        return _crawler


def _markdown(result: Any) -> str | None:
    """``result.markdown`` is a str on some versions and an object on others."""
    md = getattr(result, "markdown", None)
    if md is None:
        return None
    if isinstance(md, str):
        return md
    for attr in ("raw_markdown", "fit_markdown", "markdown_with_citations"):
        value = getattr(md, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return str(md) or None


async def aclose() -> None:
    global _crawler
    async with _get_lock():
        if _crawler is not None:
            try:
                await _crawler.close()
            finally:
                _crawler = None
