"""The escalation ladder.

Tries the cheapest tier that can plausibly work, checks whether what came back is
actually usable, and climbs only when it is not. Most URLs stop at tier 1.
"""

from __future__ import annotations

import re
import time
from dataclasses import replace

from . import robots
from .backends.agentreach_backend import AgentReachBackend, platform_for
from .backends.base import Backend
from .backends.cloakbrowser_backend import CloakBrowserBackend
from .backends.crawl4ai_backend import Crawl4AIBackend
from .backends.crawl4ai_backend import aclose as _crawl4ai_close
from .backends.httpx_backend import HttpxBackend
from .backends.scrapling_backend import ScraplingBackend, ScraplingStealthBackend
from .config import FetchConfig
from .types import Attempt, FetchFailed, FetchResult, RobotsDenied, Tier

#: Substrings that mean "you got the interstitial, not the page".
CLOUDFLARE_MARKERS: tuple[str, ...] = (
    "just a moment",
    "attention required! | cloudflare",
    "checking your browser before accessing",
    "enable javascript and cookies to continue",
    "ddos protection by cloudflare",
    "/cdn-cgi/challenge-platform",
)

OTHER_WALL_MARKERS: tuple[str, ...] = (
    "verify you are human",
    "please verify you are a human",
    "captcha-delivery.com",
    "px-captcha",
    "are you a robot",
    "unusual traffic from your computer network",
)

BOT_WALL_MARKERS: tuple[str, ...] = CLOUDFLARE_MARKERS + OTHER_WALL_MARKERS


def default_backends() -> list[Backend]:
    """Tier 4 lists CloakBrowser first, then Scrapling's free Camoufox fallback."""
    return [
        AgentReachBackend(),
        HttpxBackend(),
        ScraplingBackend(),
        Crawl4AIBackend(),
        CloakBrowserBackend(),
        ScraplingStealthBackend(),
    ]


#: Empty client-side mount points and "turn on JS" notices.
JS_SHELL_MARKERS: tuple[str, ...] = (
    "you need to enable javascript to run this app",
    "please enable javascript to continue",
    "this site requires javascript",
    '<div id="root"></div>',
    "<div id='root'></div>",
    '<div id="app"></div>',
    "<div id='app'></div>",
    '<div id="__next"></div>',
)

_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.S)


def looks_unrendered(html: str | None, text_len: int) -> tuple[bool, str]:
    """Does thin HTML look like a JS shell, or is the page just genuinely short?

    Without this distinction a 145-character page like example.com escalates all
    the way to a headless browser -- 70 ms of work turned into 14 seconds -- for
    content tier 1 already had in full.
    """
    if not html:
        return False, ""
    low = html.lower()

    for marker in JS_SHELL_MARKERS:
        if marker in low:
            return True, f"js shell ({marker!r})"

    if "</html>" not in low:
        return True, "truncated html (no </html>)"

    script_chars = sum(len(block) for block in _SCRIPT_BLOCK.findall(low))
    if len(html) > 2000 and script_chars > 0.6 * len(html) and text_len < 400:
        return True, "script-dominated body"

    return False, ""


def _is_cloudflare(result: FetchResult) -> bool:
    haystack = (result.html or result.content)[:8000].lower()
    return any(marker in haystack for marker in CLOUDFLARE_MARKERS)


def is_sufficient(result: FetchResult, cfg: FetchConfig) -> tuple[bool, str]:
    """Whether this result is good enough to stop climbing."""
    if result.status is not None and result.status in cfg.escalate_on_status:
        return False, f"status {result.status}"

    content = result.content.strip()
    haystack = (result.html or content)[:8000].lower()

    if cfg.detect_bot_walls:
        for marker in BOT_WALL_MARKERS:
            if marker in haystack:
                return False, f"bot wall ({marker!r})"

    # Length is only meaningful for documents; a 40-byte JSON API reply is fine.
    content_type = str(result.meta.get("content_type", "")).lower()
    is_document = not content_type or "html" in content_type or "xml" in content_type

    if is_document and cfg.min_text_chars and len(content) < cfg.min_text_chars:
        # Short on its own is not a failure -- only short *and* unrendered is.
        unrendered, why = looks_unrendered(result.html, len(content))
        if unrendered:
            return False, f"thin body ({len(content)} chars), {why}"

    return True, "ok"


