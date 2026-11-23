"""Hermetic unit tests for the independent SERP Truth Gate (no live network)."""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest

from legends_dataforseo import (
    TRUTH_GATE_THRESHOLDS,
    VERIFIED_BASES,
    Credentials,
    UnverifiedSerpError,
    assess_decoy_signals,
    cli,
    client,
    evaluate_independent_consensus,
    firecrawl_search_view,
    load_firecrawl_api_key,
    organic_site_keys,
    serp,
    truth_gate,
    verify_serp_envelope,
)

AUTH = Credentials("fixture-login", "fixture-password")
REAL = [
    "semrush.com", "link-assistant.com", "zapier.com", "ahrefs.com", "moz.com",
    "seranking.com", "nightwatch.io", "agencyanalytics.com", "reddit.com", "g2.com",
]
DECOY = [
    "filler-1.net", "filler-2.org", "filler-3.info", "filler-4.biz", "filler-5.com",
    "filler-6.net", "filler-7.org", "filler-8.info", "filler-9.biz", "filler-10.com",
]


def _organic(rank: int, domain: str, *, checks=None):
    item = {
        "type": "organic",
        "rank_group": rank,
        "rank_absolute": rank,
        "domain": domain,
        "url": f"https://{domain}/page-{rank}",
        "title": f"Result {rank} ({domain})",
    }
    if checks is not None:
        item["checks"] = list(checks)
    return item


def _aio(*domains: str):
    return {
        "type": "ai_overview",
        "items": [{
            "type": "ai_overview_element",
            "references": [{"domain": d, "url": f"https://{d}/ref"} for d in domains],
        }],
    }


def _env(domains: list[str], *, checks=None, aio_domains=None, task_id="t-1", cost=0.004, keyword="rank tracker tool"):
    items = []
    if aio_domains:
        items.append(_aio(*aio_domains))
    for idx, d in enumerate(domains, start=1):
        items.append(_organic(idx, d, checks=checks))
    return {
        "status_code": 20000,
        "cost": cost,
        "tasks": [{
            "id": task_id,
            "status_code": 20000,
            "cost": cost,
            "data": {"keyword": keyword, "resolve_poisoned_results": True},
            "result": [{"keyword": keyword, "items": items}],
        }],
    }


def _fake_dfs(monkeypatch, responses: list[dict]):
    sent = []
    queue = list(responses)

    def _open(req, timeout):
        sent.append((req.full_url, json.loads(req.data.decode("utf-8")) if req.data else None))
        return io.BytesIO(json.dumps(queue.pop(0)).encode("utf-8"))

    monkeypatch.setattr(client, "_open", _open)
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    return sent


def test_consensus_swapped_strict_rejects_l02_l05_n13_shapes():
    # L02 shape: high Jaccard (0.818) and same #1, but top-3 churn (2/3) under from_web > 0 -> refused.
    dfs_l02 = ["kallerroofing.com", "yelp.com", "centimark.com", "dreamhomeroofers.com", "mission-roof.org",
               "rockroofingcontractor.com", "qualityroofsandexteriors.com", "angi.com", "rightdeal.com", "rapunzel.com"]
    fc_l02 = ["kallerroofing.com", "rockroofingcontractor.com", "yelp.com", "centimark.com", "mission-roof.org",
              "dreamhomeroofers.com", "rapunzel.com", "qualityroofsandexteriors.com", "facebook.com", "angi.com"]
    c_l02 = evaluate_independent_consensus(dfs_l02, fc_l02, from_web_count=50)
    assert c_l02["passed"] is False
    assert any("top3_overlap" in r for r in c_l02["reasons"])

    # L05 shape: top-3 matches, but tail Jaccard 0.429 < 0.50 -> refused.
    dfs_l05 = ["justia.com", "klinespecter.com", "rosenbaum.com", "superlawyers.com", "levin.com",
               "pearce.com", "quinn.com", "kpattorney.com", "rossfeller.com", "phillylawyers.com"]
    fc_l05 = ["justia.com", "klinespecter.com", "rosenbaum.com", "superlawyers.com", "lundy.com",
              "kpattorney.com", "hilljustice.com", "mccann.com", "rosenjustice.com", "levin.com"]
    c_l05 = evaluate_independent_consensus(dfs_l05, fc_l05, from_web_count=50)
    assert c_l05["passed"] is False
    assert any("top10_jaccard" in r for r in c_l05["reasons"])

    # N13 shape: Jaccard 0.667, but #1 disagrees (link-assistant vs semrush) -> refused.
    dfs_n13 = ["link-assistant.com", "semrush.com", "zapier.com", "seranking.com", "nightwatch.io",
               "daxrm.com", "localo.com", "agencyanalytics.com", "seoreviewtools.com", "nathan.com"]
    fc_n13 = ["semrush.com", "link-assistant.com", "nightwatch.io", "zapier.com", "agencyanalytics.com",
              "ahrefs.com", "seoreviewtools.com", "daxrm.com", "seranking.com", "spyserp.com"]
    c_n13 = evaluate_independent_consensus(dfs_n13, fc_n13, from_web_count=50)
    assert c_n13["passed"] is False
    assert any("rank1_mismatch" in r for r in c_n13["reasons"])


