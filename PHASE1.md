# Phase 1 — Local client foundation

Phase 1 delivers a static, offline-first web front-end that runs directly on
GitHub Pages, or as the static web directory inside a Hugging Face Space. There
is still no language model and no server of our own; Phase 1 is the reader shell,
the local state, and direct RSS ingestion.

## What Phase 1 delivers

1. **Curated feed registry — [`sources.json`](sources.json).** Feeds categorized
   by geographic scope (local, regional, national, international). Each entry
   keeps an `id`, `name`, `url`, and editorial `type`, plus a `lastVerified`
   field (see *Verification status* below).
2. **Local state controller — [`app.js`](app.js).** Manages user preferences
   (theme, geographic ratio mix) and listened-to story history entirely in the
   browser's `localStorage`. No account, no telemetry, no server call of ours.
3. **RSS ingestion with direct link lineage.** Feeds are fetched client-side and
   parsed with the browser's `DOMParser`. Every card links straight to the
   original publisher URL taken from the feed; links are never rewritten.
4. **Accessible reader shell — [`index.html`](index.html) + [`styles.css`](styles.css).**
   A high-contrast, keyboard-navigable UI with Web Speech API playback controls.

## Local state (localStorage keys)

| Key | Contents |
| --- | --- |
| `sojo_theme` | `dark` or `high-contrast`. |
| `sojo_ratios` | Story counts per scope, e.g. `{ "local": 1, "regional": 1, "national": 2, "international": 1 }`. |
| `sojo_history` | Up to 50 recently-read story links, so bulletins avoid repeats. |
| `sojo_proxy` | Optional override for the CORS proxy base URL. |

All reads are wrapped so a private window or blocked storage degrades to defaults
rather than breaking the page.

## Accessibility

- Skip link to main content; semantic landmarks and a single `h1`.
- Every control is a real `<button>`, keyboard operable, with a visible focus
  ring (`:focus-visible`, 3px, offset).
- The settings drawer uses `aria-expanded` on its toggle, is hidden with the
  `hidden` attribute, moves focus into itself on open, returns focus to the
  toggle on close, and closes on `Escape`.
- Status messages go through a single `role="status"` region (polite), so
  progress and errors are announced without a disruptive `alert()`, and the story
  list is not itself a live region (it would otherwise read the whole deck aloud
  on every fetch).
- The theme toggle is a toggle button (`aria-pressed`) offering a WCAG-friendly
  high-contrast palette.
- `prefers-reduced-motion` is honoured.
- **Transcript parity:** the text read aloud is exactly the title and summary
  shown on each card, so audio is never a separate, unverifiable channel.

## Anti-hallucination and link lineage (carried from Phase 0)

- **Extractive only.** The deck shows and reads the feed's own title and summary.
  No text is generated or paraphrased in Phase 1.
- **Safe rendering.** Feed content is inserted with `textContent`, never
  `innerHTML`, so a hostile or compromised feed/proxy cannot inject markup or
  script.
- **Link lineage.** Source links come straight from the feed entry and open the
  original publisher in a new tab.

## Known trade-off: the CORS proxy

A purely static page cannot fetch most cross-origin RSS feeds directly, because
publishers do not send permissive CORS headers. Phase 1 therefore routes feed
requests through a third-party CORS proxy (default `api.allorigins.win`,
overridable via the `sojo_proxy` localStorage key).

This is a real privacy and integrity trade-off and is disclosed in the UI: the
proxy can observe which feeds load and, in principle, alter feed content in
transit. Source links still point directly at the publisher. The
privacy-preserving alternative is the Phase 0 architecture's **server-side
pre-fetch** (feeds fetched once, server-side, and served as static JSON), which a
later phase should adopt for anything beyond local testing. The default proxy is
unverified from this environment.

## Verification status

Feeds in `sources.json` carry `lastVerified: null`: none has been confirmed
reachable and valid RSS/Atom from a browser yet. Confirming reachability, feed
validity, and per-feed CORS/proxy behaviour — and recording the dates — remains
open, consistent with the Phase 0 rule that a feed is not presented as a
guaranteed working source until it has a recorded verification date.

## Running it

Serve the directory over http(s) (not `file://`, which blocks `fetch`):

```bash
python3 -m http.server 8000
# then open http://localhost:8000
```

For GitHub Pages, enable Pages for the repository (Settings → Pages → deploy from
the default branch, root). The site is plain static files with no build step.
