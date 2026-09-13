---
title: Audio News Reader
emoji: 📻
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: agpl-3.0
---

<!-- The YAML block above configures the Hugging Face Space (Docker SDK). It is
     rendered as a small table on GitHub. On the Space, the Dockerfile builds a
     FastAPI backend that serves both the /api endpoints and the static frontend
     (index.html, styles.css, app.js, sources.json) from one origin. -->


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

## Status: Phase 2 (AI broadcast engine)

Phase 2 adds a Python backend — FastAPI + `llama-cpp-python` running a small
quantized local model — deployed as a **Docker** Hugging Face Space that serves
both the API and the Phase 1 static frontend from one origin. The frontend can
now ask the backend to classify each story against the SJN four pillars and
synthesize a continuous radio-broadcast script; if the backend is unreachable it
falls back to the Phase 1 local reader.

Backend (`app.py`, `Dockerfile`, `requirements.txt`):

- `POST /api/generate-bulletin` — evaluate a deck and return a broadcast script,
  per-story metadata, and numeric-grounding warnings.
- `GET /api/health`, `GET /mcp/tools` (tool manifest), and same-origin static
  serving of the frontend assets.
- Every inference call is deterministic (`temperature = 0.0`, `top_p = 1.0`).

Frontend:

- [`index.html`](index.html), [`styles.css`](styles.css), [`app.js`](app.js) —
  the accessible reader shell, now with a **Generate AI broadcast** control and a
  visible transcript that shows exactly what is read aloud.
- [`sources.json`](sources.json) — the editorial feed registry (feeds
  **unverified**, `lastVerified: null`, until confirmed live).

Docs: [`PHASE2.md`](PHASE2.md), [`PHASE1.md`](PHASE1.md), [`PHASE0.md`](PHASE0.md),
[`decisions/`](decisions/).

### Run the frontend only (no backend)

```bash
python3 -m http.server 8000   # then open http://localhost:8000
```

### Run the full backend locally

```bash
pip install -r requirements.txt
export MODEL_PATH=/path/to/model.gguf   # a Qwen2.5-1.5B-Instruct GGUF, for example
uvicorn app:app --port 7860             # open http://localhost:7860
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

**AGPL-3.0-or-later.** See [`LICENSE`](LICENSE). Because this project is meant to
run as a hosted web service, the AGPL's network-use clause (section 13) applies:
anyone who runs a modified version and lets others use it over a network must
offer those users its source. The running app links back to this repository to
satisfy that.
