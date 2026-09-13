#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Server-side RSS pre-fetch.

Reads sources.json, fetches each feed, and writes feeds-cache.json with deck-ready
items plus per-feed verification metadata (fetchedAt, httpStatus, itemCount).

Runs on a schedule in GitHub Actions, where egress is open, so it needs no browser
CORS proxy and nothing about a reader's activity reaches a third party. This is the
privacy-preserving, sustainable path from PHASE0.md; the browser proxy remains only
as a fallback when the cache is stale or absent.
"""

import json
import re
import sys
import datetime
import urllib.request
import urllib.error

import feedparser

MAX_ITEMS = 12
MAX_DESC = 320
TIMEOUT = 25
USER_AGENT = "audio-news-prefetch/1.0 (+https://github.com/mgifford/audio-news)"
SOURCES_PATH = "sources.json"
CACHE_PATH = "feeds-cache.json"

_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")


def clean(text: str) -> str:
    """Strip tags, collapse whitespace, and cap length so a summary is speech-ready."""
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", text or "")).strip()
    if len(text) <= MAX_DESC:
        return text
    slice_ = text[:MAX_DESC]
    cut = slice_.rfind(" ")
    return (slice_[:cut] if cut > 40 else slice_).strip() + "…"


def parse_feed(raw: bytes | str) -> list[dict]:
    """Parse feed bytes into a bounded list of {title, description, link}. Pure/offline."""
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
    """Return (http_status, raw_bytes). http_status is None on a transport error."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, b""
    except Exception as err:  # noqa: BLE001 - network errors are expected and logged
        print(f"  ! {url}: {err}", file=sys.stderr)
        return None, b""


def build(sources: dict) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    out = {"generatedAt": now, "geography": {}}
    for geo, feeds in sources.get("geography", {}).items():
        out["geography"][geo] = []
        for feed in feeds:
            status, raw = fetch(feed["url"])
            items = parse_feed(raw) if raw else []
            print(f"  {geo:13} {feed['name']:32} HTTP {status} {len(items)} items")
            out["geography"][geo].append({
                "id": feed.get("id"),
                "name": feed["name"],
                "scope": geo,
                "httpStatus": status,
                "fetchedAt": now,
                "itemCount": len(items),
                "items": items,
            })
    return out


def main() -> int:
    with open(SOURCES_PATH, encoding="utf-8") as fh:
        sources = json.load(fh)
    cache = build(sources)
    with open(CACHE_PATH, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    total = sum(s["itemCount"] for feeds in cache["geography"].values() for s in feeds)
    print(f"Wrote {CACHE_PATH}: {total} items across {len(cache['geography'])} scopes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
