"""Tests for /goto redirect-wrapper unmasking, SearchGuard poison detection, and Labs helpers."""

from __future__ import annotations

import io
import json
import pytest

from legends_dataforseo import (
    Credentials,
    RouteError,
    assess_serp_integrity,
    cli,
    client,
    competitors,
    extract_breadcrumb_target,
    is_goto_wrapped_url,
    normalize_organic_item,
    normalize_serp_envelope,
    ranked_keywords,
    relevant_pages,
    response_summary,
    serp,
)

AUTH = Credentials("fixture-login", "fixture-password")


def test_goto_url_detection_and_breadcrumb_extraction():
    assert is_goto_wrapped_url("https://google.com/goto?url=CAES1gEabc")
    assert is_goto_wrapped_url("https://www.google.com/url?q=https://example.com")
    assert not is_goto_wrapped_url("https://www.gattilaw.com/locations/")
    assert not is_goto_wrapped_url(None)

    t1 = extract_breadcrumb_target("https://www.gattilaw.com › Locations", "The Gatti Law Firm")
    assert t1["host"] == "www.gattilaw.com"
    assert t1["domain"] == "gattilaw.com"
    assert t1["path_hint"] == "Locations"
    assert t1["website_name"] == "The Gatti Law Firm"

    t2 = extract_breadcrumb_target("https://warrenallen.com › choosing-personal-injury-atto...")
    assert t2["host"] == "warrenallen.com"
    assert t2["domain"] == "warrenallen.com"
    assert t2["path_hint"] == "choosing-personal-injury-atto"

    t3 = extract_breadcrumb_target("https://attorneys.superlawyers.com › ... › Portland")
    assert t3["host"] == "attorneys.superlawyers.com"
    assert t3["domain"] == "attorneys.superlawyers.com"
    assert t3["path_hint"] == "Portland"


def test_normalize_organic_item_unmasks_goto_and_preserves_provenance():
    item = {
        "type": "organic",
        "rank_group": 1,
        "domain": "google.com",
        "url": "https://google.com/goto?url=CAES1gExyz",
        "breadcrumb": "https://www.gattilaw.com › Locations",
        "website_name": "The Gatti Law Firm",
        "title": "Portland Personal Injury Lawyers",
    }
    normalize_organic_item(item)
    assert item["goto_wrapped"] is True
    assert item["raw_domain"] == "google.com"
    assert item["raw_url"] == "https://google.com/goto?url=CAES1gExyz"
    assert item["domain"] == "www.gattilaw.com"
    assert item["resolved_domain"] == "gattilaw.com"
    assert item["breadcrumb_host"] == "www.gattilaw.com"
    assert item["breadcrumb_path_hint"] == "Locations"


def test_assess_serp_integrity_distinguishes_clean_and_poisoned():
    clean_block = {
        "keyword": "portland personal injury lawyer",
        "items": [
            {
                "type": "organic",
                "rank_group": 1,
                "domain": "google.com",
                "url": "https://google.com/goto?url=CAES1",
                "breadcrumb": "https://www.gattilaw.com › Locations",
                "title": "Portland Personal Injury Lawyers",
            },
            {
                "type": "organic",
                "rank_group": 2,
                "domain": "google.com",
                "url": "https://google.com/goto?url=CAES2",
                "breadcrumb": "https://warrenallen.com › choosing-personal-injury-atto...",
                "title": "Tips for Choosing a Personal Injury Attorney in Portland",
            },
        ],
    }
    clean_report = assess_serp_integrity(
        clean_block,
        task_data={"keyword": "portland personal injury lawyer", "location_name": "Portland,Oregon,United States"},
    )
    assert clean_report["status"] == "clean"
    assert clean_report["organic_count"] == 2
    assert clean_report["goto_wrapped_count"] == 2
    assert clean_report["resolved_domain_count"] == 2
    assert clean_report["canary_hits"] == []

    poisoned_block = {
        "keyword": "personal injury lawyer",
        "items": [
            {
                "type": "organic",
                "rank_group": 1,
                "domain": "google.com",
                "url": "https://google.com/goto?url=CAES_WIKI",
                "breadcrumb": "https://en.wikipedia.org › wiki › Personal_injury_lawyer",
                "title": "Personal injury lawyer - Wikipedia",
            },
            {
                "type": "organic",
                "rank_group": 2,
                "domain": "google.com",
                "url": "https://google.com/goto?url=CAES_BLS",
                "breadcrumb": "https://www.bls.gov › ooh › legal › lawyers",
                "title": "Lawyers : Occupational Outlook Handbook",
            },
        ],
    }
    poison_report = assess_serp_integrity(
        poisoned_block,
        task_data={"keyword": "personal injury lawyer", "location_coordinate": "45.5152,-122.6784,12z"},
    )
    assert poison_report["status"] == "suspect_poisoned"
    assert "en.wikipedia.org" in poison_report["canary_hits"]
    assert "bls.gov" in poison_report["canary_hits"]
    assert poison_report["used_location_coordinate"] is True
    assert len(poison_report["reasons"]) >= 2


