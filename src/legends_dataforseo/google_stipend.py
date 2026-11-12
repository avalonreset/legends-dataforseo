"""Zero-Overage Google Free-Stipend Ledger, Circuit Breaker, and Free-First API Helpers.

Enforces hard local ceilings strictly below Google's official monthly/daily free
limits (80-85%) so the caller is never billed a single cent of overage by Google.
When a stipend cap is reached, a key is absent, or a Google service returns 403
(SERVICE_DISABLED / API_KEY_SERVICE_BLOCKED), callers fail open to DataForSEO or
keyless fallbacks with all API keys redacted.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import ProxyHandler, Request, build_opener

from .client import MAX_ERROR_BYTES, MAX_RESPONSE_BYTES, _NoRedirect, _safe_local_path

LEDGER_SCHEMA = "legends-google-stipend-ledger/v1"

DEFAULT_GOOGLE_KEY_ENVS: tuple[str, ...] = (
    "GOOGLE_PLACES_API_KEY",
    "GOOGLE_MAPS_API_KEY",
    "GOOGLE_MAP_TILES_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_CSE_API_KEY",
)

STIPEND_SKUS: dict[str, dict[str, Any]] = {
    "places_ids_only": {
        "period": "unlimited",
        "free_limit": None,
        "hard_cap": None,
        "overage_usd_per_1k": 0.0,
        "dataforseo_fallback": "business",
    },
    "places_essentials": {
        "period": "monthly",
        "free_limit": 10000,
        "hard_cap": 8000,
        "overage_usd_per_1k": 5.0,
        "dataforseo_fallback": "business",
    },
    "places_pro": {
        "period": "monthly",
        "free_limit": 5000,
        "hard_cap": 4000,
        "overage_usd_per_1k": 32.0,
        "dataforseo_fallback": "business",
    },
    "places_enterprise": {
        "period": "monthly",
        "free_limit": 1000,
        "hard_cap": 800,
        "overage_usd_per_1k": 35.0,
        "dataforseo_fallback": "business",
    },
    "geocoding": {
        "period": "monthly",
        "free_limit": 10000,
        "hard_cap": 8000,
        "overage_usd_per_1k": 5.0,
        "dataforseo_fallback": "maps",
    },
    "map_tiles_2d": {
        "period": "monthly",
        "free_limit": 100000,
        "hard_cap": 85000,
        "overage_usd_per_1k": 0.60,
        "dataforseo_fallback": "osm-legends",
    },
    "static_maps": {
        "period": "monthly",
        "free_limit": 10000,
        "hard_cap": 8000,
        "overage_usd_per_1k": 2.0,
        "dataforseo_fallback": "osm-legends",
    },
    "pagespeed_insights": {
        "period": "daily",
        "free_limit": 25000,
        "hard_cap": 20000,
        "overage_usd_per_1k": 0.0,
        "dataforseo_fallback": "onpage",
    },
    "custom_search": {
        "period": "daily",
        "free_limit": 100,
        "hard_cap": 85,
        "overage_usd_per_1k": 5.0,
        "dataforseo_fallback": "serp",
    },
}

PLACES_FIELD_MASKS: dict[str, str] = {
    "ids_only": "places.id,places.name",
    "essentials": "places.id,places.name,places.formattedAddress,places.location,places.types",
    "pro": (
        "places.id,places.name,places.displayName,places.formattedAddress,"
        "places.location,places.primaryType,places.types,places.businessStatus,"
        "places.googleMapsUri,places.googleMapsLinks"
    ),
    "enterprise": (
        "places.id,places.name,places.displayName,places.formattedAddress,"
        "places.location,places.primaryType,places.types,places.businessStatus,"
        "places.googleMapsUri,places.googleMapsLinks,places.rating,"
        "places.userRatingCount,places.websiteUri,places.nationalPhoneNumber,"
        "places.internationalPhoneNumber,places.regularOpeningHours"
    ),
}

_LEDGER_LOCK = threading.Lock()
_DISABLED_SERVICES: set[tuple[str, str]] = set()
_AIZA_RE = re.compile(r"AIza[0-9A-Za-z_\-]{10,}")
_QUERY_KEY_RE = re.compile(r"([?&]key=)[^&\s\"']+", re.IGNORECASE)
_CID_QUERY_RE = re.compile(r"[?&]cid=(\d+)")


def _google_open(request: Request, timeout: float):
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


def redact_google_key(text: Any, key: str | None = None) -> str:
    """Strip any AIza... pattern, key= query param, or supplied key from strings/URLs."""
    if text is None:
        return ""
    value = text if isinstance(text, str) else str(text)
    if isinstance(key, str) and key.strip():
        clean_key = key.strip()
        value = value.replace(clean_key, "[REDACTED_GOOGLE_KEY]")
        encoded_key = quote(clean_key, safe="")
        if encoded_key and encoded_key != clean_key:
            value = value.replace(encoded_key, "[REDACTED_GOOGLE_KEY]")
    value = _AIZA_RE.sub("[REDACTED_GOOGLE_KEY]", value)
    value = _QUERY_KEY_RE.sub(r"\1[REDACTED_GOOGLE_KEY]", value)
    return value


never_echo_key = redact_google_key


def _key_fingerprint(key: str | None) -> str:
    if not key or not isinstance(key, str):
        return "keyless"
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()[:16]


def _normalize_service_name(service: str) -> str:
    cleaned = (service or "").strip().lower()
    if "." in cleaned:
        cleaned = cleaned.split(".", 1)[0]
    return cleaned


def is_service_disabled(key: str | None, service: str) -> bool:
    fp = _key_fingerprint(key)
    norm = _normalize_service_name(service)
    return (fp, norm) in _DISABLED_SERVICES or (fp, service) in _DISABLED_SERVICES


def mark_service_disabled(key: str | None, service: str) -> None:
    fp = _key_fingerprint(key)
    norm = _normalize_service_name(service)
    _DISABLED_SERVICES.add((fp, norm))


def clear_disabled_services() -> None:
    _DISABLED_SERVICES.clear()


def google_ledger_path(ledger_path: Path | str | None = None) -> Path:
    """Resolve the Zero-Overage Google stipend ledger path outside the git tree."""
    raw = (
        ledger_path
        or os.environ.get("LEGENDS_GOOGLE_LEDGER_PATH")
        or (Path.home() / ".legends" / "google-stipend-ledger.json")
    )
    return _safe_local_path(raw)


def _coerce_utc(now: datetime | str | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    if isinstance(now, str):
        text = now.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if re.fullmatch(r"\d{4}-\d{2}", text):
            return datetime.strptime(text + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    raise TypeError("now must be a datetime, ISO string, or None")


def _period_key_for(period: str, dt: datetime) -> str:
    if period == "daily":
        return dt.strftime("%Y-%m-%d")
    if period == "monthly":
        return dt.strftime("%Y-%m")
    return "unlimited"


@contextmanager
def _ledger_lock(target: Path):
    """Acquire both in-process thread lock and cross-process OS file lock (fails closed on error)."""
    with _LEDGER_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = _safe_local_path(target.with_suffix(target.suffix + ".lock"))
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass


def _read_ledger_unlocked(target: Path) -> dict[str, Any]:
    if not target.exists():
        return {"schema": LEDGER_SCHEMA, "usage": {}, "monthly": {}}
    try:
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("Corrupted or unreadable Google stipend ledger; refusing fail-open execution") from exc
    if not isinstance(raw, dict):
        raise ValueError("Google stipend ledger root must be a JSON object")
    if "usage" in raw and not isinstance(raw.get("usage"), dict):
        raise ValueError("Google stipend ledger 'usage' field must be an object")
    raw.setdefault("usage", {})
    if not isinstance(raw.get("monthly"), dict):
        raw["monthly"] = {}
    return raw


def _write_ledger_unlocked(target: Path, data: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".google-stipend-", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _valid_nonneg_int(val: Any) -> int | None:
    if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
        return int(val)
    if isinstance(val, float) and val.is_integer() and val >= 0:
        return int(val)
    return None


def _sku_used_in_period(ledger_data: dict[str, Any], sku: str, active_period_key: str) -> int:
    candidates: list[int] = [0]
    usage = ledger_data.get("usage")
    if isinstance(usage, dict):
        entry = usage.get(sku)
        if isinstance(entry, dict) and entry.get("period_key") == active_period_key:
            v = _valid_nonneg_int(entry.get("used"))
            if v is not None:
                candidates.append(v)
        elif isinstance(entry, dict) and isinstance(entry.get(active_period_key), dict):
            v = _valid_nonneg_int(entry[active_period_key].get(sku))
            if v is not None:
                candidates.append(v)
    for section_key in ("monthly", "months"):
        sec = ledger_data.get(section_key)
        if isinstance(sec, dict):
            p_dict = sec.get(active_period_key)
            if isinstance(p_dict, dict):
                v = _valid_nonneg_int(p_dict.get(sku))
                if v is not None:
                    candidates.append(v)
    p_top = ledger_data.get(active_period_key)
    if isinstance(p_top, dict):
        v = _valid_nonneg_int(p_top.get(sku))
        if v is not None:
            candidates.append(v)
    return max(candidates)


def _validate_sku_and_count(sku: str, count: int) -> dict[str, Any]:
    if not isinstance(sku, str) or sku not in STIPEND_SKUS:
        raise ValueError(f"Unknown Google stipend SKU: {sku!r}")
    if type(count) is not int or count < 1:
        raise ValueError("count must be a positive integer")
    return STIPEND_SKUS[sku]


def resolve_google_api_key(
    *env_names: str,
    dotenv_path: Path | str | None = None,
) -> str | None:
    """Resolve a Google API key from os.environ (or optional .env file)."""
    search_order: list[str] = []
    for name in (*env_names, *DEFAULT_GOOGLE_KEY_ENVS):
        if isinstance(name, str) and name and name not in search_order:
            search_order.append(name)

    for name in search_order:
        val = os.environ.get(name)
        if isinstance(val, str) and val.strip():
            return val.strip()

    if dotenv_path is not None:
        try:
            env_file = _safe_local_path(dotenv_path)
            if env_file.is_file():
                parsed: dict[str, str] = {}
                for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.removeprefix("export ").strip()
                    v = v.strip().strip("'").strip('"').strip()
                    if k and v:
                        parsed[k] = v
                for name in search_order:
                    if name in parsed:
                        return parsed[name]
        except (OSError, ValueError):
            pass
    return None


def stipend_status(
    ledger_path: Path | str | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Return current usage, hard caps, remaining free calls, and rollover periods."""
    dt = _coerce_utc(now)
    day_key = dt.strftime("%Y-%m-%d")
    month_key = dt.strftime("%Y-%m")
    ledger_corrupt = False
    try:
        target = google_ledger_path(ledger_path)
        with _ledger_lock(target):
            ledger_data = _read_ledger_unlocked(target)
    except (OSError, ValueError):
        ledger_corrupt = True
        target = Path(str(ledger_path or "~/.legends/google-stipend-ledger.json"))
        ledger_data = {"schema": LEDGER_SCHEMA, "usage": {}, "monthly": {}}

    skus_report: dict[str, dict[str, Any]] = {}
    for sku, meta in STIPEND_SKUS.items():
        period = meta["period"]
        p_key = _period_key_for(period, dt)
        hard_cap = meta["hard_cap"]
        used = (hard_cap or 0) if ledger_corrupt else _sku_used_in_period(ledger_data, sku, p_key)
        remaining = 0 if ledger_corrupt else (None if hard_cap is None else max(0, hard_cap - used))
        available = False if ledger_corrupt else (True if hard_cap is None else (used < hard_cap))
        skus_report[sku] = {
            "sku": sku,
            "period": period,
            "period_key": p_key,
            "used": used,
            "free_limit": meta["free_limit"],
            "hard_cap": hard_cap,
            "remaining": remaining,
            "available": available,
            "overage_usd_per_1k": meta["overage_usd_per_1k"],
            "dataforseo_fallback": meta["dataforseo_fallback"],
        }

    return {
        "schema": LEDGER_SCHEMA,
        "ledger_path": str(target),
        "ledger_healthy": not ledger_corrupt,
        "day_key": day_key,
        "month_key": month_key,
        "google_key_configured": bool(resolve_google_api_key()),
        "disabled_services": sorted({svc for _, svc in _DISABLED_SERVICES}),
        "skus": skus_report,
    }


