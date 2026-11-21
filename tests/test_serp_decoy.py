"""0.1.12 decoy SERP defense: resolve_poisoned_results default, suspect_decoy detector, re-pull."""

from __future__ import annotations

import io
import json

import pytest

from legends_dataforseo import (
    DECOY_THRESHOLDS,
    CostLimitError,
    Credentials,
    apply_decoy_assessment,
    assess_decoy_signals,
    assess_serp_integrity,
    cli,
    client,
    organic_site_keys,
    overlap_ratio,
    resolve_poisoned_default,
    serp,
    site_key,
    site_traffic,
)

AUTH = Credentials("fixture-login", "fixture-password")
NOW = "2026-11-20 12:00:00 +00:00"


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


def block(domains, *, ages=None, aio_domains=None, checks=None, keyword="fractional cto"):
    items = []
    if aio_domains:
        items.append(aio(*aio_domains))
    for idx, dom in enumerate(domains, start=1):
        ts = ages[idx - 1] if ages else None
        items.append(organic(idx, dom, timestamp=ts, checks=checks))
    return {"keyword": keyword, "datetime": NOW, "items": items}


def envelope(res_block, *, task_id="t-1", cost=0.004, data=None):
    return {"status_code": 20000, "cost": cost, "tasks": [{
        "id": task_id, "status_code": 20000, "cost": cost,
        "data": data or {"keyword": res_block["keyword"]}, "result": [res_block]}]}


def fake_transport(monkeypatch, responses):
    sent = []
    queue = list(responses)

    def fake_open(req, timeout):
        sent.append((req.full_url, json.loads(req.data.decode("utf-8")) if req.data else None))
        return io.BytesIO(json.dumps(queue.pop(0)).encode("utf-8"))

    monkeypatch.setattr(client, "_open", fake_open)
    return sent


OLD = "2019-01-01 00:00:00 +00:00"
FRESH = "2026-06-01 00:00:00 +00:00"


def test_site_key_and_overlap_helpers():
    assert site_key("https://www.blog.example.co.uk/path") == "example.co.uk"
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


def test_stale_median_age_fires_suspect_decoy():
    res = block(FILLER, ages=[OLD] * 10)
    integrity = assess_serp_integrity(res, task_data={"keyword": "fractional cto"})
    assert integrity["status"] == "suspect_decoy"
    assert integrity["decoy"]["fired"] == ["stale_results"]
    assert integrity["decoy"]["signals"]["stale_results"]["median_age_days"] > DECOY_THRESHOLDS["stale_median_days"]
    assert any("Median dated organic result age" in r for r in integrity["reasons"])


def test_fresh_dated_results_stay_clean():
    integrity = assess_serp_integrity(block(REAL, ages=[FRESH] * 10))
    assert integrity["status"] == "clean"
    assert integrity["decoy"]["signals"]["stale_results"]["fired"] is False


def test_ai_overview_disjoint_from_organics_fires():
    res = block(FILLER, aio_domains=REAL[:5])
    integrity = assess_serp_integrity(res)
    assert integrity["status"] == "suspect_decoy"
    assert integrity["decoy"]["signals"]["aio_organic_disjoint"]["overlap"] == 0.0

    healthy = assess_serp_integrity(block(REAL, aio_domains=REAL[:5]))
    assert healthy["status"] == "clean"
    assert healthy["decoy"]["signals"]["aio_organic_disjoint"]["overlap"] == 1.0


def test_poisoned_keeps_precedence_over_decoy():
    res = block(["en.wikipedia.org"] + FILLER[:9], ages=[OLD] * 10, keyword="plumber norristown")
    integrity = assess_serp_integrity(res, task_data={"keyword": "plumber norristown"})
    assert integrity["status"] == "suspect_poisoned"
    assert integrity["decoy"]["suspect"] is True


def test_provider_resolve_states():
    swapped = assess_decoy_signals(block(REAL, checks=["from_web"]),
                                   task_data={"resolve_poisoned_results": True})
    assert swapped["provider_resolve"] == "resolved" and swapped["from_web_count"] == 10
    silent = assess_decoy_signals(block(REAL), task_data={"resolve_poisoned_results": True})
    assert silent["provider_resolve"] == "requested_no_swap"


