"""Offline tests. Anything touching the network is marked ``network``."""

from __future__ import annotations

import pytest

from unifetch import FetchConfig, Router, Tier
from unifetch.backends.agentreach_backend import platform_for
from unifetch.backends.httpx_backend import strip_html
from unifetch.router import is_sufficient, looks_unrendered
from unifetch.types import FetchResult

HTML_SHELL = '<html><head><title>x</title></head><body><div id="root"></div></body></html>'
HTML_SHORT = (
    "<html><head><title>Example</title></head><body><h1>Example Domain</h1>"
    "<p>This domain is for illustrative examples.</p></body></html>"
)
HTML_CLOUDFLARE = "<html><body><h1>Just a moment...</h1><p>Checking your browser</p></body></html>"


def result(**kwargs) -> FetchResult:
    kwargs.setdefault("url", "https://example.com")
    kwargs.setdefault("status", 200)
    return FetchResult(**kwargs)


class TestSufficiency:
    def test_short_but_complete_page_is_accepted(self):
        """The regression that mattered: example.com must stop at tier 1."""
        r = result(html=HTML_SHORT, text=strip_html(HTML_SHORT))
        ok, reason = is_sufficient(r, FetchConfig())
        assert ok, reason

    def test_js_shell_escalates(self):
        r = result(html=HTML_SHELL, text=strip_html(HTML_SHELL))
        ok, reason = is_sufficient(r, FetchConfig())
        assert not ok
        assert "js shell" in reason

    def test_cloudflare_wall_escalates(self):
        r = result(html=HTML_CLOUDFLARE, text=strip_html(HTML_CLOUDFLARE))
        ok, reason = is_sufficient(r, FetchConfig())
        assert not ok
        assert "bot wall" in reason

    def test_short_json_is_accepted(self):
        r = result(text='{"ok":true}', meta={"content_type": "application/json"})
        ok, _ = is_sufficient(r, FetchConfig())
        assert ok

    def test_403_escalates_but_404_does_not(self):
        cfg = FetchConfig()
        assert not is_sufficient(result(status=403, html=HTML_SHORT), cfg)[0]
        assert is_sufficient(result(status=404, html=HTML_SHORT), cfg)[0]

    def test_truncated_html_escalates(self):
        r = result(html="<html><body><p>cut off", text="cut off")
        ok, reason = is_sufficient(r, FetchConfig())
        assert not ok
        assert "truncated" in reason

    def test_min_text_chars_zero_disables_the_check(self):
        r = result(html=HTML_SHELL, text="")
        assert is_sufficient(r, FetchConfig(min_text_chars=0))[0]


class TestLooksUnrendered:
    @pytest.mark.parametrize("html", [HTML_SHELL, "<html><body>no close tag"])
    def test_detects(self, html):
        assert looks_unrendered(html, 10)[0]

    def test_ignores_genuinely_short_pages(self):
        assert not looks_unrendered(HTML_SHORT, 60)[0]

    def test_empty_html_is_not_a_shell(self):
        assert not looks_unrendered(None, 0)[0]


class TestPlatformRouting:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://twitter.com/x/status/1", "twitter"),
            ("https://x.com/x/status/1", "twitter"),
            ("https://www.reddit.com/r/p/", "reddit"),
            ("https://youtu.be/abc", "youtube"),
            ("https://github.com/encode/httpx", "github"),
            ("https://example.com", None),
            ("https://notgithub.com/a/b", None),
        ],
    )
    def test_maps_hosts(self, url, expected):
        assert platform_for(url) == expected


