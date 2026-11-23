"""Organic SERP redirect-wrapper unmasking, SearchGuard degradation and decoy-SERP detection."""

from __future__ import annotations

import math
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
_GOOGLE_WRAPPER_HOST_RE = re.compile(
    r"^(?:www\.)?google\.(?:com|[a-z]{2}|(?:co|com)\.[a-z]{2})$",
    re.I,
)
_GOTO_URL_RE = re.compile(
    r"^https?://(?:www\.)?google\.(?:com|[a-z]{2}|(?:co|com)\.[a-z]{2})/(?:goto|url)\?",
    re.I,
)


def _is_google_wrapper_host(value: Any) -> bool:
    """True only for bare/www Google search hosts (google.com, www.google.co.uk), not product subdomains."""
    if not isinstance(value, str) or not value.strip():
        return False
    raw = value.strip().lower()
    host = (urlparse(raw).hostname or "") if "://" in raw else raw.split("/", 1)[0]
    host = host.split(":", 1)[0].rstrip(".")
    return bool(host and _GOOGLE_WRAPPER_HOST_RE.match(host))

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
_HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})$", re.I)

# Closed allowlist: SERP `website_name` first segment -> registrable domain.
# Only platforms whose display name maps to exactly one domain belong here.
# Anything else (a business name) stays unresolved; never slug-guess a domain.
WEBSITE_NAME_PLATFORMS: dict[str, str] = {
    "reddit": "reddit.com", "youtube": "youtube.com", "facebook": "facebook.com",
    "instagram": "instagram.com", "x": "x.com", "twitter": "x.com",
    "linkedin": "linkedin.com", "tiktok": "tiktok.com", "yelp": "yelp.com",
    "nextdoor": "nextdoor.com", "quora": "quora.com", "pinterest": "pinterest.com",
    "tripadvisor": "tripadvisor.com", "medium": "medium.com",
}
_PLATFORM_DOMAINS = frozenset(WEBSITE_NAME_PLATFORMS.values()) | {"twitter.com"}
_WEBSITE_NAME_SPLIT_RE = re.compile(r"\s*[\u00b7\u2022|]\s*")
_ENGAGEMENT_HINT_RE = re.compile(r"\b(comments?|likes?|views?|followers?|reactions?|ago|subscribers?)\b", re.I)
_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9._-]{2,40}$")
_SUBREDDIT_RE = re.compile(r"^r/([A-Za-z0-9_]{2,25})$")
_HANDLE_PLATFORMS = frozenset({"instagram.com", "tiktok.com", "x.com", "medium.com"})


def _clean_path_hint(hint: Any) -> str | None:
    """Drop empty hints and engagement breadcrumb text ('30+ comments', '2 years ago')."""
    if not isinstance(hint, str) or not hint.strip() or _ENGAGEMENT_HINT_RE.search(hint):
        return None
    return hint.strip()


def website_name_target(website_name: Any) -> dict[str, Any] | None:
    """Resolve a website_name-only SERP item ('Reddit \u00b7 r/Phoenixville') to a platform domain + hint.

    Returns None unless the leading segment is an allowlisted platform (WEBSITE_NAME_PLATFORMS).
    The qualifier after the separator becomes a path hint for search/scoring; it is never used
    to construct a final URL (the ranked thread is not the subreddit front page).
    """
    if not isinstance(website_name, str) or not website_name.strip():
        return None
    flat = website_name.replace("\xa0", " ").strip()
    parts = [p.strip() for p in _WEBSITE_NAME_SPLIT_RE.split(flat) if p.strip()]
    if not parts:
        return None
    platform, qualifier = parts[0], (" \u00b7 ".join(parts[1:]) or None)
    domain = WEBSITE_NAME_PLATFORMS.get(platform.casefold())
    if domain is None:
        return None
    path_hint = None
    if qualifier and not _ENGAGEMENT_HINT_RE.search(qualifier):
        subreddit = _SUBREDDIT_RE.match(qualifier)
        if domain == "reddit.com" and subreddit:
            path_hint = f"r/{subreddit.group(1)}"
        elif domain in _HANDLE_PLATFORMS and _HANDLE_RE.match(qualifier):
            path_hint = qualifier.lstrip("@")
        else:
            path_hint = qualifier  # page / channel / company name: search term only
    return {"target": domain, "domain": domain, "path_hint": path_hint, "resolution_basis": "website_name"}


