#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Server-side RSS pre-fetch (scheduled in GitHub Actions, open egress, no CORS proxy).

Reads sources.json and writes feeds-cache.json with deck-ready items plus per-feed
verification metadata. Shares fetch/parse logic with the /api/feeds backend endpoint
via feedfetch.py. This is the privacy-preserving path from PHASE0.md; the browser
proxy remains only as a last-resort fallback.
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import feedfetch  # noqa: E402

SOURCES_PATH = "sources.json"
CACHE_PATH = "feeds-cache.json"


def main() -> int:
    with open(SOURCES_PATH, encoding="utf-8") as fh:
        sources = json.load(fh)
    cache = feedfetch.build_cache(sources)
    for geo, feeds in cache["geography"].items():
        for s in feeds:
            print(f"  {geo:13} {s['name']:32} HTTP {s['httpStatus']} {s['itemCount']} items")
    with open(CACHE_PATH, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"Wrote {CACHE_PATH}: {feedfetch.total_items(cache)} items across {len(cache['geography'])} scopes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
