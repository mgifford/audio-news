# Phase 3 — Automation & deployment pipeline

Phase 3 makes deployment automatic and adds the scheduled feed pre-fetch. It keeps
one workflow for deployment (no duplicate) and gates the expensive Docker build on
cheap CI.

## Deployment: `.github/workflows/sync-to-hf-space.yml`

One workflow, three stages:

1. **CI gate (`ci` job).** On every push to `main` it runs fast checks on a plain
   runner — `py_compile`, `node --check`, `sources.json` validation, and the backend
   `pytest` suite (`tests/test_app.py`, which needs no model because `llama_cpp` is
   imported lazily). A failure here stops the pipeline in seconds instead of ~10
   minutes into a Docker build.
2. **Sync (`sync` job, `needs: ci`).** Force-pushes `main` to the Space, guarded on
   the `HF_TOKEN` secret.
3. **Health wait.** Polls the Space `/api/health` (instant — the model loads lazily)
   until HTTP 200, up to ~20 minutes, so a red pipeline means a real deploy problem,
   not just "git push worked".

**Path filter:** docs-only commits (`PHASE*.md`, `decisions/**`, any `*.md` except
that `README.md` is intentionally *not* excluded since its front matter is the Space
config) do not trigger a rebuild. `sources.json` and `feeds-cache.json` are not
excluded because the frontend reads them.

There is deliberately **no second `deploy-hf.yml`** — two workflows force-pushing to
the same Space branch would race and double every build.

## Scheduled pre-fetch: `.github/workflows/prefetch-feeds.yml`

Every 6 hours (and on demand) a runner fetches the feeds **server-side** — open
egress, no browser CORS proxy, so nothing about a reader's activity reaches a third
party — via `scripts/prefetch_feeds.py`, and commits `feeds-cache.json` with
deck-ready items plus per-feed verification (`httpStatus`, `fetchedAt`, `itemCount`).

The commit message carries **`[skip ci]`**, so a data refresh never triggers a
Docker rebuild.

### How the frontend uses it

On load the client tries `feeds-cache.json`. If it is present **and generated within
the last 12 hours**, the deck is built from it (no proxy). A stale or missing cache
silently falls back to the live CORS proxy, so:

- On **GitHub Pages**, the cron keeps the cache fresh, so the proxy is rarely used.
- On the **Docker Space**, the client uses the runtime **`/api/feeds`** endpoint
  (added later): the backend fetches the allowlisted feeds server-side, so fresh
  feeds are served without rebuilds and without the third-party CORS proxy.

## Rollback

The sync force-pushes, so the Space is an exact mirror of `main`: roll back with
`git revert` + push, and never edit files directly on the Space or the next sync
overwrites them.

## Operational notes

- Make `HF_TOKEN` a fine-grained token scoped to just this Space (write), not an
  account-wide token.
- First Docker build installs a prebuilt `llama-cpp-python` CPU wheel and downloads
  the ~1.1 GB model; later builds are cached. (Source compilation OOM-killed the HF
  build container, so a prebuilt wheel is used.)
