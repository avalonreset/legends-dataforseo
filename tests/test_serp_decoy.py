"""0.1.12 fail-closed decoy SERP defense.

Mandatory resolve_poisoned_results, LEGENDS_DFS_ALLOW_POISONED escape hatch, decoy detector
(AIO backstop, pull instability, incumbents; no age or traffic signals), re-pull, verification
pull, serp_integrity.verified stamping, UnverifiedSerpError, 40101 retry, per-keyword lock.
"""

from __future__ import annotations

import io
import json

import pytest

from legends_dataforseo import (
    ALLOW_POISONED_ENV,
    DECOY_THRESHOLDS,
    SERP_TIMEOUT_S,
    CostLimitError,
    Credentials,
    RouteError,
    UnverifiedSerpError,
    WEBSITE_NAME_PLATFORMS,
    allow_poisoned,
    api_request,
    apply_decoy_assessment,
    assess_decoy_signals,
    assess_serp_integrity,
    cli,
    client,
    extract_breadcrumb_target,
    normalize_organic_item,
    organic_item_site_key,
    organic_site_keys,
    overlap_ratio,
    resolve_poisoned_default,
    serp,
    site_key,
    stamp_verified,
)

AUTH = Credentials("fixture-login", "fixture-password")
NOW = "2026-11-20 12:00:00 +00:00"
ORGANIC_LIVE = "/v3/serp/google/organic/live/advanced"
ORGANIC_TASK_POST = "/v3/serp/google/organic/task_post"


def organic(rank, domain, *, timestamp=None, checks=None, title="Result"):
    item = {"type": "organic", "rank_group": rank, "rank_absolute": rank, "domain": domain,
            "url": f"https://{domain}/page-{rank}", "title": title}
    if timestamp:
        item["timestamp"] = timestamp
    if checks is not None:
        item["checks"] = checks
    return item


def aio(*domains):
    return {"type": "ai_overview", "items": [{"type": "ai_overview_element",
            "references": [{"domain": d, "url": f"https://{d}/x"} for d in domains]}]}


REAL = ["fractionalcto.com", "cto-legends.com", "toptal.com", "forbes.com", "indeed.com",
        "reddit.com", "linkedin.com", "chiefgroup.com", "graphite.com", "hbr.org"]
FILLER = ["old-blog-a.net", "dusty-dir.org", "filler-b.info", "parked-c.biz", "stale-d.net",
          "archive-e.org", "seo-f.net", "random-g.com", "ghost-h.net", "lost-i.org"]
OLD = "2019-01-01 00:00:00 +00:00"


def block(domains, *, ages=None, aio_domains=None, checks=None, keyword="fractional cto"):
    items = []
    if aio_domains:
        items.append(aio(*aio_domains))
    for idx, dom in enumerate(domains, start=1):
        ts = ages[idx - 1] if ages else None
        items.append(organic(idx, dom, timestamp=ts, checks=checks))
    return {"keyword": keyword, "datetime": NOW, "items": items}


def envelope(res_block, *, task_id="t-1", cost=0.004, data=None, status_code=20000):
    task = {"id": task_id, "status_code": status_code, "cost": cost,
            "data": data or {"keyword": res_block["keyword"] if res_block else "fractional cto"}}
    task["result"] = [res_block] if res_block is not None else None
    return {"status_code": 20000, "cost": cost, "tasks": [task]}


RESOLVE_DATA = {"keyword": "fractional cto", "resolve_poisoned_results": True}


def renv(domains, *, checks=None, task_id="t-1", aio_domains=None, cost=0.004):
    return envelope(block(domains, checks=checks, aio_domains=aio_domains), task_id=task_id,
                    data=dict(RESOLVE_DATA), cost=cost)


def transient(task_id="t-err"):
    return envelope(None, task_id=task_id, cost=0, data=dict(RESOLVE_DATA), status_code=40101)


def fake_transport(monkeypatch, responses):
    sent = []
    queue = list(responses)

    def fake_open(req, timeout):
        sent.append((req.full_url, json.loads(req.data.decode("utf-8")) if req.data else None, timeout))
        return io.BytesIO(json.dumps(queue.pop(0)).encode("utf-8"))

    monkeypatch.setattr(client, "_open", fake_open)
    return sent


def _integrity(result):
    return result["tasks"][0]["result"][0]["serp_integrity"]


@pytest.fixture(autouse=True)
def _protected_env(monkeypatch):
    monkeypatch.delenv(ALLOW_POISONED_ENV, raising=False)
    monkeypatch.delenv("LEGENDS_DATAFORSEO_RESOLVE_POISONED", raising=False)
    monkeypatch.setattr(client, "_sleep", lambda s: None)


# --- offline detector -------------------------------------------------------------------------

def test_site_key_and_overlap_helpers():
    assert site_key("https://www.blog.example.co.uk/path") == "example.co.uk"
    assert site_key("https://www.heartfoundation.org.nz/health") == "heartfoundation.org.nz"
    assert site_key("WWW.Sub.Example.com") == "example.com"
    assert site_key("") is None and site_key(None) is None
    assert overlap_ratio(["a.com", "b.com"], ["b.com"]) == 0.5
    assert overlap_ratio([], ["a.com"]) is None
    assert organic_site_keys(block(REAL), limit=3) == ["fractionalcto.com", "cto-legends.com", "toptal.com"]


def test_no_evidence_is_not_scored_as_clean_evidence():
    decoy = assess_decoy_signals(block(REAL))
    assert decoy["suspect"] is False
    assert decoy["signals"] == {} and decoy["signals_evaluated"] == 0
    assert decoy["provider_resolve"] == "not_requested"


def test_age_and_traffic_are_not_signals():
    integrity = assess_serp_integrity(block(FILLER, ages=[OLD] * 10), task_data={"keyword": "fractional cto"})
    assert integrity["status"] == "clean"
    assert "stale_results" not in integrity["decoy"]["signals"]
    assert set(DECOY_THRESHOLDS) == {"aio_min_reference_sites", "aio_overlap_max", "pull_overlap_max",
                                     "incumbent_overlap_max", "min_signals"}
    with pytest.raises(TypeError):
        assess_decoy_signals(block(FILLER), site_traffic={k: 3.0 for k in FILLER})


def test_ai_overview_disjoint_from_organics_fires():
    integrity = assess_serp_integrity(block(FILLER, aio_domains=REAL[:5]))
    assert integrity["status"] == "suspect_decoy"
    sig = integrity["decoy"]["signals"]["aio_organic_disjoint"]
    assert sig["overlap"] == 0.0 and sig["threshold"] == 0.6
    healthy = assess_serp_integrity(block(REAL, aio_domains=REAL[:5]))
    assert healthy["status"] == "clean"
    assert healthy["decoy"]["signals"]["aio_organic_disjoint"]["overlap"] == 1.0


def test_aio_backstop_threshold_is_flag_happy_at_0_6():
    cited = REAL[:3] + FILLER[:2]  # 3/5 cited sites also rank -> 0.6 -> flags
    at = assess_decoy_signals(block(REAL, aio_domains=cited))
    assert at["signals"]["aio_organic_disjoint"]["overlap"] == 0.6 and at["suspect"] is True
    above = assess_decoy_signals(block(REAL, aio_domains=REAL[:4] + FILLER[:1]))
    assert above["signals"]["aio_organic_disjoint"]["overlap"] == 0.8 and above["suspect"] is False


