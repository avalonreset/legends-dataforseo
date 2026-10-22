"""Synchronous API v3 transport. No retries, prompts, or account switching."""

from __future__ import annotations

import base64
import json
import math
import os
import re
import warnings
from http.client import HTTPException
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_ROOT = "https://api.dataforseo.com/v3"


class CredentialError(RuntimeError):
    """Credentials are missing or incomplete."""


class ApiError(RuntimeError):
    """Transport/envelope failure; task statuses remain caller-owned."""

    def __init__(self, message: str, *, response: dict[str, Any] | None = None,
                 http_status: int | None = None, request_may_have_completed: bool = False) -> None:
        super().__init__(message)
        self.response = response
        self.http_status = http_status
        self.request_may_have_completed = request_may_have_completed


class CostLimitError(ApiError):
    """A request was refused before HTTP by a local cost/confirmation guard."""


class RouteError(ValueError):
    """Invalid endpoint, route, or request shape."""


@dataclass(frozen=True)
class Credentials:
    login: str = field(repr=False)
    password: str = field(repr=False)
    source: str = "explicit"

    def __post_init__(self) -> None:
        if not isinstance(self.login, str) or not isinstance(self.password, str):
            raise CredentialError("Credentials must be nonempty strings.")
        if not self.login or not self.password or ":" in self.login:
            raise CredentialError("Credentials are incomplete or the login is invalid.")


