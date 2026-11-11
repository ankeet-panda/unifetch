"""Shared result and error types for unifetch."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Tier(IntEnum):
    """Fetch tiers.

    ``PLATFORM`` is not part of the escalation ladder -- it is selected up front
    by hostname. The remaining tiers are ordered cheapest-first and are tried in
    sequence until one returns a result that passes the sufficiency check.
    """

    PLATFORM = 0  # agent-reach: per-platform readers (Twitter, Reddit, YouTube, ...)
    HTTP = 1  # httpx: plain HTTP, no JS
    ADAPTIVE = 2  # scrapling: adaptive parsing, curl_cffi/stealth fetchers
    RENDER = 3  # crawl4ai: full JS rendering, LLM-ready markdown
    STEALTH = 4  # cloakbrowser: patched stealth Chromium

    @property
    def label(self) -> str:
        return self.name.lower()


class UniFetchError(Exception):
    """Base class for every error raised by unifetch."""


class BackendUnavailable(UniFetchError):
    """A backend's dependency is missing, or it is not configured to run."""


class RobotsDenied(UniFetchError):
    """robots.txt disallows this URL and ``obey_robots`` is enabled."""


@dataclass
class FetchFailed(UniFetchError):
    """Every eligible tier was tried and none produced a usable result."""

    url: str
    attempts: list[Attempt] = field(default_factory=list)

    def __str__(self) -> str:
        if not self.attempts:
            return f"{self.url}: no backend was available to try"
        lines = [f"{self.url}: all {len(self.attempts)} tier(s) failed"]
        lines += [f"  - {a.backend} ({a.tier.label}): {a.reason}" for a in self.attempts]
        return "\n".join(lines)


@dataclass
class Attempt:
    """One tier's outcome, kept for diagnostics whether it succeeded or not."""

    backend: str
    tier: Tier
    ok: bool
    reason: str
    elapsed_ms: int


@dataclass
class FetchResult:
    """Whatever the winning backend produced, normalised across all tiers."""

    url: str
    """The final URL, after redirects where the backend reports them."""

    status: int | None = None
    html: str | None = None
    markdown: str | None = None
    text: str | None = None

    backend: str = ""
    tier: Tier = Tier.HTTP
    elapsed_ms: int = 0

    attempts: list[Attempt] = field(default_factory=list)
    """Every tier tried on the way here, including the one that won."""

    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is None or 200 <= self.status < 400

    @property
    def content(self) -> str:
        """Best available textual representation, richest form first."""
        return self.markdown or self.text or self.html or ""

    def __repr__(self) -> str:  # keep reprs short -- html can be megabytes
        return (
            f"FetchResult(url={self.url!r}, status={self.status}, "
            f"backend={self.backend!r}, tier={self.tier.label!r}, "
            f"chars={len(self.content)}, elapsed_ms={self.elapsed_ms})"
        )

