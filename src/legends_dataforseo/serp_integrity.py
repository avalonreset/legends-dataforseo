"""Organic SERP redirect-wrapper unmasking, SearchGuard degradation and decoy-SERP detection."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

GOTO_URL_PREFIXES = (
    "https://google.com/goto?",
    "https://www.google.com/goto?",
    "http://google.com/goto?",
    "http://www.google.com/goto?",
    "https://google.com/url?",
    "https://www.google.com/url?",
    "http://google.com/url?",
    "http://www.google.com/url?",
)

DEGRADED_SERP_CANARY_DOMAINS = frozenset({
    "wikipedia.org",
    "en.wikipedia.org",
    "bls.gov",
    "www.bls.gov",
    "onetonline.org",
    "www.onetonline.org",
    "britannica.com",
    "www.britannica.com",
    "dictionary.com",
    "www.dictionary.com",
    "merriam-webster.com",
    "www.merriam-webster.com",
})

INFORMATIONAL_QUERY_TOKENS = frozenset({
    "wikipedia",
    "wiki",
    "bls",
    "bureau of labor",
    "salary",
    "salaries",
    "outlook",
    "definition",
    "meaning",
    "what is",
    "who is",
    "history of",
    "how to become",
})

_BREADCRUMB_SPLIT_RE = re.compile(r"\s*(?:\u203a|\u00bb|\ufffd|>|/)\s*")
_SCHEME_PREFIX_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.I)


def is_goto_wrapped_url(url: Any) -> bool:
    """Return True when a URL is an opaque Google redirect wrapper (/goto or /url)."""
    if not isinstance(url, str) or not url:
        return False
    lowered = url.strip().lower()
    return lowered.startswith(GOTO_URL_PREFIXES)


def extract_breadcrumb_target(
    breadcrumb: Any,
    website_name: Any = None,
) -> dict[str, str | None]:
    """Extract the real hostname, root domain, and slug hint from a SERP breadcrumb.

    Examples:
    - 'https://www.gattilaw.com › Locations'
      -> {'host': 'www.gattilaw.com', 'domain': 'gattilaw.com', 'path_hint': 'Locations'}
    - 'https://warrenallen.com › choosing-personal-injury-atto...'
      -> {'host': 'warrenallen.com', 'domain': 'warrenallen.com',
          'path_hint': 'choosing-personal-injury-atto'}
    """
    result: dict[str, str | None] = {
        "host": None,
        "domain": None,
        "path_hint": None,
        "website_name": website_name.strip() if isinstance(website_name, str) and website_name.strip() else None,
    }
    if not isinstance(breadcrumb, str) or not breadcrumb.strip():
        return result

    raw = _SCHEME_PREFIX_RE.sub("", breadcrumb.strip()).strip()
    parts = [segment.strip() for segment in _BREADCRUMB_SPLIT_RE.split(raw) if segment.strip()]
    if not parts:
        return result

    candidate_host = parts[0].split(":", 1)[0].lower().rstrip(".")
    trail = parts[1:]

    if candidate_host and _HOST_RE.match(candidate_host) and candidate_host not in {"google.com", "www.google.com"}:
        result["host"] = candidate_host
        result["domain"] = candidate_host.removeprefix("www.")

    cleaned_segments: list[str] = []
    for seg in trail:
        stripped = seg.strip().rstrip(".\u2026").strip()
        if stripped and stripped not in {"...", "\u2026"}:
            cleaned_segments.append(stripped)

    if cleaned_segments:
        result["path_hint"] = cleaned_segments[-1]
    return result


def normalize_organic_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize an organic SERP item in-place when Google wraps its URL in /goto."""
    if not isinstance(item, dict) or item.get("type") != "organic":
        return item

    url = item.get("url")
    current_domain = (item.get("domain") or "").strip().lower()
    wrapped = is_goto_wrapped_url(url) or (
        current_domain in {"google.com", "www.google.com"} and bool(item.get("breadcrumb"))
    )
    if wrapped:
        target = extract_breadcrumb_target(item.get("breadcrumb"), item.get("website_name"))
        if "raw_domain" not in item:
            item["raw_domain"] = item.get("domain")
        if "raw_url" not in item and isinstance(url, str):
            item["raw_url"] = url
        item["goto_wrapped"] = True
        if target["host"]:
            item["domain"] = target["host"]
            item["resolved_domain"] = target["domain"]
            item["breadcrumb_host"] = target["host"]
        elif current_domain not in {"google.com", "www.google.com", ""}:
            item["resolved_domain"] = current_domain.removeprefix("www.")
        else:
            item["resolved_domain"] = None
        if target["path_hint"]:
            item["breadcrumb_path_hint"] = target["path_hint"]
    else:
        item.setdefault("goto_wrapped", False)
        if current_domain:
            item.setdefault("resolved_domain", current_domain.removeprefix("www."))
        else:
            item.setdefault("resolved_domain", None)
    return item


