# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fast CI checks for the backend. No model or llama-cpp needed: app.py imports
llama_cpp lazily and loads the model only on first inference, so these exercise
routing, static serving, schema, and the grounding guardrail on a cheap runner."""

from fastapi.testclient import TestClient

import app as m

client = TestClient(m.app)


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


def test_empty_bulletin_rejected():
    assert client.post("/api/generate-bulletin", json={"articles": []}).status_code == 400


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
