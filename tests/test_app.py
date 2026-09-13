# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fast CI checks for the backend. No model or llama-cpp needed: app.py imports
llama_cpp lazily and loads the model only on first inference, so these exercise
routing, static serving, schema, and the grounding guardrail on a cheap runner."""

from fastapi.testclient import TestClient

import app as m
import feedfetch

client = TestClient(m.app)

_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Co-op cuts bills</title><description>&lt;p&gt;A pilot helped 200 homes.&lt;/p&gt;</description><link>https://example.org/a</link></item>
<item><title>No link</title><description>x</description></item>
</channel></rss>"""


def test_feedfetch_parse_is_extractive_and_drops_linkless():
    items = feedfetch.parse_feed(_RSS)
    assert len(items) == 1  # linkless item dropped (link lineage)
    assert items[0]["link"] == "https://example.org/a"
    assert "<" not in items[0]["description"] and "200 homes" in items[0]["description"]


def test_feedfetch_strips_boilerplate_and_bylines():
    assert feedfetch.clean("Real news. The post Foo appeared first on ProPublica.") == "Real news."
    assert feedfetch.clean("A headline sofia Wed, 07/01/2026 - 15:50") == "A headline"


def test_feedfetch_tidy_ends_on_a_sentence_no_ellipsis():
    text = "One complete sentence here. " + "and more words " * 60
    out = feedfetch.tidy(text)
    assert "…" not in out
    assert out.endswith((".", "!", "?"))
    assert out.startswith("One complete sentence here.")


def test_feedfetch_prefers_full_content_over_summary():
    entry = {"content": [{"value": "<p>" + "Full body. " * 20 + "</p>"}], "summary": "short"}
    assert "Full body" in feedfetch._best_text(entry)


def test_feedfetch_build_cache_offline():
    sources = {"geography": {"local": [{"id": "x", "name": "X", "url": "https://x", "type": "civic"}]}}
    cache = feedfetch.build_cache(sources, fetcher=lambda url: (200, _RSS))
    assert cache["geography"]["local"][0]["itemCount"] == 1
    assert feedfetch.total_items(cache) == 1


def _registry_urls():
    reg = json_sources()
    urls = set()
    for region in reg.get("regions", {}).values():
        for scope in ("local", "regional", "national"):
            urls |= {f["url"] for f in region.get(scope, [])}
    urls |= {f["url"] for f in reg.get("international", [])}
    urls |= {f["url"] for f in reg.get("beats", [])}
    return urls


def test_api_feeds_only_reads_registry(monkeypatch):
    # /api/feeds must never fetch a caller-supplied URL — only sources.json feeds.
    seen = []
    monkeypatch.setattr(feedfetch, "fetch", lambda url: (seen.append(url), (200, _RSS))[1])
    m._feeds_cache.clear()  # bypass TTL cache
    r = client.get("/api/feeds?region=paris")
    assert r.status_code == 200
    body = r.json()
    assert "geography" in body and body["region"] == "paris" and body["language"] == "fr"
    assert seen and all(u in _registry_urls() for u in seen)


def test_api_regions_lists_expected_regions():
    ids = {r["id"] for r in client.get("/api/regions").json()["regions"]}
    assert {"ottawa", "toronto", "vancouver", "eugene", "london", "paris"} <= ids


def test_french_framing():
    stories = [{"scope": "local", "source": "Le Parisien", "title": "Une nouvelle piste cyclable",
                "summary": "La ville agrandit son réseau.", "is_solutions_story": True,
                "response": "une piste protégée", "evidence": "", "limitation": ""}]
    script = m.assemble_script(stories, "Alex", lang="fr")
    assert "Voici votre bulletin" in script and "À la une :" in script
    assert "La réponse : Une piste protégée." in script
    assert "Voilà votre bulletin." in script


def json_sources():
    import json, os
    with open(os.path.join(m.BASE_DIR, "sources.json")) as fh:
        return json.load(fh)


def test_health_does_not_load_model():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["model_loaded"] is False
    assert "build" in body  # SHA-stamped so the deploy check verifies this exact build


