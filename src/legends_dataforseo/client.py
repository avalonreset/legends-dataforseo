"""Synchronous API v3 transport. No retries, prompts, or account switching."""

from __future__ import annotations

import base64
import json
import math
import os
import re
import threading
import time
import warnings
from http.client import HTTPException
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

API_ROOT = "https://api.dataforseo.com/v3"
MAX_RESPONSE_BYTES = 50 * 1024 * 1024
MAX_ERROR_BYTES = 4 * 1024 * 1024
_MACOS_SYSTEM_SYMLINKS = {Path("/var"), Path("/tmp"), Path("/etc")}


def _safe_local_path(value: Path | str) -> Path:
    """Refuse symlinks and Windows junctions across target and non-system ancestors."""
    path = Path(value).expanduser().absolute()
    for idx, part in enumerate((path, *path.parents)):
        if idx > 0 and sys.platform == "darwin" and part in _MACOS_SYSTEM_SYMLINKS:
            continue
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ValueError("Refusing symlink or junction path.")
        try:
            st = part.lstat()
            if getattr(st, "st_reparse_tag", 0) == 0xA0000003 or (
                getattr(st, "st_file_attributes", 0) & 0x400
            ):
                raise ValueError("Refusing reparse-point path.")
        except OSError:
            pass
    return path


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


class UnverifiedSerpError(ApiError):
    """serp() could not verify the SERP (fail closed). `.response` holds the stamped envelope."""


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
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


def ledger_path() -> Path:
    configured = os.environ.get("LEGENDS_DATAFORSEO_LEDGER")
    if configured:
        return Path(configured).expanduser()
    state = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
                 or (Path.home() / ".local" / "state"))
    legacy = state / "legends-dataforseo-kit" / "cost-ledger.jsonl"
    return legacy if legacy.exists() else state / "legends-dataforseo" / "cost-ledger.jsonl"


def _append_ledger(path: str, result: dict[str, Any], consumer: str) -> None:
    target = _safe_local_path(ledger_path())
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
    credentials: Credentials | None = None, timeout: float | None = None,
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
    if timeout is None:
        timeout = SERP_TIMEOUT_S if standard_path.startswith("/serp/") else 90.0
    timeout = _money(timeout, "timeout")
    if timeout == 0:
        raise ValueError("timeout must be positive")
    if type(confirm) is not bool:
        raise ValueError("confirm must be a boolean")
    if (verb == "POST" or not _known_free_get(normalized)) and not confirm:
        raise CostLimitError("Paid or unclassified requests require confirm=True before HTTP.")
    resolve_sent: bool | None = None
    if verb == "POST" and _is_organic_task_path(standard_path):
        payload, injected = _enforce_resolve(payload)
        resolve_sent = all(task.get("resolve_poisoned_results") is True for task in payload)
        if injected and estimated_cost_usd is not None:
            surcharge = float(estimate_cost("serp", tasks=1, depth=10)["estimated_cost_usd"] or 0.002)
            estimated_cost_usd = round(_money(estimated_cost_usd, "estimated_cost_usd") + surcharge * injected, 6)
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
            raw_body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw_body) > MAX_RESPONSE_BYTES:
            raise ApiError("DataForSEO response exceeds maximum size limit; request was not retried.",
                           request_may_have_completed=verb == "POST")
        result = json.loads(raw_body.decode("utf-8"))
    except HTTPError as exc:
        try:
            raw_err = exc.read(MAX_ERROR_BYTES + 1)
            rejected = json.loads(raw_err.decode("utf-8")) if len(raw_err) <= MAX_ERROR_BYTES else None
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
    if standard_path.startswith("/serp/google/organic/"):
        from .serp_integrity import normalize_serp_envelope, stamp_envelope_verified
        normalize_serp_envelope(result, endpoint=standard_path)
        # Raw calls skip serp()'s verification pull, so only provider-resolved blocks verify.
        stamp_envelope_verified(result, verification={"status": "not_run"}, resolve_sent=resolve_sent)
    return result


