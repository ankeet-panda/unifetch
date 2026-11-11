"""Scrapling backends -- tier 2 and a free tier 4.

Scrapling ships two very different fetchers, so it earns two slots:

* ``AsyncFetcher.get`` is curl_cffi with real browser TLS/JA3 impersonation. It is
  barely slower than httpx and defeats fingerprint-based blocking, which makes it
  the natural rung above tier 1.
* ``StealthyFetcher.async_fetch`` drives a patched Chromium through ``patchright``
  and can solve Cloudflare interstitials. That is tier-4 work, and it is
  BSD-licensed -- so it backs up CloakBrowser and keeps tier 4 usable without a
  paid licence.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import FetchConfig
from ..types import FetchResult, Tier
from .base import Backend, module_installed

_quieted = False


def _quiet() -> None:
    """Scrapling logs every fetch at INFO, which would corrupt piped stdout.

    It routes through a ``LoggerProxy`` that holds its own level, so raising the
    level on the stdlib ``scrapling`` logger alone is not enough.
    """
    global _quieted
    if _quieted:
        return
    logging.getLogger("scrapling").setLevel(logging.WARNING)
    try:
        from scrapling.core.utils import log  # type: ignore[import-not-found]

        log.setLevel(logging.WARNING)
    except Exception:
        pass
    _quieted = True


def _text(response: Any, attr: str) -> str | None:
    """Read an attribute that may be a property or a method, tolerating errors."""
    value = getattr(response, attr, None)
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    if isinstance(value, str) and value.strip():
        return value
    return None


def _to_result(backend: Backend, url: str, response: Any, **meta: Any) -> FetchResult:
    return FetchResult(
        url=str(getattr(response, "url", None) or url),
        status=getattr(response, "status", None),
        html=_text(response, "html_content") or _text(response, "body"),
        markdown=_text(response, "markdown"),
        text=_text(response, "get_all_text"),
        backend=backend.name,
        tier=backend.tier,
        meta=meta,
    )


class ScraplingBackend(Backend):
    """Tier 2 -- impersonated HTTP, no browser."""

    name = "scrapling"
    tier = Tier.ADAPTIVE

    def available(self, cfg: FetchConfig) -> tuple[bool, str]:
        if not module_installed("scrapling"):
            return False, "scrapling not installed"
        return True, ""

    async def fetch(self, url: str, cfg: FetchConfig) -> FetchResult:
        _quiet()
        from scrapling.fetchers import AsyncFetcher

        kwargs: dict[str, Any] = {
            "timeout": cfg.timeout,
            "follow_redirects": True,
            "impersonate": "chrome",
            "stealthy_headers": True,
        }
        if cfg.headers:
            kwargs["headers"] = cfg.headers
        if cfg.proxy:
            kwargs["proxy"] = cfg.proxy

        response = await AsyncFetcher.get(url, **kwargs)
        return _to_result(self, url, response, mode="impersonate:chrome")


class ScraplingStealthBackend(Backend):
    """Tier 4 -- patched Chromium with Cloudflare solving. Free alternative to CloakBrowser."""

    name = "scrapling-stealth"
    tier = Tier.STEALTH

    def available(self, cfg: FetchConfig) -> tuple[bool, str]:
        if not module_installed("scrapling"):
            return False, "scrapling not installed"
        if not module_installed("patchright"):
            return False, "patchright missing -- run: pip install 'scrapling[fetchers]'"
        return True, ""

    async def fetch(self, url: str, cfg: FetchConfig) -> FetchResult:
        _quiet()
        from scrapling.fetchers import StealthyFetcher

        # "auto" means: only solve once the router has seen a real interstitial.
        solve = cfg.solve_cloudflare is True

        kwargs: dict[str, Any] = {
            "headless": True,
            "network_idle": True,
            "solve_cloudflare": solve,
            "block_ads": True,
            "timeout": int(cfg.timeout * 1000),  # Playwright-style milliseconds
        }
        if cfg.headers:
            kwargs["extra_headers"] = cfg.headers
        if cfg.proxy:
            kwargs["proxy"] = cfg.proxy

        response = await StealthyFetcher.async_fetch(url, **kwargs)
        return _to_result(self, url, response, mode="patchright", solve_cloudflare=solve)

