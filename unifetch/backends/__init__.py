"""Backend implementations, one per tier.

Nothing heavy is imported here -- each module defers its dependency to call time.
"""

from .agentreach_backend import AgentReachBackend
from .base import Backend
from .cloakbrowser_backend import CloakBrowserBackend
from .crawl4ai_backend import Crawl4AIBackend
from .httpx_backend import HttpxBackend
from .scrapling_backend import ScraplingBackend, ScraplingStealthBackend

__all__ = [
    "Backend",
    "AgentReachBackend",
    "HttpxBackend",
    "ScraplingBackend",
    "Crawl4AIBackend",
    "CloakBrowserBackend",
    "ScraplingStealthBackend",
]
