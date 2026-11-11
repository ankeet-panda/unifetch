"""Runtime configuration for the fetch ladder."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .types import Tier

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

#: Hostname suffixes that Agent-Reach reads better than any generic HTTP tier.
#: Keys are matched as exact hosts or as domain suffixes. The platform names
#: mirror Agent-Reach's own channel list (``agent-reach doctor``).
PLATFORM_HOSTS: dict[str, str] = {
    "twitter.com": "twitter",
    "x.com": "twitter",
    "reddit.com": "reddit",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "github.com": "github",
    "bilibili.com": "bilibili",
    "b23.tv": "bilibili",
    "xiaohongshu.com": "xiaohongshu",
    "xhslink.com": "xiaohongshu",
    "v2ex.com": "v2ex",
    "xueqiu.com": "xueqiu",
    "xiaoyuzhoufm.com": "xiaoyuzhou",
    "linkedin.com": "linkedin",
    "zhipin.com": "boss",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class FetchConfig:
    """Everything that changes how a fetch behaves.

    The defaults are deliberately conservative: obey robots.txt, stop escalating
    at tier 3, and leave the proprietary tier 4 switched off.
    """

    timeout: float = 30.0
    user_agent: str = DEFAULT_USER_AGENT
    headers: dict[str, str] = field(default_factory=dict)
    proxy: str | None = None

    min_tier: Tier | str = Tier.HTTP
    max_tier: Tier | str = Tier.RENDER
    """Inclusive bounds on the escalation ladder. Raise ``max_tier`` to
    ``"stealth"`` only once CloakBrowser is licensed -- see ``enable_cloak``."""

    force_tier: Tier | str | None = None
    """Run exactly one tier and do not escalate. Useful for benchmarking."""

    obey_robots: bool = True
    robots_timeout: float = 10.0

    use_platform_routes: bool = True
    """Send known platform URLs to Agent-Reach before touching the ladder."""

    agentreach_cmd: str = "agent-reach"

    enable_cloak: bool | str = field(
        default_factory=lambda: True
        if (_env_flag("UNIFETCH_ENABLE_CLOAK") or os.environ.get("CLOAKBROWSER_LICENSE_KEY"))
        else "auto"
    )
    """Tri-state. ``"auto"`` (default) uses CloakBrowser when its binary is already
    on disk and skips it otherwise -- the gate exists to prevent an *implicit*
    200 MB download, not to block a binary you deliberately installed. ``True``
    forces it, ``False`` never uses it."""

    cloak_license_key: str | None = field(
        default_factory=lambda: os.environ.get("CLOAKBROWSER_LICENSE_KEY")
    )

    # --- sufficiency heuristics -------------------------------------------------

    min_text_chars: int = 200
    """A body with less extractable text than this is treated as a soft failure
    and triggers escalation. Set to 0 to accept whatever the first tier returns."""

    escalate_on_status: frozenset[int] = frozenset(
        {401, 403, 405, 406, 409, 429, 500, 502, 503, 520, 521, 522, 523, 525}
    )
    """Status codes that usually mean "wrong tool", not "page genuinely absent".
    404 is excluded on purpose -- a heavier browser will not conjure a missing page."""

    detect_bot_walls: bool = True

    solve_cloudflare: bool | str = "auto"
    """``"auto"`` turns Cloudflare solving on only after a lower tier actually hit
    a Cloudflare interstitial. Solving is slow and logs an error when there is no
    challenge present, so it is not worth doing speculatively."""

    def resolved(self, name: str) -> Tier | None:
        """Coerce a tier-ish attribute (``Tier``, name string, or ``None``)."""
        value = getattr(self, name)
        if value is None:
            return None
        if isinstance(value, Tier):
            return value
        key = str(value).strip().upper()
        try:
            return Tier[key]
        except KeyError:
            raise ValueError(
                f"{name}={value!r} is not a tier; expected one of "
                + ", ".join(t.label for t in Tier)
            ) from None

    @classmethod
    def research(cls, **overrides: object) -> FetchConfig:
        """Preset for competitive/review research: climb all the way, ignore robots.

        Deliberately different from the defaults. ``obey_robots=False`` is a
        considered choice, not an oversight -- several review sites disallow the
        very pages the research needs. Review pages are long, so the thin-body
        threshold is raised: a short body there means a wall, not a short page.
        """
        base: dict[str, object] = {
            "obey_robots": False,
            "max_tier": Tier.STEALTH,
            "min_text_chars": 600,
            "timeout": 45.0,
        }
        base.update(overrides)
        return cls(**base)  # type: ignore[arg-type]

    def request_headers(self) -> dict[str, str]:
        base = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        base.update(self.headers)
        return base