class Router:
    def __init__(self, backends: list[Backend] | None = None) -> None:
        self.backends = backends if backends is not None else default_backends()

    def _ladder(self, cfg: FetchConfig) -> list[Backend]:
        forced = cfg.resolved("force_tier")
        if forced is not None:
            return [b for b in self.backends if b.tier == forced]

        low = cfg.resolved("min_tier") or Tier.HTTP
        high = cfg.resolved("max_tier") or Tier.RENDER
        if low > high:
            raise ValueError(f"min_tier ({low.label}) is above max_tier ({high.label})")

        rungs = [b for b in self.backends if low <= b.tier <= high and b.tier != Tier.PLATFORM]
        return sorted(rungs, key=lambda b: b.tier)

    async def afetch(self, url: str, cfg: FetchConfig | None = None) -> FetchResult:
        cfg = cfg or FetchConfig()
        attempts: list[Attempt] = []

        if not await robots.allowed(url, cfg):
            raise RobotsDenied(
                f"robots.txt disallows {url} for {cfg.user_agent!r}; "
                "set FetchConfig(obey_robots=False) to override"
            )

        forced = cfg.resolved("force_tier")

        # Tier 0 is chosen by hostname, not reached by climbing.
        if cfg.use_platform_routes and forced in (None, Tier.PLATFORM) and platform_for(url):
            platform = next((b for b in self.backends if b.tier == Tier.PLATFORM), None)
            if platform is not None:
                result = await self._try(platform, url, cfg, attempts)
                if result is not None:
                    ok, _ = is_sufficient(result, cfg)
                    if ok or forced is Tier.PLATFORM:
                        result.attempts = attempts
                        return result

        best: FetchResult | None = None
        ladder = self._ladder(cfg)

        for backend in ladder:
            result = await self._try(backend, url, cfg, attempts)
            if result is None:
                continue

            ok, reason = is_sufficient(result, cfg)
            if ok:
                result.attempts = attempts
                return result

            attempts[-1] = Attempt(
                backend.name,
                backend.tier,
                False,
                f"insufficient: {reason}",
                attempts[-1].elapsed_ms,
            )
            if best is None or result.tier > best.tier:
                best = result

            # Turn on Cloudflare solving for the tiers above, but only once we
            # have actually seen a Cloudflare interstitial.
            if cfg.solve_cloudflare == "auto" and _is_cloudflare(result):
                cfg = replace(cfg, solve_cloudflare=True)

        if best is not None:
            # Every tier was tried and none passed, but something came back --
            # hand back the richest one rather than throwing it away.
            best.attempts = attempts
            best.meta["insufficient"] = True
            return best

        raise FetchFailed(url, attempts)

    async def _try(
        self, backend: Backend, url: str, cfg: FetchConfig, attempts: list[Attempt]
    ) -> FetchResult | None:
        ok, reason = backend.available(cfg)
        if not ok:
            attempts.append(Attempt(backend.name, backend.tier, False, f"skipped: {reason}", 0))
            return None

        start = time.perf_counter()
        try:
            result = await backend.fetch(url, cfg)
        except Exception as exc:
            elapsed = int((time.perf_counter() - start) * 1000)
            detail = f"{type(exc).__name__}: {exc}".strip()
            attempts.append(Attempt(backend.name, backend.tier, False, detail[:300], elapsed))
            return None

        elapsed = int((time.perf_counter() - start) * 1000)
        result.elapsed_ms = elapsed
        attempts.append(Attempt(backend.name, backend.tier, True, "ok", elapsed))
        return result


async def aclose() -> None:
    """Release the shared Crawl4AI browser."""
    await _crawl4ai_close()
