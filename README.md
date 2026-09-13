# audio-news

A solutions-oriented audio reader: it turns public RSS feeds into short, spoken,
action-oriented news segments. Each story is reframed with the Solutions
Journalism Network (SJN) four-pillar framework — into a solutions arc or a
crisis-and-mutual-aid arc — and read aloud through the browser's own speech
synthesis.

The project is accessibility-first and web-sustainable by design: speech is
always paired with a readable transcript and a working source link, and the
runtime is zero-cost by construction (a scale-to-zero server plus an in-browser
fallback).

## Status: Phase 1 (local client foundation)

Phase 1 delivers a static, offline-first web front-end that runs directly on
GitHub Pages (or as the static directory inside a Hugging Face Space): a curated
feed registry, a local state controller, direct RSS ingestion with link lineage,
and an accessible reader shell with Web Speech API playback. There is still no
language model and no server of ours.

- [`index.html`](index.html), [`styles.css`](styles.css), [`app.js`](app.js) —
  the accessible reader shell and the local state controller.
- [`sources.json`](sources.json) — the editorial feed registry, organised by
  geography with an editorial `type` per feed. Every entry is **unverified**
  (`lastVerified: null`) until confirmed live from a browser.
- [`PHASE1.md`](PHASE1.md) — what Phase 1 delivers, the accessibility notes, and
  the CORS-proxy trade-off.
- [`PHASE0.md`](PHASE0.md) — the foundation document: blueprint, candidate
  models, SJN reframing protocol, anti-hallucination controls, and the
  validation checklist.
- [`decisions/`](decisions/) — architecture decision records.

### Run it

Serve over http(s) (not `file://`):

```bash
python3 -m http.server 8000   # then open http://localhost:8000
```

## Core principles

- **Extractive-only.** The model restructures and summarizes the source entry;
  it never adds facts from pre-trained knowledge. Unsupported statements are
  dropped, not filled.
- **Deterministic.** Classification and synthesis run at `temperature = 0.0`,
  so the same entry yields the same script.
- **Link lineage.** URLs are extracted as data and attached by the UI; the model
  never emits links or markup.
- **Accessible.** Every segment has a transcript and source link; playback is
  fully keyboard operable and announced to assistive technology.
- **Sustainable.** No idle compute, static pre-fetched reads, low-footprint
  models, and reusable deterministic output.

## Licence

MIT. See [`LICENSE`](LICENSE).
