"""0.1.12 hardening: documentation TLS context and the expanded v3 route catalog (offline)."""

from __future__ import annotations

import json
import ssl
from urllib.request import HTTPSHandler, Request

from legends_dataforseo import cli, documentation, estimate_cost, load_routes, route_for
from legends_dataforseo.client import _known_free_get

# Captured at import, before conftest's autouse fixture replaces _open with a network tripwire.
_REAL_DOCS_OPEN = documentation._open


def test_documentation_open_passes_ssl_context(monkeypatch):
    context = ssl.create_default_context()
    captured = {}

    class Opener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return "response"

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(documentation, "_ssl_context", lambda: context)
    monkeypatch.setattr(documentation, "build_opener", fake_build_opener)
    request = Request(documentation.INDEX_URL)
    assert _REAL_DOCS_OPEN(request, 7) == "response"
    https = [h for h in captured["handlers"] if isinstance(h, HTTPSHandler)]
    assert len(https) == 1 and https[0]._context is context  # context= reaches the TLS handshake
    assert any(isinstance(h, documentation._NoRedirect) for h in captured["handlers"])
    assert captured["timeout"] == 7 and captured["request"] is request


def test_documentation_open_falls_back_without_certifi(monkeypatch):
    captured = {}

    class Opener:
        def open(self, request, timeout):
            return "ok"

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(documentation, "_ssl_context", lambda: None)
    monkeypatch.setattr(documentation, "build_opener", fake_build_opener)
    assert _REAL_DOCS_OPEN(Request(documentation.INDEX_URL), 5) == "ok"
    assert not any(isinstance(h, HTTPSHandler) for h in captured["handlers"])


NEW_ROUTES = {
    "serp-task-post": ("serp", "POST", "/serp/google/organic/task_post", "standard_queue", 0.0012),
    "serp-task-get": ("serp", "GET", "/serp/google/organic/task_get/advanced/{id}", "free", 0.0),
    "local-finder": ("serp", "POST", "/serp/google/local_finder/live/advanced", "live", 0.002),
    "ai-mode": ("serp", "POST", "/serp/google/ai_mode/live/advanced", "live", 0.004),
    "serp-ai-summary": ("serp", "POST", "/serp/ai_summary", "live", 0.003),
    "serp-screenshot": ("serp", "POST", "/serp/screenshot", "live", 0.002),
    "keywords-for-site": ("labs", "POST", "/dataforseo_labs/google/keywords_for_site/live", "live", 0.012),
    "historical-serp": ("labs", "POST", "/dataforseo_labs/google/historical_serps/live", "live", 0.012),
    "domain-intersection": ("labs", "POST", "/dataforseo_labs/google/domain_intersection/live", "live", 0.012),
    "subdomains": ("labs", "POST", "/dataforseo_labs/google/subdomains/live", "live", 0.012),
    "ai-keyword-volume": ("ai_optimization", "POST",
                          "/ai_optimization/ai_keyword_data/keywords_search_volume/live", "live", 0.015),
    "llm-mentions-top-domains": ("ai_optimization", "POST", "/ai_optimization/llm_mentions/top_domains/live",
                                 "live", 0.02),
    "llm-mentions-top-pages": ("ai_optimization", "POST", "/ai_optimization/llm_mentions/top_pages/live",
                               "live", 0.02),
    "llm-mentions-aggregated": ("ai_optimization", "POST",
                                "/ai_optimization/llm_mentions/aggregated_metrics/live", "live", 0.02),
    "llm-responses-chatgpt": ("ai_optimization", "POST", "/ai_optimization/chat_gpt/llm_responses/live",
                              "live", 0.03),
    "llm-responses-claude": ("ai_optimization", "POST", "/ai_optimization/claude/llm_responses/live",
                             "live", 0.03),
    "llm-responses-gemini": ("ai_optimization", "POST", "/ai_optimization/gemini/llm_responses/live",
                             "live", 0.03),
    "llm-responses-perplexity": ("ai_optimization", "POST", "/ai_optimization/perplexity/llm_responses/live",
                                 "live", 0.03),
    "referring-domains": ("backlinks", "POST", "/backlinks/referring_domains/live", "live", 0.02),
    "anchors": ("backlinks", "POST", "/backlinks/anchors/live", "live", 0.02),
    "backlinks-list": ("backlinks", "POST", "/backlinks/backlinks/live", "live", 0.02),
    "content-parsing": ("on_page", "POST", "/on_page/content_parsing/live", "live", 0.005),
    "lighthouse": ("on_page", "POST", "/on_page/lighthouse/live/json", "live", 0.005),
}


def test_route_catalog_has_53_unique_routes_with_uniform_schema():
    routes = load_routes()["routes"]
    assert len(routes) == 53
    names = [r["name"] for r in routes]
    assert len(set(names)) == 53
    keys = {"name", "aliases", "kind", "method", "path", "family", "section", "mode", "charged", "summary",
            "estimate"}
    for route in routes:
        assert set(route) == keys, route["name"]
        assert route["mode"] in {"live", "standard_queue", "free"}
        assert (route["mode"] == "free") == (route["charged"] is False)


def test_new_v3_routes_match_catalog_spec():
    for name, (section, method, path, mode, usd) in NEW_ROUTES.items():
        route = route_for(name)
        assert (route["section"], route["method"], route["path"], route["mode"]) == (section, method, path, mode)
        assert estimate_cost(name)["estimated_cost_usd"] == usd, name
    assert estimate_cost("serp-task-post", depth=100)["estimated_cost_usd"] == 0.012
    assert route_for("serp")["section"] == "serp" and route_for("demand")["section"] == "labs"
    assert _known_free_get("/serp/google/organic/task_get/advanced/abc-123")


def test_cli_lists_and_previews_new_routes(capsys):
    assert cli.main(["routes"]) == 0
    assert len(json.loads(capsys.readouterr().out)["routes"]) == 53
    assert cli.main(["doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["routes"] == 53
    assert cli.main(["route", "llm-responses-claude"]) == 0
    assert json.loads(capsys.readouterr().out)["path"] == "/ai_optimization/claude/llm_responses/live"
    assert cli.main(["call", "/v3/on_page/lighthouse/live/json"]) == 0
    assert json.loads(capsys.readouterr().out)["preview"] is True