def test_aio_backstop_needs_three_cited_sites():
    few = assess_decoy_signals(block(FILLER, aio_domains=REAL[:2]))
    assert "aio_organic_disjoint" not in few["signals"] and few["suspect"] is False


def test_aio_backstop_matches_brand_slugs_from_goto_items():
    reddit = {"type": "organic", "domain": "google.com", "url": "https://google.com/goto?url=CAEX",
              "breadcrumb": "50+ comments", "website_name": "Reddit · r/cto"}
    res = {"keyword": "fractional cto", "items": [aio("reddit.com", "toptal.com", "forbes.com"), reddit,
                                                   organic(2, "toptal.com"), organic(3, "forbes.com")]}
    integrity = assess_serp_integrity(res)
    assert integrity["decoy"]["signals"]["aio_organic_disjoint"]["overlap"] == 1.0
    assert integrity["status"] == "clean"


def test_poisoned_keeps_precedence_over_decoy():
    res = block(["en.wikipedia.org"] + FILLER[:9], aio_domains=REAL[:5], keyword="plumber norristown")
    integrity = assess_serp_integrity(res, task_data={"keyword": "plumber norristown"})
    assert integrity["status"] == "suspect_poisoned"
    assert integrity["decoy"]["suspect"] is True


def test_provider_resolve_states():
    swapped = assess_decoy_signals(block(REAL, checks=["from_web"]),
                                   task_data={"resolve_poisoned_results": True})
    assert swapped["provider_resolve"] == "resolved" and swapped["from_web_count"] == 10
    silent = assess_decoy_signals(block(REAL), task_data={"resolve_poisoned_results": True})
    assert silent["provider_resolve"] == "requested_no_swap"


def test_caller_evidence_second_pull_and_incumbents():
    res = block(FILLER)
    d1 = assess_decoy_signals(res, second_pull_sites=REAL)
    assert d1["signals"]["pull_instability"]["fired"] is True
    d2 = assess_decoy_signals(res, incumbent_sites=["https://www.fractionalcto.com", "toptal.com", "forbes.com"])
    assert d2["signals"]["incumbent_mismatch"]["overlap"] == 0.0 and d2["suspect"] is True


def test_apply_decoy_assessment_redecides_without_stale_reasons():
    integrity = assess_serp_integrity(block(FILLER, aio_domains=REAL[:5]))
    assert integrity["status"] == "suspect_decoy"
    apply_decoy_assessment(integrity, assess_decoy_signals(block(REAL, aio_domains=REAL[:5])))
    assert integrity["status"] == "clean"
    assert integrity["reasons"] == []


def test_goto_items_key_by_breadcrumb_then_website_name():
    wrapped = {"type": "organic", "domain": "google.com", "url": "https://google.com/goto?url=CAES",
               "breadcrumb": "https://www.rotorooter.com › philadelphia", "website_name": "Roto-Rooter"}
    name_only = {"type": "organic", "domain": "google.com", "url": "https://google.com/goto?url=CAEX",
                 "breadcrumb": "50+ comments · 9 months ago", "website_name": "Reddit · r/philadelphia"}
    bare = {"type": "organic", "domain": "google.com", "url": "https://google.com/goto?url=CAEZ"}
    assert organic_item_site_key(wrapped) == "rotorooter.com"
    assert organic_item_site_key(name_only) == "reddit.com"  # closed website_name platform allowlist
    assert organic_item_site_key(bare) is None
    res_block = {"keyword": "plumber", "items": [wrapped, name_only, bare]}
    assess_serp_integrity(res_block)
    assert organic_site_keys(res_block) == ["rotorooter.com", "reddit.com"]


# --- verified stamping ------------------------------------------------------------------------

def _clean(provider):
    return {"status": "clean", "organic_count": 10,
            "decoy": {"suspect": False, "fired": [], "provider_resolve": provider,
                      "signals": {"aio_organic_disjoint": {"fired": False}}}}


def test_stamp_verified_requires_independent_consensus():
    # Provider-only signals (from_web > 0, from_web == 0, double_pull_stable) are NEVER sufficient alone.
    for vstatus in (None, "not_run", "not_needed", "skipped_disabled", "stable", "resolved_on_verify"):
        for prov in ("resolved", "requested_no_swap"):
            rejected = stamp_verified(_clean(prov), verification={"status": vstatus})
            assert rejected["verified"] is False and rejected["unverified"] is True, (prov, vstatus)
            assert rejected["verified_basis"] is None

    # Independent consensus grants verified=True.
    cons = stamp_verified(
        _clean("resolved"),
        verification={"status": "independent_consensus", "consensus_passed": True},
    )
    assert cons["verified"] is True and cons["verified_basis"] == "independent_consensus"
    assert cons["integrity"] == "protected" and "unverified" not in cons
    assert cons["signals_used"] == ["aio_organic_disjoint"]

    repaired = stamp_verified(
        _clean("resolved"),
        verification={"status": "repaired_on_repull", "consensus_passed": True},
    )
    assert repaired["verified"] is True and repaired["verified_basis"] == "independent_consensus_repaired"

    repaired_ind = stamp_verified(
        _clean("requested_no_swap"),
        verification={"status": "repaired_from_independent", "consensus_passed": True},
    )
    assert repaired_ind["verified"] is True and repaired_ind["verified_basis"] == "independent_consensus_repaired"

    for vstatus in ("unstable", "verify_pull_suspect", "disagreed", "consensus_disagreed",
                    "failed", "verify_failed", "skipped_budget", "independent_unavailable"):
        rejected = stamp_verified(_clean("requested_no_swap"), verification={"status": vstatus})
        assert rejected["verified"] is False and rejected["unverified"] is True, vstatus
        assert rejected["verified_basis"] is None
    budget = stamp_verified(_clean("requested_no_swap"), verification={
        "status": "failed", "reason": "max_cost_usd_insufficient_for_second_pull"})
    assert any("max_cost_usd_insufficient_for_second_pull" in r for r in budget["unverified_reasons"])
    fired = _clean("resolved")
    fired["decoy"]["fired"] = ["aio_organic_disjoint"]
    assert stamp_verified(
        fired, verification={"status": "independent_consensus", "consensus_passed": True}
    )["verified"] is False
    unprotected = stamp_verified(
        _clean("resolved"),
        verification={"status": "independent_consensus", "consensus_passed": True},
        unprotected=True,
    )
    assert unprotected["verified"] is False and unprotected["integrity"] == "unprotected"
    disabled = stamp_verified(_clean("not_requested"), unprotected=True)
    assert disabled["verified"] is False
    decoy = stamp_verified({"status": "unverified_decoy", "decoy": {"suspect": True, "fired": ["pull_instability"],
                                                                     "provider_resolve": "resolved"}})
    assert decoy["verified"] is False and decoy["verified_basis"] is None


def test_resolve_is_mandatory_and_escape_env_is_exact(monkeypatch):
    monkeypatch.setenv("LEGENDS_DATAFORSEO_RESOLVE_POISONED", "0")  # retired opt-out is ignored
    assert resolve_poisoned_default() is True
    assert allow_poisoned() is False
    for value in ("true", "yes", "0", ""):
        monkeypatch.setenv(ALLOW_POISONED_ENV, value)
        assert allow_poisoned() is False
    monkeypatch.setenv(ALLOW_POISONED_ENV, "1")
    assert allow_poisoned() is True


# --- raw api_request --------------------------------------------------------------------------

