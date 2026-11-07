"""Organic SERP redirect-wrapper unmasking and SearchGuard degradation detection."""

from __future__ import annotations

import re
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

    return {
        "status": "suspect_poisoned" if reasons else "clean",
        "organic_count": organic_count,
        "goto_wrapped_count": goto_wrapped_count,
        "resolved_domain_count": resolved_domain_count,
        "canary_hits": canary_hits,
        "used_location_coordinate": used_coord,
        "reasons": reasons,
    }


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
