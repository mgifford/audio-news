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
MAX_DESC = 480       # more room so a summary is substantial, not a one-liner
TIMEOUT = 12          # per-feed cap; concurrency keeps total wall time near one feed
MAX_WORKERS = 8       # feeds are fetched in parallel, not one after another
# Some publishers (e.g. CBC) return nothing to a non-browser User-Agent, so present a
# common desktop browser UA. This is a read-only RSS fetch of public feeds.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36 audio-news/1.0 (+https://github.com/mgifford/audio-news)")

_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")

# Common RSS boilerplate that shouldn't be read aloud.
_BOILERPLATE = [
    re.compile(r"\s*The post\b.*?\bappeared first on\b.*$", re.I | re.S),
    re.compile(r"\s*\[(?:…|\.\.\.|read more)\]\s*$", re.I),
    re.compile(r"\s*(?:Continue reading|Read more|Read full story)\b.*$", re.I),
    # trailing byline + timestamp, e.g. "sofia Wed, 07/01/2026 - 15:50"
    re.compile(r"\s+\S+\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+\d{1,2}/\d{1,2}/\d{4}\s*-\s*\d{1,2}:\d{2}\s*$", re.I),
]


def clean(text: str) -> str:
    """Strip tags and known boilerplate, collapse whitespace. No truncation here."""
    text = _TAG_RE.sub(" ", text or "")
    text = _WS_RE.sub(" ", text).strip()
    for pat in _BOILERPLATE:
        text = pat.sub("", text).strip()
    return text


def tidy(text: str, max_len: int = MAX_DESC) -> str:
    """Trim to a whole-sentence boundary so a summary never ends mid-thought (no '…')."""
    text = (text or "").strip()
    if not text:
        return ""
    if len(text) <= max_len:
        return text if text[-1:] in ".!?" else text + "."
    window = text[:max_len]
    ends = [m.end() for m in re.finditer(r"[.!?](?:\s|$)", window)]
    if ends and ends[-1] >= 80:            # end on the last complete sentence
        return text[:ends[-1]].strip()
    cut = window.rfind(" ")                # fallback: last word, closed with a period
    return (window[:cut] if cut > 80 else window).strip().rstrip(".,;:") + "."


def _best_text(entry) -> str:
    """Prefer the fullest text a feed offers (content:encoded) over a one-line summary."""
    candidates = []
    for c in entry.get("content", []) or []:
        val = c.get("value") if isinstance(c, dict) else None
        if val:
            candidates.append(val)
    for key in ("summary", "description"):
        if entry.get(key):
            candidates.append(entry[key])
    # Pick the candidate with the most visible text.
    return max(candidates, key=lambda v: len(_TAG_RE.sub(" ", v)), default="")


def _published_iso(entry) -> str | None:
    """The entry's own publish (or update) time as an ISO-8601 UTC string, so the
    reader can show how recent each story is. None when the feed omits a date."""
    import calendar
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            try:
                dt = datetime.datetime.fromtimestamp(calendar.timegm(st), datetime.timezone.utc)
                return dt.isoformat(timespec="seconds")
            except (ValueError, OverflowError, TypeError):
                continue
    return None


def parse_feed(raw) -> list[dict]:
    """Parse feed bytes/str into a bounded list of {title, description, link, published}."""
    import feedparser  # lazy: keeps `import feedfetch` free of the dependency
    parsed = feedparser.parse(raw)
    items = []
    for entry in parsed.entries[:MAX_ITEMS]:
        link = (entry.get("link") or "").strip()
        if not link:
            continue  # link lineage: drop items with no verifiable source URL
        items.append({
            "title": (entry.get("title") or "Untitled").strip(),
            "description": tidy(clean(_best_text(entry))),
            "link": link,
            "published": _published_iso(entry),
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