def test_api_request_injects_resolve_on_every_organic_task(monkeypatch):
    sent = fake_transport(monkeypatch, [renv(REAL), renv(REAL, checks=["from_web"]),
                                        {"status_code": 20000, "tasks": []}])
    body = [{"keyword": "fractional cto", "location_code": 2840, "language_code": "en"}]
    raw = api_request(ORGANIC_LIVE, body, credentials=AUTH, confirm=True)
    assert sent[0][1][0]["resolve_poisoned_results"] is True and "resolve_poisoned_results" not in body[0]
    assert "resolve_goto_urls" not in sent[0][1][0]  # raw calls never pay redirect latency implicitly
    assert sent[0][2] == SERP_TIMEOUT_S == 300.0
    # Provider-only raw api_request cannot self-verify without independent consensus (Truth Gate).
    assert _integrity(raw)["verified"] is False
    assert _integrity(raw)["verified_basis"] is None
    assert _integrity(raw)["integrity"] == "protected"
    swapped = api_request(ORGANIC_LIVE, body, credentials=AUTH, confirm=True)
    assert sent[1][1][0]["resolve_poisoned_results"] is True
    assert _integrity(swapped)["verified"] is False
    assert _integrity(swapped)["verified_basis"] is None
    api_request("/v3/serp/google/maps/live/advanced", body, credentials=AUTH, confirm=True)
    assert "resolve_poisoned_results" not in sent[2][1][0]


def test_api_request_refuses_unprotected_organic_tasks(monkeypatch):
    sent = fake_transport(monkeypatch, [envelope(block(REAL)), envelope(block(REAL))])
    off = [{"keyword": "x", "location_code": 2840, "resolve_poisoned_results": False}]
    web = [{"keyword": "x", "location_code": 2840, "search_param": "udm=14"}]
    for payload in (off, web):
        with pytest.raises(RouteError):
            api_request(ORGANIC_LIVE, payload, credentials=AUTH, confirm=True)
    assert sent == []
    monkeypatch.setenv(ALLOW_POISONED_ENV, "1")
    for payload in (off, web):
        result = api_request(ORGANIC_LIVE, payload, credentials=AUTH, confirm=True)
        assert _integrity(result)["integrity"] == "unprotected" and _integrity(result)["verified"] is False
    assert sent[0][1][0]["resolve_poisoned_results"] is False
    assert "resolve_poisoned_results" not in sent[1][1][0]


# --- serp() pipeline --------------------------------------------------------------------------

def test_serp_sends_resolve_and_verifies_with_stable_pull(monkeypatch):
    sent = fake_transport(monkeypatch, [renv(REAL), renv(REAL, task_id="t-2")])
    result = serp("fractional cto", verify_no_swap=True, independent_sites=REAL, credentials=AUTH, confirm=True,
                  estimated_cost_usd=0.004, max_cost_usd=0.02)
    assert len(sent) == 2 and all(body[0]["resolve_poisoned_results"] is True for _, body, _ in sent)
    integrity = _integrity(result)
    assert integrity["status"] == "clean" and integrity["verification"]["status"] == "independent_consensus"
    assert integrity["verification"]["second_pull"]["status"] == "stable"
    assert integrity["verified"] is True and integrity["verified_basis"] == "independent_consensus"
    assert "unverified" not in integrity and "unresolved" not in integrity
    meta = result["legends_serp"]
    assert meta["verified"] is True and meta["integrity"] == "protected"
    assert meta["resolve_poisoned_results_default"] is True
    assert [a["reason"] for a in meta["attempts"]] == ["initial", "verify_no_swap"]
    assert meta["attempts"][0]["latency_ms"] >= 0
    assert meta["cost_usd_total"] == 0.008


def test_serp_provider_swaps_verify_in_one_pull_with_independent_consensus(monkeypatch):
    sent = fake_transport(monkeypatch, [renv(REAL, checks=["from_web"])])
    result = serp("fractional cto", independent_sites=REAL, credentials=AUTH, confirm=True,
                  estimated_cost_usd=0.004, max_cost_usd=0.02)
    assert len(sent) == 1
    assert _integrity(result)["verified_basis"] == "independent_consensus"
    assert result["legends_serp"]["verified"] is True


def test_serp_refuses_resolve_off_without_escape(monkeypatch):
    sent = fake_transport(monkeypatch, [])
    with pytest.raises(ValueError, match=ALLOW_POISONED_ENV):
        serp("fractional cto", resolve_poisoned_results=False, credentials=AUTH, confirm=True)
    for bad in ("yes", 1):
        with pytest.raises(ValueError):
            serp("x", resolve_poisoned_results=bad, credentials=AUTH, confirm=True)
    with pytest.raises(ValueError):
        serp("x", allow_unverified="yes", credentials=AUTH, confirm=True)
    assert sent == []


def test_escape_hatch_returns_stamped_unprotected(monkeypatch):
    monkeypatch.setenv(ALLOW_POISONED_ENV, "1")
    sent = fake_transport(monkeypatch, [envelope(block(REAL)), renv(REAL, checks=["from_web"])])
    off = serp("fractional cto", resolve_poisoned_results=False, credentials=AUTH, confirm=True,
               estimated_cost_usd=0.002, max_cost_usd=0.002)
    assert sent[0][1][0]["resolve_poisoned_results"] is False
    assert _integrity(off)["integrity"] == "unprotected" and _integrity(off)["verified"] is False
    assert _integrity(off)["verification"]["status"] == "resolve_off"
    assert off["legends_serp"]["integrity"] == "unprotected" and off["legends_serp"]["verified"] is False
    # The env alone never turns resolve off.
    serp("fractional cto", independent_sites=REAL, credentials=AUTH, confirm=True,
         estimated_cost_usd=0.004, max_cost_usd=0.02)
    assert sent[1][1][0]["resolve_poisoned_results"] is True


def test_serp_refuses_cap_that_cannot_cover_resolve_surcharge(monkeypatch):
    sent = fake_transport(monkeypatch, [])
    with pytest.raises(CostLimitError):
        serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.002, max_cost_usd=0.002)
    assert sent == []


def test_suspect_decoy_is_repulled_and_verified(monkeypatch):
    decoy_env = renv(FILLER, aio_domains=REAL[:5], task_id="t-decoy", cost=0.002)
    fixed_env = renv(REAL, checks=["from_web"], task_id="t-fixed")
    sent = fake_transport(monkeypatch, [decoy_env, fixed_env])
    result = serp("fractional cto", independent_sites=REAL, credentials=AUTH, confirm=True,
                  estimated_cost_usd=0.004, max_cost_usd=0.02)
    assert all(body[0]["resolve_poisoned_results"] is True for _, body, _ in sent)
    integrity = _integrity(result)
    assert integrity["status"] == "clean" and integrity["verified"] is True
    assert integrity["verified_basis"] == "independent_consensus_repaired"
    assert integrity["decoy"]["provider_resolve"] == "resolved"
    assert integrity["attempt_task_ids"] == ["t-decoy", "t-fixed"]
    assert integrity["repull_top10_overlap"] == 0.0
    meta = result["legends_serp"]
    assert [a["reason"] for a in meta["attempts"]] == ["initial", "suspect_decoy"]
    assert [a["status"] for a in meta["attempts"]] == ["suspect_decoy", "clean"]
    assert meta["cost_usd_total"] == 0.006


