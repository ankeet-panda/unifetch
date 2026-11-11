"""Backend contract.

Every backend imports its heavy dependency inside ``_run``/``available`` rather
than at module scope. That is what keeps ``import unifetch`` around 30 ms despite
the installed tiers carrying three separate browser runtimes between them.
"""

from __future__ import annotations

import importlib.util
import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from ..config import FetchConfig
from ..types import FetchResult, Tier


def module_installed(name: str) -> bool:
    """True if ``name`` can be imported without actually importing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def filter_kwargs(target: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop kwargs ``target`` does not accept.

    These libraries rename constructor arguments between minor versions. Filtering
    against the live signature means a renamed option degrades to "not passed"
    instead of raising TypeError. If the signature takes **kwargs, pass everything.
    """
    try:
        sig = inspect.signature(target)
    except (TypeError, ValueError):
        return dict(kwargs)

    if any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values()):
        return dict(kwargs)
    return {k: v for k, v in kwargs.items() if k in sig.parameters}


class Backend(ABC):
    name: str
    tier: Tier

    def available(self, cfg: FetchConfig) -> tuple[bool, str]:
        """Whether this backend can run at all, and why not if it cannot."""
        return True, ""

    @abstractmethod
    async def fetch(self, url: str, cfg: FetchConfig) -> FetchResult:
        """Retrieve ``url``. Raise on failure; the router records and escalates."""

    def _result(self, url: str, **kwargs: object) -> FetchResult:
        return FetchResult(url=url, backend=self.name, tier=self.tier, **kwargs)  # type: ignore[arg-type]
