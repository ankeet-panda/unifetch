"""Tier 1 -- plain HTTP via httpx.

No JavaScript, no browser. Answers the large majority of URLs in tens of
milliseconds, which is the entire reason the ladder starts here.
"""

from __future__ import annotations

import re

from ..config import FetchConfig
from ..types import FetchResult, Tier
from .base import Backend

_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")


def strip_html(html: str) -> str:
    """Cheap tag strip. Good enough for the length-based sufficiency check."""
    import html as html_mod

    text = _SCRIPT_STYLE.sub(" ", html)
    text = _TAGS.sub("\n", text)
    text = html_mod.unescape(text)
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANKS.sub("\n\n", text).strip()


class HttpxBackend(Backend):
    name = "httpx"
    tier = Tier.HTTP

    async def fetch(self, url: str, cfg: FetchConfig) -> FetchResult:
        import httpx

        client_kwargs: dict[str, object] = {
            "timeout": cfg.timeout,
            "follow_redirects": True,
            "headers": cfg.request_headers(),
        }
        if cfg.proxy:
            client_kwargs["proxy"] = cfg.proxy

        try:
            client_kwargs["http2"] = True
            client = httpx.AsyncClient(**client_kwargs)  # type: ignore[arg-type]
        except ImportError:  # httpx[http2] extra missing -- fall back to 1.1
            client_kwargs.pop("http2", None)
            client = httpx.AsyncClient(**client_kwargs)  # type: ignore[arg-type]

        async with client:
            response = await client.get(url)

        content_type = response.headers.get("content-type", "")
        is_markup = "html" in content_type or "xml" in content_type

        body = response.text
        return self._result(
            str(response.url),
            status=response.status_code,
            html=body if is_markup else None,
            text=strip_html(body) if is_markup else body,
            meta={
                "content_type": content_type,
                "http_version": response.http_version,
                "redirects": len(response.history),
            },
        )
