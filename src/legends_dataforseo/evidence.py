"""Offline evidence handoff. No provider calls or canonical vault mutations."""
import argparse
import hashlib
import json
import math
import sys

from .output import response_view
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "legends-research-evidence/v1"


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)


def _loads(value):
    """Reject ambiguous/nonstandard JSON before hashing or interpreting evidence."""
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result
    def constant(value):
        raise ValueError("nonfinite JSON number")
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite JSON number")
        return number
    return json.loads(value, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


_MACOS_SYSTEM_SYMLINKS = {Path("/var"), Path("/tmp"), Path("/etc")}


def _safe_path(value):
    """Do not erase linked ancestors before checking package provenance paths."""
    path = Path(value).expanduser().absolute()
    for idx, part in enumerate((path, *path.parents)):
        if idx > 0 and sys.platform == "darwin" and part in _MACOS_SYSTEM_SYMLINKS:
            continue
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ValueError("linked evidence path")
        try:
            # Python 3.10/3.11 Windows lack Path.is_junction().
            st = part.lstat()
            if getattr(st, "st_reparse_tag", None) == 0xA0000003 or (
                getattr(st, "st_file_attributes", 0) & 0x400
            ):
                raise ValueError("junction evidence path")
        except FileNotFoundError:
            pass
    return path


def _date(value):
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("observation time requires timezone")
    return date


def export(response, destination, *, workspace, endpoint, request, observed_at,
           producer="legends-dataforseo"):
    """Bank one standard response with explicit scope; never infer collection time."""
    if not isinstance(workspace, str) or not workspace.strip():
        raise ValueError("explicit client/workspace identity required")
    if not endpoint.startswith("/") or any(x in endpoint for x in ("?", "#", ":", "..")):
        raise ValueError("endpoint must be a v3-relative path without query credentials")
    if not isinstance(request, (list, dict)) or not request:
        raise ValueError("full original request/settings required")
    _date(observed_at)
    raw = _safe_path(response).read_bytes()
    data = _loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ValueError("standard task envelope required; compact views are not raw evidence")
    if any(not isinstance(t, dict) for t in data["tasks"]):
        raise ValueError("invalid task envelope")
    text = ("# Research evidence\n\n"
            "Unreviewed source material; not an accepted finding or recommendation.\n\n"
            f"Observed: {observed_at}\n\n"
            "[Settings, task IDs and reported costs](manifest.json) · "
            "[Complete provider response](response.json)\n\n"
            "Costs are snapshots, not incremental charges. Do not sum repeated task receipts.\n"
            "Use the explicit workspace and complete request settings when considering reuse.\n"
            "Pending, empty and error responses are evidence of those states, not completed research.\n"
            "For Legends Obsidian: stage in the selected vault inbox, capture immutably, "
            "then ingest through the existing transaction protocol.\n")
    note = text.encode("utf-8")
    manifest = dict(schema=SCHEMA, workspace=workspace, endpoint=endpoint,
                    request=request, observed_at=observed_at, producer=producer,
                    response_sha256=_hash(raw), note_sha256=_hash(note), status_code=data.get("status_code"),
                    reported_response_cost=data.get("cost"),
                    tasks=[{k: t.get(k) for k in ("id", "status_code", "cost")} for t in data["tasks"]])
    identity = _hash(_json(manifest).encode())
    manifest["evidence_id"] = identity
    target = _safe_path(destination) / identity
    if target.exists():
        verify(target)
        return target
    target.mkdir(parents=True, exist_ok=False)
    # Exclusive writes: interrupted exports remain detectable, never silently repaired.
    with (target / "response.json").open("xb") as f:
        f.write(raw)
    with (target / "manifest.json").open("x", encoding="utf-8") as f:
        f.write(_json(manifest))
    with (target / "README.md").open("xb") as f:
        f.write(note)
    return target


def verify(package):
    root = _safe_path(package)
    if root.is_symlink():
        raise ValueError("linked evidence package")
    for name in ("manifest.json", "response.json", "README.md"):
        if (root / name).is_symlink() or not (root / name).is_file():
            raise ValueError("incomplete or linked evidence package")
    m = _loads((root / "manifest.json").read_text(encoding="utf-8"))
    identity = m.pop("evidence_id")
    if m.get("schema") != SCHEMA or _hash(_json(m).encode()) != identity:
        raise ValueError("manifest integrity mismatch")
    if _hash((root / "response.json").read_bytes()) != m["response_sha256"]:
        raise ValueError("response integrity mismatch")
    if "note_sha256" in m and _hash((root / "README.md").read_bytes()) != m["note_sha256"]:
        raise ValueError("note integrity mismatch")
    raw = _loads((root / "response.json").read_bytes())
    expected_tasks = [{k: t.get(k) for k in ("id", "status_code", "cost")}
                      for t in raw["tasks"]]
    if (m["tasks"] != expected_tasks or m["status_code"] != raw.get("status_code")
            or m["reported_response_cost"] != raw.get("cost")):
        raise ValueError("manifest metadata differs from raw response")
    return dict(m, evidence_id=identity, note_integrity="verified" if "note_sha256" in m else "not_recorded")


def assess_reuse(package, *, workspace, endpoint, request, max_age_hours, now=None):
    """Conservative eligibility, not permission to spend or proof of semantic relevance."""
    if not math.isfinite(max_age_hours) or max_age_hours < 0:
        raise ValueError("finite nonnegative freshness limit required")
    m = verify(package)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now requires timezone")
    reasons = []
    for key, value in (("workspace", workspace), ("endpoint", endpoint), ("request", request)):
        if m[key] != value:
            reasons.append(key + " differs")
    age = (current - _date(m["observed_at"])).total_seconds() / 3600
    if age < 0 or age > max_age_hours:
        reasons.append("future or stale observation")
    if m["status_code"] != 20000 or not m["tasks"] or any(t["status_code"] != 20000 for t in m["tasks"]):
        reasons.append("response is not fully successful")
    raw = _loads((_safe_path(package) / "response.json").read_bytes())
    if any(not t.get("result") for t in raw["tasks"]):
        reasons.append("result absent; review empty evidence separately")
    return dict(eligible=not reasons, reasons=reasons, evidence_id=m["evidence_id"],
                requires_semantic_review=True, provider_calls=0)



def inventory(bank, *, workspace=None, endpoint=None, query=None, limit=50, offset=0):
    """List manifest metadata only. Raw response/note integrity is NOT checked."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if query is not None and (not isinstance(query, str) or not query.strip()):
        raise ValueError("query must be nonempty text")
    root = _safe_path(bank)
    if not root.is_dir():
        raise ValueError("evidence bank directory does not exist")
    matches, errors, inspected = [], [], 0
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() and not child.is_symlink():
            continue
        inspected += 1
        try:
            manifest = child / "manifest.json"
            if child.is_symlink() or manifest.is_symlink():
                raise ValueError("linked package")
            if manifest.stat().st_size > 2000000:
                raise ValueError("manifest too large")
            m = _loads(manifest.read_text(encoding="utf-8"))
            identity = m.pop("evidence_id")
            if m.get("schema") != SCHEMA or _hash(_json(m).encode()) != identity:
                raise ValueError("invalid manifest identity")
            if not isinstance(m.get("workspace"), str) or not isinstance(m.get("endpoint"), str):
                raise ValueError("invalid scope")
            _date(m["observed_at"])
            if workspace is not None and m["workspace"] != workspace:
                continue
            if endpoint is not None and m["endpoint"] != endpoint:
                continue
            if query is not None and query.casefold() not in _json(m).casefold():
                continue
            matches.append({key: m.get(key) for key in
                ("workspace", "endpoint", "observed_at", "producer", "status_code", "reported_response_cost", "tasks")}
                | {"evidence_id": identity, "package": str(child),
                   "manifest_integrity": "verified", "response_integrity": "not_checked",
                   "note_integrity": "not_checked" if "note_sha256" in m else "not_recorded"})
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            errors.append({"package": str(child), "reason": "unreadable_or_invalid_manifest"})
    return {"schema": "legends-research-inventory/v1", "bank": str(root),
            "packages_scanned": inspected, "matches": len(matches), "offset": offset,
            "limit": limit, "items": matches[offset:offset + limit],
            "next_offset": offset + limit if offset + limit < len(matches) else None,
            "errors": errors[:limit], "errors_omitted": max(0, len(errors)-limit),
            "provider_calls": 0, "response_integrity": "not_checked"}


def view(package, *, summary=False, select=None, limit=None):
    """Verify the complete package then derive a labelled local view."""
    manifest = verify(package)
    safe_pkg = _safe_path(package)
    raw = _loads((safe_pkg / "response.json").read_bytes())
    # Full response by default; the caller explicitly chooses a focused view.
    result = response_view(raw, summary=summary,
                           select=select, limit=limit)
    return {"evidence_id": manifest["evidence_id"], "package": str(safe_pkg.resolve()),
            "integrity": "verified", "note_integrity": manifest["note_integrity"],
            "provider_calls": 0, "response_view": result}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None)
        if isinstance(encoding, str) and encoding.lower().replace("-", "") != "utf8":
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                try:
                    reconfigure(encoding="utf-8", errors="replace")
                except (OSError, ValueError, AttributeError):
                    pass
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    save = sub.add_parser("export")
    save.add_argument("response")
    save.add_argument("destination")
    for name in ("workspace", "endpoint", "request-file", "observed-at"):
        save.add_argument("--" + name, required=True)
    check = sub.add_parser("verify")
    check.add_argument("package")
    for name in ("inventory", "find"):
        listing = sub.add_parser(name)
        listing.add_argument("bank")
        listing.add_argument("--workspace")
        listing.add_argument("--endpoint")
        listing.add_argument("--query", required=name == "find")
        listing.add_argument("--limit", type=int, default=50)
        listing.add_argument("--offset", type=int, default=0)
    reuse = sub.add_parser("reuse")
    reuse.add_argument("package")
    for name in ("workspace", "endpoint", "request-file"):
        reuse.add_argument("--" + name, required=True)
    reuse.add_argument("--max-age-hours", type=float, required=True)
    reuse.add_argument("--now", help="Explicit timezone-aware evaluation time; default current UTC")
    display = sub.add_parser("view")
    display.add_argument("package")
    display.add_argument("--summary", action="store_true")
    display.add_argument("--select", action="append")
    display.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify(args.package)
            print(_json({"verified": True, "evidence_id": result["evidence_id"],
                         "note_integrity": result["note_integrity"]}))
        elif args.command in ("inventory", "find"):
            print(_json(inventory(args.bank, workspace=args.workspace, endpoint=args.endpoint,
                                 query=args.query, limit=args.limit, offset=args.offset)))
        elif args.command == "reuse":
            req_path = _safe_path(args.request_file)
            if req_path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError("request file too large")
            request = _loads(req_path.read_text(encoding="utf-8-sig"))
            result = assess_reuse(args.package, workspace=args.workspace, endpoint=args.endpoint,
                                 request=request, max_age_hours=args.max_age_hours,
                                 now=_date(args.now) if args.now else None)
            print(_json(result))
            return 0 if result["eligible"] else 2
        elif args.command == "view":
            print(_json(view(args.package, summary=args.summary, select=args.select, limit=args.limit)))
        else:
            req_path = _safe_path(args.request_file)
            if req_path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError("request file too large")
            request = _loads(req_path.read_text(encoding="utf-8-sig"))
            print(export(args.response, args.destination, workspace=args.workspace,
                         endpoint=args.endpoint, request=request, observed_at=args.observed_at))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print(_json({"error": "EvidenceError", "message": "Invalid input, unavailable file, or evidence integrity failure. No provider request made."}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