# Resolved pulls can take 66 s+; never let a timeout turn protection into a transport error.
SERP_TIMEOUT_S = 300.0
_ORGANIC_TASK_RE = re.compile(r"^/serp/google/organic/(?:live/(?:advanced|regular|html)|task_post)$")


def _is_organic_task_path(standard_path: str) -> bool:
    return bool(_ORGANIC_TASK_RE.match(standard_path))


def _web_mode(task: dict[str, Any]) -> bool:
    text = " ".join(str(task.get(k) or "") for k in ("url", "search_param")).lower()
    return "udm=14" in text or "udm=web" in text


def _enforce_resolve(payload: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Every Google organic task carries resolve_poisoned_results=true unless the escape hatch is set."""
    from .serp_integrity import ALLOW_POISONED_ENV, allow_poisoned
    escape = allow_poisoned()
    out: list[dict[str, Any]] = []
    injected = 0
    for task in payload:
        body = dict(task)
        value = body.get("resolve_poisoned_results", None)
        if _web_mode(body) and not escape:
            raise RouteError("udm=14 / udm=web cannot be combined with resolve_poisoned_results; such tasks bypass "
                             f"decoy defense and need {ALLOW_POISONED_ENV}=1 (results are stamped unprotected).")
        if value is None and not _web_mode(body):
            body["resolve_poisoned_results"] = True
            injected += 1
        elif value is not True and not escape:
            raise RouteError("Google organic SERP tasks must send resolve_poisoned_results=true (decoy defense). "
                             f"Set {ALLOW_POISONED_ENV}=1 to send unprotected tasks; results are stamped unprotected.")
        out.append(body)
    return out, injected


_DECOY_STATUSES = frozenset({"suspect_poisoned", "suspect_decoy"})
# DataForSEO transient task errors (40101 "Internal SE Server Error", seen on ~12% of bench pulls and
# reproducibly on concurrent same-keyword requests). Errored tasks report cost 0.
TRANSIENT_TASK_CODES = frozenset({40101})
TRANSIENT_BACKOFF_S = (2.0, 5.0, 10.0)
_sleep = time.sleep
_SERP_KEY_GUARD = threading.Lock()
_SERP_KEY_LOCKS: dict[str, threading.Lock] = {}


def resolve_poisoned_default() -> bool:
    """0.1.12: resolve_poisoned_results is mandatory on Google organic SERPs (always True).

    The only way to send a task without it is LEGENDS_DFS_ALLOW_POISONED=1 plus an explicit
    resolve_poisoned_results=False, and those results are stamped unprotected.
    """
    return True


def _serp_key_lock(keyword: Any) -> threading.Lock:
    key = " ".join(str(keyword).lower().split())
    with _SERP_KEY_GUARD:
        return _SERP_KEY_LOCKS.setdefault(key, threading.Lock())


def _serp_integrities(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for task in envelope.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        for res_obj in task.get("result") or []:
            if isinstance(res_obj, dict) and isinstance(res_obj.get("serp_integrity"), dict):
                found.append(res_obj["serp_integrity"])
    return found


def _serp_status(envelope: dict[str, Any]) -> str:
    statuses = [i.get("status") for i in _serp_integrities(envelope)]
    if "unverified_decoy" in statuses:
        return "unverified_decoy"
    if "suspect_poisoned" in statuses:
        return "suspect_poisoned"
    if "suspect_decoy" in statuses:
        return "suspect_decoy"
    return "clean" if statuses else "unassessed"


def _serp_is_suspect_poisoned(envelope: dict[str, Any]) -> bool:
    return _serp_status(envelope) == "suspect_poisoned"


def _serp_first_block(envelope: dict[str, Any]) -> dict[str, Any] | None:
    for task in envelope.get("tasks") or []:
        if isinstance(task, dict):
            for res_obj in task.get("result") or []:
                if isinstance(res_obj, dict) and isinstance(res_obj.get("items"), list):
                    return res_obj
    return None


def _attach_serp_retry_metadata(
    envelope: dict[str, Any],
    *,
    retry_attempts: int,
    attempt_task_ids: list[str],
    labs_competitors_items: list[dict[str, Any]] | None = None,
    budget_exhausted: bool = False,
) -> None:
    for task in envelope.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        for res_obj in task.get("result") or []:
            if not isinstance(res_obj, dict):
                continue
            integrity = res_obj.get("serp_integrity")
            if isinstance(integrity, dict):
                integrity["retry_attempts"] = retry_attempts
                integrity["attempt_task_ids"] = list(attempt_task_ids)
                if budget_exhausted:
                    integrity["budget_exhausted"] = True
            if labs_competitors_items is not None:
                res_obj["labs_competitors"] = labs_competitors_items


def _traffic_cache_path() -> Path:
    configured = os.environ.get("LEGENDS_DATAFORSEO_TRAFFIC_CACHE")
    if configured:
        return Path(configured).expanduser()
    return ledger_path().parent / "site-traffic-cache.json"


def site_traffic(
    sites: list[str],
    *,
    location_code: int = 2840,
    language_code: str = "en",
    max_age_days: int = 30,
    **request_options: Any,
) -> dict[str, float]:
    """Organic ETV per site from Labs bulk traffic estimation, cached locally (site -> etv)."""
    from .serp_integrity import site_key
    keys = [k for k in dict.fromkeys(site_key(s) for s in sites) if k]
    if not keys:
        return {}
    cache_file = _safe_local_path(_traffic_cache_path())
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    now = datetime.now(timezone.utc)
    bucket = f"{location_code}:{language_code}"
    rows = cache.get(bucket) if isinstance(cache.get(bucket), dict) else {}
    fresh: dict[str, float] = {}
    for key in keys:
        row = rows.get(key)
        try:
            age = (now - datetime.fromisoformat(row["ts"])).days if isinstance(row, dict) else None
        except (KeyError, TypeError, ValueError):
            age = None
        if age is not None and age <= max_age_days and isinstance(row.get("etv"), (int, float)):
            fresh[key] = float(row["etv"])
    missing = [k for k in keys if k not in fresh]
    if missing:
        response = api_request(route_for("bulk-traffic")["path"],
                               [{"targets": missing, "location_code": location_code,
                                 "language_code": language_code, "item_types": ["organic"]}],
                               **request_options)
        for task in response.get("tasks") or []:
            for res_obj in (task.get("result") or []) if isinstance(task, dict) else []:
                for item in (res_obj.get("items") or []) if isinstance(res_obj, dict) else []:
                    if not isinstance(item, dict):
                        continue
                    key = site_key(item.get("target"))
                    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
                    organic = metrics.get("organic") if isinstance(metrics.get("organic"), dict) else {}
                    etv = organic.get("etv")
                    if key and isinstance(etv, (int, float)) and not isinstance(etv, bool):
                        fresh[key] = float(etv)
                        rows[key] = {"etv": float(etv), "ts": now.isoformat()}
        for key in missing:
            if key not in fresh:
                # Labs knows nothing about the site: treat as zero-traffic filler evidence.
                fresh[key] = 0.0
                rows[key] = {"etv": 0.0, "ts": now.isoformat()}
        cache[bucket] = rows
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass
    return fresh


def serp(keyword: str, **options: Any) -> dict[str, Any]:
    """Fail-closed Google organic SERP; see _serp_impl for parameters.

    Same-keyword calls are serialized in-process (concurrent same-keyword DataForSEO requests
    reproducibly return 40101).
    """
    with _serp_key_lock(keyword):
        return _serp_impl(keyword, **options)


def _serp_impl(
    keyword: str,
    *,
    location_code: int | None = 2840,
    location_name: str | None = None,
    location_coordinate: str | None = None,
    language_code: str = "en",
    depth: int = 10,
    device: str | None = None,
    os: str | None = None,
    max_poison_retries: int = 2,
    labs_fallback: bool = False,
    resolve_poisoned_results: bool | None = None,
    incumbent_sites: list[str] | None = None,
    verify_no_swap: bool = True,
    allow_unverified: bool = False,
    **request_options: Any,
) -> dict[str, Any]:
    """One fail-closed Google organic live task (resolve mandatory, decoy re-pull, verification pull).

    resolve_poisoned_results is always sent. resolve_poisoned_results=False is refused unless
    LEGENDS_DFS_ALLOW_POISONED=1, and such results are stamped integrity "unprotected".
    Pipeline: resolve pull -> decoy detector -> suspect: re-pull with resolve (max_poison_retries,
    cumulative max_cost_usd) -> resolve fail-open (no swaps): one verification pull -> stamp
    serp_integrity.verified. A SERP still suspect ends as status unverified_decoy (decoy flavor)
    or suspect_poisoned with unresolved=True; it is never relabelled clean.
    Fail closed: an unverified protected SERP raises UnverifiedSerpError (the stamped envelope is
    on `.response`) unless allow_unverified=True. 40101 task errors are retried with backoff.
    """
    import time as _time
    from .serp_integrity import (
        DECOY_THRESHOLDS,
        apply_decoy_assessment,
        assess_decoy_signals,
        organic_site_keys,
        overlap_ratio,
    )

    if type(max_poison_retries) is not int or max_poison_retries < 0:
        raise ValueError("max_poison_retries must be a nonnegative integer")
    if type(verify_no_swap) is not bool:
        raise ValueError("verify_no_swap must be a boolean")
    if type(allow_unverified) is not bool:
        raise ValueError("allow_unverified must be a boolean")
    from .serp_integrity import ALLOW_POISONED_ENV, allow_poisoned, stamp_verified
    if resolve_poisoned_results is None or resolve_poisoned_results is True:
        resolve = True
    elif resolve_poisoned_results is False:
        if not allow_poisoned():
            raise ValueError("resolve_poisoned_results=False is refused (decoy defense is mandatory). Set "
                             f"{ALLOW_POISONED_ENV}=1 to pull an unprotected SERP; it is stamped unprotected.")
        resolve = False
    else:
        raise ValueError("resolve_poisoned_results must be a boolean or None")
    task: dict[str, Any] = {"keyword": keyword}
    if location_coordinate is not None:
        task["location_coordinate"] = location_coordinate
    elif location_name is not None:
        task["location_name"] = location_name
    else:
        task["location_code"] = 2840 if location_code is None else location_code
    task["language_code"] = language_code
    task["depth"] = depth
    if device is not None:
        task["device"] = device
    if os is not None:
        task["os"] = os

    path = route_for("serp")["path"]
    base_step = float(
        estimate_cost("serp", tasks=1, depth=depth if type(depth) is int and depth >= 0 else 10)["estimated_cost_usd"]
        or 0.002
    )
    resolve_surcharge = float(estimate_cost("serp", tasks=1, depth=10)["estimated_cost_usd"] or 0.002)

    def _step_estimate(with_resolve: bool) -> float:
        return round(base_step + (resolve_surcharge if with_resolve else 0.0), 6)

    serp_step_estimate = _step_estimate(resolve)
    labs_floor = max(
        0.015,
        float(estimate_cost("competitors", tasks=1, items=20)["estimated_cost_usd"] or 0.015),
    )
    if request_options.get("max_cost_usd") is not None:
        cap = _money(request_options["max_cost_usd"], "max_cost_usd")
        if cap < serp_step_estimate or (labs_fallback and cap < round(serp_step_estimate + labs_floor, 6)):
            raise CostLimitError("Request estimate exceeds max_cost_usd; no HTTP request made.")
        first_opts = dict(request_options)
        first_opts["estimated_cost_usd"] = max(
            serp_step_estimate,
            _money(first_opts["estimated_cost_usd"], "estimated_cost_usd")
            if first_opts.get("estimated_cost_usd") is not None else 0.0,
        )
    else:
        first_opts = request_options

    attempts: list[dict[str, Any]] = []

    def _step_spend(envelope: dict[str, Any], default_est: float) -> float:
        cost = envelope.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost > 0:
            return max(float(cost), default_est)
        return default_est

    def _run(body: dict[str, Any], opts: dict[str, Any], reason: str) -> dict[str, Any]:
        for transient_try in range(len(TRANSIENT_BACKOFF_S) + 1):
            started = _time.perf_counter()
            envelope = api_request(path, [body], **opts)
            latency = round((_time.perf_counter() - started) * 1000.0, 1)
            reported = envelope.get("cost")
            tasks = [t for t in (envelope.get("tasks") or []) if isinstance(t, dict)]
            codes = [t.get("status_code") for t in tasks]
            transient = bool(codes) and all(c in TRANSIENT_TASK_CODES for c in codes)
            attempts.append({
                "attempt": len(attempts) + 1,
                "reason": reason if transient_try == 0 else f"{reason}:transient_retry_{transient_try}",
                "task_ids": [str(t["id"]) for t in tasks if t.get("id")],
                "task_status_codes": codes,
                "resolve_poisoned_results": body.get("resolve_poisoned_results") is True,
                "cost_usd": reported if isinstance(reported, (int, float)) and not isinstance(reported, bool) else None,
                "latency_ms": latency,
                "status": _serp_status(envelope),
            })
            if not transient or transient_try == len(TRANSIENT_BACKOFF_S):
                return envelope
            _sleep(TRANSIENT_BACKOFF_S[transient_try])
        return envelope

    # Explicit False under the escape hatch; api_request would otherwise inject True.
    task["resolve_poisoned_results"] = resolve
    result = _run(task, first_opts, "initial")
    spent_usd = _step_spend(result, serp_step_estimate)
    budget_exhausted = False

    attempt_ids = [tid for a in attempts for tid in a["task_ids"]]
    retries_used = 0
    previous_top10: list[str] | None = None
    while _serp_status(result) in _DECOY_STATUSES and retries_used < max_poison_retries:
        status_before = _serp_status(result)
        retry_task = dict(task)
        if status_before == "suspect_decoy":
            # Decoy flavor: re-pull with provider-side Web-mode resolve.
            retry_task["resolve_poisoned_results"] = True
        if "location_coordinate" in retry_task:
            retry_task.pop("location_coordinate", None)
            if location_name is not None:
                retry_task["location_name"] = location_name
            else:
                retry_task["location_code"] = 2840 if location_code is None else location_code
        step_est = _step_estimate(retry_task.get("resolve_poisoned_results") is True)
        retry_opts = dict(request_options)
        if retry_opts.get("max_cost_usd") is not None:
            remaining_max = round(_money(retry_opts["max_cost_usd"], "max_cost_usd") - spent_usd, 6)
            if remaining_max < step_est:
                budget_exhausted = True
                break
            retry_opts["max_cost_usd"] = remaining_max
            retry_opts["estimated_cost_usd"] = step_est
        retries_used += 1
        block = _serp_first_block(result)
        previous_top10 = organic_site_keys(block, limit=10) if block else None
        result = _run(retry_task, retry_opts, status_before)
        spent_usd = round(spent_usd + _step_spend(result, step_est), 6)
        attempt_ids.extend(attempts[-1]["task_ids"])

    def _task_data(envelope: dict[str, Any]) -> dict[str, Any] | None:
        for t in envelope.get("tasks") or []:
            if isinstance(t, dict) and isinstance(t.get("data"), dict):
                return t["data"]
        return None

    def _provider_resolve(envelope: dict[str, Any]) -> str | None:
        for integrity in _serp_integrities(envelope):
            decoy = integrity.get("decoy")
            if isinstance(decoy, dict):
                return decoy.get("provider_resolve")
        return None

    # Fail-open guard: resolve requested, nothing swapped, looks clean -> one verification pull.
    verification: dict[str, Any] = {"status": "not_needed"}
    verify_top10: list[str] | None = None
    if not attempts[-1]["resolve_poisoned_results"]:
        verification = {"status": "resolve_off"}
    elif _serp_status(result) == "clean" and _provider_resolve(result) != "resolved":
        # Keyed on what we sent, not on DFS echoing resolve_poisoned_results back in task data.
        if not verify_no_swap:
            verification = {"status": "skipped_disabled"}
        else:
            verify_task = dict(task)
            verify_task["resolve_poisoned_results"] = True
            if "location_coordinate" in verify_task:
                verify_task.pop("location_coordinate", None)
                if location_name is not None:
                    verify_task["location_name"] = location_name
                else:
                    verify_task["location_code"] = 2840 if location_code is None else location_code
            step_est = _step_estimate(True)
            verify_opts = dict(request_options)
            affordable = True
            if verify_opts.get("max_cost_usd") is not None:
                remaining_max = round(_money(verify_opts["max_cost_usd"], "max_cost_usd") - spent_usd, 6)
                if remaining_max < step_est:
                    affordable = False
                    budget_exhausted = True
                else:
                    verify_opts["max_cost_usd"] = remaining_max
                    verify_opts["estimated_cost_usd"] = step_est
            if not affordable:
                verification = {"status": "skipped_budget"}
            else:
                first_block = _serp_first_block(result)
                first_top = organic_site_keys(first_block, limit=10) if first_block else []
                candidate = _run(verify_task, verify_opts, "verify_no_swap")
                spent_usd = round(spent_usd + _step_spend(candidate, step_est), 6)
                attempt_ids.extend(attempts[-1]["task_ids"])
                cand_block = _serp_first_block(candidate)
                cand_top = organic_site_keys(cand_block, limit=10) if cand_block else []
                ov = overlap_ratio(first_top, cand_top)
                cand_status = _serp_status(candidate)
                if _provider_resolve(candidate) == "resolved" and cand_status == "clean":
                    result = candidate
                    previous_top10 = first_top
                    verification = {"status": "resolved_on_verify", "overlap": ov}
                elif cand_block is None or not cand_top:
                    verification = {"status": "verify_failed", "overlap": None}
                else:
                    verify_top10 = cand_top
                    stable = ov is not None and ov > DECOY_THRESHOLDS["pull_overlap_max"]
                    if stable and cand_status == "clean":
                        verification = {"status": "stable", "overlap": ov}
                    else:
                        verification = {"status": "unstable" if not stable else "verify_pull_suspect", "overlap": ov}
                    final = _serp_first_block(result)
                    integrity = final.get("serp_integrity") if final else None
                    if isinstance(integrity, dict):
                        decoy = assess_decoy_signals(final, task_data=_task_data(result), second_pull_sites=cand_top)
                        if cand_status in _DECOY_STATUSES and not decoy.get("suspect"):
                            decoy["suspect"] = True
                            decoy["fired"] = list(decoy.get("fired") or []) + ["verify_pull_suspect"]
                            decoy["reasons"] = list(decoy.get("reasons") or []) + [
                                f"Verification pull (resolve, no swap) was itself {cand_status}"]
                        apply_decoy_assessment(integrity, decoy)
                        attempts[-1]["status"] = _serp_status(result)

    labs_items: list[dict[str, Any]] | None = None
    if labs_fallback and (
        _serp_status(result) in _DECOY_STATUSES
        or any(
            isinstance(r, dict) and (r.get("serp_integrity") or {}).get("goto_wrapped_count", 0) > 0
            for t in (result.get("tasks") or [])
            if isinstance(t, dict)
            for r in (t.get("result") or [])
        )
    ):
        labs_opts = dict(request_options)
        can_run_labs = True
        if labs_opts.get("max_cost_usd") is not None:
            remaining_max = round(_money(labs_opts["max_cost_usd"], "max_cost_usd") - spent_usd, 6)
            if remaining_max < labs_floor:
                budget_exhausted = True
                can_run_labs = False
            else:
                labs_opts["max_cost_usd"] = remaining_max
                labs_opts["estimated_cost_usd"] = labs_floor
        elif labs_opts.get("estimated_cost_usd") is not None:
            labs_opts["estimated_cost_usd"] = max(
                _money(labs_opts["estimated_cost_usd"], "estimated_cost_usd"),
                labs_floor,
            )
        if can_run_labs:
            comp_resp = competitors(
                [keyword],
                location_code=2840 if location_code is None else location_code,
                language_code=language_code,
                **labs_opts,
            )
            spent_usd = round(spent_usd + _step_spend(comp_resp, labs_floor), 6)
            labs_items = []
            for t in comp_resp.get("tasks") or []:
                if isinstance(t, dict):
                    for r in t.get("result") or []:
                        if isinstance(r, dict) and isinstance(r.get("items"), list):
                            labs_items.extend(i for i in r["items"] if isinstance(i, dict))

    # Optional caller evidence: known incumbents.
    final_block = _serp_first_block(result)
    if final_block is not None and incumbent_sites:
        integrity = final_block.get("serp_integrity")
        if isinstance(integrity, dict):
            task_data = None
            for t in result.get("tasks") or []:
                if isinstance(t, dict) and isinstance(t.get("data"), dict):
                    task_data = t["data"]
                    break
            apply_decoy_assessment(integrity, assess_decoy_signals(
                final_block, task_data=task_data, second_pull_sites=verify_top10,
                incumbent_sites=incumbent_sites))
            attempts[-1]["status"] = _serp_status(result)

    attempt_ids = [tid for a in attempts for tid in a["task_ids"]]
    if retries_used > 0 or labs_items is not None or budget_exhausted:
        _attach_serp_retry_metadata(
            result,
            retry_attempts=retries_used,
            attempt_task_ids=attempt_ids,
            labs_competitors_items=labs_items,
            budget_exhausted=budget_exhausted,
        )
    final_status = _serp_status(result)
    total_cost = sum(a["cost_usd"] for a in attempts if isinstance(a.get("cost_usd"), (int, float)))
    total_latency = round(sum(a["latency_ms"] for a in attempts), 1)
    repull_overlap = None
    if previous_top10 is not None and final_block is not None:
        current = organic_site_keys(final_block, limit=10)
        if previous_top10:
            repull_overlap = round(sum(1 for k in previous_top10 if k in set(current)) / len(previous_top10), 4)
    for integrity in _serp_integrities(result):
        integrity["resolve_poisoned_results"] = attempts[-1]["resolve_poisoned_results"]
        integrity["cost_usd_total"] = round(total_cost, 6)
        integrity["latency_ms_total"] = total_latency
        if repull_overlap is not None:
            integrity["repull_top10_overlap"] = repull_overlap
        integrity["verification"] = verification
        integrity.pop("unverified", None)
        if final_status in _DECOY_STATUSES:
            integrity["unresolved"] = True
            if integrity.get("status") == "suspect_decoy":
                integrity["detected_status"] = "suspect_decoy"
                integrity["status"] = "unverified_decoy"
        stamp_verified(integrity, verification=verification, unprotected=not attempts[-1]["resolve_poisoned_results"])
    blocks = _serp_integrities(result)
    verified = bool(blocks) and all(b.get("verified") is True for b in blocks)
    final_status = _serp_status(result) if blocks else "no_organic_result"
    result["legends_serp"] = {
        "resolve_poisoned_results_default": resolve,
        "integrity": "protected" if attempts[-1]["resolve_poisoned_results"] else "unprotected",
        "verified": verified,
        "verification": verification,
        "attempts": attempts,
        "final_status": final_status,
        "cost_usd_total": round(total_cost, 6),
        "cost_scope": "provider-reported cost of SERP attempts only (Labs/traffic calls excluded)",
        "latency_ms_total": total_latency,
        "budget_exhausted": budget_exhausted,
    }
    if not verified and resolve and not allow_unverified:
        reasons = sorted({r for b in blocks for r in b.get("unverified_reasons") or []}) or [
            "no organic result block to verify"]
        raise UnverifiedSerpError(
            f"SERP for {keyword!r} is not verified ({final_status}); refusing to return it. "
            "Reasons: " + "; ".join(reasons) + ". Pass allow_unverified=True only to inspect it.",
            response=result, request_may_have_completed=True)
    return result


def demand(keywords: list[str], *, location_code: int = 2840,
           language_code: str = "en", **request_options: Any) -> dict[str, Any]:
    """One keyword overview task; request_options are api_request keywords."""
    if not isinstance(keywords, list) or not keywords or any(not isinstance(k, str) or not k for k in keywords):
        raise RouteError("keywords must be a nonempty list of strings.")
    return api_request(route_for("demand")["path"], [{"keywords": keywords,
                       "location_code": location_code, "language_code": language_code}], **request_options)


def competitors(
    keywords: list[str],
    *,
    location_code: int = 2840,
    language_code: str = "en",
    limit: int = 20,
    **request_options: Any,
) -> dict[str, Any]:
    """One DataForSEO Labs SERP competitors live task across a keyword list."""
    if not isinstance(keywords, list) or not keywords or any(not isinstance(k, str) or not k for k in keywords):
        raise RouteError("keywords must be a nonempty list of strings.")
    if type(limit) is not int or limit < 1 or limit > 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    return api_request(
        route_for("competitors")["path"],
        [{"keywords": keywords, "location_code": location_code, "language_code": language_code, "limit": limit}],
        **request_options,
    )


def _normalize_target_domain(target: str) -> str:
    if not isinstance(target, str) or not target.strip():
        raise RouteError("target domain must be a nonempty string.")
    cleaned = target.strip()
    if "://" in cleaned:
        from urllib.parse import urlparse
        cleaned = (urlparse(cleaned).hostname or "").strip()
    cleaned = cleaned.split("/", 1)[0].strip().lower().rstrip(".").removeprefix("www.")
    if not cleaned or "." not in cleaned:
        raise RouteError("target must be a valid domain name.")
    return cleaned


def ranked_keywords(
    target: str,
    *,
    location_code: int = 2840,
    language_code: str = "en",
    limit: int = 100,
    **request_options: Any,
) -> dict[str, Any]:
    """One DataForSEO Labs ranked keywords live task for a target domain."""
    domain = _normalize_target_domain(target)
    if type(limit) is not int or limit < 1 or limit > 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    return api_request(
        route_for("ranked-keywords")["path"],
        [{"target": domain, "location_code": location_code, "language_code": language_code, "limit": limit}],
        **request_options,
    )


def relevant_pages(
    target: str,
    *,
    location_code: int = 2840,
    language_code: str = "en",
    limit: int = 100,
    **request_options: Any,
) -> dict[str, Any]:
    """One DataForSEO Labs relevant pages live task for a target domain."""
    domain = _normalize_target_domain(target)
    if type(limit) is not int or limit < 1 or limit > 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    return api_request(
        route_for("relevant-pages")["path"],
        [{"target": domain, "location_code": location_code, "language_code": language_code, "limit": limit}],
        **request_options,
    )


def maps(keyword: str, *, location_coordinate: str, language_code: str = "en",
         depth: int = 100, **request_options: Any) -> dict[str, Any]:
    """One coordinate-pinned Google Maps live task."""
    return api_request(route_for("maps")["path"], [{"keyword": keyword,
                       "location_coordinate": location_coordinate,
                       "language_code": language_code, "depth": depth}], **request_options)


def business(
    keyword: str | None = None,
    *,
    target: str | None = None,
    location_code: int | None = 2840,
    location_name: str | None = None,
    location_coordinate: str | None = None,
    language_code: str = "en",
    tag: str | None = None,
    **request_options: Any,
) -> dict[str, Any]:
    """One Google Business Profile live task (accepts 'cid:<id>', 'place_id:<id>', or query)."""
    if keyword is not None and target is not None:
        raise RouteError("Specify either keyword or target, not both.")
    query = target if keyword is None else keyword
    if not isinstance(query, str) or not query.strip():
        raise RouteError("keyword must be a nonempty string.")
    task: dict[str, Any] = {"keyword": query, "language_code": language_code}
    if location_coordinate is not None:
        task["location_coordinate"] = location_coordinate
    elif location_name is not None:
        task["location_name"] = location_name
    elif location_code is not None:
        task["location_code"] = location_code
    if tag is not None:
        task["tag"] = tag
    return api_request(route_for("business")["path"], [task], **request_options)


def total_cost(responses: list[dict[str, Any]]) -> float:
    return round(sum(float(item.get("cost") or 0) for item in responses), 6)


def write_json(path: Path, payload: Any) -> None:
    target = _safe_local_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