def test_caller_evidence_second_pull_incumbents_and_traffic():
    res = block(FILLER)
    d1 = assess_decoy_signals(res, second_pull_sites=REAL)
    assert d1["signals"]["pull_instability"]["fired"] is True
    d2 = assess_decoy_signals(res, incumbent_sites=["https://www.fractionalcto.com", "toptal.com", "forbes.com"])
    assert d2["signals"]["incumbent_mismatch"]["overlap"] == 0.0 and d2["suspect"] is True
    d3 = assess_decoy_signals(res, site_traffic={k: 3.0 for k in FILLER})
    assert d3["signals"]["filler_traffic"]["filler_share"] == 1.0
    d4 = assess_decoy_signals(block(REAL), site_traffic={k: 90000.0 for k in REAL})
    assert d4["suspect"] is False


def test_apply_decoy_assessment_redecides_without_stale_reasons():
    integrity = assess_serp_integrity(block(FILLER, ages=[OLD] * 10))
    assert integrity["status"] == "suspect_decoy"
    apply_decoy_assessment(integrity, assess_decoy_signals(block(REAL, ages=[FRESH] * 10)))
    assert integrity["status"] == "clean"
    assert integrity["reasons"] == []


def test_resolve_default_and_env_opt_out(monkeypatch):
    monkeypatch.delenv("LEGENDS_DATAFORSEO_RESOLVE_POISONED", raising=False)
    assert resolve_poisoned_default() is True
    for off in ("0", "false", "OFF", "no"):
        monkeypatch.setenv("LEGENDS_DATAFORSEO_RESOLVE_POISONED", off)
        assert resolve_poisoned_default() is False
    monkeypatch.setenv("LEGENDS_DATAFORSEO_RESOLVE_POISONED", "1")
    assert resolve_poisoned_default() is True


def test_serp_sends_resolve_by_default_and_records_cost_latency(monkeypatch):
    monkeypatch.delenv("LEGENDS_DATAFORSEO_RESOLVE_POISONED", raising=False)
    sent = fake_transport(monkeypatch, [envelope(block(REAL, ages=[FRESH] * 10)),
                                        envelope(block(REAL, ages=[FRESH] * 10), task_id="t-2")])
    result = serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.01)
    assert sent[0][1][0]["resolve_poisoned_results"] is True
    meta = result["legends_serp"]
    assert meta["resolve_poisoned_results_default"] is True
    assert meta["final_status"] == "clean"
    assert meta["attempts"][0]["cost_usd"] == 0.004 and meta["attempts"][0]["latency_ms"] >= 0
    # zero swaps under resolve -> one verification pull (fail-open guard), stable -> verified
    assert meta["attempts"][1]["reason"] == "verify_no_swap"
    assert meta["cost_usd_total"] == 0.008
    integrity = result["tasks"][0]["result"][0]["serp_integrity"]
    assert integrity["resolve_poisoned_results"] is True and "unresolved" not in integrity
    assert integrity["verification"]["status"] == "stable" and "unverified" not in integrity


def test_serp_opt_out_param_and_env(monkeypatch):
    sent = fake_transport(monkeypatch, [envelope(block(REAL)), envelope(block(REAL))])
    serp("fractional cto", resolve_poisoned_results=False, credentials=AUTH, confirm=True,
         estimated_cost_usd=0.002, max_cost_usd=0.002)
    monkeypatch.setenv("LEGENDS_DATAFORSEO_RESOLVE_POISONED", "0")
    serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.002, max_cost_usd=0.002)
    assert all("resolve_poisoned_results" not in body[0] for _, body in sent)
    with pytest.raises(ValueError):
        serp("x", resolve_poisoned_results="yes", credentials=AUTH, confirm=True)


def test_serp_refuses_cap_that_cannot_cover_resolve_surcharge(monkeypatch):
    monkeypatch.delenv("LEGENDS_DATAFORSEO_RESOLVE_POISONED", raising=False)
    sent = fake_transport(monkeypatch, [])
    with pytest.raises(CostLimitError):
        serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.002, max_cost_usd=0.002)
    assert sent == []