def test_unresolved_decoy_fails_closed(monkeypatch):
    stuck = renv(FILLER, aio_domains=REAL[:5])
    fake_transport(monkeypatch, [stuck, stuck, stuck])
    with pytest.raises(UnverifiedSerpError) as caught:
        serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.05)
    assert caught.value.request_may_have_completed is True
    result = caught.value.response
    integrity = _integrity(result)
    assert integrity["status"] == "unverified_decoy" and integrity["detected_status"] == "suspect_decoy"
    assert integrity["unresolved"] is True and integrity["verified"] is False
    assert integrity["retry_attempts"] == 2
    assert result["legends_serp"]["final_status"] == "unverified_decoy"
    assert result["legends_serp"]["verified"] is False


def test_allow_unverified_returns_the_stamped_decoy(monkeypatch):
    stuck = renv(FILLER, aio_domains=REAL[:5])
    fake_transport(monkeypatch, [stuck, stuck, stuck])
    result = serp("fractional cto", allow_unverified=True, credentials=AUTH, confirm=True,
                  estimated_cost_usd=0.004, max_cost_usd=0.05)
    assert _integrity(result)["status"] == "unverified_decoy" and _integrity(result)["verified"] is False


def test_decoy_repull_respects_cumulative_cap(monkeypatch):
    stuck = renv(FILLER, aio_domains=REAL[:5])
    sent = fake_transport(monkeypatch, [stuck, stuck])
    with pytest.raises(UnverifiedSerpError) as caught:
        serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.006)
    assert len(sent) == 1
    integrity = _integrity(caught.value.response)
    assert integrity["budget_exhausted"] is True and integrity["unresolved"] is True


def test_incumbent_sites_flag_a_surface_clean_decoy(monkeypatch):
    fake_transport(monkeypatch, [renv(FILLER, checks=["from_web"])])
    result = serp("fractional cto", max_poison_retries=0, incumbent_sites=REAL[:5], allow_unverified=True,
                  credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.01)
    integrity = _integrity(result)
    assert integrity["status"] == "unverified_decoy" and integrity["unresolved"] is True
    assert result["legends_serp"]["attempts"][-1]["status"] == "suspect_decoy"  # per-attempt detection


def test_verification_pull_with_swaps_is_adopted(monkeypatch):
    fake_transport(monkeypatch, [renv(FILLER), renv(REAL, checks=["from_web"], task_id="t-2")])
    result = serp("fractional cto", independent_sites=REAL, credentials=AUTH, confirm=True,
                  estimated_cost_usd=0.004, max_cost_usd=0.02)
    integrity = _integrity(result)
    assert integrity["status"] == "clean" and integrity["decoy"]["provider_resolve"] == "resolved"
    assert integrity["verified"] is True
    assert integrity["verified_basis"] == "independent_consensus_repaired"
    assert result["legends_serp"]["verification"]["status"] == "repaired_on_repull"
    assert organic_site_keys(result["tasks"][0]["result"][0])[0] == REAL[0]


def test_unstable_no_swap_pulls_fail_closed(monkeypatch):
    fake_transport(monkeypatch, [renv(FILLER), renv(REAL, task_id="t-2")])
    with pytest.raises(UnverifiedSerpError) as caught:
        serp("fractional cto", verify_no_swap=True, max_poison_retries=0, consensus_provider=None,
             credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.02)
    integrity = _integrity(caught.value.response)
    assert integrity["status"] == "unverified_decoy" and integrity["unresolved"] is True
    assert integrity["verification"]["status"] == "unstable"
    assert "pull_instability" in integrity["decoy"]["fired"]


def test_verify_no_swap_budget_shortfall_fails_closed(monkeypatch):
    sent = fake_transport(monkeypatch, [renv(REAL), renv(REAL)])
    with pytest.raises(UnverifiedSerpError) as budget:
        serp("fractional cto", verify_no_swap=True, independent_sites=REAL, credentials=AUTH, confirm=True,
             estimated_cost_usd=0.004, max_cost_usd=0.005)
    assert len(sent) == 1
    integrity = _integrity(budget.value.response)
    assert integrity["status"] == "clean" and integrity["verified"] is False
    assert integrity["verification"]["status"] == "failed"
    assert integrity["verification"]["reason"] == "max_cost_usd_insufficient_for_second_pull"
    assert budget.value.response["legends_serp"]["budget_exhausted"] is True
    with pytest.raises(ValueError):
        serp("x", verify_no_swap="yes", credentials=AUTH, confirm=True)


def test_default_single_pull_requires_independent_consensus(monkeypatch):
    sent = fake_transport(monkeypatch, [renv(REAL), renv(REAL)])
    # Without independent view (missing FIRECRAWL_API_KEY), fails closed instead of self-verifying.
    with pytest.raises(UnverifiedSerpError) as missing:
        serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.004)
    assert _integrity(missing.value.response)["verified"] is False
    assert _integrity(missing.value.response)["verification"]["status"] == "independent_unavailable"
    # With independent consensus supplied, verifies cleanly in one DFS pull.
    result = serp("fractional cto", independent_sites=REAL, credentials=AUTH, confirm=True,
                  estimated_cost_usd=0.004, max_cost_usd=0.004)
    assert len(sent) == 2
    integrity = _integrity(result)
    assert integrity["verified"] is True and integrity["verified_basis"] == "independent_consensus"
    assert result["legends_serp"]["verified"] is True
    assert [a["reason"] for a in result["legends_serp"]["attempts"]] == ["initial"]


def test_default_single_pull_still_fails_closed_on_decoy_signal(monkeypatch):
    stuck = renv(FILLER, aio_domains=REAL[:5])
    sent = fake_transport(monkeypatch, [stuck])
    with pytest.raises(UnverifiedSerpError):
        serp("fractional cto", max_poison_retries=0, independent_sites=REAL, credentials=AUTH, confirm=True,
             estimated_cost_usd=0.004, max_cost_usd=0.004)
    assert len(sent) == 1


def test_resolve_goto_urls_defaults_by_depth(monkeypatch):
    sent = fake_transport(monkeypatch, [renv(REAL)] * 4)
    opts = dict(credentials=AUTH, confirm=True, independent_sites=REAL)
    serp("fractional cto", **opts)
    assert sent[-1][1][0]["resolve_goto_urls"] is True and sent[-1][1][0]["depth"] == 10
    serp("fractional cto", depth=100, **opts)
    assert "resolve_goto_urls" not in sent[-1][1][0]
    serp("fractional cto", depth=100, resolve_goto_urls=True, **opts)
    assert sent[-1][1][0]["resolve_goto_urls"] is True
    serp("fractional cto", resolve_goto_urls=False, **opts)
    assert "resolve_goto_urls" not in sent[-1][1][0]
    assert all(body[0]["resolve_poisoned_results"] is True for _, body, _ in sent)
    with pytest.raises(ValueError):
        serp("x", resolve_goto_urls="yes", **opts)


def test_transient_40101_is_retried_with_backoff(monkeypatch):
    slept = []
    monkeypatch.setattr(client, "_sleep", slept.append)
    sent = fake_transport(monkeypatch, [transient(), renv(REAL, checks=["from_web"], task_id="t-ok")])
    result = serp("fractional cto", independent_sites=REAL, credentials=AUTH, confirm=True,
                  estimated_cost_usd=0.004, max_cost_usd=0.02)
    assert len(sent) == 2 and slept == [2.0]
    attempts = result["legends_serp"]["attempts"]
    assert [a["reason"] for a in attempts] == ["initial", "initial:transient_retry_1"]
    assert [a["task_status_codes"] for a in attempts] == [[40101], [20000]]
    assert result["legends_serp"]["verified"] is True


