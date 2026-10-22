# Consumer integration

For optional offline research memory and legends-empire intake, see
[Research memory handoff](RESEARCH-MEMORY.md). The local candidate preserves
standalone operation; it does not change the transport or consumer pins.

Install into the consumer's virtual environment using the release wheel or
an immutable Git commit, then import `legends_dataforseo`. Do not vendor a second
credential loader or HTTP implementation. Offline rendering, estimates, and
fixture processing should remain available without a provider account.

## legends-geogrid (and optional legends-jev OCR handoff)

Lazy-import the transport only when the user executes a paid scan or retrieves
an existing task. Preserve the consumer's budget and task-state checks.

```python
def request_for_scan(path, payload=None, *, timeout=90):
    from legends_dataforseo import api_request
    return api_request(path, payload, timeout=timeout,
                       confirm=payload is not None, consumer="legends-geogrid")
```

Use `/serp/google/maps/task_post` for standard queued scans and
`/serp/google/maps/task_get/advanced/{id}` for results. Live scans use
`/serp/google/maps/live/advanced`. For Google Business Profile enrichment and
title-stuffing inspection (`business_name_review.py`), use
`/business_data/google/my_business_info/task_post` (`business-task-post`) and
`/business_data/google/my_business_info/task_get/{id}` (`business-task-get`), or
the live helper `business("cid:<id>", confirm=True, ...)`. Persist task IDs in
the consumer so a pending response or timeout does not lead to another paid
submission. When `legends-jev` (`>=0.2.0`) is also installed in the user's
`cto-legends` ecosystem, `legends-geogrid` can optionally pass GBP profile
imagery (`logo`, `main_image`) and multi-vantage Street View frames to
`legends_jev.inspect_signage` for optical signage verification.

## legends-github

Research connectors can use the named helpers or the generic transport:

```python
from legends_dataforseo import demand, serp

# Only after explicit execution approval and a reviewed budget:
result = demand(["repository documentation"], confirm=True,
                consumer="legends-github", estimated_cost_usd=0.02,
                max_cost_usd=0.02)
```

The amounts above illustrate a request ceiling, not a current provider quote.
Keep returned research, raw queries, and account data out of public repositories.
The connector owns result selection, summaries, and total research spending.

## Agent-assisted setup

1. Install a pinned release in the consumer environment.
2. Run `legends-dataforseo doctor` and `legends-dataforseo routes` offline.
3. Ask the user to set credentials through their secret manager if missing;
   never request credential values in chat.
4. With credentials configured, run `legends-dataforseo doctor --live`.
5. Preview the request and budget. Pass the caller's existing authorization to
   the noninteractive `confirm`/`--execute` option when executing.
6. Inspect per-task status and actual cost. Save private results only where the
   consumer expects them.
