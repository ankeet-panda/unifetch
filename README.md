# unifetch

One `fetch()` over five web-retrieval tools, with automatic escalation from cheap to expensive.

Every one of these libraries answers the same question — *"get me the content at this URL"* — at a
different cost and capability. `unifetch` puts them behind a single call and walks up the ladder
only as far as a given URL actually requires.

| Tier | Backend | Measured | Handles |
|---|---|---|---|
| 0 · `platform` | Agent-Reach toolchain → [Jina Reader](https://jina.ai/reader) | ~540 ms | Twitter/X, Reddit, YouTube, GitHub, Bilibili, XiaoHongShu |
| 1 · `http` | [httpx](https://github.com/encode/httpx) | ~75 ms | static HTML, APIs, JSON |
| 2 · `adaptive` | [Scrapling](https://github.com/D4Vinci/Scrapling) `AsyncFetcher` | ~0.9 s | TLS/JA3 fingerprinting, light bot walls |
| 3 · `render` | [Crawl4AI](https://github.com/unclecode/crawl4ai) | ~6–8 s | JS-rendered pages, LLM-ready markdown |
| 4 · `stealth` | [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) *or* Scrapling `StealthyFetcher` | ~7 s | hardened anti-bot, Cloudflare interstitials |

Tier 0 is chosen by hostname, not by escalation. Tiers 1–4 are tried in order until one returns a
result that passes the sufficiency check (HTTP status, body length, and bot-wall fingerprints).

**Tier 4 has two backends.** CloakBrowser is tried first if you have licensed it; otherwise
Scrapling's `StealthyFetcher` (patched Chromium via `patchright`, BSD-3) takes over. Tier 4 is
therefore fully functional without paying for anything.

## Install

```bash
uv venv --python 3.13
uv pip install -e ".[cloak]"
```

Crawl4AI needs a browser before tier 3 works:

```bash
.venv/Scripts/python -m playwright install chromium
```

## Use

```python
from unifetch import fetch

r = fetch("https://example.com")
print(r.tier.label, r.status, len(r.content))
print(r.markdown or r.text)
```

Async, which is what the core actually is:

```python
import asyncio
from unifetch import afetch

r = asyncio.run(afetch("https://example.com"))
```

From the shell:

```bash
unifetch https://example.com --format markdown
unifetch https://example.com --explain        # show every tier tried
unifetch https://example.com --tier render    # force one tier, no escalation
```

## Configuration

```python
from unifetch import FetchConfig, fetch

cfg = FetchConfig(
    max_tier="render",       # never escalate past tier 3
    obey_robots=True,        # default; set False to ignore robots.txt
    min_text_chars=200,      # shorter than this counts as insufficient
    timeout=30.0,
    user_agent="my-crawler/1.0 (+https://example.com/bot)",
)
fetch("https://example.com", config=cfg)
```

### robots.txt

`obey_robots` defaults to **True**. `unifetch` fetches and caches `robots.txt` per host and raises
`RobotsDenied` for disallowed paths. Turning it off is a deliberate, per-config choice — the tiers
above 1 exist to render JavaScript and survive fragile bot detection, not as a licence to ignore a
site's stated wishes. Check the terms of service of anything you point this at.

## CloakBrowser is opt-in and not fully open source

The Python wrapper is MIT, but the stealth Chromium binary is **proprietary** (~200 MB). The free
tier is v146 and needs no key at all; the current v151 build is also free for one concurrent
session via `cloakbrowser login`. More than one concurrent session needs a paid Pro subscription.

`enable_cloak` is tri-state and defaults to `"auto"`: CloakBrowser is used when its binary is
already on disk, and skipped when it is not. The gate exists to prevent an *implicit* 200 MB
download, not to block a binary you deliberately installed.

```bash
.venv/Scripts/unifetch cloak-install     # prompts before downloading
export CLOAKBROWSER_LICENSE_KEY=...      # optional: force-enable + pull the latest build
```

### Endpoint security will probably block it

A stealth-patched, unsigned Chromium is exactly what corporate EDR classifies as a hacktool. On
this machine **the corporate endpoint security / web filter blocks it by application-control policy**:

```
File C:\Users\...\.cloakbrowser\...\chrome.exe of blocked controlled application
```

The binary downloads and extracts fine, then fails at spawn with `WinError 5`. `unifetch doctor`
probes this and reports `launch FAILS` rather than pretending the tier is healthy. **Do not try to
work around it** — that is your organisation's security control, and unblocking it is an IT
decision, not a code change.

Tier 4 keeps working regardless: the ladder falls through to Scrapling's `StealthyFetcher`, which
is free, BSD-3, and unaffected. That fallback is the reason tier 4 has two backends.

## Layout

```
unifetch/
  router.py              escalation ladder + sufficiency heuristics
  config.py              FetchConfig
  robots.py              cached robots.txt gate
  types.py               FetchResult, Tier, errors
  backends/
    httpx_backend.py         tier 1
    scrapling_backend.py     tier 2
    crawl4ai_backend.py      tier 3
    cloakbrowser_backend.py  tier 4
    agentreach_backend.py    tier 0
  cli.py
```

Every backend imports its heavy dependency **inside** the call. `import unifetch` is ~590 ms, and
essentially all of that is stdlib `asyncio`: none of httpx, Scrapling, Crawl4AI, CloakBrowser or
Playwright is loaded until a tier actually runs. `tests/test_unifetch.py::TestImportIsLazy` asserts
this, so it stays true.

## Agent-Reach: never `pip install agent-reach`

Three unrelated projects share this name, and only one is the right one:

| Source | Project | Correct? |
|---|---|---|
| PyPI `agent-reach` 0.1.0 | `github.com/jgalea/agent-reach` | ❌ different author |
| npm `agent-reach` 0.3.4 | `github.com/AustinEral/agent-reach` (Nostr) | ❌ different project |
| GitHub archive | [`Panniantong/Agent-Reach`](https://github.com/Panniantong/Agent-Reach) 1.5.0 | ✅ |

The correct project **publishes to neither registry**, so `pip install agent-reach` silently
installs a stranger's package. Install it from the repo archive, into an **isolated** environment:

```bash
uv tool install --python 3.13 https://github.com/Panniantong/Agent-Reach/archive/main.zip
```

Isolation matters. Agent-Reach pulls `yt-dlp[default]`, `loguru`, `feedparser` and `requests>=2.32`;
none of those conflict with this project's pins *today*, but tier 0 talks to it over **subprocess**,
never by import, so there is no reason to let the two dependency trees touch at all. Verify with:

```bash
agent-reach --version          # must print 1.5.0
uv pip list | grep agent       # must print nothing
```

Agent-Reach is an installer, not a wrapper — there is no `agent-reach read <url>`. It provisions
per-platform CLIs and expects callers to invoke those directly, which is what tier 0 does, falling
back to Jina Reader when a channel has no CLI. `agent-reach install --system` would additionally
install global npm packages and shell out to `apt-get`/`brew` for `gh` — neither of which exists on
Windows — so it is **not** run here. `unifetch doctor` shows which routes are live.

## Licence

MIT (this package). The bundled tiers keep their own licences.
