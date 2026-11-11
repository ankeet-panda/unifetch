"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .config import FetchConfig
from .router import Router, aclose
from .types import FetchFailed, FetchResult, RobotsDenied, Tier

TIER_NAMES = [t.label for t in Tier]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unifetch",
        description="One fetch() over httpx, Scrapling, Crawl4AI, CloakBrowser and Agent-Reach.",
    )
    sub = parser.add_subparsers(dest="command")

    get = sub.add_parser("fetch", help="fetch a URL, escalating tiers as needed")
    get.add_argument("url")
    get.add_argument(
        "--format",
        default="auto",
        choices=["auto", "markdown", "text", "html", "json"],
        help="output shape; 'auto' picks the richest available",
    )
    get.add_argument("--tier", choices=TIER_NAMES, help="force one tier, no escalation")
    get.add_argument("--max-tier", choices=TIER_NAMES, default="render")
    get.add_argument("--min-tier", choices=TIER_NAMES, default="http")
    get.add_argument("--timeout", type=float, default=30.0)
    get.add_argument("--proxy")
    get.add_argument("--user-agent", dest="user_agent")
    get.add_argument("--min-chars", type=int, default=200, help="0 disables the length check")
    get.add_argument("--no-robots", action="store_true", help="ignore robots.txt")
    get.add_argument("--no-platform", action="store_true", help="skip Agent-Reach routes")
    get.add_argument("--explain", action="store_true", help="print every tier tried, to stderr")
    get.add_argument("-o", "--output", help="write content to a file instead of stdout")

    sub.add_parser("doctor", help="report which tiers are live")
    sub.add_parser(
        "cloak-install",
        help="download the proprietary CloakBrowser binary (~200MB, explicit opt-in)",
    )
    return parser


def _config(args: argparse.Namespace) -> FetchConfig:
    return FetchConfig(
        timeout=args.timeout,
        proxy=args.proxy,
        user_agent=args.user_agent or FetchConfig().user_agent,
        min_tier=args.min_tier,
        max_tier=args.max_tier,
        force_tier=args.tier,
        obey_robots=not args.no_robots,
        use_platform_routes=not args.no_platform,
        min_text_chars=args.min_chars,
    )


def _render(result: FetchResult, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(
            {
                "url": result.url,
                "status": result.status,
                "backend": result.backend,
                "tier": result.tier.label,
                "elapsed_ms": result.elapsed_ms,
                "chars": len(result.content),
                "meta": result.meta,
                "attempts": [
                    {
                        "backend": a.backend,
                        "tier": a.tier.label,
                        "ok": a.ok,
                        "reason": a.reason,
                        "elapsed_ms": a.elapsed_ms,
                    }
                    for a in result.attempts
                ],
                "content": result.content,
            },
            indent=2,
            ensure_ascii=False,
        )
    if fmt == "auto":
        return result.content
    return getattr(result, fmt, None) or ""


def _explain(result: FetchResult) -> None:
    print(f"  url      {result.url}", file=sys.stderr)
    print(f"  winner   {result.backend} (tier {result.tier}: {result.tier.label})", file=sys.stderr)
    print(f"  status   {result.status}   {result.elapsed_ms} ms", file=sys.stderr)
    if result.meta.get("insufficient"):
        print("  NOTE     no tier passed the quality check; showing the best one", file=sys.stderr)
    print("  ladder:", file=sys.stderr)
    for a in result.attempts:
        mark = "OK  " if a.ok else "    "
        line = f"    {mark} {a.tier}:{a.backend:<18} {a.reason} ({a.elapsed_ms} ms)"
        print(line, file=sys.stderr)


async def _cmd_fetch(args: argparse.Namespace) -> int:
    cfg = _config(args)
    try:
        result = await Router().afetch(args.url, cfg)
    except RobotsDenied as exc:
        print(f"unifetch: {exc}", file=sys.stderr)
        return 3
    except FetchFailed as exc:
        print(f"unifetch: {exc}", file=sys.stderr)
        return 4
    finally:
        await aclose()

    if args.explain:
        _explain(result)

    body = _render(result, args.format)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(body)
        print(f"wrote {len(body)} chars to {args.output}", file=sys.stderr)
    else:
        print(body)
    return 0 if result.ok else 1


async def _cmd_doctor() -> int:
    from .backends.agentreach_backend import AgentReachBackend
    from .backends.cloakbrowser_backend import CloakBrowserBackend

    cfg = FetchConfig()
    print(f"unifetch doctor   (python {sys.version.split()[0]})\n")

    print("tiers:")
    for backend in Router().backends:
        ok, reason = backend.available(cfg)
        mark = "up  " if ok else "down"
        print(f"  [{mark}] {backend.tier}:{backend.name:<18} {reason or 'ready'}")

    print("\nplatform routes (tier 0):")
    for platform, tool in AgentReachBackend().routes().items():
        print(f"  {platform:<14} -> {tool}")
    print(f"\n  agent-reach doctor: {await AgentReachBackend().doctor(cfg)}")

    backend = CloakBrowserBackend()
    info = backend.binary_info()
    print("\ncloakbrowser binary (proprietary, opt-in):")
    for key in ("version", "tier", "installed", "platform", "binary_path"):
        if key in info:
            print(f"  {key:<14} {info[key]}")
    if info.get("installed"):
        can_run, why = backend.probe()
        print(f"  {'launch':<14} {'OK' if can_run else 'FAILS'} -- {why}")
        if not can_run:
            print("  " + "-" * 60)
            print("  Tier 4 will fall through to scrapling-stealth (free, BSD-3).")
    return 0


def _cmd_cloak_install() -> int:
    from .backends.cloakbrowser_backend import CloakBrowserBackend

    backend = CloakBrowserBackend()
    info = backend.binary_info()
    print("CloakBrowser ships a PROPRIETARY Chromium build.")
    print(f"  version   {info.get('version')}   tier {info.get('tier')}")
    print(f"  source    {info.get('download_url')}")
    print("  size      ~200 MB\n")
    reply = input("Download it now? [y/N] ").strip().lower()
    if reply not in {"y", "yes"}:
        print("Aborted; nothing downloaded.")
        return 1

    cfg = FetchConfig()
    try:
        path = backend.ensure(cfg)
    except Exception as exc:
        print(f"Download failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("A licence key may be required: set CLOAKBROWSER_LICENSE_KEY.", file=sys.stderr)
        return 1
    print(f"Installed: {path}")
    print("Enable tier 4 with UNIFETCH_ENABLE_CLOAK=1")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    argv = list(sys.argv[1:] if argv is None else argv)
    # Allow `unifetch <url>` as shorthand for `unifetch fetch <url>`.
    if argv and argv[0] not in {"fetch", "doctor", "cloak-install", "-h", "--help"}:
        argv.insert(0, "fetch")

    args = build_parser().parse_args(argv)

    if args.command == "doctor":
        return asyncio.run(_cmd_doctor())
    if args.command == "cloak-install":
        return _cmd_cloak_install()
    if args.command == "fetch":
        return asyncio.run(_cmd_fetch(args))

    build_parser().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
