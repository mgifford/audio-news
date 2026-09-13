# Phase 2 — AI broadcast engine

Phase 2 turns the static reader into an AI-assisted radio-broadcast producer. It
adds a Python backend (FastAPI + `llama-cpp-python` with a small quantized local
model) deployed as a **Docker** Hugging Face Space that serves both the API and
the Phase 1 frontend from one origin.

## Components

- **`app.py`** — FastAPI app with:
  - `POST /api/generate-bulletin` — classifies each article against the SJN four
    pillars, then synthesizes a continuous broadcast script. Returns the script,
    per-story metadata (including the untouched source URL), and numeric-grounding
    warnings.
  - `GET /api/health` — liveness plus whether the model has loaded.
  - `GET /mcp/tools` — a tool manifest (see *MCP status* below).
  - Same-origin static serving of `index.html`, `styles.css`, `app.js`,
    `sources.json` via an explicit allowlist (so `app.py`, the `Dockerfile`, and
    docs are never served over the web).
- **`Dockerfile`** — CPU container; builds `llama-cpp-python` against OpenBLAS and
  downloads the GGUF model at build time.
- **`requirements.txt`** — backend dependencies.
- **Frontend** — a **Generate AI broadcast** control and a visible transcript.

## How this honours (and stretches) the Phase 0 boundaries

- **Deterministic — kept.** Every inference call runs at `temperature = 0.0`,
  `top_p = 1.0`. The reference's `temperature = 0.2` for script synthesis was
  overridden; ADR 0001 makes deterministic sampling non-negotiable.
- **Link lineage — kept.** URLs never pass through the model. They travel as
  metadata and are returned to the client unchanged; the script prompt forbids
  emitting URLs, and the transcript renders source links from that metadata.
- **Extractive — stretched, and this is the honest caveat.** Phase 1 read the
  feed text verbatim. Phase 2's synthesis step is genuinely *generative*: it
  rewrites stories into broadcast prose. The prompt forbids outside facts,
  numbers, names, and causes, but a prompt is not a guarantee. This is the central
  risk Phase 0 flagged, and it is why the next two safeguards exist.
- **Transcript parity — kept.** The script is shown on screen exactly as it is
  read aloud (production cues like `[AUDIO: …]` are stripped from both), so audio
  is never an unverifiable channel and a listener can check it against the sources.
- **Numeric grounding — added, best-effort.** `ground_numbers` flags any
  digit-number in the script that does not appear in the source articles, and the
  frontend shows the warning above the transcript. **Limits:** the prompt asks the
  model to spell large numbers as words ("three million"), which a digit check
  cannot verify, and it does not police invented names or causal claims. It is a
  review signal, not proof.

Because synthesis is generative, Phase 0's validation gate still applies before
this speaks to a real audience: run a labelled sample and confirm no introduced
facts, numbers, causes, or names. Until then, treat the AI broadcast as a draft
to review against the deck, not a finished bulletin.

## Deploying (Docker Space)

The Space `mgifford/audio-news` moves from Static to **Docker** SDK:

1. In the Space settings, set/confirm **SDK = Docker** (the `sdk: docker` +
   `app_port: 7860` front matter in `README.md` drives this on sync).
2. The existing `HF_TOKEN` GitHub Action still mirrors `main` to the Space; on
   push, HF builds the `Dockerfile`.
3. First build downloads the model (~1.1 GB) and compiles `llama-cpp-python`, so
   it is slow. Later builds are cached.

### Model

Default is **Qwen2.5-1.5B-Instruct GGUF (Q4_K_M)**, chosen partly because it is
**Apache-2.0**, which is compatible with this repo's AGPL-3.0 licence. Llama-3.2-3B
is more expressive but ships under Meta's community licence with use conditions;
confirm those before switching. The model URL is a `Dockerfile` build ARG, and
the exact filename should be confirmed against the source repo before building.

## Known operational caveats

- **Cold start + inference latency.** A scale-to-zero Space cold-starts, loads the
  model lazily on the first request, and then runs CPU inference for several
  stories plus an 800-token script. Expect the first bulletin to take tens of
  seconds. The model loads lazily so `/api/health` stays instant.
- **Open endpoint.** `/api/generate-bulletin` is unauthenticated, so anyone can
  spend the Space's compute. Tighten `ALLOWED_ORIGINS` and add rate limiting
  before promoting this beyond a prototype.
- **Not yet run end-to-end here.** This environment cannot run `llama-cpp` or
  reach Hugging Face, so the model path has not been exercised live; the non-model
  endpoints, routing, schema, and grounding logic are covered by the checks in
  the repo.

## MCP status

`GET /mcp/tools` returns a tool *manifest*, not a conformant Model Context
Protocol server over SSE/JSON-RPC. A real transport remains a later phase, as
noted in the Phase 0 checklist.
