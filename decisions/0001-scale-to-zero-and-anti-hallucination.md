# ADR 0001: Scale-to-Zero Runtime and Anti-Hallucination Boundary

- Status: Accepted (Phase 0 planning)
- Date: 2026-09-13

## Context

audio-news needs a language model to reframe and summarize public RSS entries
into spoken segments. It must do this at zero platform cost, without becoming a
source of invented facts, and without making audio the only carrier of
information.

## Decision

**Runtime.** audio-news uses a single scale-to-zero Hugging Face Space running
FastAPI plus a quantized local GGUF model, serving a static frontend. The Space
cold-starts on demand and returns to sleep, so idle time is unbilled. Where the
visitor's hardware allows, the reader falls back to Transformers.js in the
browser. The server is the convenience path and the browser is the resilient
one; that relationship stays visible to the user.

**Anti-hallucination boundary.** Spoken output is bound by four controls:

- Extractive-only context — the model may restructure and summarize the RSS
  entry but must not add facts from pre-trained knowledge; unsupported statements
  are dropped.
- Deterministic sampling — classification and synthesis run at
  `temperature = 0.0` and `top_p = 1.0`, so a fixed entry yields a reproducible
  script.
- Link lineage — URLs are extracted with `feedparser` and attached by the UI as
  data; the model is prohibited from emitting markup or links.
- No introduced numbers, dates, or causal claims beyond the source entry.

## Consequences

The common browsing path is served from pre-fetched static JSON and triggers no
inference, keeping the default zero-cost and low-energy. A cold Space, a
rate-limit, or an unavailable server degrades to the in-browser path rather than
to failure. Reproducible sampling allows a script for an unchanged entry to be
cached rather than regenerated.

The trade-off is an operational surface (a Space, a sync workflow, and model
licence obligations). Model licence compatibility with this project's AGPL-3.0 licence
must be confirmed before any weights are bundled or auto-downloaded, and every
anti-hallucination control must be demonstrated against a labelled sample in
Phase 1 before the reader speaks to a real listener. Accessibility is a
precondition, not a follow-up: every spoken segment must have a readable
transcript and a working source link.
