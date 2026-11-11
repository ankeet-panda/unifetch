"""Tier 0 -- platform readers provisioned by Agent-Reach.

Agent-Reach is deliberately *not* a wrapper: it installs and health-checks a set
of upstream CLIs (``gh``, ``yt-dlp``, ``twitter``, ``rdt-cli``, ``bili-cli``) and
then expects the caller to invoke those tools directly. There is no
``agent-reach read <url>`` to shell out to.

So this backend dispatches to the provisioned toolchain per platform, in the same
priority order Agent-Reach documents, and falls back to Jina Reader over plain
HTTP when no CLI is installed for that host. ``agent-reach doctor`` is used only
to report which routes are live.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from urllib.parse import urlsplit

from ..config import PLATFORM_HOSTS, FetchConfig
from ..types import FetchResult, Tier
from .base import Backend

#: platform -> ordered candidate commands. ``{url}`` and ``{slug}`` are filled in.
#: An empty list means "no CLI for this channel" -- those fall straight through to
#: Jina Reader, which Agent-Reach itself uses as the any-webpage backend.
PLATFORM_COMMANDS: dict[str, list[tuple[str, list[str], str]]] = {
    "github": [("gh", ["repo", "view", "{slug}"], "text")],
    "youtube": [("yt-dlp", ["--dump-json", "--no-warnings", "{url}"], "json")],
    "twitter": [("twitter", ["tweet", "{url}"], "text")],
    "reddit": [("rdt-cli", ["read", "{url}"], "text")],
    "bilibili": [("bili-cli", ["info", "{url}"], "text")],
    "xiaoyuzhou": [("agent-reach", ["transcribe", "{url}"], "text")],
    "xiaohongshu": [],  # needs `agent-reach configure xhs-cookies`
    "v2ex": [],  # public API, no CLI
    "xueqiu": [],
    "linkedin": [],
    "boss": [],
    "facebook": [],
    "instagram": [],
}

JINA_READER = "https://r.jina.ai/"


def _version() -> str:
    from .. import __version__

    return __version__


def platform_for(url: str) -> str | None:
    """Map a URL to an Agent-Reach platform name, or None."""
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    for suffix, platform in PLATFORM_HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return platform
    return None


def _github_slug(url: str) -> str:
    parts = [p for p in urlsplit(url).path.split("/") if p]
    return "/".join(parts[:2]) if len(parts) >= 2 else url


class AgentReachBackend(Backend):
    name = "agent-reach"
    tier = Tier.PLATFORM

    def available(self, cfg: FetchConfig) -> tuple[bool, str]:
        if not cfg.use_platform_routes:
            return False, "platform routes disabled in config"
        return True, ""

    def routes(self) -> dict[str, str]:
        """Which platform routes have their CLI installed right now."""
        status = {}
        for platform, candidates in PLATFORM_COMMANDS.items():
            live = next((exe for exe, _, _ in candidates if shutil.which(exe)), None)
            status[platform] = live or "jina-reader (fallback)"
        return status

    async def doctor(self, cfg: FetchConfig) -> str:
        """Summarise ``agent-reach doctor``.

        Its raw output is a double-spaced, localised wall of text that would bury
        everything else, so keep only the per-channel status lines.
        """
        if not shutil.which(cfg.agentreach_cmd):
            return f"{cfg.agentreach_cmd} not installed"

        code, out, err = await _run([cfg.agentreach_cmd, "doctor"], cfg.timeout)
        if not out.strip():
            return err.strip() or f"exit {code}"

        keep = [
            line.strip()
            for line in out.splitlines()
            if line.strip().startswith(("✅", "[!]", "[X]"))
        ]
        summary = next(
            (ln.strip() for ln in out.splitlines() if "/" in ln and "16" in ln), ""
        )
        up = sum(1 for line in keep if line.startswith("✅"))
        head = summary or f"{up} channel(s) ready"
        # One line per channel, trimmed to the name before the em dash.
        names = [line.split("—")[0].strip() for line in keep]
        return head + "".join(f"\n    {n}" for n in names[:16])

    async def fetch(self, url: str, cfg: FetchConfig) -> FetchResult:
        platform = platform_for(url)
        if platform is None:
            raise ValueError(f"no platform route for {url}")

        for exe, template, kind in PLATFORM_COMMANDS.get(platform, []):
            if not shutil.which(exe):
                continue
            args = [
                a.format(url=url, slug=_github_slug(url) if platform == "github" else url)
                for a in template
            ]
            code, out, _err = await _run([exe, *args], cfg.timeout)
            if code == 0 and out.strip():
                return self._build(url, platform, exe, out, kind)

        return await self._jina(url, platform, cfg)

    def _build(self, url: str, platform: str, tool: str, out: str, kind: str) -> FetchResult:
        text = out
        if kind == "json":
            try:
                data = json.loads(out)
                text = "\n".join(
                    f"{k}: {v}"
                    for k, v in data.items()
                    if isinstance(v, (str, int, float)) and str(v).strip()
                )
            except json.JSONDecodeError:
                pass
        return self._result(
            url, status=200, text=text, meta={"platform": platform, "tool": tool}
        )

    async def _jina(self, url: str, platform: str, cfg: FetchConfig) -> FetchResult:
        """Jina Reader returns clean markdown over plain HTTP -- no CLI needed.

        It 403s on browser-like User-Agents, so send a plain identifying one
        rather than ``cfg.request_headers()``.
        """
        import httpx

        headers = {"Accept": "text/plain", "User-Agent": f"unifetch/{_version()}"}
        async with httpx.AsyncClient(
            timeout=cfg.timeout, follow_redirects=True, headers=headers
        ) as client:
            response = await client.get(JINA_READER + url)
        response.raise_for_status()
        return self._result(
            url,
            status=response.status_code,
            markdown=response.text,
            text=response.text,
            meta={"platform": platform, "tool": "jina-reader"},
        )


async def _run(cmd: list[str], timeout: float) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except (FileNotFoundError, NotImplementedError, OSError) as exc:
        return 127, "", str(exc)

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", f"timed out after {timeout}s"

    return (
        proc.returncode or 0,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )
