"""Tier 4 -- CloakBrowser patched stealth Chromium.

This is the only non-free tier. The pip wrapper is MIT, but the Chromium it drives
is a proprietary ~200 MB download: the free tier is pinned to v146 and allows one
concurrent session, and v148+ requires a paid Pro subscription.

So this backend is **off unless explicitly enabled**. ``available()`` uses
``binary_info()``, which reports whether the binary is on disk *without*
downloading it, and nothing here ever triggers a download implicitly --
``ensure()`` must be called deliberately.
"""

from __future__ import annotations

import contextlib
import inspect
from typing import Any

from ..config import FetchConfig
from ..types import BackendUnavailable, FetchResult, Tier
from .base import Backend, filter_kwargs, module_installed


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class CloakBrowserBackend(Backend):
    name = "cloakbrowser"
    tier = Tier.STEALTH

    def available(self, cfg: FetchConfig) -> tuple[bool, str]:
        if not module_installed("cloakbrowser"):
            return False, "cloakbrowser not installed (pip install '.[cloak]')"
        if cfg.enable_cloak is False:
            return False, "disabled by config (enable_cloak=False)"

        info = self.binary_info()
        if info.get("installed"):
            return True, f"chromium {info.get('version')} ({info.get('tier')} tier)"

        # Binary absent: using this tier would mean a ~200 MB proprietary download,
        # so never do it as a side effect of an ordinary fetch.
        if cfg.enable_cloak == "auto":
            return False, "binary not downloaded -- run: unifetch cloak-install"
        return False, (
            f"binary not downloaded ({info.get('version', '?')}, tier "
            f"{info.get('tier', '?')}) -- run: unifetch cloak-install"
        )

    def binary_info(self) -> dict[str, Any]:
        try:
            import cloakbrowser

            return cloakbrowser.binary_info()
        except Exception as exc:
            return {"error": str(exc), "installed": False}

    def probe(self) -> tuple[bool, str]:
        """Actually try to spawn the binary.

        Presence on disk is not the same as permission to run it: endpoint
        security (corporate EDR / web filters) commonly classifies a
        stealth-patched Chromium as a blocked controlled application, which
        surfaces as WinError 5 at spawn time. ``available()`` stays cheap for the
        hot path, so this deeper check is only used by ``unifetch doctor``.
        """
        import subprocess

        info = self.binary_info()
        exe = info.get("binary_path")
        if not info.get("installed") or not exe:
            return False, "binary not downloaded"

        try:
            proc = subprocess.Popen(
                [exe, "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except PermissionError as exc:
            return False, (
                f"binary present but BLOCKED from launching (WinError "
                f"{getattr(exc, 'winerror', 13)}) -- endpoint security policy"
            )
        except OSError as exc:
            return False, f"launch failed: {type(exc).__name__}: {exc}"

        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
        return True, "launches"

    def ensure(self, cfg: FetchConfig) -> str:
        """Download the binary. Explicit, never called implicitly by ``fetch``."""
        import cloakbrowser

        return cloakbrowser.ensure_binary(license_key=cfg.cloak_license_key)

    async def fetch(self, url: str, cfg: FetchConfig) -> FetchResult:
        ok, reason = self.available(cfg)
        if not ok:
            raise BackendUnavailable(reason)

        import cloakbrowser

        kwargs = filter_kwargs(
            cloakbrowser.launch_async,
            {
                "headless": True,
                "stealth_args": True,
                "humanize": True,
                "license_key": cfg.cloak_license_key,
                **({"proxy": cfg.proxy} if cfg.proxy else {}),
            },
        )

        browser = await _maybe_await(cloakbrowser.launch_async(**kwargs))
        try:
            page = await _maybe_await(browser.new_page())
            response = await _maybe_await(
                page.goto(url, timeout=int(cfg.timeout * 1000), wait_until="domcontentloaded")
            )
            html = await _maybe_await(page.content())
            final_url = await _maybe_await(getattr(page, "url", url))
            try:
                text = await _maybe_await(page.inner_text("body"))
            except Exception:
                text = None
        finally:
            with contextlib.suppress(Exception):
                await _maybe_await(browser.close())

        return self._result(
            str(final_url or url),
            status=getattr(response, "status", None),
            html=html,
            text=text,
            meta={"chromium": self.binary_info().get("version")},
        )