def test_persistent_40101_fails_closed(monkeypatch):
    slept = []
    monkeypatch.setattr(client, "_sleep", slept.append)
    sent = fake_transport(monkeypatch, [transient()] * 4)
    with pytest.raises(UnverifiedSerpError) as caught:
        serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.02)
    assert len(sent) == 4 and slept == [2.0, 5.0, 10.0]
    assert caught.value.response["legends_serp"]["final_status"] == "no_organic_result"


def test_same_keyword_calls_share_one_lock():
    assert client._serp_key_lock("Fractional  CTO") is client._serp_key_lock("fractional cto")
    assert client._serp_key_lock("fractional cto") is not client._serp_key_lock("plumber")


# --- CLI ------------------------------------------------------------------------------------------

def test_cli_preview_always_sends_resolve(monkeypatch, capsys):
    assert cli.main(["serp", "fractional cto"]) == 0
    body = json.loads(capsys.readouterr().out)["body"][0]
    assert body["resolve_poisoned_results"] is True and body["resolve_goto_urls"] is True
    assert cli.main(["serp", "fractional cto", "--depth", "100"]) == 0
    assert "resolve_goto_urls" not in json.loads(capsys.readouterr().out)["body"][0]
    assert cli.main(["serp", "fractional cto", "--depth", "100", "--resolve-goto-urls"]) == 0
    assert json.loads(capsys.readouterr().out)["body"][0]["resolve_goto_urls"] is True
    assert cli.main(["serp", "fractional cto", "--no-resolve-goto-urls"]) == 0
    assert "resolve_goto_urls" not in json.loads(capsys.readouterr().out)["body"][0]
    assert cli.main(["serp", "fractional cto", "--no-resolve-poisoned"]) == 2
    assert ALLOW_POISONED_ENV in capsys.readouterr().err
    monkeypatch.setenv(ALLOW_POISONED_ENV, "1")
    assert cli.main(["serp", "fractional cto", "--no-resolve-poisoned"]) == 0
    assert json.loads(capsys.readouterr().out)["body"][0]["resolve_poisoned_results"] is False