def test_suspect_decoy_is_repulled_with_resolve(monkeypatch):
    decoy_env = envelope(block(FILLER, ages=[OLD] * 10), task_id="t-decoy", cost=0.002)
    fixed_env = envelope(block(REAL, ages=[FRESH] * 10, checks=["from_web"]), task_id="t-fixed",
                         data={"keyword": "fractional cto", "resolve_poisoned_results": True})
    sent = fake_transport(monkeypatch, [decoy_env, fixed_env])
    result = serp("fractional cto", resolve_poisoned_results=False, credentials=AUTH, confirm=True,
                  estimated_cost_usd=0.002, max_cost_usd=0.02)
    assert "resolve_poisoned_results" not in sent[0][1][0]
    assert sent[1][1][0]["resolve_poisoned_results"] is True
    integrity = result["tasks"][0]["result"][0]["serp_integrity"]
    assert integrity["status"] == "clean"
    assert integrity["decoy"]["provider_resolve"] == "resolved"
    assert integrity["attempt_task_ids"] == ["t-decoy", "t-fixed"]
    assert integrity["repull_top10_overlap"] == 0.0
    meta = result["legends_serp"]
    assert [a["reason"] for a in meta["attempts"]] == ["initial", "suspect_decoy"]
    assert [a["status"] for a in meta["attempts"]] == ["suspect_decoy", "clean"]
    assert meta["cost_usd_total"] == 0.006


def test_unresolved_decoy_is_never_relabelled_clean(monkeypatch):
    stuck = envelope(block(FILLER, ages=[OLD] * 10))
    fake_transport(monkeypatch, [stuck, stuck, stuck])
    result = serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.05)
    integrity = result["tasks"][0]["result"][0]["serp_integrity"]
    assert integrity["status"] == "suspect_decoy"
    assert integrity["unresolved"] is True
    assert integrity["retry_attempts"] == 2
    assert result["legends_serp"]["final_status"] == "suspect_decoy"


def test_decoy_repull_respects_cumulative_cap(monkeypatch):
    stuck = envelope(block(FILLER, ages=[OLD] * 10))
    sent = fake_transport(monkeypatch, [stuck, stuck])
    result = serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.006)
    assert len(sent) == 1
    integrity = result["tasks"][0]["result"][0]["serp_integrity"]
    assert integrity["budget_exhausted"] is True and integrity["unresolved"] is True


def test_incumbent_sites_flag_a_surface_clean_decoy(monkeypatch):
    fake_transport(monkeypatch, [envelope(block(FILLER))])
    result = serp("fractional cto", max_poison_retries=0, incumbent_sites=REAL[:5], verify_no_swap=False,
                  credentials=AUTH,
                  confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.01)
    integrity = result["tasks"][0]["result"][0]["serp_integrity"]
    assert integrity["status"] == "suspect_decoy" and integrity["unresolved"] is True
    assert result["legends_serp"]["attempts"][-1]["status"] == "suspect_decoy"


def test_site_traffic_uses_local_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("LEGENDS_DATAFORSEO_TRAFFIC_CACHE", str(tmp_path / "traffic.json"))
    labs = {"status_code": 20000, "cost": 0.0102, "tasks": [{"id": "lab-1", "status_code": 20000, "result": [{
        "items": [{"target": "toptal.com", "metrics": {"organic": {"etv": 12345.5}}}]}]}]}
    sent = fake_transport(monkeypatch, [labs])
    opts = dict(credentials=AUTH, confirm=True, estimated_cost_usd=0.011, max_cost_usd=0.02)
    first = site_traffic(["https://www.toptal.com/x", "unknown-filler.net"], **opts)
    assert first == {"toptal.com": 12345.5, "unknown-filler.net": 0.0}
    assert sent[0][0].endswith("/dataforseo_labs/google/bulk_traffic_estimation/live")
    assert sent[0][1][0]["targets"] == ["toptal.com", "unknown-filler.net"]
    second = site_traffic(["toptal.com", "unknown-filler.net"], **opts)
    assert second == first and len(sent) == 1


def test_cli_preview_shows_resolve_and_opt_out(monkeypatch, capsys):
    monkeypatch.delenv("LEGENDS_DATAFORSEO_RESOLVE_POISONED", raising=False)
    assert cli.main(["serp", "fractional cto"]) == 0
    assert json.loads(capsys.readouterr().out)["body"][0]["resolve_poisoned_results"] is True
    assert cli.main(["serp", "fractional cto", "--no-resolve-poisoned"]) == 0
    assert "resolve_poisoned_results" not in json.loads(capsys.readouterr().out)["body"][0]


# --- 0.1.12 fail-open verification + /goto site keys (calibrated on the 2026-11-20 bench) ---

from legends_dataforseo import organic_item_site_key  # noqa: E402

RESOLVE_DATA = {"keyword": "fractional cto", "resolve_poisoned_results": True}


def renv(domains, *, checks=None, task_id="t-1"):
    return envelope(block(domains, checks=checks), task_id=task_id, data=dict(RESOLVE_DATA))


