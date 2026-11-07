"""Explicit, offline response views. Never modify the caller's raw envelope."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any


def validate_view(select: list[str] | None = None, limit: int | None = None) -> None:
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be a positive integer")
    if select is not None and (not isinstance(select, list) or any(
        not isinstance(path, str) or len(path) > 512 or not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", path)
        for path in select
    )):
        raise ValueError("Select fields with dotted keys and numeric list indexes, such as tasks.0.result.0.items")


def _lookup(value: Any, path: str) -> tuple[bool, Any]:
    for key in path.split("."):
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isascii() and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            return False, None
    return True, value


def _bounded(value: Any, limit: int | None, path: str, omitted: dict[str, int]) -> Any:
    if isinstance(value, list):
        if limit is not None and len(value) > limit:
            omitted[path] = len(value) - limit
        return [_bounded(item, limit, f"{path}.{i}", omitted)
                for i, item in enumerate(value if limit is None else value[:limit])]
    if isinstance(value, dict):
        return {key: _bounded(item, limit, f"{path}.{key}", omitted) for key, item in value.items()}
    return value


def response_summary(response: Any) -> dict[str, Any]:
    """Retain every task ID/status/cost; omit query bodies and result contents."""
    if not isinstance(response, dict):
        return {"type": type(response).__name__}
    report = {key: response[key] for key in ("id", "status_code", "cost", "tasks_count", "tasks_error")
              if key in response}
    report["cost_reported"] = response.get("cost") is not None
    if isinstance(response.get("items"), list):
        report["items_returned"] = len(response["items"])
    tasks = response.get("tasks")
    if isinstance(tasks, list):
        report["tasks"] = []
        for task in tasks:
            if not isinstance(task, dict):
                report["tasks"].append({"malformed": True})
                continue
            row = {key: task[key] for key in ("id", "status_code", "cost", "result_count") if key in task}
            results = task.get("result")
            if isinstance(results, list):
                row["result_objects"] = len(results)
                row["items_returned"] = sum(len(result["items"]) for result in results
                                             if isinstance(result, dict) and isinstance(result.get("items"), list))
                integrities = [result["serp_integrity"] for result in results
                               if isinstance(result, dict) and isinstance(result.get("serp_integrity"), dict)]
                if len(integrities) == 1:
                    row["serp_integrity"] = integrities[0]
                elif len(integrities) > 1:
                    row["serp_integrity"] = integrities
            report["tasks"].append(row)
    # Preserve the control state of a bounded wait without dumping its response twice.
    if "response" in response and "attempts" in response:
        report.update({key: response[key] for key in
                       ("state", "stop_reason", "attempts", "elapsed_seconds", "reported_cost_usd", "reported_cost_complete", "cost_scope")
                       if key in response})
        report["response"] = response_summary(response["response"])
    if response.get("preview") is True:
        report.update({key: response[key] for key in ("preview", "path", "method", "estimated_cost_usd", "max_cost_usd")
                       if key in response})
    return report


def response_view(response: Any, *, summary: bool = False, select: list[str] | None = None,
                  limit: int | None = None, output_file: str | None = None) -> Any:
    """Return a raw copy by default, or a labelled view with omission counts."""
    validate_view(select, limit)
    if not (summary or select or limit is not None or output_file):
        return copy.deepcopy(response)
    view: dict[str, Any] = {"view": True, "summary": response_summary(response)}
    omitted: dict[str, int] = {}
    if select:
        view["selected"] = {}
        missing = []
        for path in select:
            found, value = _lookup(response, path)
            if found:
                view["selected"][path] = _bounded(value, limit, path, omitted)
            else:
                missing.append(path)
        if missing:
            view["missing_fields"] = missing
    elif limit is not None and not summary:
        view["data"] = _bounded(response, limit, "$", omitted)
    if omitted:
        view["omitted_items"] = omitted
    if output_file:
        view["output_file"] = output_file
    return view


class ResponseFile:
    """Reserve an explicit new file before HTTP; never overwrite existing results."""

    def __init__(self, path: Path):
        self.path = path
        self.stream = None
        self.written = False

    def __enter__(self):
        # Do not create directory trees silently: a typo must fail before a paid call.
        self.stream = self.path.open("x", encoding="utf-8")
        return self

    def write(self, response: Any) -> None:
        text = json.dumps(response, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        self.stream.write(text)
        self.stream.flush()
        self.written = True

    def __exit__(self, exc_type, exc_value, traceback):
        self.stream.close()
        # Keep partial output after a write failure for diagnosis. Empty reservations
        # are removed only while we still own this open operation's newly created file.
        if not self.written and self.path.exists() and self.path.stat().st_size == 0:
            self.path.unlink()