def test_cli_execute_fails_closed_unless_allow_unverified(monkeypatch, capsys):
    from legends_dataforseo import truth_gate

    monkeypatch.setenv("DATAFORSEO_LOGIN", "fixture-login")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "fixture-secret")
    monkeypatch.setattr(
        truth_gate,
        "firecrawl_search_view",
        lambda kw, **kw_args: {
            "provider": "firecrawl_search", "ok": True, "sites": list(REAL),
            "items": [], "latency_ms": 12.0, "credits_estimated": 2, "error": None,
        },
    )
    sent = fake_transport(monkeypatch, [renv(REAL), renv(REAL), renv(REAL), renv(REAL, task_id="t-4")])
    argv = ["serp", "fractional cto", "--execute", "--estimated-cost-usd", "0.004", "--max-cost-usd", "0.004"]
    assert cli.main(argv) == 0  # independent consensus passes in one DFS pull
    out = json.loads(capsys.readouterr().out)
    assert out["legends_serp"]["verified"] is True and len(sent) == 1
    assert out["legends_serp"]["verified_basis"] == "independent_consensus"
    # --verify-no-swap with a cap that cannot cover the second pull fails closed
    assert cli.main(argv + ["--verify-no-swap"]) == 2
    assert "UnverifiedSerpError" in capsys.readouterr().err
    assert cli.main(argv + ["--verify-no-swap", "--allow-unverified"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["legends_serp"]["verified"] is False
    assert out["legends_serp"]["verification"]["status"] == "failed"
    wide = ["serp", "fractional cto", "--execute", "--estimated-cost-usd", "0.022", "--max-cost-usd", "0.06",
            "--depth", "100", "--verify-no-swap"]
    sent.clear()
    deep = fake_transport(monkeypatch, [renv(REAL), renv(REAL, task_id="t-5")])
    assert cli.main(wide) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["legends_serp"]["verification"]["status"] == "independent_consensus"
    assert out["legends_serp"]["verification"]["second_pull"]["status"] == "stable"
    assert len(deep) == 2 and all("resolve_goto_urls" not in body[0] for _, body, _ in deep)


# --- website_name platform mapping ------------------------------------------------------------

def test_engagement_breadcrumb_maps_allowlisted_website_name():
    # L08 rank 3: breadcrumb is engagement text, website_name names the platform.
    target = extract_breadcrumb_target("30+ comments \u00b7 2 years ago", "Reddit \u00b7 r/Phoenixville")
    assert target["host"] is None
    assert target["resolved_domain"] == "reddit.com" and target["domain"] == "reddit.com"
    assert target["path_hint"] == "r/Phoenixville" and target["resolution_basis"] == "website_name"
    nbsp = extract_breadcrumb_target("12 comments", "Reddit\xa0\u00b7\xa0r/Phoenixville")
    assert nbsp["resolved_domain"] == "reddit.com" and nbsp["path_hint"] == "r/Phoenixville"
    item = {"type": "organic", "domain": "google.com", "url": "https://google.com/goto?url=CAEX",
            "breadcrumb": "30+ comments \u00b7 2 years ago", "website_name": "Reddit \u00b7 r/Phoenixville"}
    normalize_organic_item(item)
    assert item["resolved_domain"] == "reddit.com" and item["resolution_basis"] == "website_name"
    assert item["breadcrumb_path_hint"] == "r/Phoenixville"
    assert item["goto_wrapped"] is True and item["raw_domain"] == "google.com"


def test_non_allowlisted_website_name_is_never_slug_guessed():
    target = extract_breadcrumb_target("4 days ago", "Joe's Plumbing \u00b7 Phoenixville")
    assert target["resolved_domain"] is None and target["domain"] is None and target["path_hint"] is None
    item = {"type": "organic", "domain": "google.com", "url": "https://google.com/goto?url=CAEZ",
            "breadcrumb": "4 days ago", "website_name": "Joe's Plumbing"}
    normalize_organic_item(item)
    assert item["resolved_domain"] is None and "breadcrumb_path_hint" not in item
    for platform, domain in (("YouTube", "youtube.com"), ("Twitter", "x.com"), ("LinkedIn", "linkedin.com"),
                             ("Yelp", "yelp.com"), ("Tripadvisor", "tripadvisor.com")):
        assert WEBSITE_NAME_PLATFORMS[platform.lower()] == domain
        assert extract_breadcrumb_target("", platform)["resolved_domain"] == domain


def test_engagement_text_never_becomes_path_hint_and_platform_hint_enriches():
    target = extract_breadcrumb_target("https://www.reddit.com \u203a r \u203a comments", "Reddit \u00b7 r/philadelphia")
    assert target["host"] == "www.reddit.com" and target["path_hint"] == "r/philadelphia"
    wrapped = {"type": "organic", "domain": "google.com", "url": "https://google.com/goto?url=CAEQ",
               "breadcrumb": "https://www.reddit.com \u203a 50+ comments", "website_name": "Reddit \u00b7 r/philadelphia"}
    normalize_organic_item(wrapped)
    assert wrapped["resolved_domain"] == "reddit.com" and wrapped["breadcrumb_path_hint"] == "r/philadelphia"
    plain = {"type": "organic", "domain": "www.reddit.com", "url": "https://www.reddit.com/r/philadelphia/x",
             "website_name": "Reddit \u00b7 r/philadelphia"}
    normalize_organic_item(plain)
    assert plain["resolved_domain"] == "reddit.com" and plain["breadcrumb_path_hint"] == "r/philadelphia"
    other = {"type": "organic", "domain": "www.gattilaw.com", "url": "https://www.gattilaw.com/",
             "website_name": "Reddit \u00b7 r/philadelphia"}
    normalize_organic_item(other)
    assert "breadcrumb_path_hint" not in other  # hint only when the domain is the named platform
    kept = extract_breadcrumb_target("https://www.gattilaw.com \u203a Locations", "The Gatti Law Firm")
    assert kept["path_hint"] == "Locations" and kept["resolution_basis"] == "breadcrumb"


# --- standard queue task_post / task_get ------------------------------------------------------

def test_organic_path_split_covers_queue():
    assert client._is_organic_serp_request_path("/serp/google/organic/live/advanced")
    assert client._is_organic_serp_request_path("/serp/google/organic/task_post")
    assert not client._is_organic_serp_request_path("/serp/google/organic/task_get/advanced/abc")
    assert client._is_organic_serp_result_path("/serp/google/organic/live/regular")
    assert client._is_organic_serp_result_path("/serp/google/organic/task_get/advanced/abc-123")
    assert not client._is_organic_serp_result_path("/serp/google/organic/task_post")
    assert not client._is_organic_serp_result_path("/serp/google/maps/task_get/advanced/abc")


def test_task_post_enforces_resolve_and_task_get_is_stamped(monkeypatch):
    posted = {"status_code": 20000, "cost": 0.0024, "tasks": [
        {"id": "q-1", "status_code": 20100, "cost": 0.0024, "data": dict(RESOLVE_DATA), "result": None}]}
    sent = fake_transport(monkeypatch, [posted, renv(REAL, task_id="q-1"), renv(FILLER, aio_domains=REAL[:5]),
                                        envelope(block(REAL), task_id="q-3")])
    body = [{"keyword": "fractional cto", "location_code": 2840, "language_code": "en", "depth": 100}]
    queued = api_request(ORGANIC_TASK_POST, body, credentials=AUTH, confirm=True)
    assert sent[0][1][0]["resolve_poisoned_results"] is True and "resolve_goto_urls" not in sent[0][1][0]
    assert queued["tasks"][0]["id"] == "q-1"  # task id preserved, no stamping on the post envelope
    got = api_request("/v3/serp/google/organic/task_get/advanced/q-1", credentials=AUTH)
    assert sent[1][1] is None  # free GET, no body, no confirm needed
    # Queued task_get is provider-only until run through verify_serp_envelope().
    assert _integrity(got)["verified"] is False
    assert _integrity(got)["integrity"] == "protected"
    from legends_dataforseo import verify_serp_envelope
    verify_serp_envelope(got, independent_sites=REAL)
    assert _integrity(got)["verified"] is True
    assert _integrity(got)["verified_basis"] == "independent_consensus"
    decoy = api_request("/v3/serp/google/organic/task_get/advanced/q-2", credentials=AUTH)
    assert _integrity(decoy)["status"] == "suspect_decoy" and _integrity(decoy)["verified"] is False
    unconfirmed = api_request("/v3/serp/google/organic/task_get/advanced/q-3", credentials=AUTH)
    # task data echo does not confirm resolve_poisoned_results=true: fail closed
    assert _integrity(unconfirmed)["verified"] is False and _integrity(unconfirmed)["integrity"] == "unprotected"


# --- 0.1.12 battle-test fixes (Bugs #7, #8, #9) ----------------------------------------------

def test_normalize_organic_item_unwrapped_url_with_google_domain_is_not_goto_wrapped():
    edge_c = {
        "type": "organic",
        "url": "https://www.reddit.com/r/Phoenixville/comments/1abc/test/",
        "domain": "google.com",
        "breadcrumb": "30+ comments \u00b7 2 years ago",
        "website_name": "Reddit \u00b7 r/Phoenixville",
    }
    normalize_organic_item(edge_c)
    assert edge_c["goto_wrapped"] is False
    assert edge_c["domain"] == "www.reddit.com"
    assert edge_c["resolved_domain"] == "reddit.com"
    assert edge_c["raw_domain"] == "google.com"
    assert edge_c["breadcrumb_path_hint"] == "r/Phoenixville"

    no_name = {
        "type": "organic",
        "url": "https://www.reddit.com/r/Phoenixville/comments/1abc/test/",
        "domain": "google.com",
        "breadcrumb": "30+ comments \u00b7 2 years ago",
    }
    normalize_organic_item(no_name)
    assert no_name["goto_wrapped"] is False
    assert no_name["domain"] == "www.reddit.com"
    assert no_name["resolved_domain"] == "reddit.com"

    # Missing URL or Google-host URL with google.com domain and breadcrumb still counts as wrapped.
    missing_url = {
        "type": "organic",
        "url": None,
        "domain": "google.com",
        "breadcrumb": "https://www.gattilaw.com \u203a Locations",
    }
    normalize_organic_item(missing_url)
    assert missing_url["goto_wrapped"] is True
    assert missing_url["resolved_domain"] == "gattilaw.com"

    google_url = {
        "type": "organic",
        "url": "https://www.google.com/search?q=gattilaw",
        "domain": "google.com",
        "breadcrumb": "https://www.gattilaw.com \u203a Locations",
    }
    normalize_organic_item(google_url)
    assert google_url["goto_wrapped"] is True
    assert google_url["resolved_domain"] == "gattilaw.com"


def test_serp_5x_operator_detection_and_cost_preflight(monkeypatch, capsys):
    from legends_dataforseo import estimate_cost, has_serp_cost_multiplier_operator

    operators = [
        "allinanchor:seo", "allintext:seo", "allintitle:seo", "allinurl:seo",
        "cache:example.com", "define:serp", "definition:serp", "filetype:pdf report",
        "id:123", "inanchor:seo", "info:example.com", "intext:seo",
        "intitle:seo", "inurl:blog", "link:example.com", "site:reddit.com auto repair",
        "auto repair -site:yelp.com", "SITE:REDDIT.COM", "site%3Areddit.com+auto+repair",
    ]
    for kw in operators:
        assert has_serp_cost_multiplier_operator(kw) is True, kw

    for benign in ("website: example", "site reliability engineer", "define love", "", None, 42):
        assert has_serp_cost_multiplier_operator(benign) is False, benign

    est = estimate_cost("serp", tasks=1, depth=10, keyword="site:reddit.com auto repair")
    assert est["estimated_cost_usd"] == 0.01
    assert est["operator_cost_multiplier"] == 5

    queue_est = estimate_cost("serp-task-post", tasks=1, depth=10, keyword="inurl:blog")
    assert queue_est["estimated_cost_usd"] == 0.006
    assert queue_est["operator_cost_multiplier"] == 5

    plain_est = estimate_cost("serp", tasks=1, depth=10, keyword="auto repair")
    assert plain_est["estimated_cost_usd"] == 0.002
    assert "operator_cost_multiplier" not in plain_est

    maps_est = estimate_cost("maps", tasks=1, depth=100, keyword="site:reddit.com")
    assert maps_est["estimated_cost_usd"] == 0.002
    assert "operator_cost_multiplier" not in maps_est

    # serp() refuses under-budgeted 5x operator queries before HTTP.
    sent = fake_transport(monkeypatch, [renv(REAL, cost=0.02), renv(REAL, cost=0.02)])
    with pytest.raises(CostLimitError):
        serp("site:reddit.com auto repair", credentials=AUTH, confirm=True,
             estimated_cost_usd=0.004, max_cost_usd=0.01)
    assert sent == []

    # Properly budgeted 5x operator query succeeds without double-multiplying.
    ok = serp("site:reddit.com auto repair", independent_sites=REAL, credentials=AUTH, confirm=True,
              estimated_cost_usd=0.02, max_cost_usd=0.02)
    assert len(sent) == 1
    assert ok["legends_serp"]["operator_cost_multiplier"] == 5

    # Raw api_request() also enforces the 5x operator floor before HTTP.
    with pytest.raises(CostLimitError):
        api_request(ORGANIC_LIVE, [{"keyword": "site:reddit.com auto repair", "location_code": 2840}],
                    credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.01)
    assert len(sent) == 1

    raw_ok = api_request(
        ORGANIC_LIVE,
        [{"keyword": "site:reddit.com auto repair", "location_code": 2840, "resolve_poisoned_results": True}],
        credentials=AUTH, confirm=True, estimated_cost_usd=0.02, max_cost_usd=0.02,
    )
    assert len(sent) == 2 and raw_ok["status_code"] == 20000

    # CLI estimate and preview surface operator_cost_multiplier.
    assert cli.main(["estimate", "serp", "--depth", "10", "--keyword", "filetype:pdf annual report"]) == 0
    cli_est = json.loads(capsys.readouterr().out)
    assert cli_est["estimated_cost_usd"] == 0.01 and cli_est["operator_cost_multiplier"] == 5

    assert cli.main(["serp", "site:reddit.com auto repair"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["preview"] is True
    assert preview["operator_cost_multiplier"] == 5
    assert preview["operator_baseline_cost_usd"] == 0.02
    assert "5x" in preview["warning"]


def test_web_mode_blocks_encoded_udm_and_always_rejects_udm_plus_resolve(monkeypatch):
    sent = fake_transport(monkeypatch, [envelope(block(REAL))])
    encoded_cases = [
        {"keyword": "x", "location_code": 2840, "search_param": "udm%3D14"},
        {"keyword": "x", "location_code": 2840, "search_param": "udm%253D14"},
        {"keyword": "x", "location_code": 2840, "search_param": "gl=us&UDM%3dWEB"},
        {"keyword": "x", "location_code": 2840, "url": "https://www.google.com/search?q=x&udm%3D14"},
    ]
    for task in encoded_cases:
        assert client._web_mode(task) is True, task
        with pytest.raises(RouteError):
            api_request(ORGANIC_LIVE, [task], credentials=AUTH, confirm=True)
    assert sent == []

    # Non-web udm values must not false-positive.
    assert client._web_mode({"search_param": "udm=140"}) is False
    assert client._web_mode({"search_param": "udm=webinar"}) is False

    # Even with LEGENDS_DFS_ALLOW_POISONED=1, udm=14/web + resolve_poisoned_results=True is rejected.
    monkeypatch.setenv(ALLOW_POISONED_ENV, "1")
    invalid_pairs = [
        {"keyword": "x", "location_code": 2840, "search_param": "udm=14", "resolve_poisoned_results": True},
        {"keyword": "x", "location_code": 2840, "search_param": "udm%3D14", "resolve_poisoned_results": True},
        {"keyword": "x", "location_code": 2840, "url": "https://www.google.com/search?q=x&udm=web",
         "resolve_poisoned_results": True},
    ]
    for task in invalid_pairs:
        with pytest.raises(RouteError):
            api_request(ORGANIC_LIVE, [task], credentials=AUTH, confirm=True)
    assert sent == []

    # Encoded udm without resolve_poisoned_results=True is allowed under escape hatch and stamped unprotected.
    allowed = api_request(
        ORGANIC_LIVE,
        [{"keyword": "x", "location_code": 2840, "search_param": "udm%3D14"}],
        credentials=AUTH, confirm=True,
    )
    assert len(sent) == 1
    assert "resolve_poisoned_results" not in sent[0][1][0]
    assert _integrity(allowed)["integrity"] == "unprotected" and _integrity(allowed)["verified"] is False


# --- Stage 4 Carbon convergence regression tests (CF-01 .. CF-06) ------------------------------

def test_case_normalized_paths_cannot_bypass_organic_or_batch_guards(monkeypatch):
    assert client.normalize_path("/V3/SERP/Google/Organic/Live/Advanced.AI") == "/serp/google/organic/live/advanced.ai"
    assert (
        client.normalize_path("/SERP/Google/Organic/Task_Get/Advanced/MyTask_ID-9")
        == "/serp/google/organic/task_get/advanced/MyTask_ID-9"
    )
    sent = fake_transport(monkeypatch, [renv(REAL)])
    with pytest.raises(RouteError):
        api_request("/SERP/Google/Organic/Live/Advanced", [{"keyword": "a"}, {"keyword": "b"}],
                    credentials=AUTH, confirm=True)
    with pytest.raises(RouteError):
        api_request("/SERP/Google/Organic/Live/Advanced", [{"keyword": "a", "resolve_poisoned_results": False}],
                    credentials=AUTH, confirm=True)
    with pytest.raises(CostLimitError):
        api_request("/SERP/Google/Organic/Live/Advanced", [{"keyword": "a"}],
                    credentials=AUTH, confirm=True, estimated_cost_usd=0.001, max_cost_usd=0.002)
    assert sent == []
    res = api_request("/SERP/Google/Organic/Live/Advanced", [{"keyword": "fractional cto"}],
                      credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.004)
    assert sent[0][1][0]["resolve_poisoned_results"] is True
    assert _integrity(res)["integrity"] == "protected" and _integrity(res)["verified"] is False


def test_depth_float_and_max_crawl_pages_and_raw_organic_floor_enforced(monkeypatch, tmp_path, capsys):
    from legends_dataforseo import maps

    for bad_depth in (0, -5, 701, 100.0, True, "10"):
        with pytest.raises(ValueError):
            serp("fractional cto", depth=bad_depth, credentials=AUTH, confirm=True)
        with pytest.raises(ValueError):
            maps("coffee", location_coordinate="40.7,-74.0,15z", depth=bad_depth, credentials=AUTH, confirm=True)

    assert cli.main(["serp", "fractional cto", "--depth", "0"]) == 2
    capsys.readouterr()
    assert cli.main(["competitors", "fractional cto", "--item-limit", "0"]) == 2
    capsys.readouterr()
    assert cli.main(["ranked-keywords", "example.com", "--item-limit", "1001"]) == 2
    capsys.readouterr()

    sent = fake_transport(monkeypatch, [])
    # Pre-populated resolve_poisoned_results=True without operator still enforces base+resolve floor ($0.004 / $0.022).
    with pytest.raises(CostLimitError):
        api_request(ORGANIC_LIVE, [{"keyword": "plumber", "resolve_poisoned_results": True}],
                    credentials=AUTH, confirm=True, estimated_cost_usd=0.002, max_cost_usd=0.002)
    with pytest.raises(CostLimitError):
        api_request(ORGANIC_LIVE, [{"keyword": "plumber", "depth": 100.0, "resolve_poisoned_results": True}],
                    credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.01)
    with pytest.raises(CostLimitError):
        api_request(ORGANIC_TASK_POST, [{"keyword": "site:reddit.com plumber", "max_crawl_pages": 5}],
                    credentials=AUTH, confirm=True, estimated_cost_usd=0.012, max_cost_usd=0.02)
    assert sent == []

    # CLI `call` preview injects resolve_poisoned_results and surfaces the true 5x baseline ($0.02, not $0.01),
    # and `--execute` at that exact reviewed 0.02 budget succeeds without double-charging the resolve page.
    body_file = tmp_path / "organic_tasks.json"
    body_file.write_text(json.dumps([{"keyword": "(site:reddit.com) auto repair", "location_code": 2840}]),
                         encoding="utf-8")
    assert cli.main(["call", "/serp/google/organic/live/advanced", "--body-file", str(body_file)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["body"][0]["resolve_poisoned_results"] is True
    assert preview["operator_cost_multiplier"] == 5
    assert preview["operator_baseline_cost_usd"] == 0.02

    monkeypatch.setenv("DATAFORSEO_LOGIN", "fixture-login")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "fixture-secret")
    exec_sent = fake_transport(monkeypatch, [renv(REAL, cost=0.02)])
    assert cli.main([
        "call", "/serp/google/organic/live/advanced", "--body-file", str(body_file),
        "--execute", "--estimated-cost-usd", "0.02", "--max-cost-usd", "0.02",
    ]) == 0
    capsys.readouterr()
    assert len(exec_sent) == 1 and exec_sent[0][1][0]["resolve_poisoned_results"] is True


def test_operator_punctuation_and_url_query_param_detection(monkeypatch):
    from legends_dataforseo import has_serp_cost_multiplier_operator

    for grouped in (
        "(site:reddit.com) auto repair",
        "plumber (inurl:forum|inurl:thread)",
        '"auto repair"site:reddit.com',
        "+site:reddit.com",
        "(-site:yelp.com) auto repair",
    ):
        assert has_serp_cost_multiplier_operator(grouped) is True, grouped

    for benign in ("on-site: training", "website: example", "https://example.com/path"):
        assert has_serp_cost_multiplier_operator(benign) is False, benign

    sent = fake_transport(monkeypatch, [])
    with pytest.raises(CostLimitError):
        api_request(
            ORGANIC_LIVE,
            [{"url": "https://www.google.com/search?q=site%3Areddit.com+plumber"}],
            credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.01,
        )
    assert sent == []


def test_incumbent_sites_triggers_repull_and_preserves_verify_pull_suspect(monkeypatch):
    # Surface-clean decoy (no AIO) caught by incumbent_sites -> re-pulled within max_poison_retries -> verified.
    sent = fake_transport(monkeypatch, [renv(FILLER), renv(REAL, checks=["from_web"], task_id="t-2")])
    result = serp("fractional cto", incumbent_sites=REAL[:5], independent_sites=REAL, max_poison_retries=2,
                  credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.02)
    assert len(sent) == 2
    assert [a["status"] for a in result["legends_serp"]["attempts"]] == ["suspect_decoy", "clean"]
    assert _integrity(result)["verified"] is True

    # verify_no_swap + incumbent_sites: if Pull 1 matches incumbents (clean) and Pull 2 is poisoned/suspect,
    # verify_pull_suspect must NOT be overwritten by incumbent assessment.
    poison_verify = envelope(
        block(["en.wikipedia.org"] + REAL[:9], keyword="plumber norristown"),
        task_id="t-v2", data={"keyword": "plumber norristown", "resolve_poisoned_results": True}, cost=0.004,
    )
    clean_first = envelope(
        block(REAL, keyword="plumber norristown"),
        task_id="t-v1", data={"keyword": "plumber norristown", "resolve_poisoned_results": True}, cost=0.004,
    )
    fake_transport(monkeypatch, [clean_first, poison_verify])
    with pytest.raises(UnverifiedSerpError) as caught:
        serp("plumber norristown", incumbent_sites=REAL[:5], verify_no_swap=True, max_poison_retries=0,
             credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.02)
    integrity = _integrity(caught.value.response)
    assert integrity["verified"] is False
    assert "verify_pull_suspect" in integrity["decoy"]["fired"]


def test_zero_organic_html_blocks_and_all_opaque_goto_fail_closed(monkeypatch):
    # Raw HTML task_get has items=[{"type": "html", ...}] and organic_count == 0 -> must NOT stamp verified=True.
    html_env = {
        "status_code": 20000,
        "cost": 0,
        "tasks": [{
            "id": "q-html",
            "status_code": 20000,
            "cost": 0,
            "data": {"keyword": "fractional cto", "resolve_poisoned_results": True},
            "result": [{"keyword": "fractional cto", "items": [{"type": "html", "html": "<html></html>"}]}],
        }],
    }
    fake_transport(monkeypatch, [html_env])
    got = api_request("/serp/google/organic/task_get/html/q-html", credentials=AUTH)
    assert _integrity(got)["organic_count"] == 0
    assert _integrity(got)["verified"] is False
    assert "no parsed organic items in result block" in _integrity(got)["unverified_reasons"]

    # All-opaque /goto organic items must still fire AIO / incumbent decoy signals (overlap 0.0) instead of skipping.
    opaque_items = [
        aio(*REAL[:4]),
        *[{"type": "organic", "domain": "google.com", "url": f"https://google.com/goto?url={i}"} for i in range(5)],
    ]
    opaque_report = assess_serp_integrity({"keyword": "fractional cto", "items": opaque_items})
    assert opaque_report["status"] == "suspect_decoy"
    assert opaque_report["decoy"]["signals"]["aio_organic_disjoint"]["overlap"] == 0.0


def test_international_google_goto_unmasked_and_google_subdomains_preserved():
    from legends_dataforseo import is_goto_wrapped_url

    uk_wrapped = {
        "type": "organic",
        "domain": "www.google.co.uk",
        "url": "https://www.google.co.uk/goto?url=CAES_UK",
        "breadcrumb": "https://www.bbc.co.uk \u203a news",
        "title": "BBC News",
    }
    assert is_goto_wrapped_url(uk_wrapped["url"]) is True
    normalize_organic_item(uk_wrapped)
    assert uk_wrapped["goto_wrapped"] is True
    assert uk_wrapped["resolved_domain"] == "bbc.co.uk"
    assert organic_item_site_key(uk_wrapped) == "bbc.co.uk"

    # Real Google product subdomains (developers.google.com, cloud.google.com) are not wrapper hosts
    # and match AIO reference brand keys ("google").
    dev_google = {
        "type": "organic",
        "domain": "developers.google.com",
        "url": "https://developers.google.com/search/docs/fundamentals/seo-starter-guide",
        "breadcrumb": "https://developers.google.com \u203a search \u203a docs",
        "website_name": "Google for Developers",
    }
    normalize_organic_item(dev_google)
    assert dev_google["goto_wrapped"] is False
    assert dev_google["resolved_domain"] == "developers.google.com"
    assert organic_item_site_key(dev_google) == "google.com"
    res = {
        "keyword": "technical seo",
        "items": [
            aio("developers.google.com", "cto-legends.com", "forbes.com"),
            dev_google,
            organic(2, "cto-legends.com"),
            organic(3, "forbes.com"),
        ],
    }
    assert assess_serp_integrity(res)["decoy"]["signals"]["aio_organic_disjoint"]["overlap"] == 1.0