def test_load_cap_limits_crisis_to_one_in_three():
    stories = [
        {"is_crisis": True}, {"is_crisis": True}, {"is_crisis": True},
        {"is_crisis": False}, {"is_crisis": False}, {"is_crisis": False},
    ]
    kept, omitted = m.apply_load_cap(stories)
    # 6 stories -> at most 2 crisis kept; the third crisis is omitted.
    assert sum(1 for s in kept if s["is_crisis"]) == 2
    assert len(omitted) == 1
    assert all(s["is_crisis"] for s in omitted)


def test_assemble_script_is_extractive_and_omits_urls():
    stories = [{
        "scope": "local", "source": "CBC", "title": "Co-op cuts bills",
        "summary": "A pilot helped 200 homes.", "url": "https://example.org/x",
        "is_crisis": False, "action_anchor": "",
    }]
    script = m.assemble_script(stories, "Alex")
    assert "Co-op cuts bills" in script and "200 homes" in script
    assert "https://" not in script  # URLs are never spoken (link lineage)


def test_assemble_script_leads_with_top_stories_headlines():
    stories = [{"scope": "local", "source": "S", "title": "A local thing", "summary": "x"}]
    script = m.assemble_script(stories)
    assert "Our top stories:" in script and "Now, the details." in script


def test_solutions_arc_uses_extracted_pillars():
    stories = [{
        "scope": "national", "source": "Grist", "title": "City cuts commute",
        "summary": "s", "is_solutions_story": True,
        "response": "a new transit lane", "evidence": "trips fell",
        "limitation": "still a pilot", "action_anchor": "",
    }]
    script = m.assemble_script(stories)
    assert "The response: A new transit lane." in script
    assert "The evidence so far: Trips fell." in script
    assert "The limitation: Still a pilot." in script


def test_crisis_arc_and_breaking_first_ordering():
    stories = [
        {"scope": "local", "source": "L", "title": "Local fair", "summary": "s",
         "is_solutions_story": False, "is_crisis": False},
        {"scope": "international", "source": "TNH", "title": "Floods hit region", "summary": "s",
         "is_crisis": True, "root_cause": "heavy rains", "action_anchor": "donate to relief"},
    ]
    script = m.assemble_script(stories)
    # Breaking/international crisis leads the details ahead of the local item.
    assert script.index("Floods hit region") < script.index("Local fair")
    assert "Heavy rains." in script
    assert "If you would like to help: Donate to relief." in script
    assert "resource link is in your player deck" in script


def test_beat_transition_is_audible():
    stories = [{"scope": "national", "beat": "health", "source": "KFF",
                "title": "Clinics cut waits", "summary": "s",
                "is_solutions_story": False, "is_crisis": False}]
    script = m.assemble_script(stories)
    assert "In health news, from KFF:" in script


def test_and_finally_closer_ends_on_a_solutions_story():
    stories = [
        {"scope": "international", "source": "TNH", "title": "Crisis one", "summary": "s", "is_crisis": True, "root_cause": "cause"},
        {"scope": "national", "source": "N", "title": "Middle story", "summary": "s"},
        {"scope": "local", "source": "L", "title": "Good news", "summary": "s",
         "is_solutions_story": True, "response": "a fix"},
    ]
    script = m.assemble_script(stories)
    assert "And finally, some better news, from L: Good news." in script
    # the closer really is last
    assert script.index("And finally") > script.index("Crisis one")


def test_word_cap_keeps_bulletin_near_target():
    stories = [{"scope": "national", "source": f"S{i}", "title": f"Story number {i} about things",
                "summary": "This is a fairly long summary sentence that repeats content to add words. " * 4}
               for i in range(20)]
    script = m.assemble_script(stories)
    # Soft cap ~320 words; allow headroom for the closer + framing.
    assert len(script.split()) < 420


def test_cluster_by_topic_groups_same_event():
    stories = [
        {"source": "A", "title": "Delta flooding displaces thousands"},
        {"source": "B", "title": "Thousands flee as delta flooding worsens"},
        {"source": "C", "title": "Rainforest corridor restored for wildlife"},
    ]
    clusters = m.cluster_by_topic(stories)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2]  # A+B cluster, C alone