def test_consensus_unswapped_all_tab_catches_fail_open_decoy_and_passes_clean():
    # Fail-open decoy with from_web == 0 has low Jaccard -> rejected.
    fail_open = evaluate_independent_consensus(DECOY, REAL, from_web_count=0)
    assert fail_open["passed"] is False and fail_open["mode"] == "unswapped_all_tab"

    # Genuine unswapped All-tab with modest All-vs-Web drift (Jaccard >= 0.35 + same #1) -> verified.
    clean_unswapped = evaluate_independent_consensus(
        REAL[:6] + ["extra1.com", "extra2.com", "extra3.com", "extra4.com"],
        REAL,
        from_web_count=0,
    )
    assert clean_unswapped["passed"] is True
    assert clean_unswapped["jaccard"] >= TRUTH_GATE_THRESHOLDS["unswapped_jaccard_min"]


def test_serp_repairs_from_aio_corroborated_independent_source(monkeypatch):
    # DFS returns a persistent decoy (even after re-pull), while Google's AIO cites REAL[:5]
    # and the independent source returns REAL[:8] with full URLs -> repaired & verified.
    poisoned = _env(DECOY, aio_domains=REAL[:5], checks=["from_web"])
    _fake_dfs(monkeypatch, [poisoned, poisoned])
    ind_items = [
        {"rank": idx, "url": f"https://{dom}/guide", "domain": dom, "resolved_domain": dom, "title": f"Guide {dom}"}
        for idx, dom in enumerate(REAL[:8], start=1)
    ]
    res = serp(
        "rank tracker tool",
        max_poison_retries=1,
        independent_items=ind_items,
        credentials=AUTH,
        confirm=True,
        estimated_cost_usd=0.004,
        max_cost_usd=0.02,
    )
    blk = res["tasks"][0]["result"][0]
    integ = blk["serp_integrity"]
    assert integ["verified"] is True
    assert integ["verified_basis"] == "independent_consensus_repaired"
    assert integ["verification"]["status"] == "repaired_from_independent"
    assert integ["repaired_from_dfs_top10"] == DECOY
    assert organic_site_keys(blk, limit=8) == REAL[:8]
    assert res["legends_serp"]["verified"] is True
    assert res["legends_serp"]["truth_gate"]["repaired_from_independent"] is True


def test_serp_refuses_when_independent_source_is_decoy_without_guessing(monkeypatch):
    # Simulates Firecrawl decoy (like I02/N14): DFS has REAL, Firecrawl returns DECOY -> refuse loudly.
    _fake_dfs(monkeypatch, [_env(REAL, checks=["from_web"])])
    with pytest.raises(UnverifiedSerpError) as caught:
        serp(
            "serp api",
            max_poison_retries=0,
            independent_sites=DECOY,
            credentials=AUTH,
            confirm=True,
            estimated_cost_usd=0.004,
            max_cost_usd=0.01,
        )
    integ = caught.value.response["tasks"][0]["result"][0]["serp_integrity"]
    assert integ["verified"] is False
    assert integ["verification"]["status"] == "consensus_disagreed"
    assert "independent_consensus" in integ["decoy"]["fired"]


