"""Independent SERP Truth Gate: cross-provider consensus, AIO-corroborated repair, and Maps diagnostics.

Uses only the Python standard library (HTTPS-only, no redirects, strict env-only credentials)
so legends-dataforseo stays zero-dependency while verifying DataForSEO organic SERPs against
an independent search view (Firecrawl /v2/search or caller-supplied provider/sites).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .serp_integrity import (
    _brand,
    _collect_reference_sites,
    apply_decoy_assessment,
    assess_decoy_signals,
    normalize_organic_item,
    organic_site_keys,
    overlap_ratio,
    site_key,
    stamp_verified,
)

FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v2/search"
MAX_FIRECRAWL_BYTES = 4 * 1024 * 1024
FIRECRAWL_SEARCH_CREDITS = 2

# Calibrated on the 2026-11-20 40-keyword decoy bench (M1 measurements):
# - All 35 DFS default decoys had Jaccard <= 0.25 vs Firecrawl /v2/search.
# - Clean unswapped All-tab pulls (from_web == 0) had Jaccard >= 0.385 with natural All-vs-Web drift.
# - Provider-swapped Web-mode pulls (from_web > 0) require stricter top-of-page consensus
#   (Jaccard >= 0.50, exact rank-1 agreement, and 3/3 top-3 set agreement) to reject
#   Web-mode vs All-tab reshuffling (L02, L05, N13) fail-closed.
TRUTH_GATE_THRESHOLDS: dict[str, Any] = {
    "unswapped_jaccard_min": 0.35,
    "unswapped_require_rank1": True,
    "swapped_jaccard_min": 0.50,
    "swapped_top3_overlap_min": 1.0,
    "swapped_require_rank1": True,
    "aio_repair_min_sites": 3,
    "aio_repair_dfs_max": 0.60,
    "aio_repair_independent_min": 0.65,
    "aio_repair_min_items": 5,
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _ssl_context():
    try:
        import certifi
        import ssl

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _open_firecrawl(request: Request, timeout: float):
    handlers: list[Any] = [ProxyHandler({}), _NoRedirect()]
    ctx = _ssl_context()
    if ctx is not None:
        handlers.insert(1, HTTPSHandler(context=ctx))
    return build_opener(*handlers).open(request, timeout=timeout)


def _windows_user_env(name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
        return value if isinstance(value, str) and value else None
    except (ImportError, OSError):
        return None


def load_firecrawl_api_key() -> tuple[str, str] | None:
    """Load FIRECRAWL_API_KEY strictly from process env or Windows user env (never echoes value)."""
    val = os.environ.get("FIRECRAWL_API_KEY")
    if isinstance(val, str) and val.strip():
        cleaned = val.strip()
        if not any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
            return cleaned, "process-env"
        return None
    if not os.environ.get("FIRECRAWL_DISABLE_USER_ENV"):
        val = _windows_user_env("FIRECRAWL_API_KEY")
        if isinstance(val, str) and val.strip():
            cleaned = val.strip()
            if not any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
                return cleaned, "user-env"
    return None


def _normalize_site_list(sites: list[str] | tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for raw in sites:
        if not isinstance(raw, str) or not raw.strip():
            continue
        key = site_key(raw)
        if key and key not in out:
            out.append(key)
    return out


def _keys_match(a: str, b: str) -> bool:
    if a == b:
        return True
    # Match a dotless website_name slug (e.g. "reddit") against its domain ("reddit.com").
    if ("." not in a) != ("." not in b):
        return _brand(a) == _brand(b)
    return False


def _shared_count(left: list[str], right: list[str]) -> int:
    matched_right: set[int] = set()
    hits = 0
    for a in left:
        for idx, b in enumerate(right):
            if idx not in matched_right and _keys_match(a, b):
                matched_right.add(idx)
                hits += 1
                break
    return hits


def evaluate_independent_consensus(
    dfs_sites: list[str],
    independent_sites: list[str],
    *,
    from_web_count: int = 0,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare DataForSEO top-10 site keys against an independent provider's top-10 site keys."""
    th = dict(TRUTH_GATE_THRESHOLDS)
    if thresholds:
        th.update(thresholds)
    dfs10 = _normalize_site_list(dfs_sites)[:10]
    ind10 = _normalize_site_list(independent_sites)[:10]
    mode = "swapped_strict" if from_web_count > 0 else "unswapped_all_tab"

    if not dfs10 or not ind10:
        reason = "empty_dfs_sites" if not dfs10 else "empty_independent_sites"
        return {
            "passed": False,
            "mode": mode,
            "from_web_count": from_web_count,
            "dfs_count": len(dfs10),
            "independent_count": len(ind10),
            "shared_count": 0,
            "jaccard": 0.0,
            "dfs_in_independent": 0.0,
            "independent_in_dfs": 0.0,
            "rank1_match": False,
            "top3_overlap": 0.0,
            "top5_overlap": 0.0,
            "reasons": [reason],
        }

    shared = _shared_count(dfs10, ind10)
    union = len(dfs10) + len(ind10) - shared
    jaccard = round(shared / max(1, union), 4)
    dfs_in_ind = round(shared / len(dfs10), 4)
    ind_in_dfs = round(shared / len(ind10), 4)
    rank1_match = _keys_match(dfs10[0], ind10[0])

    top3_denom = min(3, len(dfs10), len(ind10))
    top3_shared = _shared_count(dfs10[:3], ind10[:3])
    top3_overlap = round(top3_shared / max(1, top3_denom), 4)

    top5_denom = min(5, len(dfs10), len(ind10))
    top5_shared = _shared_count(dfs10[:5], ind10[:5])
    top5_overlap = round(top5_shared / max(1, top5_denom), 4)

    reasons: list[str] = []
    if mode == "swapped_strict":
        jmin = float(th["swapped_jaccard_min"])
        t3min = float(th["swapped_top3_overlap_min"])
        req_r1 = bool(th["swapped_require_rank1"])
        if jaccard < jmin:
            reasons.append(f"top10_jaccard {jaccard:.3f} < {jmin:.2f} (swapped_strict)")
        if req_r1 and not rank1_match:
            reasons.append(f"rank1_mismatch: {dfs10[0]} vs {ind10[0]}")
        if top3_overlap < t3min:
            reasons.append(f"top3_overlap {top3_overlap:.3f} < {t3min:.2f} (swapped_strict)")
    else:
        jmin = float(th["unswapped_jaccard_min"])
        req_r1 = bool(th["unswapped_require_rank1"])
        if jaccard < jmin:
            reasons.append(f"top10_jaccard {jaccard:.3f} < {jmin:.2f} (unswapped_all_tab)")
        if req_r1 and not rank1_match:
            reasons.append(f"rank1_mismatch: {dfs10[0]} vs {ind10[0]}")

    return {
        "passed": not reasons,
        "mode": mode,
        "from_web_count": from_web_count,
        "dfs_count": len(dfs10),
        "independent_count": len(ind10),
        "shared_count": shared,
        "jaccard": jaccard,
        "dfs_in_independent": dfs_in_ind,
        "independent_in_dfs": ind_in_dfs,
        "rank1_match": rank1_match,
        "top3_overlap": top3_overlap,
        "top5_overlap": top5_overlap,
        "reasons": reasons,
    }