def test_international_roundup_is_multi_source_and_attributed():
    stories = [
        {"scope": "international", "source": "The New Humanitarian",
         "title": "Delta flooding displaces thousands", "summary": "Families forced from homes."},
        {"scope": "international", "source": "BBC World Service",
         "title": "Thousands flee as delta flooding worsens", "summary": "Water levels keep rising."},
    ]
    script = m.assemble_script(stories)
    assert "several outlets are following" in script
    assert "The New Humanitarian reports: Families forced from homes." in script
    assert "BBC World Service reports: Water levels keep rising." in script


def test_no_roundup_for_single_source():
    # Two stories from the SAME outlet must not be presented as a multi-source roundup.
    stories = [
        {"scope": "international", "source": "BBC", "title": "Delta flooding displaces thousands", "summary": "x"},
        {"scope": "international", "source": "BBC", "title": "Thousands flee delta flooding", "summary": "y"},
    ]
    assert "several outlets" not in m.assemble_script(stories)


def test_mcp_manifest_lists_tools():
    names = [t["name"] for t in client.get("/mcp/tools").json()["tools"]]
    assert names == ["evaluate_sojo_story", "generate_radio_bulletin"]


def test_static_frontend_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "Generated radio script" in r.text


def test_static_allowlist_hides_source():
    # app.py must never be served over HTTP.
    assert client.get("/app.py").status_code == 404


def test_docs_and_openapi_disabled():
    # Reduce public attack surface: no interactive docs / schema.
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_build_cache_is_concurrent():
    import time
    import feedfetch as ff

    def slow(url):
        time.sleep(0.3)
        return (200, _RSS)

    src = {"geography": {"a": [{"id": "1", "name": "A", "url": "u"}],
                         "b": [{"id": "2", "name": "B", "url": "u"}],
                         "c": [{"id": "3", "name": "C", "url": "u"}]}}
    start = time.time()
    cache = ff.build_cache(src, fetcher=slow)
    assert time.time() - start < 0.7  # 3x0.3s sequentially would be ~0.9s
    assert ff.total_items(cache) == 3


def test_empty_bulletin_rejected():
    assert client.post("/api/generate-bulletin", json={"articles": []}).status_code == 400


def test_too_many_articles_rejected():
    art = {"title": "t", "summary": "s", "url": "https://x", "scope": "international", "source_name": "S"}
    r = client.post("/api/generate-bulletin", json={"articles": [art] * (m.MAX_ARTICLES + 5)})
    assert r.status_code == 413  # bounds per-request compute before any model call


def test_per_ip_rate_limit():
    m._rate_hits.clear()
    ip = "198.51.100.7"
    assert all(m._rate_ok(ip)[0] for _ in range(m.RATE_MAX))  # first N allowed
    allowed, retry = m._rate_ok(ip)
    assert not allowed and retry > 0                            # then blocked with Retry-After
    assert m._rate_ok("198.51.100.8")[0]                        # a different IP is unaffected


def test_client_ip_prefers_forwarded_for():
    class Req:
        headers = {"x-forwarded-for": "203.0.113.5, 10.0.0.1"}
        client = None
    assert m._client_ip(Req()) == "203.0.113.5"


def test_json_extraction_tolerates_fences_and_prose():
    assert m.extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert m.extract_json_object('sure: {"x": true} done') == {"x": True}


def test_grounding_flags_only_introduced_numbers():
    arts = [m.RawArticle(title="Aid reaches 200 families",
                         summary="A pilot in 2024 helped 200 homes.",
                         url="https://example.org", scope="local", source_name="S")]
    # 200 and 2024 are in the source -> no warning; 999 is introduced -> warned.
    assert m.ground_numbers("Two hundred families were helped in 2024.", arts) == []
    warnings = m.ground_numbers("A record 999 people and 200 families.", arts)
    assert any("999" in w for w in warnings)
    assert not any("200" in w for w in warnings)