def _is_informational_query(keyword: str) -> bool:
    lowered = keyword.lower()
    return any(token in lowered for token in INFORMATIONAL_QUERY_TOKENS)


def _extract_city_token(task_data: dict[str, Any] | None, keyword: str) -> str | None:
    if isinstance(task_data, dict):
        loc_name = task_data.get("location_name")
        if isinstance(loc_name, str) and "," in loc_name:
            city = loc_name.split(",", 1)[0].strip().lower()
            if len(city) >= 3:
                return city
    return None


def assess_serp_integrity(
    result_block: dict[str, Any],
    *,
    task_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect a Google Organic result block, normalize /goto items, and detect SearchGuard poisoning."""
    items = result_block.get("items") if isinstance(result_block, dict) else None
    if not isinstance(items, list):
        return {
            "status": "clean",
            "organic_count": 0,
            "goto_wrapped_count": 0,
            "resolved_domain_count": 0,
            "canary_hits": [],
            "used_location_coordinate": False,
            "reasons": [],
            "decoy": assess_decoy_signals({}, task_data=task_data),
        }

    organic_items: list[dict[str, Any]] = []
    for entry in items:
        if isinstance(entry, dict) and entry.get("type") == "organic":
            normalize_organic_item(entry)
            organic_items.append(entry)

    organic_count = len(organic_items)
    goto_wrapped_count = sum(1 for item in organic_items if item.get("goto_wrapped") is True)
    resolved_domain_count = sum(
        1
        for item in organic_items
        if isinstance(item.get("resolved_domain"), str)
        and item["resolved_domain"]
        and item["resolved_domain"] not in {"google.com", "www.google.com"}
    )

    keyword = ""
    if isinstance(result_block.get("keyword"), str):
        keyword = result_block["keyword"]
    elif isinstance(task_data, dict) and isinstance(task_data.get("keyword"), str):
        keyword = task_data["keyword"]

    used_coord = bool(isinstance(task_data, dict) and task_data.get("location_coordinate"))
    informational = _is_informational_query(keyword)

    canary_hits: list[str] = []
    top_slice = organic_items[:5]
    for item in top_slice:
        dom = (item.get("resolved_domain") or item.get("domain") or "").strip().lower()
        base_dom = dom.removeprefix("www.")
        if dom in DEGRADED_SERP_CANARY_DOMAINS or base_dom in DEGRADED_SERP_CANARY_DOMAINS:
            if base_dom not in canary_hits:
                canary_hits.append(base_dom)

    reasons: list[str] = []
    if canary_hits and not informational:
        reasons.append(
            "Top-5 organic results contain generic encyclopedia/bureau canary domains: "
            + ", ".join(canary_hits)
        )

    city_token = _extract_city_token(task_data, keyword)
    if city_token and top_slice and not informational:
        city_mentions = 0
        for item in top_slice:
            blob = " ".join(
                str(item.get(field) or "")
                for field in ("title", "description", "breadcrumb", "website_name", "domain")
            ).lower()
            if city_token in blob:
                city_mentions += 1
        if city_mentions == 0 and (canary_hits or used_coord):
            reasons.append(
                f"Zero top-5 organic items reference target city '{city_token}' while degradation signals are present"
            )

    if used_coord and canary_hits:
        reasons.append(
            "location_coordinate was used on organic web search and triggered degraded index results; "
            "use location_name or location_code for organic web SERPs"
        )

    integrity = {
        "status": "suspect_poisoned" if reasons else "clean",
        "organic_count": organic_count,
        "goto_wrapped_count": goto_wrapped_count,
        "resolved_domain_count": resolved_domain_count,
        "canary_hits": canary_hits,
        "used_location_coordinate": used_coord,
        "reasons": reasons,
    }
    return apply_decoy_assessment(integrity, assess_decoy_signals(result_block, task_data=task_data))


def normalize_serp_envelope(
    envelope: dict[str, Any],
    *,
    endpoint: str | None = None,
) -> dict[str, Any]:
    """Normalize organic items and attach serp_integrity metadata on Google Organic envelopes."""
    if not isinstance(envelope, dict):
        return envelope
    tasks = envelope.get("tasks")
    if not isinstance(tasks, list):
        return envelope

    is_organic_endpoint = bool(
        isinstance(endpoint, str) and "/serp/google/organic/" in endpoint
    )
    for task in tasks:
        if not isinstance(task, dict):
            continue
        results = task.get("result")
        if not isinstance(results, list):
            continue
        task_data = task.get("data") if isinstance(task.get("data"), dict) else None
        for res_obj in results:
            if not isinstance(res_obj, dict):
                continue
            items = res_obj.get("items")
            if not isinstance(items, list):
                continue
            has_organic = any(isinstance(i, dict) and i.get("type") == "organic" for i in items)
            if has_organic or (is_organic_endpoint and items):
                res_obj["serp_integrity"] = assess_serp_integrity(res_obj, task_data=task_data)
    return envelope


# ---------------------------------------------------------------------------
# 0.1.12: decoy SERP detection (flavor 2: real page-1 winners replaced by
# shuffled back-half / stale low-traffic filler while the surface looks normal).
# ---------------------------------------------------------------------------

# Calibrated on the 2026-11-20 decoy bench (see CHANGELOG 0.1.12). A signal is
# only scored when enough evidence exists to compute it; absent evidence never
# counts as clean evidence.
DECOY_THRESHOLDS: dict[str, float] = {
    # AIO backstop (bench: decoys 0.0-0.6, real 0.8-1.0; P=R=1.0 on default rows, n=20).
    "aio_min_reference_sites": 3.0,
    "aio_overlap_max": 0.6,
    "pull_overlap_max": 0.5,
    "incumbent_overlap_max": 0.2,
    "min_signals": 1.0,
}

# The only way to send an organic SERP task without resolve_poisoned_results. Results are
# stamped serp_integrity.integrity == "unprotected" and verified == False.
ALLOW_POISONED_ENV = "LEGENDS_DFS_ALLOW_POISONED"


def allow_poisoned() -> bool:
    """True only when the deliberately ugly escape hatch LEGENDS_DFS_ALLOW_POISONED=1 is set."""
    return os.environ.get(ALLOW_POISONED_ENV, "").strip() == "1"

_TWO_LEVEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz", "org.nz", "govt.nz", "ac.nz", "co.jp",
    "com.br", "com.mx", "co.in", "co.za", "com.sg", "com.tr",
})


def site_key(domain_or_url: Any) -> str | None:
    """Registrable-ish site key: lowercased host without www and subdomains."""
    if not isinstance(domain_or_url, str) or not domain_or_url.strip():
        return None
    raw = domain_or_url.strip().lower()
    host = urlparse(raw).hostname if "://" in raw else raw.split("/", 1)[0]
    if not host:
        return None
    host = host.split(":", 1)[0].removeprefix("www.").strip(".")
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return host or None
    if ".".join(labels[-2:]) in _TWO_LEVEL_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


_GOOGLE_KEYS = {"google.com"}
_NAME_SPLIT_RE = re.compile(r"[\u00b7|\u2022\-\u2013\u2014:]")


def organic_item_site_key(entry: dict[str, Any]) -> str | None:
    """Site key for one organic item, robust to Google /goto wrapping.

    Order: resolved_domain, then a non-Google domain or URL, then the breadcrumb host, then a
    `website_name` slug such as `reddit` (Google sometimes shows only a site name).
    Never returns `google.com` for a wrapped result.
    """
    if not isinstance(entry, dict):
        return None
    for candidate in (entry.get("resolved_domain"), entry.get("domain"), entry.get("url")):
        if is_goto_wrapped_url(candidate):
            continue
        key = site_key(candidate)
        if key and key not in _GOOGLE_KEYS:
            return key
    target = extract_breadcrumb_target(entry.get("breadcrumb"), entry.get("website_name"))
    key = site_key(target.get("host"))
    if key and key not in _GOOGLE_KEYS:
        return key
    name = entry.get("website_name")
    if isinstance(name, str) and name.strip():
        head = _NAME_SPLIT_RE.split(name.replace("\u00a0", " "), maxsplit=1)[0]
        slug = re.sub(r"[^a-z0-9]", "", head.lower())
        if slug:
            return slug
    return None


def organic_site_keys(result_block: dict[str, Any], *, limit: int = 10) -> list[str]:
    """Ordered unique site keys of the first `limit` organic items."""
    keys: list[str] = []
    items = result_block.get("items") if isinstance(result_block, dict) else None
    for entry in items if isinstance(items, list) else []:
        if not isinstance(entry, dict) or entry.get("type") != "organic":
            continue
        key = organic_item_site_key(entry)
        if key and key not in keys:
            keys.append(key)
        if len(keys) >= limit:
            break
    return keys


def _collect_reference_sites(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        refs = node.get("references")
        if isinstance(refs, list):
            for ref in refs:
                if isinstance(ref, dict):
                    key = site_key(ref.get("domain") or ref.get("url"))
                    if key and key not in out:
                        out.append(key)
        for value in node.values():
            if isinstance(value, (dict, list)):
                _collect_reference_sites(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_reference_sites(value, out)


def _parse_dfs_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def overlap_ratio(a: list[str], b: list[str]) -> float | None:
    """Share of `a` present in `b` (None when `a` is empty)."""
    if not a:
        return None
    pool = set(b)
    return round(sum(1 for key in a if key in pool) / len(a), 4)


def _decide_decoy(signals: dict[str, Any], thresholds: dict[str, float]) -> tuple[bool, list[str]]:
    fired = [name for name, sig in signals.items() if isinstance(sig, dict) and sig.get("fired") is True]
    return len(fired) >= max(1, int(thresholds.get("min_signals", 1))), fired


def _decoy_reason(name: str, sig: dict[str, Any]) -> str:
    if name == "aio_organic_disjoint":
        return (f"AI Overview cites {sig.get('reference_sites')} sites but only {sig.get('overlap')} of them "
                "appear in organic top 20 (AIO stays real while organics are swapped)")
    if name == "pull_instability":
        return f"Top-10 site overlap vs an independent second pull is {sig.get('overlap')}"
    if name == "incumbent_mismatch":
        return f"Top-10 site overlap vs known incumbents is {sig.get('overlap')}"
    return name


def _brand(key: str) -> str:
    """First label of a site key, so `reddit.com` (AIO reference) matches a `reddit` website_name slug."""
    return key.split(".", 1)[0]


def assess_decoy_signals(
    result_block: dict[str, Any],
    *,
    task_data: dict[str, Any] | None = None,
    second_pull_sites: list[str] | None = None,
    incumbent_sites: list[str] | None = None,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Score decoy-SERP signals for one organic result block (offline; extra evidence optional).

    Signals: AI Overview reference overlap with organic top 20 (needs >= 3 cited sites),
    second-pull top-10 overlap, and caller-known incumbents. Result age and Labs traffic were
    tested on the 2026-11-20 bench and do not separate decoys, so they are not scored.
    """
    th = dict(DECOY_THRESHOLDS)
    if thresholds:
        th.update(thresholds)
    items = result_block.get("items") if isinstance(result_block, dict) else None
    items = items if isinstance(items, list) else []
    organic = [i for i in items if isinstance(i, dict) and i.get("type") == "organic"]
    from_web = sum(1 for i in organic if isinstance(i.get("checks"), list) and "from_web" in i["checks"])
    top10 = organic_site_keys(result_block, limit=10)
    top20 = organic_site_keys(result_block, limit=20)
    signals: dict[str, Any] = {}

    aio_sites: list[str] = []
    for entry in items:
        if isinstance(entry, dict) and entry.get("type") == "ai_overview":
            _collect_reference_sites(entry, aio_sites)
    if len(aio_sites) >= int(th["aio_min_reference_sites"]) and top20:
        ratio = overlap_ratio([_brand(k) for k in aio_sites], [_brand(k) for k in top20])
        signals["aio_organic_disjoint"] = {"fired": ratio is not None and ratio <= th["aio_overlap_max"],
                                           "overlap": ratio, "reference_sites": len(aio_sites),
                                           "threshold": th["aio_overlap_max"]}

    if second_pull_sites is not None and top10:
        ratio = overlap_ratio(top10, list(second_pull_sites))
        signals["pull_instability"] = {"fired": ratio is not None and ratio <= th["pull_overlap_max"],
                                       "overlap": ratio, "threshold": th["pull_overlap_max"]}
    if incumbent_sites and top10:
        ratio = overlap_ratio(top10, [k for k in (site_key(s) for s in incumbent_sites) if k])
        signals["incumbent_mismatch"] = {"fired": ratio is not None and ratio <= th["incumbent_overlap_max"],
                                         "overlap": ratio, "threshold": th["incumbent_overlap_max"]}

    suspect, fired = _decide_decoy(signals, th)
    resolve_requested = bool(isinstance(task_data, dict) and task_data.get("resolve_poisoned_results") is True)
    if from_web:
        provider = "resolved"
    elif resolve_requested:
        # DFS is fail-open: zero from_web means clean OR the reference fetch failed.
        provider = "requested_no_swap"
    else:
        provider = "not_requested"
    return {
        "suspect": suspect,
        "fired": fired,
        "signals": signals,
        "signals_evaluated": len(signals),
        "from_web_count": from_web,
        "provider_resolve": provider,
        "reasons": [_decoy_reason(name, signals[name]) for name in fired],
    }


def apply_decoy_assessment(integrity: dict[str, Any], decoy: dict[str, Any]) -> dict[str, Any]:
    """Merge a decoy assessment into serp_integrity; suspect_poisoned keeps precedence."""
    previous = integrity.get("decoy") if isinstance(integrity.get("decoy"), dict) else {}
    stale = set(previous.get("reasons") or [])
    integrity["reasons"] = [r for r in integrity.get("reasons") or [] if r not in stale]
    integrity["decoy"] = decoy
    if integrity.get("status") == "suspect_poisoned":
        return integrity
    if decoy.get("suspect"):
        integrity["status"] = "suspect_decoy"
        integrity["reasons"].extend(decoy["reasons"])
    elif integrity.get("status") == "suspect_decoy":
        integrity["status"] = "clean"
    return integrity


def stamp_verified(
    integrity: dict[str, Any],
    *,
    verification: dict[str, Any] | None = None,
    unprotected: bool = False,
) -> dict[str, Any]:
    """Set serp_integrity.verified (fail closed) plus the signals and reasons behind it.

    verified is True only when the provider resolve ran, the block is clean, no decoy signal
    fired, and the result was either provider-resolved (`from_web` swaps) or confirmed by a
    stable verification pull. DFS resolve is fail-open, so zero swaps alone never verifies.
    """
    decoy = integrity.get("decoy") if isinstance(integrity.get("decoy"), dict) else {}
    status = integrity.get("status")
    vstatus = (verification or {}).get("status")
    reasons: list[str] = []
    if unprotected:
        reasons.append(f"unprotected: resolve_poisoned_results was not sent ({ALLOW_POISONED_ENV}=1)")
    if status != "clean":
        reasons.append(f"serp_integrity.status is {status}")
    if decoy.get("suspect"):
        reasons.append("decoy signals fired: " + ", ".join(decoy.get("fired") or []))
    if decoy.get("provider_resolve") != "resolved" and vstatus != "stable":
        reasons.append("no from_web swaps and no stable verification pull "
                       f"(verification: {vstatus or 'not_run'}); DFS resolve is fail-open")
    verified = not reasons
    integrity["integrity"] = "unprotected" if unprotected else "protected"
    integrity["verified"] = verified
    integrity["verified_basis"] = (
        "provider_resolved" if verified and decoy.get("provider_resolve") == "resolved"
        else "stable_verification_pull" if verified else None
    )
    integrity["signals_used"] = sorted((decoy.get("signals") or {}).keys())
    integrity["unverified_reasons"] = reasons
    if verified:
        integrity.pop("unverified", None)
    else:
        integrity["unverified"] = True
    return integrity


def stamp_envelope_verified(
    envelope: dict[str, Any],
    *,
    verification: dict[str, Any] | None = None,
    resolve_sent: bool | None = None,
) -> dict[str, Any]:
    """Stamp every organic result block of a raw envelope (resolve_sent None: read task data echo)."""
    if not isinstance(envelope, dict):
        return envelope
    for task in envelope.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        data = task.get("data") if isinstance(task.get("data"), dict) else {}
        sent = resolve_sent if resolve_sent is not None else data.get("resolve_poisoned_results") is True
        for res_obj in task.get("result") or []:
            if isinstance(res_obj, dict) and isinstance(res_obj.get("serp_integrity"), dict):
                stamp_verified(res_obj["serp_integrity"], verification=verification, unprotected=not sent)
    return envelope
