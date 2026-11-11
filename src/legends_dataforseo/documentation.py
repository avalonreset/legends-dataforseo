"""Unauthenticated discovery of official documentation, with an offline cache.

Documentation is reference material, not executable agent instructions. Search
only reads the index; it never fans out to download individual endpoint pages.
"""
from __future__ import annotations

import hashlib
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .client import _safe_local_path

DOCS_ROOT = "https://docs.dataforseo.com/v3"
INDEX_URL = DOCS_ROOT + "/llms.txt/"
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024


class DocumentationError(ValueError):
    """Invalid documentation input, unavailable cache, or sanitized fetch error."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DocumentationError("Documentation redirects are not permitted")


def _open(request, timeout):
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


def _url(path):
    if not isinstance(path, str) or not path:
        raise DocumentationError("A documentation path is required")
    if path.startswith("https://"):
        parsed = urlsplit(path)
        if (parsed.netloc != "docs.dataforseo.com" or parsed.query or parsed.fragment):
            raise DocumentationError("Use an official DataForSEO documentation URL")
        path = parsed.path.rstrip("/")
        if path != "/v3.md" and not path.startswith("/v3/"):
            raise DocumentationError("Use a v3 documentation path")
    if path in ("/v3.md", "v3.md"):
        return DOCS_ROOT + ".md"
    path = path.removeprefix("/").removeprefix("v3/").rstrip("/")
    path = path.removesuffix(".md")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*", path):
        raise DocumentationError("Invalid documentation path")
    return DOCS_ROOT + "/" + path + ".md/"


def _default_cache():
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    legacy = root / "legends-dataforseo-kit" / "docs"
    return legacy if legacy.exists() else root / "legends-dataforseo" / "docs"


def _number(value, name, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or (positive and value == 0)):
        raise DocumentationError(f"{name} must be a finite {'positive' if positive else 'nonnegative'} number")


def _document(url, *, cache_dir=None, offline=False, refresh=False, ttl=86400, timeout=20):
    _number(ttl, "ttl")
    _number(timeout, "timeout", positive=True)
    if not isinstance(offline, bool) or not isinstance(refresh, bool):
        raise DocumentationError("offline and refresh must be booleans")
    if offline and refresh:
        raise DocumentationError("offline and refresh cannot be combined")
    try:
        cache = _safe_local_path(cache_dir if cache_dir is not None else _default_cache())
        target = _safe_local_path(cache / (hashlib.sha256(url.encode()).hexdigest() + ".json"))
    except ValueError as exc:
        raise DocumentationError("Linked documentation cache paths are not permitted") from exc
    saved = None
    try:
        if target.stat().st_size <= MAX_DOCUMENT_BYTES * 7:
            candidate = json.loads(target.read_text(encoding="utf-8"))
            fetched = candidate.get("fetched_at")
            if (candidate.get("source_url") == url and isinstance(candidate.get("text"), str)
                    and len(candidate["text"].encode("utf-8")) <= MAX_DOCUMENT_BYTES
                    and isinstance(fetched, (int, float)) and not isinstance(fetched, bool)
                    and math.isfinite(fetched) and 0 <= fetched <= time.time()):
                saved = candidate
    except (OSError, ValueError, AttributeError, UnicodeError):
        pass
    if saved is not None:
        stale = time.time() - saved["fetched_at"] >= ttl
        if offline or (not refresh and not stale):
            return {"source_url": url, "text": saved["text"], "fetched_at": saved["fetched_at"],
                    "cached": True, "stale": stale}
    if offline:
        raise DocumentationError("No valid cached documentation is available offline")
    request = Request(url, headers={"Accept": "text/plain, text/markdown", "User-Agent": "legends-dataforseo docs"})
    try:
        with _open(request, timeout) as response:
            if response.geturl() != url:
                raise DocumentationError("Documentation redirects are not permitted")
            raw = response.read(MAX_DOCUMENT_BYTES + 1)
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise DocumentationError("Documentation exceeds the download size limit")
        text = raw.decode("utf-8")
        if not text.strip() or text.lstrip().lower().startswith(("<!doctype html", "<html")):
            raise DocumentationError("Expected plain text or Markdown documentation")
    except HTTPError as error:
        raise DocumentationError(f"Documentation HTTP request failed ({error.code})") from None
    except (URLError, OSError, UnicodeError, HTTPException):
        raise DocumentationError("Documentation could not be downloaded or decoded") from None
    result = {"source_url": url, "text": text, "fetched_at": time.time(), "cached": False, "stale": False}
    temporary = None
    try:
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(result, handle, ensure_ascii=False)
        temporary.replace(target)
    except OSError:
        # A read-only cache must not discard a successfully downloaded document.
        result["cache_warning"] = "Documentation was fetched but could not be cached"
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return result


def docs_read(path, **options):
    """Read endpoint Markdown. Options: cache_dir, offline, refresh, ttl, timeout."""
    return _document(_url(path), **options)


def _entries(text):
    headings = {}
    entries = []
    seen = set()
    for line in text.splitlines():
        heading = re.match(r"^(#{2,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            level = len(heading[1])
            headings = {key: value for key, value in headings.items() if key < level}
            headings[level] = heading[2]
            continue
        link = re.match(r"^\s*[-*]\s+\[([^\]]+)\]\((https://[^\s)]+)\)(?:\s*[—–-]\s*(.*))?$", line)
        if not link:
            continue
        try:
            url = _url(link[2])
        except DocumentationError:
            continue
        if url in seen:
            continue
        seen.add(url)
        path = url.removeprefix(DOCS_ROOT + "/").removesuffix(".md/")
        if url == DOCS_ROOT + ".md":
            path = "/v3.md"
        entries.append({"title": link[1], "path": path, "url": url,
                        "section": headings.get(2, "General"),
                        "headings": list(headings.values()), "description": link[3] or ""})
    return entries


def docs_index(section=None, **options):
    """List official endpoint references, optionally by exact section name."""
    if section is not None and (not isinstance(section, str) or not section.strip()):
        raise DocumentationError("section must be a nonempty string")
    result = _document(INDEX_URL, **options)
    entries = _entries(result.pop("text"))
    if not entries:
        raise DocumentationError("The official documentation index contained no recognized entries")
    if section is not None:
        entries = [entry for entry in entries if entry["section"].casefold() == section.strip().casefold()]
    return {**result, "entries": entries}


def docs_sections(**options):
    """List top-level documentation sections in source order."""
    result = docs_index(**options)
    entries = result.pop("entries")
    return {**result, "sections": list(dict.fromkeys(entry["section"] for entry in entries))}


def docs_search(query, *, limit=20, section=None, **options):
    """Search index titles, paths, and descriptions; no endpoint fan-out."""
    if not isinstance(query, str) or not query.strip():
        raise DocumentationError("query must be a nonempty string")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise DocumentationError("limit must be an integer between 1 and 1000")
    terms = query.casefold().split()
    result = docs_index(section=section, **options)
    entries = result.pop("entries")
    def score(entry):
        title_path = (entry["title"] + " " + entry["path"]).casefold()
        all_text = title_path + " " + entry["description"].casefold()
        return sum(3 if term in title_path else 1 for term in terms) if all(term in all_text for term in terms) else 0
    ranked = [(score(entry), entry) for entry in entries]
    ranked = sorted((item for item in ranked if item[0]), key=lambda item: item[0], reverse=True)
    return {**result, "query": query, "total_matches": len(ranked), "matches": [entry for _, entry in ranked[:limit]]}
