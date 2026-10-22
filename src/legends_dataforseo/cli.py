"""JSON CLI with offline previews and explicit execution."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import sys
from pathlib import Path

from . import __version__
from .client import (ApiError, CredentialError, RouteError, api_request,
                     credential_status, estimate_cost, load_routes, route_for, normalize_path)
from .documentation import DocumentationError, docs_index, docs_read, docs_search, docs_sections
from .output import ResponseFile, response_view, validate_view
from .tasks import retrieval_path, wait_task


def _output_flags(parser):
    parser.add_argument("--output", type=Path, help="Save full JSON to a NEW file; print a summary")
    parser.add_argument("--summary", action="store_true", help="Show task IDs, statuses and costs without results")
    parser.add_argument("--select", action="append", help="Show a dotted field; repeat for multiple fields")
    parser.add_argument("--limit", type=int, help="Limit displayed lists; full output file is unaffected")


def _execution_flags(parser):
    parser.add_argument("--execute", action="store_true", help="Confirm this request may incur charges")
    parser.add_argument("--estimated-cost-usd", type=float, help="Reviewed estimate for this entire request")
    parser.add_argument("--max-cost-usd", type=float, help="Local preflight ceiling; not a provider billing cap")
    parser.add_argument("--timeout", type=float, default=90)
    _output_flags(parser)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["evidence"]:
        from .evidence import main as evidence_main
        return evidence_main(argv[1:])
    parser = argparse.ArgumentParser(prog="legends-dataforseo", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("routes", help="Offline operation registry")
    sub.add_parser("evidence", help="Offline export, inventory, verify, reuse, and view")
    route = sub.add_parser("route")
    route.add_argument("operation")
    estimate = sub.add_parser("estimate", help="Offline baseline estimate")
    estimate.add_argument("operation")
    estimate.add_argument("--tasks", type=int, default=1)
    estimate.add_argument("--depth", type=int, default=0)
    estimate.add_argument("--items", type=int, default=0)
    doctor = sub.add_parser("doctor", help="Installation and credential presence; no values")
    doctor.add_argument("--live", action="store_true", help="Also make a no-charge account authentication probe")
    call = sub.add_parser("call", help="Preview or execute a documented v3 endpoint")
    call.add_argument("path")
    call.add_argument("--method", choices=["GET", "POST"], default=None)
    call.add_argument("--body-file", type=Path, help="UTF-8 JSON task array; do not include credentials")
    _execution_flags(call)
    for name in ("serp", "maps", "demand", "business"):
        command = sub.add_parser(name, help="Preview a " + name + " task; --execute sends it")
        command.add_argument("keywords", nargs="+" if name == "demand" else 1)
        command.add_argument("--language-code", default="en")
        if name == "maps":
            command.add_argument("--location-coordinate", required=True)
        else:
            command.add_argument("--location-code", type=int, default=2840)
        if name in ("serp", "maps"):
            command.add_argument("--depth", type=int, default=100 if name == "maps" else 10)
        _execution_flags(command)
    view = sub.add_parser("view", help="Inspect saved JSON locally without another API request")
    view.add_argument("file", type=Path)
    _output_flags(view)
    wait = sub.add_parser("wait", help="Retrieve an existing task with bounded GET polling; never submits")
    wait.add_argument("path")
    wait.add_argument("--execute", action="store_true", help="Start bounded retrieval; otherwise preview")
    wait.add_argument("--attempts", type=int, default=5)
    wait.add_argument("--interval", type=float, default=5)
    wait.add_argument("--timeout", type=float, default=90)
    wait.add_argument("--max-elapsed", type=float, default=300)
    _output_flags(wait)
    docs = sub.add_parser("docs", help="Discover official documentation; no API credentials or charges")
    doc_commands = docs.add_subparsers(dest="docs_command", required=True)
    for name in ("index", "sections", "search", "read"):
        doc = doc_commands.add_parser(name)
        doc.add_argument("--cache-dir", type=Path)
        doc.add_argument("--offline", action="store_true", help="Use only cached documentation, even when stale")
        doc.add_argument("--refresh", action="store_true")
        doc.add_argument("--timeout", type=float, default=20)
        if name in ("index", "search"):
            doc.add_argument("--section")
        if name == "search":
            doc.add_argument("query")
            doc.add_argument("--limit", type=int, default=20)
        if name == "read":
            doc.add_argument("path")
    args = parser.parse_args(argv)
    try:
        validate_view(getattr(args, "select", None),
                      getattr(args, "limit", None) if args.command != "docs" else None)
        output = getattr(args, "output", None)
        # Reserve before credentials/HTTP so an existing file cannot cause a paid rerun.
        with ResponseFile(output) if output else nullcontext() as destination:
            try:
                result = _run(args)
            except ApiError as exc:
                if exc.response is not None:
                    _emit(exc.response, args, destination, error_response=True)
                raise
            return 0 if _emit(result, args, destination) else 2
    except (ApiError, CredentialError, ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, (ApiError, CredentialError, RouteError, DocumentationError)) else "Invalid input or local file error."
        error = {"error": type(exc).__name__, "message": message}
        if isinstance(exc, ApiError):
            error["request_may_have_completed"] = exc.request_may_have_completed
        print(json.dumps(error), file=sys.stderr)
        return 2


def _run(args):
    if args.command == "docs":
        options = dict(cache_dir=args.cache_dir, offline=args.offline,
                       refresh=args.refresh, timeout=args.timeout)
        if args.docs_command == "index":
            return docs_index(section=args.section, **options)
        if args.docs_command == "sections":
            return docs_sections(**options)
        if args.docs_command == "search":
            return docs_search(args.query, limit=args.limit, section=args.section, **options)
        return docs_read(args.path, **options)
    elif args.command == "view":
        return json.loads(args.file.read_text(encoding="utf-8-sig"))
    elif args.command == "wait":
        path = retrieval_path(args.path)
        if not args.execute:
            return {"preview": True, "path": path, "method": "GET", "max_attempts": args.attempts,
                    "interval": args.interval, "timeout": args.timeout, "max_elapsed": args.max_elapsed}
        return wait_task(path, max_attempts=args.attempts, interval=args.interval,
                         timeout=args.timeout, max_elapsed=args.max_elapsed, consumer="cli")
    elif args.command == "routes":
        result = load_routes()
    elif args.command == "route":
        result = route_for(args.operation)
    elif args.command == "estimate":
        result = estimate_cost(args.operation, tasks=args.tasks, depth=args.depth, items=args.items)
    elif args.command == "doctor":
        result = {"version": __version__, "routes": len(load_routes()["routes"]),
                  "credentials": credential_status(), "live": False}
        if args.live:
            response = api_request("/appendix/user_data")
            statuses = [t.get("status_code") for t in response.get("tasks") or []]
            if not statuses or any(s != 20000 for s in statuses):
                raise ApiError("Live authentication probe did not succeed.")
            result.update(live=True, status_code=response["status_code"], cost=response.get("cost"))
    else:
        if args.command == "call":
            path = normalize_path(args.path)
            payload = json.loads(args.body_file.read_text(encoding="utf-8-sig")) if args.body_file else None
            method = args.method or ("POST" if payload is not None else "GET")
        else:
            path = route_for(args.command)["path"]
            task = {"language_code": args.language_code}
            if args.command == "demand":
                task.update(keywords=args.keywords, location_code=args.location_code)
            elif args.command == "business":
                task.update(keyword=args.keywords[0], location_code=args.location_code)
            else:
                task.update(keyword=args.keywords[0], depth=args.depth)
                if args.command == "maps":
                    task["location_coordinate"] = args.location_coordinate
                else:
                    task["location_code"] = args.location_code
            payload, method = [task], "POST"
        if not args.execute:
            result = {"preview": True, "path": path, "method": method, "body": payload,
                      "estimated_cost_usd": args.estimated_cost_usd, "max_cost_usd": args.max_cost_usd}
        else:
            if method == "POST" and (args.estimated_cost_usd is None or args.max_cost_usd is None):
                raise RouteError("Paid CLI execution requires --estimated-cost-usd and --max-cost-usd.")
            result = api_request(path, payload, method=method, confirm=True, timeout=args.timeout,
                                 consumer="cli", estimated_cost_usd=args.estimated_cost_usd,
                                 max_cost_usd=args.max_cost_usd)
    return result


def _emit(result, args, destination, *, error_response=False):
    if destination:
        try:
            destination.write(result)
        except (OSError, ValueError):
            print(json.dumps(result, indent=2, ensure_ascii=False))
            print(json.dumps({"error": "OutputError", "message":
                  "Response received but saving failed. Full response printed to stdout; do not repeat the request."}),
                  file=sys.stderr)
            return False
    limit = getattr(args, "limit", None) if args.command != "docs" else None
    print(json.dumps(response_view(result, summary=getattr(args, "summary", False) or error_response,
                     select=getattr(args, "select", None), limit=limit,
                     output_file=str(destination.path) if destination else None),
                     indent=2, ensure_ascii=False))
    return True