def _platform_hint(domain: Any, website_name: Any) -> str | None:
    """website_name qualifier hint when `domain` is the same allowlisted platform the name maps to."""
    key = site_key(domain)
    if key not in _PLATFORM_DOMAINS:
        return None
    named = website_name_target(website_name)
    if not named:
        return None
    same = named["domain"] == key or {named["domain"], key} == {"x.com", "twitter.com"}
    return named["path_hint"] if same else None


def is_goto_wrapped_url(url: Any) -> bool:
    """Return True when a URL is an opaque Google redirect wrapper (/goto or /url)."""
    if not isinstance(url, str) or not url:
        return False
    stripped = url.strip()
    return stripped.lower().startswith(GOTO_URL_PREFIXES) or bool(_GOTO_URL_RE.match(stripped))


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
    - breadcrumb '30+ comments · 2 years ago', website_name 'Reddit · r/Phoenixville'
      -> {'host': None, 'domain': 'reddit.com', 'resolved_domain': 'reddit.com',
          'path_hint': 'r/Phoenixville', 'resolution_basis': 'website_name'}

    Engagement text ('30+ comments', '2 years ago') never becomes a path hint. A breadcrumb
    without a host falls back to the closed WEBSITE_NAME_PLATFORMS allowlist only; business
    names are never slug-guessed into domains.
    """
    result: dict[str, str | None] = {
        "host": None,
        "domain": None,
        "resolved_domain": None,
        "path_hint": None,
        "resolution_basis": None,
        "website_name": website_name.strip() if isinstance(website_name, str) and website_name.strip() else None,
    }
    parts: list[str] = []
    if isinstance(breadcrumb, str) and breadcrumb.strip():
        raw = _SCHEME_PREFIX_RE.sub("", breadcrumb.strip()).strip()
        parts = [segment.strip() for segment in _BREADCRUMB_SPLIT_RE.split(raw) if segment.strip()]

    if parts:
        candidate_host = parts[0].split(":", 1)[0].lower().rstrip(".")
        if candidate_host and _HOST_RE.match(candidate_host) and not _is_google_wrapper_host(candidate_host):
            result["host"] = candidate_host
            result["domain"] = candidate_host.removeprefix("www.")
            result["resolved_domain"] = result["domain"]
            result["resolution_basis"] = "breadcrumb"
        cleaned_segments: list[str] = []
        for seg in parts[1:]:
            stripped = seg.strip().rstrip(".\u2026").strip()
            if stripped and stripped not in {"...", "\u2026"}:
                cleaned_segments.append(stripped)
        if cleaned_segments:
            result["path_hint"] = _clean_path_hint(cleaned_segments[-1])

    if result["host"] is None:
        named = website_name_target(website_name)
        if named:
            result["domain"] = named["domain"]
            result["resolved_domain"] = named["domain"]
            result["path_hint"] = named["path_hint"]
            result["resolution_basis"] = "website_name"
    elif result["path_hint"] is None:
        result["path_hint"] = _platform_hint(result["host"], website_name)
    return result


def normalize_organic_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize an organic SERP item in-place when Google wraps its URL in /goto."""
    if not isinstance(item, dict) or item.get("type") != "organic":
        return item

    url = item.get("url")
    current_domain = (item.get("domain") or "").strip().lower()
    url_host = (
        (urlparse(url.strip()).hostname or "").lower().rstrip(".")
        if isinstance(url, str) and "://" in url
        else ""
    )
    url_is_missing_or_google = (
        not isinstance(url, str)
        or not url.strip()
        or is_goto_wrapped_url(url)
        or _is_google_wrapper_host(url)
    )
    wrapped = is_goto_wrapped_url(url) or (
        _is_google_wrapper_host(current_domain)
        and bool(item.get("breadcrumb"))
        and url_is_missing_or_google
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
            item["resolution_basis"] = "breadcrumb"
        elif current_domain and not _is_google_wrapper_host(current_domain):
            item["resolved_domain"] = current_domain.removeprefix("www.")
            item["resolution_basis"] = "domain"
        elif target["resolution_basis"] == "website_name":
            # Allowlisted platform only (WEBSITE_NAME_PLATFORMS); domain stays the raw wrapper host.
            item["resolved_domain"] = target["resolved_domain"]
            item["resolution_basis"] = "website_name"
        else:
            item["resolved_domain"] = None
        hint = target["path_hint"]
        if not hint and item.get("resolved_domain"):
            hint = _platform_hint(item["resolved_domain"], item.get("website_name"))
        if hint:
            item["breadcrumb_path_hint"] = hint
    else:
        item.setdefault("goto_wrapped", False)
        if (
            (not current_domain or _is_google_wrapper_host(current_domain))
            and url_host
            and not _is_google_wrapper_host(url_host)
        ):
            if current_domain and "raw_domain" not in item:
                item["raw_domain"] = item.get("domain")
            item["domain"] = url_host
            item["resolved_domain"] = url_host.removeprefix("www.")
        elif current_domain:
            item.setdefault("resolved_domain", current_domain.removeprefix("www."))
        else:
            item.setdefault("resolved_domain", None)
        if not item.get("breadcrumb_path_hint") and item.get("resolved_domain"):
            hint = _platform_hint(item["resolved_domain"], item.get("website_name"))
            if hint:
                item["breadcrumb_path_hint"] = hint
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
        and not _is_google_wrapper_host(item["resolved_domain"])
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

    Order: resolved_domain, then a non-Google-wrapper domain or URL, then the breadcrumb host,
    then a `website_name` slug such as `reddit` (Google sometimes shows only a site name).
    Never returns bare `google.<tld>` for a wrapped result, while preserving real Google product
    subdomains such as `developers.google.com` or `cloud.google.com`.
    """
    if not isinstance(entry, dict):
        return None
    for candidate in (entry.get("resolved_domain"), entry.get("domain"), entry.get("url")):
        if is_goto_wrapped_url(candidate) or _is_google_wrapper_host(candidate):
            continue
        key = site_key(candidate)
        if key:
            return key
    target = extract_breadcrumb_target(entry.get("breadcrumb"), entry.get("website_name"))
    host_or_dom = target.get("host") or target.get("resolved_domain")
    if not _is_google_wrapper_host(host_or_dom):
        key = site_key(host_or_dom)
        if key:
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


def _positive_depth(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return int(math.ceil(value)) if value > 0 else None


def _aio_top20_complete(
    result_block: dict[str, Any],
    task_data: dict[str, Any] | None,
    organic_count: int,
) -> tuple[bool, int | None]:
    """(top-20 comparison fully powered?, requested depth) for the AIO overlap check.

    A pull shallower than 20 cannot show AI Overview citations that rank 11-20, so its
    top-20 overlap is truncated by construction. The requested depth (task echo first,
    then the result block) decides when known; otherwise the returned organic count is
    the only depth evidence available.
    """
    requested = _positive_depth(task_data.get("depth")) if isinstance(task_data, dict) else None
    if requested is None and isinstance(result_block, dict):
        requested = _positive_depth(result_block.get("depth"))
    if requested is not None:
        return requested >= 20, requested
    return organic_count >= 20, None


def _decoy_reason(name: str, sig: dict[str, Any]) -> str:
    if name == "aio_organic_disjoint":
        return (f"AI Overview cites {sig.get('reference_sites')} sites but only {sig.get('overlap')} of them "
                f"appear in organic top {sig.get('organic_sites_compared') or 20} "
                "(AIO stays real while organics are swapped)")
    if name == "pull_instability":
        return f"Top-10 site overlap vs an independent second pull is {sig.get('overlap')}"
    if name == "incumbent_mismatch":
        return f"Top-10 site overlap vs known incumbents is {sig.get('overlap')}"
    if name == "independent_consensus":
        reasons = sig.get("reasons") or []
        detail = "; ".join(reasons) if reasons else f"jaccard={sig.get('jaccard')}"
        return f"Independent provider consensus failed ({detail})"
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
    independent_sites: list[str] | None = None,
    maps_sites: list[str] | None = None,
    consensus_result: dict[str, Any] | None = None,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Score decoy-SERP signals for one organic result block (offline; extra evidence optional).

    Signals: AI Overview reference overlap with organic top 20 (needs >= 3 cited sites; on a
    pull shallower than 20 it is diagnostic only when independent consensus passes, because
    citations ranking 11-20 are unobservable), second-pull top-10 overlap, caller-known incumbents, independent cross-provider consensus,
    and optional local Google Maps diagnostic overlap. Result age and Labs traffic were
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
    if len(aio_sites) >= int(th["aio_min_reference_sites"]) and (top20 or organic):
        ratio = overlap_ratio([_brand(k) for k in aio_sites], [_brand(k) for k in top20])
        top20_complete, requested_depth = _aio_top20_complete(result_block, task_data, len(organic))
        signals["aio_organic_disjoint"] = {"fired": ratio is not None and ratio <= th["aio_overlap_max"],
                                           "overlap": ratio, "reference_sites": len(aio_sites),
                                           "threshold": th["aio_overlap_max"],
                                           "organic_sites_compared": len(top20),
                                           "requested_depth": requested_depth,
                                           "top20_complete": top20_complete}

    if second_pull_sites is not None and top10:
        ratio = overlap_ratio(top10, list(second_pull_sites))
        signals["pull_instability"] = {"fired": ratio is not None and ratio <= th["pull_overlap_max"],
                                       "overlap": ratio, "threshold": th["pull_overlap_max"]}
    if incumbent_sites and (top10 or organic):
        inc_keys = [k for k in (site_key(s) for s in incumbent_sites) if k]
        if inc_keys:
            ratio = overlap_ratio(top10, inc_keys) if top10 else 0.0
            signals["incumbent_mismatch"] = {"fired": ratio is not None and ratio <= th["incumbent_overlap_max"],
                                             "overlap": ratio, "threshold": th["incumbent_overlap_max"]}
    if consensus_result is not None:
        signals["independent_consensus"] = {
            "fired": consensus_result.get("passed") is not True,
            "mode": consensus_result.get("mode"),
            "jaccard": consensus_result.get("jaccard"),
            "rank1_match": consensus_result.get("rank1_match"),
            "top3_overlap": consensus_result.get("top3_overlap"),
            "top5_overlap": consensus_result.get("top5_overlap"),
            "shared_count": consensus_result.get("shared_count"),
            "reasons": list(consensus_result.get("reasons") or []),
        }
    elif independent_sites is not None and (top10 or organic):
        from .truth_gate import evaluate_independent_consensus

        cons = evaluate_independent_consensus(top10, list(independent_sites), from_web_count=from_web)
        signals["independent_consensus"] = {
            "fired": cons.get("passed") is not True,
            "mode": cons.get("mode"),
            "jaccard": cons.get("jaccard"),
            "rank1_match": cons.get("rank1_match"),
            "top3_overlap": cons.get("top3_overlap"),
            "top5_overlap": cons.get("top5_overlap"),
            "shared_count": cons.get("shared_count"),
            "reasons": list(cons.get("reasons") or []),
        }
    if maps_sites is not None and top10:
        maps_keys = [k for k in (site_key(s) for s in maps_sites) if k]
        maps_set = set(maps_keys)
        hits = sum(1 for k in top10 if k in maps_set)
        # Informational local corroboration only (M1 proved directory-heavy clean local SERPs
        # can have 0 Maps hits while partial local decoys can have 2-3 hits).
        signals["local_maps_overlap"] = {
            "fired": False,
            "diagnostic": True,
            "hits": hits,
            "organic_count": len(top10),
            "maps_count": len(maps_set),
            "overlap": round(hits / max(1, len(top10)), 4),
        }

    aio_sig = signals.get("aio_organic_disjoint")
    cons_sig = signals.get("independent_consensus")
    if (
        isinstance(aio_sig, dict)
        and aio_sig.get("fired") is True
        and aio_sig.get("top20_complete") is False
        and isinstance(cons_sig, dict)
        and cons_sig.get("fired") is False
    ):
        # Shallow pull: AIO citations ranking 11-20 are unobservable, so the truncated
        # overlap cannot prove disjointness, and independent consensus (fired == False
        # means passed) already validated the returned top 10 directly. Without a
        # consensus pass the AIO backstop keeps firing exactly as before.
        aio_sig["fired"] = False
        aio_sig["diagnostic"] = True
        aio_sig["suppressed_reason"] = "insufficient_depth_for_top20_with_independent_consensus"

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


# Truth Gate verification outcomes: provider-only signals (from_web > 0, from_web == 0, or
# same-provider double-pull) are NEVER sufficient for verified=True on their own.
VERIFIED_BASES = frozenset({"independent_consensus", "independent_consensus_repaired"})
_CONSENSUS_PASSED_STATUSES = frozenset({
    "independent_consensus",
    "repaired_on_repull",
    "repaired_from_independent",
})
_VERIFY_DISAGREED = frozenset({
    "unstable", "verify_pull_suspect", "disagreed", "consensus_disagreed",
})
_VERIFY_FAILED = frozenset({
    "failed", "verify_failed", "skipped_budget", "independent_unavailable",
})
_VERIFY_NOT_RUN = frozenset({
    None, "not_run", "not_needed", "skipped_disabled", "stable", "resolved_on_verify",
})


def stamp_verified(
    integrity: dict[str, Any],
    *,
    verification: dict[str, Any] | None = None,
    unprotected: bool = False,
) -> dict[str, Any]:
    """Set serp_integrity.verified (fail closed) requiring independent cross-provider consensus.

    Rejected (verified False) when:
    - the task was unprotected (resolve_poisoned_results not sent),
    - organic_count == 0,
    - serp_integrity.status != "clean",
    - any decoy signal fired,
    - independent consensus disagreed, failed, or was unavailable/not run.

    Neither a provider-claimed swap (from_web > 0) nor from_web == 0 is sufficient on its own.
    Only independent consensus grants verified=True, with verified_basis in:
    - `independent_consensus`: DataForSEO result passed consensus against an independent source;
    - `independent_consensus_repaired`: repaired via re-pull or AIO-corroborated independent source.
    """
    decoy = integrity.get("decoy") if isinstance(integrity.get("decoy"), dict) else {}
    status = integrity.get("status")
    vdict = verification if isinstance(verification, dict) else {}
    vstatus = vdict.get("status")
    reasons: list[str] = []
    if unprotected:
        reasons.append(f"unprotected: resolve_poisoned_results was not sent ({ALLOW_POISONED_ENV}=1)")
    if type(integrity.get("organic_count")) is int and integrity["organic_count"] <= 0:
        reasons.append("no parsed organic items in result block")
    if status != "clean":
        reasons.append(f"serp_integrity.status is {status}")
    if decoy.get("suspect") or decoy.get("fired"):
        reasons.append("decoy signals fired: " + ", ".join(decoy.get("fired") or []))
    if vstatus in _VERIFY_DISAGREED:
        detail = vdict.get("reason")
        reasons.append(
            f"independent verification disagreed (verification: {vstatus}"
            + (f", {detail})" if detail else ")")
        )
    elif vstatus in _VERIFY_FAILED:
        detail = vdict.get("reason")
        reasons.append(
            f"independent verification failed (verification: {vstatus}"
            + (f", {detail})" if detail else ")")
        )

    basis: str | None = None
    if not reasons:
        if vstatus in _CONSENSUS_PASSED_STATUSES and vdict.get("consensus_passed") is True:
            if vstatus == "independent_consensus":
                basis = "independent_consensus"
            else:
                basis = "independent_consensus_repaired"
        elif vstatus in _VERIFY_NOT_RUN:
            reasons.append(
                "no independent consensus verification (provider-only signals are not sufficient)"
            )
        else:
            reasons.append(f"no independent verification basis (verification: {vstatus})")

    verified = not reasons and basis is not None
    integrity["integrity"] = "unprotected" if unprotected else "protected"
    integrity["verified"] = verified
    integrity["verified_basis"] = basis if verified else None
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
