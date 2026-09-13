# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared RSS fetch/parse used by both the /api/feeds backend endpoint and the
scheduled pre-fetch script. feedparser is imported lazily so importing this module
(and app.py) stays dependency-light for CI."""

import re
import datetime
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

MAX_ITEMS = 12
MAX_DESC = 320
TIMEOUT = 12          # per-feed cap; concurrency keeps total wall time near one feed
MAX_WORKERS = 8       # feeds are fetched in parallel, not one after another
USER_AGENT = "audio-news/1.0 (+https://github.com/mgifford/audio-news)"

_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")


def clean(text: str) -> str:
    """Strip tags, collapse whitespace, cap length so a summary is speech-ready."""
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", text or "")).strip()
    if len(text) <= MAX_DESC:
        return text
    slice_ = text[:MAX_DESC]
    cut = slice_.rfind(" ")
    return (slice_[:cut] if cut > 40 else slice_).strip() + "…"


def parse_feed(raw) -> list[dict]:
    """Parse feed bytes/str into a bounded list of {title, description, link}."""
    import feedparser  # lazy: keeps `import feedfetch` free of the dependency
    parsed = feedparser.parse(raw)
    items = []
    for entry in parsed.entries[:MAX_ITEMS]:
        link = (entry.get("link") or "").strip()
        if not link:
            continue  # link lineage: drop items with no verifiable source URL
        items.append({
            "title": (entry.get("title") or "Untitled").strip(),
            "description": clean(entry.get("summary") or entry.get("description") or ""),
            "link": link,
        })
    return items


def fetch(url: str):
    """Return (http_status, raw_bytes). status is None on a transport error."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, b""
    except Exception:
        return None, b""


def build_cache(sources: dict, fetcher=None) -> dict:
    """Fetch every feed in the registry into a deck-ready structure. Feeds are fetched
    concurrently so total wall time is ~one slow feed, not the sum. `fetcher` is
    injectable and resolves at call time so monkeypatching feedfetch.fetch also works."""
    if fetcher is None:
        fetcher = fetch
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    tasks = [(geo, feed) for geo, feeds in sources.get("geography", {}).items() for feed in feeds]
    if tasks:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fetched = list(ex.map(lambda t: fetcher(t[1]["url"]), tasks))
    else:
        fetched = []

    out = {"generatedAt": now, "geography": {geo: [] for geo in sources.get("geography", {})}}
    for (geo, feed), (status, raw) in zip(tasks, fetched):
        items = parse_feed(raw) if raw else []
        out["geography"][geo].append({
            "id": feed.get("id"),
            "name": feed["name"],
            "scope": geo,
            "type": feed.get("type"),
            "beat": feed.get("beat"),
            "httpStatus": status,
            "fetchedAt": now,
            "itemCount": len(items),
            "items": items,
        })
    return out


def total_items(cache: dict) -> int:
    return sum(s["itemCount"] for feeds in cache.get("geography", {}).values() for s in feeds)
