"""Public, agent-neutral DataForSEO API v3 transport."""

__version__ = "0.1.11"

from .client import (
    API_ROOT, ApiError, CostLimitError, CredentialError, Credentials, RouteError,
    STIPEND_SKUS, api_request, business, business_lookup_hybrid, can_use_stipend,
    check_and_reserve, competitors, credential_status, demand, estimate_cost,
    google_custom_search, google_ledger_path, google_pagespeed_insights,
    google_places_lookup, ledger_path, load_credentials, load_routes, maps,
    pagespeed_hybrid, ranked_keywords, redact_google_key, relevant_pages,
    reserve_stipend, resolve_google_api_key, route_for, serp, stipend_status,
    total_cost, write_json,
)
from .documentation import DocumentationError, docs_index, docs_read, docs_search, docs_sections
from .output import response_summary, response_view
from .serp_integrity import (
    assess_serp_integrity,
    extract_breadcrumb_target,
    is_goto_wrapped_url,
    normalize_organic_item,
    normalize_serp_envelope,
)
from .tasks import wait_task

__all__ = [
    "API_ROOT", "ApiError", "CostLimitError", "CredentialError", "Credentials",
    "RouteError", "STIPEND_SKUS", "api_request", "business", "business_lookup_hybrid",
    "can_use_stipend", "check_and_reserve", "competitors", "credential_status",
    "demand", "estimate_cost", "google_custom_search", "google_ledger_path",
    "google_pagespeed_insights", "google_places_lookup", "ledger_path",
    "load_credentials", "load_routes", "maps", "pagespeed_hybrid",
    "ranked_keywords", "redact_google_key", "relevant_pages", "reserve_stipend",
    "resolve_google_api_key", "route_for", "serp", "stipend_status", "total_cost",
    "write_json", "__version__",
    "DocumentationError", "docs_index", "docs_read", "docs_search", "docs_sections",
    "response_summary", "response_view", "wait_task",
    "assess_serp_integrity", "extract_breadcrumb_target", "is_goto_wrapped_url",
    "normalize_organic_item", "normalize_serp_envelope",
]