def _windows_user_environment(name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
        return value if isinstance(value, str) and value else None
    except (ImportError, OSError):
        return None


def load_credentials() -> Credentials:
    """Read a complete process pair, otherwise a Windows user pair; never mix."""
    for lookup, source in ((os.environ.get, "process-env"),
                           (_windows_user_environment, "user-env")):
        login = lookup("DATAFORSEO_LOGIN") or lookup("DATAFORSEO_USERNAME")
        password = lookup("DATAFORSEO_PASSWORD")
        if login and password:
            return Credentials(login, password, source)
        if login or password:
            raise CredentialError("Incomplete DataForSEO credential pair in " + source + ".")
    raise CredentialError(
        "Set DATAFORSEO_LOGIN (or DATAFORSEO_USERNAME) and DATAFORSEO_PASSWORD "
        "in the process environment or Windows user environment."
    )


def credential_status() -> dict[str, Any]:
    try:
        credentials = load_credentials()
    except CredentialError:
        return {"present": False, "source": "missing-or-incomplete"}
    return {"present": True, "source": credentials.source}


def load_routes() -> dict[str, Any]:
    return json.loads(Path(__file__).with_name("routes.json").read_text(encoding="utf-8"))


def route_for(operation: str) -> dict[str, Any]:
    for route in load_routes()["routes"]:
        if operation.lower() in [route["name"], *route.get("aliases", [])]:
            return route
    raise RouteError("Unknown operation. Use 'legends-dataforseo routes' or a documented v3 path.")


def estimate_cost(operation: str, *, tasks: int = 1, depth: int = 0,
                  items: int = 0) -> dict[str, Any]:
    """Offline baseline estimate; excludes provider surcharges and optional flags."""
    if any(type(value) is not int for value in (tasks, depth, items)):
        raise ValueError("tasks, depth, and items must be integers")
    if tasks < 1 or depth < 0 or items < 0:
        raise ValueError("tasks must be positive; depth and items cannot be negative")
    route = route_for(operation)
    model = route.get("estimate")
    amount = None
    if not route["charged"]:
        amount = 0.0
    elif model:
        multiplier = max(1, math.ceil(depth / model["depth_unit"])) if model.get("depth_unit") else 1
        amount = round(tasks * (model["base_usd"] * multiplier + model.get("item_usd", 0) * items), 6)
    return {"route": route["name"], "charged": route["charged"], "estimate_only": True,
            "tasks": tasks, "depth": depth, "items": items, "estimated_cost_usd": amount,
            "note": "Baseline only. Review current pricing and all task options before execution."}


def normalize_path(path: str) -> str:
    # Never accept a URL, query string, encoded traversal, or authority override.
    if not isinstance(path, str) or not re.fullmatch(r"/?[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*(?:\.ai)?", path):
        raise RouteError("Use an API v3 relative endpoint path without URL, query, or traversal.")
    normalized = "/" + path.lstrip("/")
    if normalized.startswith("/v3/"):
        normalized = normalized[3:]
    return normalized


def _known_free_get(path: str) -> bool:
    path = path.removesuffix(".ai")
    if re.fullmatch(r"/[a-z0-9_/-]+/task_get/(?:advanced/|regular/|html/)?[A-Za-z0-9_-]+", path):
        return True
    return any(route["method"] == "GET" and not route["charged"] and
               route["path"] == path for route in load_routes()["routes"])


def _money(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(name + " must be a finite nonnegative number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(name + " must be a finite nonnegative number") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError(name + " must be a finite nonnegative number")
    return number


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Credentials must never follow a redirect to another host.


def _open(request: Request, timeout: float):
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def ledger_path() -> Path:
    configured = os.environ.get("LEGENDS_DATAFORSEO_LEDGER")
    if configured:
        return Path(configured).expanduser()
    state = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
                 or (Path.home() / ".local" / "state"))
    legacy = state / "legends-dataforseo-kit" / "cost-ledger.jsonl"
    return legacy if legacy.exists() else state / "legends-dataforseo" / "cost-ledger.jsonl"


def _append_ledger(path: str, result: dict[str, Any], consumer: str) -> None:
    target = ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Route template only; do not persist task IDs or request/result bodies.
    endpoint = re.sub(r"(/task_get/(?:advanced/|regular/|html/)?)[^/]+$", r"\1{id}", path)
    row = {"ts": datetime.now(timezone.utc).isoformat(), "path": endpoint,
           "cost": result.get("cost"), "status_code": result.get("status_code"),
           "consumer": consumer}
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\n")


def api_request(
    path: str, payload: list[dict[str, Any]] | None = None, *,
    method: str | None = None, body: list[dict[str, Any]] | None = None,
    credentials: Credentials | None = None, timeout: float = 90,
    log_cost: bool = False, confirm: bool = False, consumer: str = "python",
    estimated_cost_usd: float | None = None, max_cost_usd: float | None = None,
) -> dict[str, Any]:
    """Return the original response envelope; never retry or raise on task status.

    confirm=True authorizes paid/unknown calls without interactive prompts.
    max_cost_usd checks a caller-reviewed estimate before HTTP. It cannot cap
    provider billing. Caller owns aggregate budget, task status, and polling.
    """
    normalized = normalize_path(path)
    standard_path = normalized.removesuffix(".ai")
    if payload is not None and body is not None:
        raise RouteError("Pass payload or body, not both.")
    payload = body if body is not None else payload
    verb = method.upper() if isinstance(method, str) else ("GET" if payload is None else "POST")
    if method is not None and not isinstance(method, str):
        raise RouteError("method must be GET or POST.")
    if verb not in {"GET", "POST"}:
        raise RouteError("Only GET and POST are supported.")
    if verb == "GET" and payload is not None:
        raise RouteError("GET cannot have a body.")
    if verb == "POST" and (not isinstance(payload, list) or not payload or
                           any(not isinstance(task, dict) for task in payload)):
        raise RouteError("POST requires a nonempty JSON task array (list of objects).")
    if verb == "POST" and normalized.startswith("/serp/") and "/live/" in normalized and len(payload) != 1:
        raise RouteError("Live SERP requests accept one task per call.")
    if verb == "POST" and standard_path.startswith("/serp/") and standard_path.endswith("/task_post") and len(payload) > 100:
        raise RouteError("SERP queue requests accept at most 100 tasks per call.")
    timeout = _money(timeout, "timeout")
    if timeout == 0:
        raise ValueError("timeout must be positive")
    if type(confirm) is not bool:
        raise ValueError("confirm must be a boolean")
    if (verb == "POST" or not _known_free_get(normalized)) and not confirm:
        raise CostLimitError("Paid or unclassified requests require confirm=True before HTTP.")
    estimate = None if estimated_cost_usd is None else _money(estimated_cost_usd, "estimated_cost_usd")
    limit = None if max_cost_usd is None else _money(max_cost_usd, "max_cost_usd")
    if limit is not None:
        if estimate is None:
            raise CostLimitError("max_cost_usd requires a reviewed estimated_cost_usd.")
        if estimate > limit:
            raise CostLimitError("Request estimate exceeds max_cost_usd; no HTTP request made.")
    try:
        encoded = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise RouteError("Task body must contain JSON-compatible finite values.") from None
    auth = credentials if credentials is not None else load_credentials()
    from . import __version__
    authorization = base64.b64encode(f"{auth.login}:{auth.password}".encode("utf-8")).decode("ascii")
    request = Request(API_ROOT + normalized, data=encoded, method=verb,
                      headers={"Authorization": "Basic " + authorization,
                               "Content-Type": "application/json",
                               "User-Agent": "legends-dataforseo/" + __version__})
    try:
        with _open(request, timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            rejected = json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeError, OSError, HTTPException):
            rejected = None
        finally:
            exc.close()
        raise ApiError(f"DataForSEO returned HTTP {exc.code}; request was not retried.",
                       response=rejected if isinstance(rejected, dict) else None,
                       http_status=exc.code, request_may_have_completed=verb == "POST") from None
    except (URLError, OSError, TimeoutError, HTTPException):
        raise ApiError("DataForSEO transport failed; request was not retried. Check task state before resubmission.",
                       request_may_have_completed=verb == "POST") from None
    except (ValueError, UnicodeError):
        raise ApiError("DataForSEO returned invalid JSON; request was not retried.",
                       request_may_have_completed=verb == "POST") from None
    if not isinstance(result, dict):
        raise ApiError("DataForSEO returned a non-object JSON response.",
                       request_may_have_completed=verb == "POST")
    if log_cost:
        try:
            _append_ledger(normalized, result, consumer)
        except (OSError, ValueError, TypeError):
            # A local disk failure must not hide a paid response or suggest retry.
            try:
                warnings.warn("Cost ledger write failed; preserve the returned response and do not repeat the request.",
                              RuntimeWarning, stacklevel=2)
            except Warning:
                # Even -W error must not discard the response to a completed call.
                pass
    if result.get("status_code") != 20000:
        raise ApiError("DataForSEO rejected the response envelope; inspect ApiError.response.", response=result)
    return result


def serp(keyword: str, *, location_code: int = 2840, language_code: str = "en",
         depth: int = 10, **request_options: Any) -> dict[str, Any]:
    """One Google organic live task; request_options are api_request keywords."""
    return api_request(route_for("serp")["path"], [{"keyword": keyword,
                       "location_code": location_code, "language_code": language_code,
                       "depth": depth}], **request_options)


def demand(keywords: list[str], *, location_code: int = 2840,
           language_code: str = "en", **request_options: Any) -> dict[str, Any]:
    """One keyword overview task; request_options are api_request keywords."""
    if not isinstance(keywords, list) or not keywords or any(not isinstance(k, str) or not k for k in keywords):
        raise RouteError("keywords must be a nonempty list of strings.")
    return api_request(route_for("demand")["path"], [{"keywords": keywords,
                       "location_code": location_code, "language_code": language_code}], **request_options)


def maps(keyword: str, *, location_coordinate: str, language_code: str = "en",
         depth: int = 100, **request_options: Any) -> dict[str, Any]:
    """One coordinate-pinned Google Maps live task."""
    return api_request(route_for("maps")["path"], [{"keyword": keyword,
                       "location_coordinate": location_coordinate,
                       "language_code": language_code, "depth": depth}], **request_options)


def business(keyword: str, *, location_code: int = 2840, language_code: str = "en",
             **request_options: Any) -> dict[str, Any]:
    """One Google Business Profile live task (accepts 'cid:<id>', 'place_id:<id>', or query)."""
    if not isinstance(keyword, str) or not keyword.strip():
        raise RouteError("keyword must be a nonempty string.")
    return api_request(route_for("business")["path"], [{"keyword": keyword,
                       "location_code": location_code,
                       "language_code": language_code}], **request_options)


def total_cost(responses: list[dict[str, Any]]) -> float:
    return round(sum(float(item.get("cost") or 0) for item in responses), 6)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
