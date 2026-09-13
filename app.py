# SPDX-License-Identifier: AGPL-3.0-or-later
"""
audio-news Phase 2 backend — FastAPI + llama-cpp local inference.

Design boundaries carried from PHASE0.md / decisions/0001:
- Deterministic: every inference call runs at temperature=0.0, top_p=1.0 (ADR 0001
  makes this non-negotiable; the reference's 0.2 for script synthesis is overridden).
- Extractive only: prompts forbid outside knowledge; the model restructures the
  supplied article text and nothing else.
- Link lineage: URLs are never sent through the model. They travel as metadata and
  are returned to the client untouched; the script prompt forbids emitting URLs.
- Numeric grounding: a post-check flags digit-numbers in the script that do not
  appear in the source articles (see ground_numbers and its documented limits).

This module imports cleanly without llama-cpp installed and without a model file:
the model is loaded lazily on first inference, so the container starts and the
health check responds immediately, and the pure helpers are unit-testable.
"""

import os
import re
import json
import time
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

import feedfetch

# Disable the interactive docs and OpenAPI schema: unnecessary attack surface on a
# public endpoint (scanners probe /openapi.json, /docs). The tool manifest we do want
# to expose lives at /mcp/tools.
app = FastAPI(
    title="Solutions News Radio Engine",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.getenv("MODEL_PATH", "/app/models/model.gguf")


def _read_build_sha() -> str:
    """The deployed commit SHA, written into BUILD_SHA at deploy time (see the sync
    workflow). Lets /api/health prove which build is live, not just that it responds."""
    try:
        with open(os.path.join(BASE_DIR, "BUILD_SHA"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return os.getenv("BUILD_SHA", "unknown")


BUILD_SHA = _read_build_sha()
N_CTX = int(os.getenv("N_CTX", "4096"))
N_THREADS = int(os.getenv("N_THREADS", str(os.cpu_count() or 2)))

# The Space serves the frontend same-origin, so CORS matters only for a separate
# GitHub Pages frontend calling this backend. Default to the project's Pages origin
# plus any *.hf.space subdomain (wildcard subdomains need a regex — allow_origins
# can't express them). Override the exact list with ALLOWED_ORIGINS.
# allow_credentials is False (this API has no cookies/auth), so no wildcard is used.
_origins_env = os.getenv("ALLOWED_ORIGINS", "").strip()
ALLOWED_ORIGINS = (
    [o.strip() for o in _origins_env.split(",") if o.strip()]
    if _origins_env
    else ["https://mgifford.github.io", "http://localhost:8000", "http://127.0.0.1:8000"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"https://[a-z0-9-]+\.hf\.space",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# --- Lazy model loader -------------------------------------------------------

_llm = None


def get_llm():
    """Load the GGUF model on first use so import and startup stay cheap."""
    global _llm
    if _llm is None:
        from llama_cpp import Llama  # imported lazily so the module loads without it
        if not os.path.exists(MODEL_PATH):
            raise RuntimeError(f"Model file not found at {MODEL_PATH}")
        _llm = Llama(model_path=MODEL_PATH, n_ctx=N_CTX, n_threads=N_THREADS, verbose=False)
    return _llm


# --- Data models -------------------------------------------------------------

class RawArticle(BaseModel):
    title: str
    summary: str
    url: str
    scope: str  # 'local', 'regional', 'national', 'international'
    source_name: str
    beat: Optional[str] = None  # 'health', 'technology', 'business', 'government', ...


class BulletinRequest(BaseModel):
    articles: List[RawArticle]
    anchor_name: Optional[str] = "Alex"
    # "deterministic" (default): the bulletin is assembled from the feed's own words,
    # so spoken text == extracted text (no added hallucination surface). "generative":
    # a local model rephrases into broadcast prose (clearly labelled, grounding-checked).
    mode: Optional[str] = "deterministic"
    lang: Optional[str] = "en"  # framing language (must match the source language)


class SoJoEvaluation(BaseModel):
    is_solutions_story: bool
    is_unresolved_crisis: bool
    # SJN pillars, EXTRACTED verbatim/near-verbatim from the article — empty when the
    # article does not state them. Never invented (that is the anti-hallucination line).
    response: str = ""       # solutions: what is being done about the problem
    evidence: str = ""       # solutions: evidence the response is (or isn't) working
    limitation: str = ""     # solutions: caveat / where it falls short
    root_cause: str = ""     # crisis: the underlying cause/context as stated
    action_anchor: str = ""  # crisis: mutual-aid or resource mentioned in the article
    clean_summary: str = ""  # 1-2 sentence core fact summary


# --- Prompts (extractive) ----------------------------------------------------

SOJO_EVAL_PROMPT = """<|im_start|>system
You are a news analyst trained by the Solutions Journalism Network (SJN). Classify the
article and EXTRACT the SJN pillars that are actually present in it.

Hard rules:
- Use ONLY the provided text. Copy short phrases from it. NEVER invent a response, number, name, or cause.
- If a field is not stated in the article, return an empty string "". Do not guess.
- 'is_solutions_story': true ONLY if the article reports a concrete effort to solve a problem.
- 'is_unresolved_crisis': true if it reports an active crisis (disaster, famine, war) with no solution.

Return JSON ONLY:
{{
  "is_solutions_story": boolean,
  "is_unresolved_crisis": boolean,
  "response": "solutions: what is being done, as a short phrase from the text, else \\"\\"",
  "evidence": "solutions: evidence it is working, from the text, else \\"\\"",
  "limitation": "solutions: a stated caveat/limitation, from the text, else \\"\\"",
  "root_cause": "crisis: the stated cause/context, from the text, else \\"\\"",
  "action_anchor": "crisis: mutual-aid or resource named in the text, else \\"\\"",
  "clean_summary": "1-2 sentence core fact summary from the text"
}}
<|im_end|>
<|im_start|>user
Title: {title}
Summary: {summary}
<|im_end|>
<|im_start|>assistant
"""

# A short, generic style exemplar (cadence only — not about any real event) so the
# generative voice sounds like a broadcast. It teaches rhythm, not content.
RADIO_STYLE_EXAMPLE = (
    "Good evening. Our top stories: a city program cuts commute times, and relief "
    "reaches a flood-hit region. First tonight, the details. Officials say the new "
    "transit lane has been in place for six months. Early figures point to shorter "
    "trips, though planners caution it is still a pilot. Turning to the wider picture, "
    "aid groups report supplies are arriving, even as access remains difficult. "
    "If you would like to help, resource links are in your player deck. That is your briefing."
)

RADIO_SCRIPT_PROMPT = """<|im_start|>system
You are a lead editor at a global public news service like the BBC World Service.
Write a continuous, spoken radio news bulletin from ONLY the provided JSON stories.
{lang_line}

Structure:
1. Open with "Top stories:" naming each story in one clause.
2. Then "First, the details." and tell each story. For a solutions story, follow the SJN
   arc using the story's fields: the response, then the evidence, then the limitation.
   For a crisis, lead with dignity and the root_cause, then the action_anchor.
3. Use natural spoken transitions between tiers ("Across the country...", "Internationally...").
4. Spell large numbers as words. No markdown, no URLs.

Absolute rule: use ONLY facts in the JSON (title, summary, response, evidence, limitation,
root_cause, action_anchor). Do NOT add any fact, number, name, cause, or claim not present.
If a field is empty, do not fabricate it.

Style reference (cadence only, NOT content to reuse):
{style_example}
<|im_end|>
<|im_start|>user
Stories JSON:
{articles_json}
<|im_end|>
<|im_start|>assistant
"""


# --- Pure helpers (no model; unit-testable) ----------------------------------

def extract_json_object(text: str) -> dict:
    """Parse the first top-level JSON object from model output, tolerating fences/prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise ValueError("No JSON object found in model output")


_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set:
    return {m.group(0).replace(",", "") for m in _NUM_RE.finditer(text or "")}


def ground_numbers(script: str, articles: List[RawArticle]) -> List[str]:
    """
    Flag digit-numbers in the script that do not appear in any source article.

    Honours the Phase 0 "no numeric invention" rule as a best-effort guardrail.
    Known limits: the script prompt asks the model to spell large numbers as words
    ("three million"), which this digit-based check cannot verify; it also does not
    police invented names or causal claims. It is a warning signal for human review,
    not a proof of grounding.
    """
    source = set()
    for art in articles:
        source |= _numbers(art.title)
        source |= _numbers(art.summary)
    introduced = sorted(n for n in _numbers(script) if n not in source and len(n) > 1)
    return [f"Script contains number '{n}' not found in the source articles." for n in introduced]


# --- Bulletin assembly (deterministic) --------------------------------------

# Cognitive-load rule: at most one crisis/heavy story per three, so a bulletin does
# not overwhelm the listener (a solutions-journalism principle, not just a UI nicety).
def apply_load_cap(stories: list[dict]) -> tuple[list[dict], list[dict]]:
    allowed_crisis = max(1, len(stories) // 3)
    kept, omitted, crisis_seen = [], [], 0
    for s in stories:
        if s.get("is_crisis"):
            crisis_seen += 1
            if crisis_seen > allowed_crisis:
                omitted.append(s)
                continue
        kept.append(s)
    return kept, omitted


_SCOPE_WEIGHT = {"international": 3, "national": 3, "regional": 2, "local": 1}

# BBC-style summary target: ~2 minutes at ~150 wpm ≈ 300 words. Soft cap so a large
# deck doesn't run long; every story is still named in the headline block.
TARGET_WORDS = 320

# Localized framing for the deterministic bulletin. Only the connective tissue is
# translated — the story text stays exactly as extracted from the (language-matched)
# source. Add a locale here to support another language's regions.
FRAMING = {
    "en": {
        "intro": "This is your news bulletin, with {anchor}.",
        "top": "Our top stories: {headlines}.",
        "details": "Now, the details.",
        "lead": "{transition}, from {source}: ",
        "also": "Also from {source}: ",
        "response": "The response: ",
        "evidence": "The evidence so far: ",
        "limitation": "The limitation: ",
        "help": "If you would like to help: ",
        "resource": "A resource link is in your player deck.",
        "and_finally": "And finally, some better news, from {source}: ",
        "outro": "That is your briefing.",
        "scope": {"local": "In local news", "regional": "Turning to regional news",
                  "national": "Across the country", "international": "Internationally"},
        "beat": {"health": "In health news", "technology": "In technology",
                 "business": "In business and the economy", "government": "In government and policy",
                 "environment": "On the environment", "justice": "In justice and rights"},
    },
    "fr": {
        "intro": "Voici votre bulletin d'information, avec {anchor}.",
        "top": "À la une : {headlines}.",
        "details": "Les détails, maintenant.",
        "lead": "{transition}, de {source} : ",
        "also": "Également, de {source} : ",
        "response": "La réponse : ",
        "evidence": "Les résultats jusqu'ici : ",
        "limitation": "La limite : ",
        "help": "Pour aider : ",
        "resource": "Un lien vers des ressources se trouve dans votre lecteur.",
        "and_finally": "Et pour finir, une meilleure nouvelle, de {source} : ",
        "outro": "Voilà votre bulletin.",
        "scope": {"local": "Dans l'actualité locale", "regional": "Au niveau régional",
                  "national": "À l'échelle nationale", "international": "À l'international"},
        "beat": {"health": "Santé", "technology": "Technologie",
                 "business": "Économie", "government": "Politique et gouvernement",
                 "environment": "Environnement", "justice": "Justice et droits"},
    },
}


def _sent(text: str) -> str:
    """Tidy an extracted phrase into a spoken sentence: capitalize the first letter and
    end with terminal punctuation. Content is unchanged (still extractive)."""
    text = (text or "").strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1:] in ".!?" else text + "."


def _story_order(stories: list[dict]) -> list[dict]:
    """Lead with breaking/top news: crises first, then broader scopes."""
    return sorted(
        stories,
        key=lambda s: (1 if s.get("is_crisis") else 0, _SCOPE_WEIGHT.get(s.get("scope"), 0)),
        reverse=True,
    )


def _transition(story: dict, F: dict) -> str:
    beat = story.get("beat")
    if beat and beat in F["beat"]:
        return F["beat"][beat]
    return F["scope"].get(story.get("scope"), F["scope"]["national"])


def _story_body(s: dict, F: dict) -> list[str]:
    """The SJN arc for one story, as sentences (extractive only)."""
    out = []
    pillars = [s.get("response"), s.get("evidence"), s.get("limitation")]
    if s.get("is_solutions_story") and any(pillars):
        if s.get("response"):
            out.append(F["response"] + _sent(s["response"]))
        if s.get("evidence"):
            out.append(F["evidence"] + _sent(s["evidence"]))
        if s.get("limitation"):
            out.append(F["limitation"] + _sent(s["limitation"]))
    elif s.get("is_crisis"):
        out.append(_sent(s.get("root_cause") or s.get("summary") or ""))
        if s.get("action_anchor"):
            out.append(F["help"] + _sent(s["action_anchor"]) + " " + F["resource"])
    elif s.get("summary"):
        out.append(_sent(s["summary"]))
    return [p for p in out if p]


def assemble_script(stories: list[dict], anchor_name: str = "Alex", lang: str = "en") -> str:
    """Build the spoken bulletin from the feeds' own words — no model, so the spoken
    text equals the extracted text (nothing invented). BBC-summary shape: a top-stories
    headline block, breaking/top first, each story in its SJN arc, a soft ~300-word cap,
    and an 'And finally' solutions closer. Framing is localized; URLs are never spoken."""
    if not stories:
        return ""
    F = FRAMING.get(lang, FRAMING["en"])
    ordered = _story_order(stories)

    # Hold back one solutions story to close on ("And finally"), BBC-style, when the
    # bulletin is long enough to warrant it and the closer isn't the lead.
    closer = None
    if len(ordered) >= 3:
        for s in reversed(ordered):
            if s.get("is_solutions_story") and s is not ordered[0]:
                closer = s
                break
    body_stories = [s for s in ordered if s is not closer]

    headlines = "; ".join(s["title"].rstrip(".") for s in ordered)
    parts = [
        F["intro"].format(anchor=anchor_name),
        F["top"].format(headlines=headlines),
        F["details"],
    ]

    words = sum(len(p.split()) for p in parts)
    reserve = 45 if closer else 0  # leave room for the closer under the word target
    last_key = None
    for s in body_stories:
        key = s.get("beat") or s.get("scope")
        if key != last_key:
            lead = F["lead"].format(transition=_transition(s, F), source=s["source"]) + _sent(s["title"])
        else:
            lead = F["also"].format(source=s["source"]) + _sent(s["title"])
        last_key = key
        chunk = [lead, *_story_body(s, F)]
        chunk_words = sum(len(p.split()) for p in chunk)
        if words + chunk_words > TARGET_WORDS - reserve and words > 40:
            break  # over the soft target; remaining stories stay in the headline block
        parts.extend(chunk)
        words += chunk_words

    if closer:
        parts.append(F["and_finally"].format(source=closer["source"]) + _sent(closer["title"]))
        parts.extend(_story_body(closer, F))

    parts.append(F["outro"])
    return " ".join(p.strip() for p in parts if p.strip())


# --- Core processing ---------------------------------------------------------

def evaluate_article(article: RawArticle) -> SoJoEvaluation:
    prompt = SOJO_EVAL_PROMPT.format(title=article.title, summary=article.summary)
    response = get_llm()(prompt, max_tokens=400, temperature=0.0, top_p=1.0, stop=["<|im_end|>"])
    raw = response["choices"][0]["text"].strip()
    try:
        return SoJoEvaluation(**extract_json_object(raw))
    except Exception:
        # Fallback keeps the pipeline extractive if JSON strays: no pillars, just the summary.
        return SoJoEvaluation(
            is_solutions_story=False,
            is_unresolved_crisis=False,
            clean_summary=article.summary[:200],
        )


# --- API ---------------------------------------------------------------------

@app.get("/api/health")
def health_check():
    return {
        "status": "online",
        "engine": "Solutions News Radio Engine v2.0",
        "model_loaded": _llm is not None,
        "build": BUILD_SHA,
    }


@app.post("/api/generate-bulletin")
def generate_bulletin(req: BulletinRequest):
    if not req.articles:
        raise HTTPException(status_code=400, detail="No articles provided.")

    try:
        processed_stories = []
        for art in req.articles:
            ev = evaluate_article(art)
            processed_stories.append({
                "scope": art.scope,
                "beat": art.beat,
                "source": art.source_name,
                "title": art.title,
                "summary": ev.clean_summary,
                "url": art.url,  # link lineage: straight from the client, never model-generated
                "is_solutions_story": ev.is_solutions_story,
                "is_crisis": ev.is_unresolved_crisis,
                "response": ev.response,
                "evidence": ev.evidence,
                "limitation": ev.limitation,
                "root_cause": ev.root_cause,
                "action_anchor": ev.action_anchor,
            })

        # Cognitive-load cap applies to both modes.
        kept, omitted = apply_load_cap(processed_stories)

        lang = req.lang or "en"
        if req.mode == "generative":
            # Optional local-model rephrase. Clearly labelled and grounding-checked;
            # spoken text is no longer guaranteed to equal the source, hence the check.
            lang_line = "" if lang == "en" else f"Write the entire bulletin in this language code: {lang}."
            articles_json = json.dumps(kept, indent=2, ensure_ascii=False)
            prompt = RADIO_SCRIPT_PROMPT.format(
                articles_json=articles_json, style_example=RADIO_STYLE_EXAMPLE, lang_line=lang_line)
            resp = get_llm()(prompt, max_tokens=900, temperature=0.0, top_p=1.0, stop=["<|im_end|>"])
            script_text = resp["choices"][0]["text"].strip()
            warnings = ground_numbers(script_text, req.articles)
        else:
            # Default: deterministic assembly from the feeds' own words (no model call).
            script_text = assemble_script(kept, req.anchor_name or "Alex", lang)
            warnings = []  # spoken text == extracted text
    except RuntimeError as err:
        raise HTTPException(status_code=503, detail=str(err))

    return {
        "mode": req.mode,
        "script": script_text,
        "story_metadata": kept,
        "grounding_warnings": warnings,
        "omitted_for_load": len(omitted),
    }


_FEEDS_TTL = int(os.getenv("FEEDS_TTL", "600"))  # seconds; avoid re-fetching on every deck build
_feeds_cache = {}  # region_id -> {"at": float, "data": dict}


def _load_registry() -> dict:
    with open(os.path.join(BASE_DIR, "sources.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _region_geography(registry: dict, region_id: str) -> tuple[dict, str]:
    """Combine one region's local/regional/national feeds with the shared international
    and beat feeds into the geography shape build_cache expects. Returns (geography, lang)."""
    regions = registry.get("regions", {})
    region = regions.get(region_id) or next(iter(regions.values()), {})
    geography = {
        "local": region.get("local", []),
        "regional": region.get("regional", []),
        "national": region.get("national", []),
        "international": registry.get("international", []),
        "beats": registry.get("beats", []),
    }
    return geography, region.get("language", "en")


@app.get("/api/regions")
def api_regions():
    """The available regions (id, name, country, language) for the region picker."""
    regions = _load_registry().get("regions", {})
    return {"regions": [
        {"id": rid, "name": r.get("name", rid), "country": r.get("country", ""),
         "language": r.get("language", "en")}
        for rid, r in regions.items()
    ]}


@app.get("/api/feeds")
def api_feeds(region: str = "ottawa"):
    """Fetch one region's ALLOWLISTED feeds (plus shared international + beats)
    server-side — no CORS proxy, no third party, never a caller-supplied URL, so not
    an open proxy. Cached in memory per region for FEEDS_TTL seconds."""
    now = time.time()
    entry = _feeds_cache.get(region)
    if entry is None or now - entry["at"] > _FEEDS_TTL:
        try:
            registry = _load_registry()
        except OSError as err:
            raise HTTPException(status_code=500, detail=f"sources.json unavailable: {err}")
        geography, lang = _region_geography(registry, region)
        data = feedfetch.build_cache({"geography": geography})
        data["region"] = region
        data["language"] = lang
        entry = {"at": now, "data": data}
        _feeds_cache[region] = entry
    return entry["data"]


@app.get("/mcp/tools")
def list_mcp_tools():
    """Tool manifest for MCP-style client integration.

    Note: this is a manifest endpoint, not a full MCP server over SSE/JSON-RPC.
    A conformant transport is a later phase (see PHASE0.md §9 MCP checklist item).
    """
    return {
        "tools": [
            {
                "name": "evaluate_sojo_story",
                "description": "Evaluates article text against Solutions Journalism Network 4-Pillar criteria.",
                "inputSchema": RawArticle.model_json_schema(),
            },
            {
                "name": "generate_radio_bulletin",
                "description": "Converts a deck of news items into a continuous broadcast radio script.",
                "inputSchema": BulletinRequest.model_json_schema(),
            },
        ]
    }


# --- Static frontend (same-origin) -------------------------------------------
# Explicit allowlist of the Phase 1 client assets. Serving specific files (rather
# than mounting the whole directory) keeps app.py, Dockerfile, and docs off the web.

_STATIC_FILES = {
    "index.html": "text/html",
    "styles.css": "text/css",
    "app.js": "text/javascript",
    "sources.json": "application/json",
}


def _static_response(name: str):
    path = os.path.join(BASE_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type=_STATIC_FILES[name])


@app.get("/")
def index():
    return _static_response("index.html")


@app.get("/{filename}")
def static_asset(filename: str):
    if filename in _STATIC_FILES:
        return _static_response(filename)
    raise HTTPException(status_code=404, detail="Not found")