def test_serp_custom_callable_provider_and_local_maps_check(monkeypatch):
    maps_resp = {
        "status_code": 20000,
        "cost": 0.002,
        "tasks": [{
            "id": "m-1",
            "status_code": 20000,
            "cost": 0.002,
            "result": [{"items": [{"type": "maps_search", "domain": REAL[0], "url": f"https://{REAL[0]}"}]}],
        }],
    }
    sent = _fake_dfs(monkeypatch, [_env(REAL, checks=["from_web"]), maps_resp])
    res = serp(
        "plumber in philadelphia",
        location_name="Philadelphia,Pennsylvania,United States",
        consensus_provider=lambda kw, **kw_args: list(REAL),
        local_maps_check=True,
        credentials=AUTH,
        confirm=True,
        estimated_cost_usd=0.004,
        max_cost_usd=0.02,
    )
    assert len(sent) == 2 and sent[1][0].endswith("/serp/google/maps/live/advanced")
    integ = res["tasks"][0]["result"][0]["serp_integrity"]
    assert integ["verified"] is True
    assert integ["verified_basis"] in VERIFIED_BASES
    assert integ["decoy"]["signals"]["local_maps_overlap"]["hits"] == 1
    assert res["legends_serp"]["truth_gate"]["maps_check"]["ok"] is True
    assert res["legends_serp"]["cost_usd_total"] == 0.006


