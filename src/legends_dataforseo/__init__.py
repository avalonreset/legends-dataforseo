"""Public, agent-neutral DataForSEO API v3 transport."""

__version__ = "0.1.12"

from .client import (
    API_ROOT, ApiError, CostLimitError, CredentialError, Credentials, RouteError,
    SERP_TIMEOUT_S, UnverifiedSerpError, api_request, business, competitors, credential_status, demand, estimate_cost,
    ledger_path, load_credentials, load_routes, maps, ranked_keywords,
    relevant_pages, resolve_poisoned_default, route_for, serp, site_traffic, total_cost, write_json,
)
from .documentation import DocumentationError, docs_index, docs_read, docs_search, docs_sections
from .output import response_summary, response_view
from .serp_integrity import (
    ALLOW_POISONED_ENV,
    DECOY_THRESHOLDS,
    allow_poisoned,
    apply_decoy_assessment,
    assess_decoy_signals,
    assess_serp_integrity,
    extract_breadcrumb_target,
    is_goto_wrapped_url,
    normalize_organic_item,
    normalize_serp_envelope,
    organic_item_site_key,
    organic_site_keys,
    overlap_ratio,
    site_key,
    stamp_envelope_verified,
    stamp_verified,
    WEBSITE_NAME_PLATFORMS,
    website_name_target,
)
from .tasks import wait_task

__all__ = [
    "API_ROOT", "ApiError", "CostLimitError", "CredentialError", "Credentials",
    "RouteError", "api_request", "business", "competitors", "credential_status",
    "demand", "estimate_cost", "ledger_path", "load_credentials", "load_routes",
    "maps", "ranked_keywords", "relevant_pages", "route_for", "serp", "total_cost",
    "write_json", "__version__",
    "DocumentationError", "docs_index", "docs_read", "docs_search", "docs_sections",
    "response_summary", "response_view", "wait_task",
    "assess_serp_integrity", "extract_breadcrumb_target", "is_goto_wrapped_url",
    "normalize_organic_item", "normalize_serp_envelope",
    "DECOY_THRESHOLDS", "apply_decoy_assessment", "assess_decoy_signals", "organic_item_site_key", "organic_site_keys",
    "overlap_ratio", "site_key", "resolve_poisoned_default", "site_traffic",
    "ALLOW_POISONED_ENV", "SERP_TIMEOUT_S", "UnverifiedSerpError", "allow_poisoned",
    "stamp_envelope_verified", "stamp_verified", "WEBSITE_NAME_PLATFORMS", "website_name_target",
]