def _integrity(result):
    return result["tasks"][0]["result"][0]["serp_integrity"]


def test_goto_items_key_by_breadcrumb_then_website_name():
    wrapped = {"type": "organic", "domain": "google.com", "url": "https://google.com/goto?url=CAES",
               "breadcrumb": "https://www.rotorooter.com › philadelphia", "website_name": "Roto-Rooter"}
    name_only = {"type": "organic", "domain": "google.com", "url": "https://google.com/goto?url=CAEX",
                 "breadcrumb": "50+ comments · 9 months ago", "website_name": "Reddit · r/philadelphia"}
    bare = {"type": "organic", "domain": "google.com", "url": "https://google.com/goto?url=CAEZ"}
    assert organic_item_site_key(wrapped) == "rotorooter.com"
    assert organic_item_site_key(name_only) == "reddit"
    assert organic_item_site_key(bare) is None
    res_block = {"keyword": "plumber", "items": [wrapped, name_only, bare]}
    assess_serp_integrity(res_block)
    assert organic_site_keys(res_block) == ["rotorooter.com", "reddit"]


def test_no_swap_under_resolve_gets_stable_verification(monkeypatch):
    monkeypatch.delenv("LEGENDS_DATAFORSEO_RESOLVE_POISONED", raising=False)
    sent = fake_transport(monkeypatch, [renv(REAL), renv(REAL, task_id="t-2")])
    result = serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.02)
    assert len(sent) == 2 and all(body[0]["resolve_poisoned_results"] is True for _, body in sent)
    integrity = _integrity(result)
    assert integrity["status"] == "clean" and integrity["verification"]["status"] == "stable"
    assert "unverified" not in integrity and "unresolved" not in integrity
    meta = result["legends_serp"]
    assert [a["reason"] for a in meta["attempts"]] == ["initial", "verify_no_swap"]
    assert meta["cost_usd_total"] == 0.008


def test_verification_pull_with_swaps_is_adopted(monkeypatch):
    fake_transport(monkeypatch, [renv(FILLER), renv(REAL, checks=["from_web"], task_id="t-2")])
    result = serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.02)
    integrity = _integrity(result)
    assert integrity["status"] == "clean" and integrity["decoy"]["provider_resolve"] == "resolved"
    assert result["legends_serp"]["verification"]["status"] == "resolved_on_verify"
    assert organic_site_keys(result["tasks"][0]["result"][0])[0] == REAL[0]


def test_unstable_no_swap_pulls_are_suspect_and_unresolved(monkeypatch):
    fake_transport(monkeypatch, [renv(FILLER), renv(REAL, task_id="t-2")])
    result = serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.02)
    integrity = _integrity(result)
    assert integrity["status"] == "suspect_decoy" and integrity["unresolved"] is True
    assert integrity["verification"]["status"] == "unstable"
    assert "pull_instability" in integrity["decoy"]["fired"]
    assert result["legends_serp"]["final_status"] == "suspect_decoy"


def test_verification_skipped_for_budget_is_unverified(monkeypatch):
    sent = fake_transport(monkeypatch, [renv(REAL)])
    result = serp("fractional cto", credentials=AUTH, confirm=True, estimated_cost_usd=0.004, max_cost_usd=0.005)
    integrity = _integrity(result)
    assert len(sent) == 1 and integrity["status"] == "clean" and integrity["unverified"] is True
    assert integrity["verification"]["status"] == "skipped_budget"
    assert result["legends_serp"]["budget_exhausted"] is True


def test_resolve_off_and_verify_disabled_are_unverified(monkeypatch):
    sent = fake_transport(monkeypatch, [envelope(block(REAL)), renv(REAL)])
    off = serp("fractional cto", resolve_poisoned_results=False, credentials=AUTH, confirm=True,
               estimated_cost_usd=0.002, max_cost_usd=0.002)
    assert _integrity(off)["unverified"] is True and _integrity(off)["verification"]["status"] == "resolve_off"
    disabled = serp("fractional cto", verify_no_swap=False, credentials=AUTH, confirm=True,
                    estimated_cost_usd=0.004, max_cost_usd=0.02)
    assert len(sent) == 2
    assert _integrity(disabled)["verification"]["status"] == "skipped_disabled"
    assert _integrity(disabled)["unverified"] is True
    with pytest.raises(ValueError):
        serp("x", verify_no_swap="yes", credentials=AUTH, confirm=True)
