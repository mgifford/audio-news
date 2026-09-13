# Architecture

This documents the **actual** layout of `audio-news`, correcting a few common
misdescriptions.

```
[ GitHub repo — source + CI/CD ]
      │  .github/workflows/sync-to-hf-space.yml
      │    1. CI gate (py_compile, node --check, sources.json, pytest)
      │    2. force-push main to the Space (stamps BUILD_SHA)
      │    3. wait until /api/health reports that exact build
      ▼
[ Hugging Face Docker Space — one origin ]
   FastAPI (app.py) on port 7860
   ├── /api/health, /api/generate-bulletin, /api/feeds, /mcp/tools
   ├── local GGUF via llama-cpp-python (Qwen2.5-1.5B), loaded lazily
   └── serves the frontend files from the repo root via an explicit allowlist
       (index.html, styles.css, app.js, sources.json) — NOT a /static dir
      ▼
[ Browser ]
   reader UI + theme, localStorage prefs/history, Web Speech API playback,
   visible transcript, Edge `read:` links (Edge only)

[ Scheduled: .github/workflows/prefetch-feeds.yml ]
   every 6h fetch feeds server-side -> commit feeds-cache.json ([skip ci])
```

## Points that are often stated wrong

- **The deploy workflow is `sync-to-hf-space.yml`**, not `deploy-hf.yml`. There is
  deliberately no second workflow (two would race and double-build). It is
  CI-gated and health-gated, not a bare push-on-push.
- **The frontend is served from the repo root via a file allowlist**, not a
  `/static` directory. `app.py`, the `Dockerfile`, and docs are never web-served.
- **CORS** defaults to the project's Pages origin plus a `*.hf.space` regex
  (configurable via `ALLOWED_ORIGINS`), not a wildcard. On the Space the frontend
  is same-origin, so CORS is not exercised there.
- **`llama-cpp-python` is installed as a prebuilt CPU wheel**, not compiled
  (compiling OOM-killed the HF builder).

## Bulletin generation: deterministic by default (hybrid)

The model classifies each story against the SJN pillars (`temperature=0.0`). The
spoken bulletin is then produced one of two ways:

The classifier also **extracts the SJN pillar phrases** present in each article
(response, evidence, limitation, root cause, action anchor) — verbatim, empty when
absent, never invented.

- **Deterministic (default).** `assemble_script` follows a BBC-summary shape: a
  top-stories headline block, breaking/crisis and broader-scope items first, a soft
  ~320-word (~2-minute) cap, and an **"And finally"** solutions closer. Each story is
  told in its arc — a **solutions arc** (response → evidence → limitation) or a
  **crisis & mutual-aid arc** (root cause → action anchor) — using only the extracted
  phrases, so spoken text equals source text and URLs are never spoken. A story from a
  beat feed announces its beat ("In health news, …") instead of its geography.

**Regions & language.** `sources.json` (v2) is organised as `regions` (each with
local/regional/national feeds, a country, and a `language`) plus shared `international`
and `beats`. The reader offers a **region picker** (Ottawa, Toronto, Vancouver,
Eugene, London, Paris) so "local" means the viewer's place, and a **language** that
localizes the deterministic bulletin's framing (`FRAMING` in `app.py`, en/fr today)
and picks a matching TTS voice. The story text always stays in the source's language;
only the connective framing is translated. `GET /api/regions` lists the choices.
Full UI-chrome translation and reliable generative (AI-voice) output in other
languages are not done yet.

**Topic beats.** Each beat feed is tagged with a `beat` (health, technology,
business, government). The reader offers a beat picker alongside the region + mix;
each selected beat adds one specialized story, sourced only from feeds curated for
that beat (deterministic — no model classifies beats).
- **Generative (opt-in, labelled).** A local model rephrases into broadcast prose,
  guided by a style exemplar and the same SJN arc, using only the extracted fields.
  It is grounding-checked and shown as "AI-rephrased"; the "no invented facts"
  guarantee does **not** hold in this mode, which is why it is off by default.

A **cognitive-load cap** keeps crisis/heavy stories to at most one in three in any
bulletin (both modes).

## Feed sourcing (no reliance on a third-party proxy)

The browser gets stories in this order, most trustworthy first:

1. **`/api/feeds?region=<id>`** — the backend fetches the **allowlisted** feeds for
   that region (local/regional/national) plus the shared international + beat feeds,
   server-side (no CORS, no third party). It only ever fetches registered feeds, never
   a caller-supplied URL, so it is not an open proxy. Cached in memory per region (TTL).
2. **`feeds-cache.json`** — the scheduled pre-fetch commits this; used on a static
   host (e.g. GitHub Pages) when there is no backend, if recent enough.
3. **Live CORS proxy** — last-resort fallback for a static host with no cache.

The public `allorigins.win` proxy is only the step-3 fallback; the Space uses
step 1, so a proxy outage no longer empties the deck.

## Integrity boundaries (carried from PHASE0 / ADR 0001)

- Extractive by default; the model never fabricates links (URLs travel as metadata
  and are attached by the UI).
- Every bulletin has a visible transcript equal to what is spoken.
- Deploys are verified against `/api/health`'s `build` SHA, so a green pipeline
  means *this* build is live, not merely that something responds.

## Licence

AGPL-3.0-or-later. The running app links to its source (AGPL §13) in the footer.