def test_serp_auto_retries_poisoned_response_and_drops_coordinate(monkeypatch):
    poisoned_envelope = {
        "status_code": 20000,
        "cost": 0.002,
        "tasks": [
            {
                "id": "task-poisoned-1",
                "status_code": 20000,
                "cost": 0.002,
                "data": {"keyword": "personal injury lawyer", "location_coordinate": "45.5152,-122.6784,12z"},
                "result": [
                    {
                        "keyword": "personal injury lawyer",
                        "items": [
                            {
                                "type": "organic",
                                "rank_group": 1,
                                "domain": "google.com",
                                "url": "https://google.com/goto?url=CAES_WIKI",
                                "breadcrumb": "https://en.wikipedia.org › wiki › Personal_injury_lawyer",
                                "title": "Personal injury lawyer - Wikipedia",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    clean_envelope = {
        "status_code": 20000,
        "cost": 0.002,
        "tasks": [
            {
                "id": "task-clean-2",
                "status_code": 20000,
                "cost": 0.002,
                "data": {"keyword": "personal injury lawyer", "location_name": "Portland,Oregon,United States"},
                "result": [
                    {
                        "keyword": "personal injury lawyer",
                        "items": [
                            {
                                "type": "organic",
                                "rank_group": 1,
                                "domain": "google.com",
                                "url": "https://google.com/goto?url=CAES_GATTI",
                                "breadcrumb": "https://www.gattilaw.com › Locations",
                                "title": "Portland Personal Injury Lawyers",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    labs_envelope = {
        "status_code": 20000,
        "cost": 0.012,
        "tasks": [
            {
                "id": "task-labs-3",
                "status_code": 20000,
                "cost": 0.012,
                "result": [{"items": [{"domain": "gattilaw.com", "avg_position": 1.0}]}],
            }
        ],
    }

    responses = iter([poisoned_envelope, clean_envelope, labs_envelope])
    requests_sent = []

    def fake_open(req, timeout):
        requests_sent.append(json.loads(req.data.decode("utf-8")))
        return io.BytesIO(json.dumps(next(responses)).encode("utf-8"))

    monkeypatch.setattr(client, "_open", fake_open)
    final = serp(
        "personal injury lawyer",
        location_coordinate="45.5152,-122.6784,12z",
        location_name="Portland,Oregon,United States",
        max_poison_retries=2,
        labs_fallback=True,
        credentials=AUTH,
        confirm=True,
        estimated_cost_usd=0.02,
        max_cost_usd=0.05,
    )
    assert len(requests_sent) == 3
    assert "location_coordinate" in requests_sent[0][0]
    assert "location_coordinate" not in requests_sent[1][0]
    assert requests_sent[1][0]["location_name"] == "Portland,Oregon,United States"

    res_obj = final["tasks"][0]["result"][0]
    assert res_obj["items"][0]["domain"] == "www.gattilaw.com"
    assert res_obj["items"][0]["resolved_domain"] == "gattilaw.com"
    assert res_obj["serp_integrity"]["status"] == "clean"
    assert res_obj["serp_integrity"]["retry_attempts"] == 1
    assert res_obj["serp_integrity"]["attempt_task_ids"] == ["task-poisoned-1", "task-clean-2"]
    assert res_obj["labs_competitors"] == [{"domain": "gattilaw.com", "avg_position": 1.0}]

    summary = response_summary(final)
    assert summary["tasks"][0]["serp_integrity"]["status"] == "clean"


def test_labs_helpers_and_cli_previews(monkeypatch, capsys):
    calls = []

    def fake_open(req, timeout):
        calls.append((req.full_url, json.loads(req.data.decode("utf-8"))))
        return io.BytesIO(b'{"status_code": 20000}')

    monkeypatch.setattr(client, "_open", fake_open)
    opts = dict(credentials=AUTH, confirm=True, estimated_cost_usd=0.02, max_cost_usd=0.02)
    competitors(["lawyer portland"], limit=15, **opts)
    ranked_keywords("https://www.gattilaw.com/locations/", limit=25, **opts)
    relevant_pages("warrenallen.com", limit=10, **opts)

    assert calls[0][0].endswith("/dataforseo_labs/google/serp_competitors/live")
    assert calls[0][1][0]["limit"] == 15
    assert calls[1][0].endswith("/dataforseo_labs/google/ranked_keywords/live")
    assert calls[1][1][0]["target"] == "gattilaw.com"
    assert calls[2][0].endswith("/dataforseo_labs/google/relevant_pages/live")
    assert calls[2][1][0]["target"] == "warrenallen.com"

    with pytest.raises(RouteError):
        ranked_keywords("invalid-domain", **opts)

    assert cli.main(["competitors", "lawyer portland", "--item-limit", "10"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["preview"] is True
    assert preview["body"][0]["limit"] == 10

    assert cli.main(["ranked-keywords", "https://www.gattilaw.com"]) == 0
    rk_preview = json.loads(capsys.readouterr().out)
    assert rk_preview["body"][0]["target"] == "gattilaw.com"


def test_serp_enforces_cumulative_max_cost_across_retries_and_labs_fallback(monkeypatch):
    from legends_dataforseo import CostLimitError

    poisoned_envelope = {
        "status_code": 20000,
        "cost": 0.002,
        "tasks": [
            {
                "id": "task-poisoned-1",
                "status_code": 20000,
                "cost": 0.002,
                "data": {"keyword": "personal injury lawyer", "location_coordinate": "45.5152,-122.6784,12z"},
                "result": [
                    {
                        "keyword": "personal injury lawyer",
                        "items": [
                            {
                                "type": "organic",
                                "rank_group": 1,
                                "domain": "google.com",
                                "url": "https://google.com/goto?url=CAES_WIKI",
                                "breadcrumb": "https://en.wikipedia.org › wiki › Personal_injury_lawyer",
                                "title": "Personal injury lawyer - Wikipedia",
                            }
                        ],
                    }
                ],
            }
        ],
    }

    calls = []

    def fake_open(req, timeout):
        calls.append(json.loads(req.data.decode("utf-8")))
        return io.BytesIO(json.dumps(poisoned_envelope).encode("utf-8"))

    monkeypatch.setattr(client, "_open", fake_open)
    capped_res = serp(
        "personal injury lawyer",
        location_coordinate="45.5152,-122.6784,12z",
        max_poison_retries=2,
        credentials=AUTH,
        confirm=True,
        estimated_cost_usd=0.002,
        max_cost_usd=0.002,
    )
    assert len(calls) == 1
    assert capped_res["tasks"][0]["result"][0]["serp_integrity"]["budget_exhausted"] is True
    assert capped_res["tasks"][0]["result"][0]["serp_integrity"]["retry_attempts"] == 0

    calls.clear()
    with pytest.raises(CostLimitError) as exc_info:
        serp(
            "personal injury lawyer",
            max_poison_retries=0,
            labs_fallback=True,
            credentials=AUTH,
            confirm=True,
            estimated_cost_usd=0.002,
            max_cost_usd=0.010,
        )
    assert len(calls) == 0
    assert exc_info.value.request_may_have_completed is False