def can_use_stipend(
    sku: str,
    count: int = 1,
    ledger_path: Path | str | None = None,
    now: datetime | str | None = None,
) -> bool:
    """Check whether `count` units of `sku` fit strictly within the zero-overage hard cap."""
    meta = _validate_sku_and_count(sku, count)
    hard_cap = meta["hard_cap"]
    if hard_cap is None:
        return True
    dt = _coerce_utc(now)
    p_key = _period_key_for(meta["period"], dt)
    try:
        target = google_ledger_path(ledger_path)
        with _ledger_lock(target):
            ledger_data = _read_ledger_unlocked(target)
            used = _sku_used_in_period(ledger_data, sku, p_key)
    except (OSError, ValueError):
        return False
    return (used + count) <= hard_cap


def reserve_stipend(
    sku: str,
    count: int = 1,
    ledger_path: Path | str | None = None,
    now: datetime | str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Atomically reserve `count` units of `sku` only when `used + count <= hard_cap`."""
    meta = _validate_sku_and_count(sku, count)
    dt = _coerce_utc(now)
    period = meta["period"]
    p_key = _period_key_for(period, dt)
    hard_cap = meta["hard_cap"]

    try:
        target = google_ledger_path(ledger_path)
        with _ledger_lock(target):
            ledger_data = _read_ledger_unlocked(target)
            used = _sku_used_in_period(ledger_data, sku, p_key)
            if hard_cap is not None and (used + count) > hard_cap:
                receipt = {
                    "allowed": False,
                    "sku": sku,
                    "period": period,
                    "period_key": p_key,
                    "used": used,
                    "requested": count,
                    "hard_cap": hard_cap,
                    "free_limit": meta["free_limit"],
                    "remaining": max(0, hard_cap - used),
                    "cost_usd": 0.0,
                    "reason": "stipend_hard_cap_reached",
                    "dataforseo_fallback": meta["dataforseo_fallback"],
                }
                return False, receipt

            new_used = used + count
            usage = ledger_data.setdefault("usage", {})
            usage[sku] = {
                "period": period,
                "period_key": p_key,
                "used": new_used,
                "updated_at": dt.isoformat(),
            }
            if period == "monthly":
                monthly = ledger_data.setdefault("monthly", {})
                if not isinstance(monthly, dict):
                    monthly = {}
                    ledger_data["monthly"] = monthly
                m_bucket = monthly.setdefault(p_key, {})
                if not isinstance(m_bucket, dict):
                    m_bucket = {}
                    monthly[p_key] = m_bucket
                m_bucket[sku] = new_used
            ledger_data["schema"] = LEDGER_SCHEMA
            ledger_data["updated_at"] = dt.isoformat()
            _write_ledger_unlocked(target, ledger_data)
    except (OSError, ValueError) as exc:
        return False, {
            "allowed": False,
            "sku": sku,
            "period": period,
            "period_key": p_key,
            "used": hard_cap if hard_cap is not None else 0,
            "requested": count,
            "hard_cap": hard_cap,
            "free_limit": meta["free_limit"],
            "remaining": 0,
            "cost_usd": 0.0,
            "reason": "ledger_unavailable_fail_closed",
            "error": type(exc).__name__,
            "dataforseo_fallback": meta["dataforseo_fallback"],
        }

    receipt = {
        "allowed": True,
        "sku": sku,
        "period": period,
        "period_key": p_key,
        "used": new_used,
        "requested": count,
        "hard_cap": hard_cap,
        "free_limit": meta["free_limit"],
        "remaining": None if hard_cap is None else max(0, hard_cap - new_used),
        "cost_usd": 0.0,
        "dataforseo_fallback": meta["dataforseo_fallback"],
    }
    return True, receipt


check_and_reserve = reserve_stipend


def _extract_cid_from_place(place: dict[str, Any]) -> str | None:
    candidates: list[str] = []
    uri = place.get("googleMapsUri")
    if isinstance(uri, str) and uri:
        candidates.append(uri)
    links = place.get("googleMapsLinks")
    if isinstance(links, dict):
        for val in links.values():
            if isinstance(val, str) and val:
                candidates.append(val)
    for url_str in candidates:
        match = _CID_QUERY_RE.search(url_str)
        if match:
            return match.group(1)
    return None


def _extract_domain(url_str: str | None) -> str | None:
    if not isinstance(url_str, str) or not url_str.strip():
        return None
    try:
        host = (urlparse(url_str.strip()).hostname or "").strip().lower().rstrip(".")
        return host.removeprefix("www.") or None
    except ValueError:
        return None


def _is_google_service_blocked(code: int | None, error_text: str) -> bool:
    if code == 403:
        return True
    upper = (error_text or "").upper()
    return any(
        token in upper
        for token in (
            "SERVICE_DISABLED",
            "API_KEY_SERVICE_BLOCKED",
            "PERMISSION_DENIED",
            "ACCESS_NOT_CONFIGURED",
        )
    )


def google_places_lookup(
    query: str,
    *,
    tier: str = "pro",
    location_bias: dict[str, Any] | str | None = None,
    api_key: str | None = None,
    ledger_path: Path | str | None = None,
    timeout: float = 15,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Lookup a business via Google Places API (New) within the zero-overage free stipend."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a nonempty string")
    tier_norm = (tier or "").strip().lower()
    if tier_norm not in PLACES_FIELD_MASKS:
        raise ValueError("tier must be one of: ids_only, essentials, pro, enterprise")

    sku = f"places_{tier_norm}"
    key = (api_key or "").strip() if isinstance(api_key, str) else ""
    if not key:
        key = resolve_google_api_key("GOOGLE_PLACES_API_KEY", "GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY") or ""

    if not key:
        return {
            "ok": False,
            "fallback_recommended": True,
            "reason": "missing_google_api_key",
            "sku": sku,
            "cost_usd": 0.0,
        }

    if is_service_disabled(key, "places"):
        return {
            "ok": False,
            "fallback_recommended": True,
            "reason": "service_disabled_in_session",
            "sku": sku,
            "cost_usd": 0.0,
        }

    allowed, receipt = reserve_stipend(sku, count=1, ledger_path=ledger_path, now=now)
    if not allowed:
        return {
            "ok": False,
            "fallback_recommended": True,
            "reason": receipt.get("reason") or "stipend_hard_cap_reached",
            "sku": sku,
            "stipend": receipt,
            "cost_usd": 0.0,
        }

    cleaned_query = re.sub(r"^(?:place_id|cid)\s*:\s*", "", query.strip(), flags=re.IGNORECASE).strip() or query.strip()
    body_obj: dict[str, Any] = {"textQuery": cleaned_query}
    if isinstance(location_bias, dict) and location_bias:
        body_obj["locationBias"] = location_bias
    elif isinstance(location_bias, str) and "," in location_bias:
        parts = [p.strip() for p in location_bias.split(",")]
        if len(parts) >= 2:
            try:
                lat_val = float(parts[0])
                lng_val = float(parts[1])
                body_obj["locationBias"] = {
                    "circle": {
                        "center": {"latitude": lat_val, "longitude": lng_val},
                        "radius": 5000.0,
                    }
                }
            except ValueError:
                pass

    encoded_body = json.dumps(body_obj, allow_nan=False).encode("utf-8")
    req = Request(
        "https://places.googleapis.com/v1/places:searchText",
        data=encoded_body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": PLACES_FIELD_MASKS[tier_norm],
        },
    )
    try:
        with _google_open(req, timeout=float(timeout)) as resp:
            raw_bytes = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(raw_bytes) > MAX_RESPONSE_BYTES:
            return {
                "ok": False,
                "fallback_recommended": True,
                "reason": "response_exceeded_max_bytes",
                "sku": sku,
                "stipend": receipt,
                "cost_usd": 0.0,
            }
        data = json.loads(raw_bytes.decode("utf-8"))
    except HTTPError as exc:
        try:
            err_raw = exc.read(MAX_ERROR_BYTES + 1).decode("utf-8", errors="replace")
        except (OSError, ValueError, HTTPException):
            err_raw = ""
        finally:
            exc.close()
        if _is_google_service_blocked(exc.code, err_raw):
            mark_service_disabled(key, "places")
        return {
            "ok": False,
            "fallback_recommended": True,
            "http_status": exc.code,
            "reason": redact_google_key(f"HTTP {exc.code}: {err_raw[:400]}", key=key),
            "sku": sku,
            "stipend": receipt,
            "cost_usd": 0.0,
        }
    except (URLError, OSError, TimeoutError, HTTPException, ValueError) as exc:
        return {
            "ok": False,
            "fallback_recommended": True,
            "reason": redact_google_key(f"{type(exc).__name__}: {exc}", key=key),
            "sku": sku,
            "stipend": receipt,
            "cost_usd": 0.0,
        }

    raw_places = data.get("places") if isinstance(data, dict) else None
    items: list[dict[str, Any]] = []
    for place in raw_places or []:
        if not isinstance(place, dict):
            continue
        display_name = place.get("displayName")
        title = (
            display_name.get("text")
            if isinstance(display_name, dict)
            else (display_name if isinstance(display_name, str) else place.get("id"))
        )
        loc = place.get("location") if isinstance(place.get("location"), dict) else {}
        website = place.get("websiteUri")
        place_id = place.get("id") or (
            place.get("name", "").removeprefix("places/") if isinstance(place.get("name"), str) else None
        )
        items.append(
            {
                "place_id": place_id,
                "cid": _extract_cid_from_place(place),
                "title": title,
                "address": place.get("formattedAddress"),
                "lat": loc.get("latitude"),
                "lng": loc.get("longitude"),
                "rating": place.get("rating"),
                "votes_count": place.get("userRatingCount"),
                "domain": _extract_domain(website),
                "url": website,
                "phone": place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber"),
                "category": place.get("primaryType")
                or ((place.get("types") or [None])[0] if isinstance(place.get("types"), list) else None),
                "google_maps_uri": place.get("googleMapsUri"),
                "business_status": place.get("businessStatus"),
                "source": "google-places-free-stipend",
            }
        )

    return {
        "ok": True,
        "fallback_recommended": False,
        "source": "google-places-free-stipend",
        "sku": sku,
        "tier": tier_norm,
        "cost_usd": 0.0,
        "stipend": receipt,
        "items": items,
        "profile": items[0] if items else None,
    }


def _parse_psi_payload(
    data: dict[str, Any],
    *,
    url: str,
    strategy: str,
    keyless: bool,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    lh = data.get("lighthouseResult") if isinstance(data.get("lighthouseResult"), dict) else {}
    lh_cats = lh.get("categories") if isinstance(lh.get("categories"), dict) else {}
    scores: dict[str, float | int | None] = {}
    for cat_key, cat_obj in lh_cats.items():
        if isinstance(cat_obj, dict) and isinstance(cat_obj.get("score"), (int, float)):
            raw_score = round(float(cat_obj["score"]) * 100, 1)
            scores[cat_key] = int(raw_score) if raw_score.is_integer() else raw_score

    loading_exp = data.get("loadingExperience") if isinstance(data.get("loadingExperience"), dict) else {}
    metrics_map = loading_exp.get("metrics") if isinstance(loading_exp.get("metrics"), dict) else {}
    crux_aliases = {
        "LCP": "LARGEST_CONTENTFUL_PAINT_MS",
        "INP": "INTERACTION_TO_NEXT_PAINT",
        "CLS": "CUMULATIVE_LAYOUT_SHIFT_SCORE",
        "FCP": "FIRST_CONTENTFUL_PAINT_MS",
        "TTFB": "EXPERIMENTAL_TIME_TO_FIRST_BYTE",
    }
    crux: dict[str, dict[str, Any] | None] = {}
    for short_name, google_key in crux_aliases.items():
        m_obj = metrics_map.get(google_key)
        if isinstance(m_obj, dict):
            crux[short_name] = {
                "percentile": m_obj.get("percentile"),
                "category": m_obj.get("category"),
            }
        else:
            crux[short_name] = None

    lh_audits = lh.get("audits") if isinstance(lh.get("audits"), dict) else {}
    audit_keys = (
        "first-contentful-paint",
        "largest-contentful-paint",
        "total-blocking-time",
        "cumulative-layout-shift",
        "speed-index",
        "interactive",
        "server-response-time",
    )
    audits: dict[str, dict[str, Any]] = {}
    for a_key in audit_keys:
        a_obj = lh_audits.get(a_key)
        if isinstance(a_obj, dict):
            audits[a_key] = {
                "score": a_obj.get("score"),
                "numericValue": a_obj.get("numericValue"),
                "displayValue": a_obj.get("displayValue"),
            }

    return {
        "ok": True,
        "fallback_recommended": False,
        "source": "google-psi-free-stipend",
        "sku": "pagespeed_insights",
        "cost_usd": 0.0,
        "keyless": keyless,
        "url": url,
        "strategy": strategy,
        "scores": scores,
        "crux": crux,
        "cwv": crux,
        "audits": audits,
        "stipend": receipt,
    }


def _validate_public_web_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url must be a valid http:// or https:// public URL")
    cleaned = url.strip()
    try:
        parsed = urlparse(cleaned)
    except ValueError as exc:
        raise ValueError("url must be a valid http:// or https:// public URL") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url must be a valid http:// or https:// public URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url must not contain embedded user credentials")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host or "." not in host or host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        raise ValueError("url must target a public hostname")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        mapped = getattr(ip, "ipv4_mapped", None) or ip
        if (
            mapped.is_private
            or mapped.is_loopback
            or mapped.is_link_local
            or mapped.is_multicast
            or mapped.is_reserved
            or mapped.is_unspecified
        ):
            raise ValueError("url must not target a private, loopback, or reserved IP address")
    return cleaned


def google_pagespeed_insights(
    url: str,
    *,
    strategy: str = "mobile",
    categories: tuple[str, ...] | list[str] = ("performance", "seo", "accessibility", "best-practices"),
    api_key: str | None = None,
    ledger_path: Path | str | None = None,
    timeout: float = 30,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Run Google PageSpeed Insights v5 ($0.00, capped at 20,000/day, with keyless retry on 403)."""
    clean_url = _validate_public_web_url(url)
    strat = (strategy or "mobile").strip().lower()
    if strat not in {"mobile", "desktop"}:
        raise ValueError("strategy must be 'mobile' or 'desktop'")

    allowed, receipt = reserve_stipend("pagespeed_insights", count=1, ledger_path=ledger_path, now=now)
    if not allowed:
        return {
            "ok": False,
            "fallback_recommended": True,
            "reason": receipt.get("reason") or "stipend_hard_cap_reached",
            "sku": "pagespeed_insights",
            "stipend": receipt,
            "cost_usd": 0.0,
        }

    raw_key = (api_key or "").strip() if isinstance(api_key, str) else ""
    if not raw_key and api_key is None:
        raw_key = (
            resolve_google_api_key(
                "GOOGLE_PAGESPEED_API_KEY",
                "GOOGLE_API_KEY",
                "GOOGLE_MAPS_API_KEY",
                "GOOGLE_PLACES_API_KEY",
            )
            or ""
        )

    active_key: str | None = None
    if raw_key and not is_service_disabled(raw_key, "pagespeedonline"):
        active_key = raw_key

    def _build_psi_url(k: str | None) -> str:
        params: list[tuple[str, str]] = [("url", clean_url), ("strategy", strat)]
        for cat in categories:
            if isinstance(cat, str) and cat.strip():
                params.append(("category", cat.strip()))
        if k:
            params.append(("key", k))
        return "https://www.googleapis.com/pagespeedonline/v5/runPagespeed?" + urlencode(params)

    for attempt in range(2):
        req = Request(_build_psi_url(active_key), method="GET")
        try:
            with _google_open(req, timeout=float(timeout)) as resp:
                raw_bytes = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw_bytes) > MAX_RESPONSE_BYTES:
                return {
                    "ok": False,
                    "fallback_recommended": True,
                    "reason": "response_exceeded_max_bytes",
                    "sku": "pagespeed_insights",
                    "stipend": receipt,
                    "cost_usd": 0.0,
                }
            data = json.loads(raw_bytes.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Non-object JSON from PageSpeed Insights")
            return _parse_psi_payload(
                data,
                url=url.strip(),
                strategy=strat,
                keyless=(active_key is None),
                receipt=receipt,
            )
        except HTTPError as exc:
            try:
                err_raw = exc.read(MAX_ERROR_BYTES + 1).decode("utf-8", errors="replace")
            except (OSError, ValueError, HTTPException):
                err_raw = ""
            finally:
                exc.close()
            if active_key is not None and _is_google_service_blocked(exc.code, err_raw):
                mark_service_disabled(active_key, "pagespeedonline")
                active_key = None
                if attempt == 0:
                    continue
            return {
                "ok": False,
                "fallback_recommended": True,
                "http_status": exc.code,
                "reason": redact_google_key(f"HTTP {exc.code}: {err_raw[:400]}", key=raw_key),
                "sku": "pagespeed_insights",
                "stipend": receipt,
                "cost_usd": 0.0,
            }
        except (URLError, OSError, TimeoutError, HTTPException, ValueError) as exc:
            return {
                "ok": False,
                "fallback_recommended": True,
                "reason": redact_google_key(f"{type(exc).__name__}: {exc}", key=raw_key),
                "sku": "pagespeed_insights",
                "stipend": receipt,
                "cost_usd": 0.0,
            }

    return {
        "ok": False,
        "fallback_recommended": True,
        "reason": "pagespeed_exhausted_attempts",
        "sku": "pagespeed_insights",
        "stipend": receipt,
        "cost_usd": 0.0,
    }


def google_custom_search(
    query: str,
    *,
    cx: str | None = None,
    api_key: str | None = None,
    num: int = 10,
    ledger_path: Path | str | None = None,
    timeout: float = 15,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Query Google Custom Search JSON API within the 85/day free stipend cap."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a nonempty string")
    if type(num) is not int or num < 1 or num > 10:
        raise ValueError("num must be an integer between 1 and 10")

    key = (api_key or "").strip() if isinstance(api_key, str) else ""
    if not key:
        key = resolve_google_api_key("GOOGLE_CSE_API_KEY", "GOOGLE_API_KEY", "GOOGLE_MAPS_API_KEY") or ""
    engine_id = (cx or os.environ.get("GOOGLE_CSE_CX") or os.environ.get("GOOGLE_CUSTOM_SEARCH_CX") or "").strip()

    if not key or not engine_id:
        return {
            "ok": False,
            "fallback_recommended": True,
            "reason": "missing_google_cse_key_or_cx",
            "sku": "custom_search",
            "cost_usd": 0.0,
        }

    if is_service_disabled(key, "customsearch"):
        return {
            "ok": False,
            "fallback_recommended": True,
            "reason": "service_disabled_in_session",
            "sku": "custom_search",
            "cost_usd": 0.0,
        }

    allowed, receipt = reserve_stipend("custom_search", count=1, ledger_path=ledger_path, now=now)
    if not allowed:
        return {
            "ok": False,
            "fallback_recommended": True,
            "reason": receipt.get("reason") or "stipend_hard_cap_reached",
            "sku": "custom_search",
            "stipend": receipt,
            "cost_usd": 0.0,
        }

    qs = urlencode({"key": key, "cx": engine_id, "q": query.strip(), "num": str(num)})
    req = Request("https://www.googleapis.com/customsearch/v1?" + qs, method="GET")
    try:
        with _google_open(req, timeout=float(timeout)) as resp:
            raw_bytes = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(raw_bytes) > MAX_RESPONSE_BYTES:
            return {
                "ok": False,
                "fallback_recommended": True,
                "reason": "response_exceeded_max_bytes",
                "sku": "custom_search",
                "stipend": receipt,
                "cost_usd": 0.0,
            }
        data = json.loads(raw_bytes.decode("utf-8"))
    except HTTPError as exc:
        try:
            err_raw = exc.read(MAX_ERROR_BYTES + 1).decode("utf-8", errors="replace")
        except (OSError, ValueError, HTTPException):
            err_raw = ""
        finally:
            exc.close()
        if _is_google_service_blocked(exc.code, err_raw):
            mark_service_disabled(key, "customsearch")
        return {
            "ok": False,
            "fallback_recommended": True,
            "http_status": exc.code,
            "reason": redact_google_key(f"HTTP {exc.code}: {err_raw[:400]}", key=key),
            "sku": "custom_search",
            "stipend": receipt,
            "cost_usd": 0.0,
        }
    except (URLError, OSError, TimeoutError, HTTPException, ValueError) as exc:
        return {
            "ok": False,
            "fallback_recommended": True,
            "reason": redact_google_key(f"{type(exc).__name__}: {exc}", key=key),
            "sku": "custom_search",
            "stipend": receipt,
            "cost_usd": 0.0,
        }

    raw_items = data.get("items") if isinstance(data, dict) else None
    items: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_items or [], start=1):
        if not isinstance(item, dict):
            continue
        link = item.get("link") or ""
        domain = _extract_domain(link) or (
            str(item.get("displayLink") or "").strip().lower().removeprefix("www.") or None
        )
        items.append(
            {
                "rank": idx,
                "title": item.get("title"),
                "url": link,
                "domain": domain,
                "snippet": item.get("snippet"),
                "source": "google-cse-free-stipend",
            }
        )

    return {
        "ok": True,
        "fallback_recommended": False,
        "source": "google-cse-free-stipend",
        "sku": "custom_search",
        "cost_usd": 0.0,
        "stipend": receipt,
        "items": items,
    }