class TestConfig:
    def test_tier_names_coerce(self):
        cfg = FetchConfig(min_tier="http", max_tier="stealth", force_tier=None)
        assert cfg.resolved("min_tier") is Tier.HTTP
        assert cfg.resolved("max_tier") is Tier.STEALTH
        assert cfg.resolved("force_tier") is None

    def test_bad_tier_name_raises(self):
        with pytest.raises(ValueError, match="not a tier"):
            FetchConfig(max_tier="turbo").resolved("max_tier")

    def test_research_preset_climbs_all_tiers_and_ignores_robots(self):
        cfg = FetchConfig.research()
        assert cfg.obey_robots is False
        assert cfg.resolved("max_tier") is Tier.STEALTH
        assert cfg.min_text_chars == 600

    def test_research_preset_accepts_overrides(self):
        cfg = FetchConfig.research(obey_robots=True, timeout=10.0)
        assert cfg.obey_robots is True
        assert cfg.timeout == 10.0

    def test_cloak_defaults_to_auto(self, monkeypatch):
        monkeypatch.delenv("CLOAKBROWSER_LICENSE_KEY", raising=False)
        monkeypatch.delenv("UNIFETCH_ENABLE_CLOAK", raising=False)
        assert FetchConfig().enable_cloak == "auto"

    def test_cloak_env_var_forces_on(self, monkeypatch):
        monkeypatch.setenv("UNIFETCH_ENABLE_CLOAK", "1")
        assert FetchConfig().enable_cloak is True

    def test_cloak_never_downloads_implicitly(self, monkeypatch):
        """auto must refuse when the binary is absent -- no surprise 200MB pull."""
        from unifetch.backends.cloakbrowser_backend import CloakBrowserBackend

        backend = CloakBrowserBackend()
        monkeypatch.setattr(backend, "binary_info", lambda: {"installed": False})
        ok, reason = backend.available(FetchConfig(enable_cloak="auto"))
        assert not ok
        assert "cloak-install" in reason

    def test_cloak_disabled_outright(self):
        from unifetch.backends.cloakbrowser_backend import CloakBrowserBackend

        ok, reason = CloakBrowserBackend().available(FetchConfig(enable_cloak=False))
        assert not ok
        assert "enable_cloak=False" in reason


class TestLadder:
    def test_default_ladder_is_ordered_and_excludes_platform(self):
        rungs = Router()._ladder(FetchConfig())
        assert [b.tier for b in rungs] == sorted(b.tier for b in rungs)
        assert Tier.PLATFORM not in {b.tier for b in rungs}

    def test_max_tier_caps_the_climb(self):
        rungs = Router()._ladder(FetchConfig(max_tier="adaptive"))
        assert {b.tier for b in rungs} == {Tier.HTTP, Tier.ADAPTIVE}

    def test_force_tier_selects_exactly_one_rung(self):
        rungs = Router()._ladder(FetchConfig(force_tier="render"))
        assert [b.name for b in rungs] == ["crawl4ai"]

    def test_inverted_bounds_raise(self):
        with pytest.raises(ValueError, match="above max_tier"):
            Router()._ladder(FetchConfig(min_tier="render", max_tier="http"))

    def test_cloakbrowser_is_tried_before_the_free_fallback(self):
        rungs = Router()._ladder(FetchConfig(force_tier="stealth"))
        assert [b.name for b in rungs] == ["cloakbrowser", "scrapling-stealth"]


class TestImportIsLazy:
    def test_heavy_deps_are_not_imported_at_module_scope(self):
        """The whole point of the package layout -- keep this honest."""
        import subprocess
        import sys

        code = (
            "import sys, unifetch;"
            "heavy = [m for m in ('crawl4ai','scrapling','httpx','cloakbrowser','playwright')"
            " if m in sys.modules];"
            "print(','.join(heavy))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        assert out.stdout.strip() == ""


@pytest.mark.network
class TestLive:
    @pytest.mark.asyncio
    async def test_static_page_resolves_at_tier_1(self):
        from unifetch import afetch

        r = await afetch("https://example.com")
        assert r.status == 200
        assert r.tier is Tier.HTTP
        assert "Example Domain" in r.content

    @pytest.mark.asyncio
    async def test_robots_blocks_disallowed_path(self):
        from unifetch import afetch
        from unifetch.types import RobotsDenied

        with pytest.raises(RobotsDenied):
            await afetch("https://www.google.com/search?q=test")
