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
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Solutions News Radio Engine", version="2.0.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.getenv("MODEL_PATH", "/app/models/model.gguf")
N_CTX = int(os.getenv("N_CTX", "4096"))
N_THREADS = int(os.getenv("N_THREADS", str(os.cpu_count() or 2)))

# Same-origin serving means CORS is not needed on the Space itself. It stays
# configurable for a GitHub Pages frontend calling this backend cross-origin.
# allow_credentials is False so a wildcard origin stays valid (browsers reject
# "*" together with credentials); this API uses no cookies or auth.
_origins_env = os.getenv("ALLOWED_ORIGINS", "*").strip()
ALLOWED_ORIGINS = ["*"] if _origins_env == "*" else [o.strip() for o in _origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
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


class BulletinRequest(BaseModel):
    articles: List[RawArticle]
    anchor_name: Optional[str] = "Alex"


class SoJoEvaluation(BaseModel):
    is_solutions_story: bool
    is_unresolved_crisis: bool
    has_response: bool
    has_evidence: bool
    has_limitations: bool
    action_anchor: str
    clean_summary: str


# --- Prompts (extractive, deterministic) -------------------------------------

SOJO_EVAL_PROMPT = """<|im_start|>system
You are an expert news analyst trained by the Solutions Journalism Network (SJN).
Evaluate the provided news article against the 4 Pillars of Solutions Journalism.

Rules:
1. 'is_solutions_story': True ONLY if it covers a concrete effort to solve a problem with evidence.
2. 'is_unresolved_crisis': True if it describes an active disaster, famine, or war without an immediate solution.
3. Extractive Accuracy: Base evaluation STRICTLY on the provided text. Do not invent details.

Return JSON ONLY matching this format:
{{
  "is_solutions_story": boolean,
  "is_unresolved_crisis": boolean,
  "has_response": boolean,
  "has_evidence": boolean,
  "has_limitations": boolean,
  "action_anchor": "Brief statement highlighting mutual aid or policy resources if crisis, else empty",
  "clean_summary": "1-2 sentence core fact summary"
}}
<|im_end|>
<|im_start|>user
Title: {title}
Summary: {summary}
<|im_end|>
<|im_start|>assistant
"""

RADIO_SCRIPT_PROMPT = """<|im_start|>system
You are a lead news editor at a global public news service like the BBC World Service.
Write a continuous, spoken 2-minute radio news bulletin script based ONLY on the provided JSON articles.

Formatting Rules for Text-to-Speech (TTS):
1. Start with an opening news sting indicator: "[AUDIO: News Sting - 3 seconds]".
2. Begin with a 15-second opening summary of the top stories.
3. Use natural spoken broadcast transitions between geographic tiers (e.g., "Turning to local news in Ontario...", "Across the country today...", "In international developments...").
4. If an article has an 'action_anchor', include a gentle broadcast transition pointing listeners to verified mutual aid or resource links in their player deck.
5. Spell out large numbers as words (e.g., "three million" not "$3M") so speech engines read them naturally.
6. Do NOT include markdown headers, bold text, or URLs in the spoken body text.

Use ONLY facts from the input stories. Do NOT invent outside information, numbers, names, or causes.
<|im_end|>
<|im_start|>user
Articles JSON:
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


# --- Core processing ---------------------------------------------------------

def evaluate_article(article: RawArticle) -> SoJoEvaluation:
    prompt = SOJO_EVAL_PROMPT.format(title=article.title, summary=article.summary)
    response = get_llm()(prompt, max_tokens=400, temperature=0.0, top_p=1.0, stop=["<|im_end|>"])
    raw = response["choices"][0]["text"].strip()
    try:
        return SoJoEvaluation(**extract_json_object(raw))
    except Exception:
        # Fallback keeps the pipeline deterministic and extractive if JSON strays.
        return SoJoEvaluation(
            is_solutions_story=False,
            is_unresolved_crisis=False,
            has_response=False,
            has_evidence=False,
            has_limitations=False,
            action_anchor="",
            clean_summary=article.summary[:200],
        )


# --- API ---------------------------------------------------------------------

@app.get("/api/health")
def health_check():
    return {"status": "online", "engine": "Solutions News Radio Engine v2.0", "model_loaded": _llm is not None}


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
                "source": art.source_name,
                "title": art.title,
                "summary": ev.clean_summary,
                "url": art.url,  # link lineage: straight from the client, never model-generated
                "is_sojo": ev.is_solutions_story,
                "is_crisis": ev.is_unresolved_crisis,
                "action_anchor": ev.action_anchor,
            })

        articles_json = json.dumps(processed_stories, indent=2)
        prompt = RADIO_SCRIPT_PROMPT.format(articles_json=articles_json)
        # Deterministic per ADR 0001 (overrides the reference's temperature=0.2).
        script_response = get_llm()(prompt, max_tokens=800, temperature=0.0, top_p=1.0, stop=["<|im_end|>"])
        script_text = script_response["choices"][0]["text"].strip()
    except RuntimeError as err:
        raise HTTPException(status_code=503, detail=str(err))

    return {
        "script": script_text,
        "story_metadata": processed_stories,
        "grounding_warnings": ground_numbers(script_text, req.articles),
    }


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
