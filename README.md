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

## Status: Phase 0 (planning)

Phase 0 establishes the architecture, the anti-hallucination protocol, the
editorial taxonomy, and the zero-cost execution model **before** any application
code is written or any cloud instance is provisioned. There is no running
service yet and no model is downloaded.

- [`PHASE0.md`](PHASE0.md) — the foundation document: blueprint, candidate
  models, SJN reframing protocol, anti-hallucination controls, accessibility and
  sustainability requirements, and the Phase 1 validation checklist.
- [`data/feeds.json`](data/feeds.json) — the editorial feed registry, organised
  by geography and perspective. Every entry is **unverified** in Phase 0
  (`lastVerified: null`); live verification is a Phase 1 task.
- [`decisions/`](decisions/) — architecture decision records.

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