def firecrawl_search_view(
    keyword: str,
    *,
    location_name: str | None = None,
    country: str = "US",
    limit: int = 10,
    timeout: float = 30.0,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Fetch an independent organic web view from Firecrawl /v2/search (never raises on provider failure)."""
    started = time.perf_counter()
    if api_key is None:
        loaded = load_firecrawl_api_key()
        if loaded is None:
            return {
                "provider": "firecrawl_search",
                "ok": False,
                "sites": [],
                "items": [],
                "latency_ms": 0.0,
                "credits_estimated": 0,
                "error": "FIRECRAWL_API_KEY not configured",
            }
        key_val, key_source = loaded
    else:
        key_val, key_source = api_key, "explicit"

    payload: dict[str, Any] = {
        "query": keyword.strip(),
        "limit": max(1, min(int(limit), 20)),
        "sources": ["web"],
        "country": (country or "US").upper(),
    }
    if isinstance(location_name, str) and location_name.strip():
        payload["location"] = location_name.strip()

    req = Request(
        FIRECRAWL_SEARCH_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {key_val}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "legends-dataforseo-truth-gate/1.0",
        },
    )
    try:
        with _open_firecrawl(req, timeout=timeout) as resp:
            raw = resp.read(MAX_FIRECRAWL_BYTES + 1)
        if len(raw) > MAX_FIRECRAWL_BYTES:
            return {
                "provider": "firecrawl_search",
                "ok": False,
                "sites": [],
                "items": [],
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
                "credits_estimated": FIRECRAWL_SEARCH_CREDITS,
                "error": "Firecrawl search response exceeded size limit",
            }
        decoded = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        return {
            "provider": "firecrawl_search",
            "ok": False,
            "sites": [],
            "items": [],
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "credits_estimated": 0,
            "error": f"Firecrawl HTTP {exc.code}",
        }
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        return {
            "provider": "firecrawl_search",
            "ok": False,
            "sites": [],
            "items": [],
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "credits_estimated": 0,
            "error": f"Firecrawl request failed ({type(exc).__name__})",
        }

    latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
    data = decoded.get("data") if isinstance(decoded, dict) else None
    web = data.get("web") if isinstance(data, dict) else (data if isinstance(data, list) else [])
    if not isinstance(web, list):
        web = []

    items: list[dict[str, Any]] = []
    sites: list[str] = []
    for idx, entry in enumerate(web, start=1):
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        dom = site_key(url)
        host = (urlparse(url.strip()).hostname or "").lower().rstrip(".")
        items.append({
            "rank": idx,
            "url": url.strip(),
            "domain": host or dom,
            "resolved_domain": dom,
            "title": entry.get("title") or "",
            "description": entry.get("description") or "",
        })
        if dom and dom not in sites:
            sites.append(dom)

    return {
        "provider": "firecrawl_search",
        "credential_source": key_source,
        "ok": True,
        "sites": sites,
        "items": items,
        "latency_ms": latency_ms,
        "credits_estimated": FIRECRAWL_SEARCH_CREDITS,
        "error": None,
    }


def fetch_independent_view(
    keyword: str,
    *,
    location_name: str | None = None,
    country: str = "US",
    independent_sites: list[str] | None = None,
    independent_items: list[dict[str, Any]] | None = None,
    consensus_provider: str | Callable[..., Any] | None = "firecrawl",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Resolve the independent SERP view from caller-supplied sites/items, a custom callable, or Firecrawl."""
    if independent_items is not None:
        items = [dict(it) for it in independent_items if isinstance(it, dict)]
        sites = _normalize_site_list(
            [str(it.get("resolved_domain") or it.get("domain") or it.get("url") or "") for it in items]
        )
        if independent_sites:
            sites = _normalize_site_list(list(independent_sites))
        return {
            "provider": "caller_items",
            "ok": bool(sites),
            "sites": sites,
            "items": items,
            "latency_ms": 0.0,
            "credits_estimated": 0,
            "error": None if sites else "caller supplied empty independent_items",
        }
    if independent_sites is not None:
        sites = _normalize_site_list(list(independent_sites))
        return {
            "provider": "caller_sites",
            "ok": bool(sites),
            "sites": sites,
            "items": [],
            "latency_ms": 0.0,
            "credits_estimated": 0,
            "error": None if sites else "caller supplied empty independent_sites",
        }
    if consensus_provider is None or consensus_provider == "none":
        return {
            "provider": "none",
            "ok": False,
            "sites": [],
            "items": [],
            "latency_ms": 0.0,
            "credits_estimated": 0,
            "error": "independent consensus provider disabled",
        }
    if consensus_provider == "firecrawl":
        return firecrawl_search_view(
            keyword,
            location_name=location_name,
            country=country,
            limit=10,
            timeout=timeout,
        )
    if callable(consensus_provider):
        started = time.perf_counter()
        try:
            raw = consensus_provider(keyword, location_name=location_name, country=country)
        except Exception as exc:
            return {
                "provider": "custom",
                "ok": False,
                "sites": [],
                "items": [],
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
                "credits_estimated": 0,
                "error": f"custom consensus_provider failed ({type(exc).__name__})",
            }
        latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
        if isinstance(raw, dict):
            items = [dict(it) for it in (raw.get("items") or []) if isinstance(it, dict)]
            raw_sites = raw.get("sites")
            if isinstance(raw_sites, list):
                sites = _normalize_site_list(raw_sites)
            else:
                sites = _normalize_site_list(
                    [str(it.get("resolved_domain") or it.get("domain") or it.get("url") or "") for it in items]
                )
            ok = raw.get("ok", bool(sites))
            return {
                "provider": str(raw.get("provider") or "custom"),
                "ok": bool(ok) and bool(sites),
                "sites": sites,
                "items": items,
                "latency_ms": float(raw.get("latency_ms", latency_ms)),
                "credits_estimated": int(raw.get("credits_estimated", 0)),
                "error": raw.get("error"),
            }
        if isinstance(raw, (list, tuple)):
            sites = _normalize_site_list(list(raw))
            return {
                "provider": "custom",
                "ok": bool(sites),
                "sites": sites,
                "items": [],
                "latency_ms": latency_ms,
                "credits_estimated": 0,
                "error": None if sites else "custom consensus_provider returned empty sites",
            }
        return {
            "provider": "custom",
            "ok": False,
            "sites": [],
            "items": [],
            "latency_ms": latency_ms,
            "credits_estimated": 0,
            "error": "custom consensus_provider returned unsupported type",
        }
    raise ValueError("consensus_provider must be 'firecrawl', 'none', None, or a callable")


def try_aio_corroborated_repair(
    result_block: dict[str, Any],
    independent_view: dict[str, Any],
    *,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Repair a demonstrably poisoned DFS organic block from an AIO-corroborated independent view.

    Only fires when ALL conditions hold:
    1. Independent view carries >= aio_repair_min_items full items with URLs.
    2. DFS AI Overview cites >= aio_repair_min_sites (3) external domains.
    3. DFS organic top-20 fails AIO overlap (<= 0.60) -> DFS organics are poisoned.
    4. Independent top-10 matches Google's AIO (>= 0.65) -> independent view is corroborated.
    """
    th = dict(TRUTH_GATE_THRESHOLDS)
    if thresholds:
        th.update(thresholds)
    ind_items = [it for it in (independent_view.get("items") or []) if isinstance(it, dict) and it.get("url")]
    if len(ind_items) < int(th["aio_repair_min_items"]):
        return {"repaired": False, "reason": "insufficient_independent_items"}

    items = result_block.get("items") if isinstance(result_block, dict) else None
    items = items if isinstance(items, list) else []
    aio_sites: list[str] = []
    for entry in items:
        if isinstance(entry, dict) and entry.get("type") == "ai_overview":
            _collect_reference_sites(entry, aio_sites)
    if len(aio_sites) < int(th["aio_repair_min_sites"]):
        return {"repaired": False, "reason": "no_aio_tiebreaker"}

    dfs_top20 = organic_site_keys(result_block, limit=20)
    ind_sites = _normalize_site_list(independent_view.get("sites") or [])[:10]
    dfs_aio_ov = overlap_ratio([_brand(k) for k in aio_sites], [_brand(k) for k in dfs_top20])
    ind_aio_ov = overlap_ratio([_brand(k) for k in aio_sites], [_brand(k) for k in ind_sites])
    if dfs_aio_ov is None or ind_aio_ov is None:
        return {"repaired": False, "reason": "aio_overlap_undefined"}
    if dfs_aio_ov > float(th["aio_repair_dfs_max"]):
        return {
            "repaired": False,
            "reason": f"dfs_not_aio_disjoint (dfs_aio={dfs_aio_ov}, ind_aio={ind_aio_ov})",
        }
    if ind_aio_ov < float(th["aio_repair_independent_min"]):
        return {
            "repaired": False,
            "reason": f"independent_not_aio_corroborated (dfs_aio={dfs_aio_ov}, ind_aio={ind_aio_ov})",
        }

    original_dfs_top10 = organic_site_keys(result_block, limit=10)
    non_organic = [it for it in items if not (isinstance(it, dict) and it.get("type") == "organic")]
    repaired_organics: list[dict[str, Any]] = []
    for idx, src in enumerate(ind_items[:10], start=1):
        url = str(src["url"]).strip()
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        dom = src.get("resolved_domain") or site_key(url)
        org_item: dict[str, Any] = {
            "type": "organic",
            "rank_group": idx,
            "rank_absolute": idx,
            "domain": host or dom or "",
            "resolved_domain": dom,
            "url": url,
            "resolved_url": url,
            "title": str(src.get("title") or ""),
            "description": str(src.get("description") or ""),
            "repaired_from": str(independent_view.get("provider") or "independent"),
        }
        normalize_organic_item(org_item)
        repaired_organics.append(org_item)

    result_block["items"] = non_organic + repaired_organics
    return {
        "repaired": True,
        "basis": "aio_corroborated_independent",
        "provider": independent_view.get("provider"),
        "dfs_aio_overlap": dfs_aio_ov,
        "independent_aio_overlap": ind_aio_ov,
        "aio_reference_sites": len(aio_sites),
        "original_dfs_top10": original_dfs_top10,
        "repaired_count": len(repaired_organics),
    }


def verify_serp_envelope(
    envelope: dict[str, Any],
    *,
    keyword: str | None = None,
    location_name: str | None = None,
    country: str = "US",
    independent_sites: list[str] | None = None,
    independent_items: list[dict[str, Any]] | None = None,
    consensus_provider: str | Callable[..., Any] | None = "firecrawl",
    maps_sites: list[str] | None = None,
    allow_repair: bool = True,
    thresholds: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Run the independent Truth Gate over an organic SERP envelope (live or queued task_get)."""
    if not isinstance(envelope, dict):
        return envelope
    tasks = envelope.get("tasks")
    if not isinstance(tasks, list):
        return envelope

    for task in tasks:
        if not isinstance(task, dict):
            continue
        t_data = task.get("data") if isinstance(task.get("data"), dict) else {}
        sent_resolve = t_data.get("resolve_poisoned_results") is True
        task_kw = keyword or (t_data.get("keyword") if isinstance(t_data.get("keyword"), str) else None)
        task_loc = location_name or (t_data.get("location_name") if isinstance(t_data.get("location_name"), str) else None)
        for res_obj in task.get("result") or []:
            if not isinstance(res_obj, dict) or not isinstance(res_obj.get("serp_integrity"), dict):
                continue
            integrity = res_obj["serp_integrity"]
            kw = task_kw or (res_obj.get("keyword") if isinstance(res_obj.get("keyword"), str) else "")
            if not sent_resolve:
                verification = {"status": "resolve_off", "consensus_passed": False}
                integrity["verification"] = verification
                stamp_verified(integrity, verification=verification, unprotected=True)
                continue

            ind_view = fetch_independent_view(
                kw,
                location_name=task_loc,
                country=country,
                independent_sites=independent_sites,
                independent_items=independent_items,
                consensus_provider=consensus_provider,
                timeout=timeout,
            )
            dfs_top10 = organic_site_keys(res_obj, limit=10)
            from_web = int((integrity.get("decoy") or {}).get("from_web_count") or 0)

            if not ind_view.get("ok"):
                verification = {
                    "status": "independent_unavailable",
                    "consensus_passed": False,
                    "provider": ind_view.get("provider"),
                    "reason": ind_view.get("error") or "independent view unavailable",
                    "latency_ms": ind_view.get("latency_ms", 0.0),
                    "credits_estimated": ind_view.get("credits_estimated", 0),
                }
                integrity["verification"] = verification
                stamp_verified(integrity, verification=verification, unprotected=False)
                continue

            consensus = evaluate_independent_consensus(
                dfs_top10,
                ind_view["sites"],
                from_web_count=from_web,
                thresholds=thresholds,
            )
            decoy = assess_decoy_signals(
                res_obj,
                task_data=t_data,
                independent_sites=ind_view["sites"],
                maps_sites=maps_sites,
                consensus_result=consensus,
            )
            apply_decoy_assessment(integrity, decoy)

            repaired_meta: dict[str, Any] | None = None
            if consensus["passed"] and integrity.get("status") == "clean":
                verification = {
                    "status": "independent_consensus",
                    "consensus_passed": True,
                    "provider": ind_view.get("provider"),
                    "consensus": consensus,
                    "latency_ms": ind_view.get("latency_ms", 0.0),
                    "credits_estimated": ind_view.get("credits_estimated", 0),
                }
            elif allow_repair:
                repair = try_aio_corroborated_repair(res_obj, ind_view, thresholds=thresholds)
                if repair.get("repaired"):
                    repaired_meta = repair
                    new_top10 = organic_site_keys(res_obj, limit=10)
                    new_consensus = evaluate_independent_consensus(
                        new_top10,
                        ind_view["sites"],
                        from_web_count=0,
                        thresholds=thresholds,
                    )
                    new_decoy = assess_decoy_signals(
                        res_obj,
                        task_data=t_data,
                        independent_sites=ind_view["sites"],
                        maps_sites=maps_sites,
                        consensus_result=new_consensus,
                    )
                    integrity["status"] = "clean"
                    integrity["reasons"] = []
                    integrity["organic_count"] = len(new_top10)
                    integrity["repaired_from_dfs_top10"] = repair["original_dfs_top10"]
                    integrity["repair"] = repair
                    apply_decoy_assessment(integrity, new_decoy)
                    verification = {
                        "status": "repaired_from_independent",
                        "consensus_passed": bool(new_consensus["passed"]),
                        "provider": ind_view.get("provider"),
                        "consensus": new_consensus,
                        "prior_consensus": consensus,
                        "repair": repair,
                        "latency_ms": ind_view.get("latency_ms", 0.0),
                        "credits_estimated": ind_view.get("credits_estimated", 0),
                    }
                else:
                    verification = {
                        "status": "consensus_disagreed",
                        "consensus_passed": False,
                        "provider": ind_view.get("provider"),
                        "consensus": consensus,
                        "repair_attempt": repair,
                        "reason": "; ".join(consensus["reasons"]) or "independent consensus disagreed",
                        "latency_ms": ind_view.get("latency_ms", 0.0),
                        "credits_estimated": ind_view.get("credits_estimated", 0),
                    }
            else:
                verification = {
                    "status": "consensus_disagreed",
                    "consensus_passed": False,
                    "provider": ind_view.get("provider"),
                    "consensus": consensus,
                    "reason": "; ".join(consensus["reasons"]) or "independent consensus disagreed",
                    "latency_ms": ind_view.get("latency_ms", 0.0),
                    "credits_estimated": ind_view.get("credits_estimated", 0),
                }

            integrity["verification"] = verification
            if integrity.get("status") == "suspect_decoy" and not verification.get("consensus_passed"):
                integrity["unresolved"] = True
                integrity["detected_status"] = "suspect_decoy"
                integrity["status"] = "unverified_decoy"
            stamp_verified(integrity, verification=verification, unprotected=False)
            if repaired_meta and integrity.get("verified"):
                integrity.pop("unresolved", None)
    return envelope
