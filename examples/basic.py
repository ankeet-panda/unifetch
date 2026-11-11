"""Worked examples. Run: .venv/Scripts/python examples/basic.py"""

import asyncio

from unifetch import FetchConfig, Tier, aclose, afetch, afetch_many, fetch
from unifetch.types import FetchFailed, RobotsDenied


def simple() -> None:
    """One URL, blocking. Stops at the cheapest tier that works."""
    r = fetch("https://example.com")
    print(f"[simple]   tier={r.tier.label} status={r.status} {r.elapsed_ms}ms")
    print(f"           {r.content[:70]}...")


async def escalation() -> None:
    """A JS-only page: watch it climb 1 -> 2 -> 3."""
    r = await afetch("https://quotes.toscrape.com/js/")
    print(f"[escalate] won at tier {r.tier} ({r.backend}) in {r.elapsed_ms}ms")
    for a in r.attempts:
        print(f"           {'OK ' if a.ok else '   '} tier {a.tier} {a.backend:<18} {a.reason}")


async def batching() -> None:
    """afetch_many reuses one browser across URLs -- the sync wrappers cannot."""
    urls = ["https://example.com", "https://example.org", "https://example.net"]
    results = await afetch_many(urls, concurrency=3)
    for r in results:
        if isinstance(r, BaseException):
            print(f"[batch]    failed: {type(r).__name__}")
        else:
            print(f"[batch]    {r.url:<28} tier={r.tier.label} {r.elapsed_ms}ms")


async def tuning() -> None:
    """Cap the climb, or pin a single tier for benchmarking."""
    cheap = FetchConfig(max_tier=Tier.ADAPTIVE)  # never launch a browser
    r = await afetch("https://example.com", cheap)
    print(f"[capped]   tier={r.tier.label} (ladder stopped at adaptive)")

    pinned = FetchConfig(force_tier="http")
    r = await afetch("https://example.com", pinned)
    print(f"[pinned]   tier={r.tier.label} {r.elapsed_ms}ms")


async def errors() -> None:
    """robots.txt is honoured by default."""
    try:
        await afetch("https://www.google.com/search?q=test")
    except RobotsDenied:
        print("[robots]   blocked, as expected")

    try:
        await afetch("https://example.com", FetchConfig(force_tier="platform"))
    except (FetchFailed, ValueError) as exc:
        print(f"[nomatch]  {type(exc).__name__}: no platform route for example.com")


async def main() -> None:
    await escalation()
    await batching()
    await tuning()
    await errors()
    await aclose()  # release the shared Crawl4AI browser


if __name__ == "__main__":
    # simple() is the blocking API, so it runs its own loop -- it must stay
    # outside asyncio.run(), which is exactly what fetch() refuses to nest inside.
    simple()
    asyncio.run(main())