def test_firecrawl_search_view_parses_and_sanitizes_errors(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-super-secret-value")
    assert load_firecrawl_api_key() == ("fc-super-secret-value", "process-env")

    captured = {}

    def fake_fc_open(req, timeout):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return io.BytesIO(json.dumps({
            "success": True,
            "data": {"web": [{"url": "https://www.semrush.com/a", "title": "Semrush"}]},
        }).encode("utf-8"))

    monkeypatch.setattr(truth_gate, "_open_firecrawl", fake_fc_open)
    view = firecrawl_search_view("rank tracker tool", location_name="Norristown,Pennsylvania,United States")
    assert view["ok"] is True
    assert view["sites"] == ["semrush.com"]
    assert captured["url"] == truth_gate.FIRECRAWL_SEARCH_URL
    assert captured["body"]["location"] == "Norristown,Pennsylvania,United States"

    # HTTP error never echoes the API key.
    def fake_fc_err(req, timeout):
        raise HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(truth_gate, "_open_firecrawl", fake_fc_err)
    err_view = firecrawl_search_view("rank tracker tool")
    assert err_view["ok"] is False
    assert "fc-super-secret-value" not in json.dumps(err_view)


def test_verify_serp_envelope_handles_queued_task_get_and_cli_preview_flags(capsys):
    raw_queued = _env(REAL, checks=["from_web"])
    from legends_dataforseo import normalize_serp_envelope, stamp_envelope_verified

    normalize_serp_envelope(raw_queued, endpoint="/serp/google/organic/task_get/advanced/q-1")
    stamp_envelope_verified(raw_queued)
    assert raw_queued["tasks"][0]["result"][0]["serp_integrity"]["verified"] is False

    verify_serp_envelope(raw_queued, independent_sites=REAL)
    integ = raw_queued["tasks"][0]["result"][0]["serp_integrity"]
    assert integ["verified"] is True and integ["verified_basis"] == "independent_consensus"

    assert cli.main(["serp", "plumber in philadelphia", "--consensus-provider", "firecrawl",
                     "--country", "US", "--local-maps-check"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["truth_gate"] == {
        "consensus_provider": "firecrawl",
        "country": "US",
        "local_maps_check": True,
    }


# --- depth=10 AIO top-20 truncation (held-out HN09/HI07/HI08 shape) ---------------------------

# Legitimate page-2 authorities an AI Overview can cite; invisible to a depth=10 pull.
PAGE2 = [
    "backlinko.com", "searchenginejournal.com", "hubspot.com", "neilpatel.com", "wordstream.com",
    "semrush-blog.net", "authorityhacker.com", "detailed.com", "growthbar.com", "surfer.com",
]
# Strict-consensus independent view of REAL: same #1, same top-3 set, 9/10 shared (Jaccard 0.818).
REAL_IND = [REAL[0], REAL[2], REAL[1]] + REAL[3:9] + ["extra-site.com"]
# AIO cites 2 page-1 sites + 3 page-2 sites: overlap vs a depth=10 pull is 0.4 by truncation,
# not by disjointness (the missing three rank beyond what depth=10 returns).
AIO_PAGE2_HEAVY = REAL[:2] + PAGE2[:3]


def _depth_env(domains, *, depth, checks=None, aio_domains=None):
    env = _env(domains, checks=checks, aio_domains=aio_domains)
    env["tasks"][0]["data"]["depth"] = depth
    return env


def _block_of(env):
    return env["tasks"][0]["result"][0]


def test_depth10_aio_refs_beyond_rank10_do_not_refuse_strict_consensus(monkeypatch):
    env = _depth_env(REAL, depth=10, checks=["from_web"], aio_domains=AIO_PAGE2_HEAVY)
    decoy = assess_decoy_signals(
        _block_of(env),
        task_data={"depth": 10, "resolve_poisoned_results": True},
        independent_sites=REAL_IND,
    )
    cons = decoy["signals"]["independent_consensus"]
    assert cons["fired"] is False and cons["mode"] == "swapped_strict"
    assert cons["jaccard"] >= 0.73 and cons["rank1_match"] is True and cons["top3_overlap"] == 1.0
    aio_sig = decoy["signals"]["aio_organic_disjoint"]
    assert aio_sig["overlap"] == 0.4 and aio_sig["fired"] is False
    assert aio_sig["top20_complete"] is False and aio_sig["requested_depth"] == 10
    assert aio_sig["organic_sites_compared"] == 10
    assert aio_sig["suppressed_reason"] == "insufficient_depth_for_top20_with_independent_consensus"
    assert decoy["suspect"] is False and decoy["fired"] == []

    # End to end: the same depth=10 pull verifies on the first attempt, with no paid re-pull.
    sent = _fake_dfs(monkeypatch, [_depth_env(REAL, depth=10, checks=["from_web"], aio_domains=AIO_PAGE2_HEAVY)])
    res = serp(
        "rank tracker tool",
        max_poison_retries=1,
        independent_sites=REAL_IND,
        credentials=AUTH,
        confirm=True,
        estimated_cost_usd=0.004,
        max_cost_usd=0.02,
    )
    assert len(sent) == 1
    integ = _block_of(res)["serp_integrity"]
    assert integ["status"] == "clean"
    assert integ["verified"] is True and integ["verified_basis"] == "independent_consensus"
    assert integ["decoy"]["signals"]["aio_organic_disjoint"]["fired"] is False


def test_aio_disjoint_still_refused_when_consensus_fails_or_depth20(monkeypatch):
    # depth=10, genuine decoy: AIO cites REAL while organics are filler and consensus fails.
    poisoned = _depth_env(DECOY, depth=10, checks=["from_web"], aio_domains=REAL[:5])
    decoy = assess_decoy_signals(
        _block_of(poisoned),
        task_data={"depth": 10, "resolve_poisoned_results": True},
        independent_sites=REAL,
    )
    aio_sig = decoy["signals"]["aio_organic_disjoint"]
    assert aio_sig["overlap"] == 0.0 and aio_sig["fired"] is True
    assert "suppressed_reason" not in aio_sig
    assert decoy["suspect"] is True
    assert {"aio_organic_disjoint", "independent_consensus"} <= set(decoy["fired"])

    # depth=10 without any independent consensus: the AIO backstop still fires (offline path).
    offline = assess_decoy_signals(
        _block_of(_depth_env(REAL, depth=10, aio_domains=AIO_PAGE2_HEAVY)),
        task_data={"depth": 10},
    )
    assert offline["signals"]["aio_organic_disjoint"]["fired"] is True
    assert offline["suspect"] is True

    # depth>=20: the top-20 comparison is fully powered, so AIO disjointness fires even
    # though strict independent consensus passes on the top 10.
    unranked = ["unranked-a.com", "unranked-b.com", "unranked-c.com"]
    deep = _depth_env(REAL + PAGE2, depth=20, checks=["from_web"], aio_domains=REAL[:2] + unranked)
    deep_decoy = assess_decoy_signals(
        _block_of(deep),
        task_data={"depth": 20, "resolve_poisoned_results": True},
        independent_sites=REAL_IND,
    )
    deep_aio = deep_decoy["signals"]["aio_organic_disjoint"]
    assert deep_decoy["signals"]["independent_consensus"]["fired"] is False
    assert deep_aio["top20_complete"] is True and deep_aio["organic_sites_compared"] == 20
    assert deep_aio["overlap"] == 0.4 and deep_aio["fired"] is True
    assert deep_decoy["suspect"] is True and "aio_organic_disjoint" in deep_decoy["fired"]

    # End to end: a poisoned depth=10 SERP with AIO disjointness is still refused loudly.
    _fake_dfs(monkeypatch, [_depth_env(DECOY, depth=10, checks=["from_web"], aio_domains=REAL[:5])])
    with pytest.raises(UnverifiedSerpError) as caught:
        serp(
            "rank tracker tool",
            max_poison_retries=0,
            independent_sites=REAL,
            credentials=AUTH,
            confirm=True,
            estimated_cost_usd=0.004,
            max_cost_usd=0.01,
        )
    integ = _block_of(caught.value.response)["serp_integrity"]
    assert integ["verified"] is False
    assert "aio_organic_disjoint" in integ["decoy"]["fired"]
