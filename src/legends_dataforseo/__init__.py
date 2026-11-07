"""Public, agent-neutral DataForSEO API v3 transport."""

__version__ = "0.1.9"

from .client import (
    API_ROOT, ApiError, CostLimitError, CredentialError, Credentials, RouteError,
    api_request, business, competitors, credential_status, demand, estimate_cost,
    ledger_path, load_credentials, load_routes, maps, ranked_keywords,
    relevant_pages, route_for, serp, total_cost, write_json,
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
    "RouteError", "api_request", "business", "competitors", "credential_status",
    "demand", "estimate_cost", "ledger_path", "load_credentials", "load_routes",
    "maps", "ranked_keywords", "relevant_pages", "route_for", "serp", "total_cost",
    "write_json", "__version__",
    "DocumentationError", "docs_index", "docs_read", "docs_search", "docs_sections",
    "response_summary", "response_view", "wait_task",
    "assess_serp_integrity", "extract_breadcrumb_target", "is_goto_wrapped_url",
    "normalize_organic_item", "normalize_serp_envelope",
]
