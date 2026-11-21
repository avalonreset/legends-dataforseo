# Python contract - 0.1.x router-native baseline (stable request signatures)

Public import: `legends_dataforseo`. Package: `legends-dataforseo`.
`API_ROOT = "https://api.dataforseo.com/v3"`.

```python
api_request(
    path, payload=None, *, method=None, body=None,
    credentials=None, timeout=90, log_cost=False, confirm=False,
    consumer="python", estimated_cost_usd=None, max_cost_usd=None,
) -> dict

serp(keyword, *, location_code=2840, location_name=None,
     location_coordinate=None, language_code="en", depth=10,
     device=None, os=None, max_poison_retries=2, labs_fallback=False,
     resolve_poisoned_results=None, incumbent_sites=None,
     decoy_traffic_check=False, verify_no_swap=True, **request_options) -> dict
demand(keywords, *, location_code=2840, language_code="en",
       **request_options) -> dict
competitors(keywords, *, location_code=2840, language_code="en", limit=20,
            **request_options) -> dict
ranked_keywords(target, *, location_code=2840, language_code="en", limit=100,
                **request_options) -> dict
relevant_pages(target, *, location_code=2840, language_code="en", limit=100,
               **request_options) -> dict
maps(keyword, *, location_coordinate, language_code="en", depth=100,
     **request_options) -> dict
business(keyword=None, *, target=None, location_code=2840, location_name=None,
         location_coordinate=None, language_code="en", tag=None,
         **request_options) -> dict
```

`payload` is the existing positional task array. `body` is its keyword alias;
specify only one. `method` is GET or POST; if omitted, a payload selects POST,
otherwise GET. POST requires a nonempty list of task objects. A live SERP call
accepts exactly one task; a SERP queue POST accepts at most 100. Use raw
`api_request` for endpoint-specific fields not exposed by the named helpers.

`path` is relative to v3, optionally beginning `/v3/`. Absolute URLs, query
strings, encoded path traversal, and redirects are rejected. No alternate host
option exists. TLS verification remains enabled.
An optional terminal `.ai` suffix is accepted explicitly. It is never added
automatically. Provider AI responses can differ from standard task envelopes and
omit cost; see [response modes](WORKFLOWS.md#optional-provider-ai-responses).

`confirm=True` is a noninteractive paid/raw opt-in. It is mandatory for POST
and unclassified GET requests. Registered free GET and task retrieval GET need
no paid opt-in. A caller that already approved a scan passes confirm on its
paid request; the transport never adds another approval dialog.

`estimated_cost_usd` is the caller's reviewed estimate for the whole request.
When `max_cost_usd` is supplied, an estimate is required and must not exceed the
ceiling. Both must be finite, nonnegative numbers. This rejects requests before
credentials or HTTP; it is not a provider-side spending limit, reservation,
multi-request budget, or guaranteed price. The caller owns those policies.

## Responses and errors

Returns the complete JSON object when the top-level status is `20000`.
Per-task statuses are never converted into transport errors, even when
`tasks_error` is nonzero. This preserves `20100`, `40601`, `40602`, `40102`, and
other statuses so the caller can distinguish pending, empty, and failed tasks.

`ApiError` indicates an HTTP, network, decoding, or top-level envelope failure.
It has `.response` (decoded object or None) and `.http_status` (integer or None).
Exception messages do not echo provider text, query bodies, or credentials.
`request_may_have_completed` is true after an ambiguous POST transport/protocol
failure. Never treat a timeout as evidence that a paid submission was rejected.
Treat `.response` as private data. `CredentialError` means missing/incomplete
credentials; `RouteError` is invalid input; `CostLimitError(ApiError)` is a
preflight refusal. There are no automatic retries on raw `api_request` calls,
and no account fallbacks, queue polls, or sleeps (`serp()` supports bounded
SearchGuard poison and decoy retries via `max_poison_retries` and optional `labs_fallback`
strictly within the caller's cumulative `max_cost_usd` ceiling). A timeout does
not prove that the provider did not charge.

```python
from legends_dataforseo import ApiError, api_request

try:
    response = api_request("/serp/google/maps/task_get/advanced/" + task_id)
except ApiError as error:
    response = error.response  # Keep private; classify before another request.
```

## Credentials and local state

`Credentials(login, password, source="explicit")` hides login/password in repr.
Explicit credentials override the environment. Otherwise `load_credentials()`
uses DATAFORSEO_LOGIN or DATAFORSEO_USERNAME plus DATAFORSEO_PASSWORD. A partial
process pair fails rather than falling through to another account. Windows
user-scope fallback applies only when the process pair is entirely absent.

No account identity, legacy credentials, account-routing state, or credential
files are bundled. `credential_status()` returns presence/source only.
`log_cost=False` is the default. Opt-in cost logging uses
`LEGENDS_DATAFORSEO_LEDGER` or a user state directory (LOCALAPPDATA,
XDG_STATE_HOME, or ~/.local/state). It stores timestamp, endpoint template,
cost, top-level status, and caller-supplied consumer label. Use a non-sensitive
consumer label. Ledger errors warn without hiding the completed API response.

Offline helpers: `load_routes()`, `route_for(operation)`,
`estimate_cost(operation, *, tasks=1, depth=0, items=0)`, `total_cost(responses)`,
`ledger_path()`. `write_json(Path, payload)` explicitly saves caller-selected data.
Organic SERP integrity helpers: `is_goto_wrapped_url(url)`,
`extract_breadcrumb_target(breadcrumb, website_name=None)`,
`normalize_organic_item(item)`, `assess_serp_integrity(result_block, *, task_data=None)`,
`normalize_serp_envelope(envelope, *, endpoint=None)`.
Decoy SERP helpers (0.1.12): `DECOY_THRESHOLDS`, `site_key(domain_or_url)`,
`organic_item_site_key(item)`, `organic_site_keys(result_block, *, limit=10)`, `overlap_ratio(a, b)`,
`assess_decoy_signals(result_block, *, task_data=None, second_pull_sites=None,
incumbent_sites=None, site_traffic=None, thresholds=None)`,
`apply_decoy_assessment(integrity, decoy)`, `resolve_poisoned_default()`, and
`site_traffic(sites, *, location_code=2840, language_code="en", max_age_days=30,
**request_options)` (paid Labs bulk traffic estimation, cached locally).

## Decoy SERP defense (0.1.12)

`serp()` sends `resolve_poisoned_results: true` on every Google organic task by
default. DataForSEO then compares the SERP against a Google Web-mode reference
page and swaps poisoned organic items; restored items carry `from_web` in their
`checks` array. It costs one extra SERP page per task whether or not anything
is swapped, so `serp()` raises its own per-step estimate by one page and refuses
before HTTP when `max_cost_usd` cannot cover it. Opt out per call with
`resolve_poisoned_results=False`, or process-wide with
`LEGENDS_DATAFORSEO_RESOLVE_POISONED=0`. Raw `api_request` never edits your
payload; add the field yourself there.

The provider resolve is fail-open: when its reference page cannot be fetched it
returns the original results unchanged, so zero `from_web` items is not proof of
a clean page. Every organic `serp_integrity` block therefore also carries a
`decoy` assessment scored from evidence in the response (median age of dated
organic results, AI Overview reference sites vs organic top 20) plus optional
caller evidence (`incumbent_sites`, a second pull, cached Labs traffic via
`decoy_traffic_check=True`). Signals that cannot be computed are omitted and
never count as clean evidence. `decoy.provider_resolve` is `resolved`,
`requested_no_swap` or `not_requested`.

Google now wraps most organic URLs in `google.com/goto` with `domain:
google.com` and no `timestamp`. Site keys therefore come from
`organic_item_site_key(item)`: resolved domain, then breadcrumb host, then a
`website_name` slug, and never `google.com`.

Because the provider is fail-open, a resolve-requested SERP that comes back
clean with zero swaps gets one verification pull (`verify_no_swap=True`,
default; one more resolve-priced page, cap-checked). A swap on the second pull
is adopted (`verification.status = resolved_on_verify`). A top-10 site overlap
at or below `DECOY_THRESHOLDS["pull_overlap_max"]` marks the SERP
`suspect_decoy` + `unresolved` (`unstable`). Agreement marks it `stable`. Any
clean result that is neither provider-resolved nor `stable` (resolve off,
verification disabled, skipped for budget, or failed) carries
`serp_integrity.unverified=true`. `serp_integrity.verification` and
`legends_serp.verification` record the outcome.

When any signal fires the status becomes `suspect_decoy` (`suspect_poisoned`
keeps precedence). `serp()` re-pulls a `suspect_decoy` SERP with resolve forced
on, within `max_poison_retries` and the cumulative `max_cost_usd`. A SERP that is
still suspect after the budget is returned with `serp_integrity.unresolved=true`;
it is never relabelled `clean`. The envelope's `legends_serp` block lists every
attempt with task IDs, resolve flag, provider-reported cost, latency and status,
plus `cost_usd_total` and `latency_ms_total`. Downstream scrapers (for example
legends-firecrawl) should refuse URL sets derived from a suspect SERP unless the
operator overrides.

## Compatibility

The baseline ships optional helpers on top of the stable request signatures and
standard response format. Existing GeoGrid and GitHub callers need no changes.

```python
wait_task(path, *, max_attempts=5, interval=5, timeout=90, max_elapsed=300,
          credentials=None, consumer="python", log_cost=False) -> dict
response_summary(response) -> dict
response_view(response, *, summary=False, select=None, limit=None,
              output_file=None)
docs_index(section=None, **options) -> dict
docs_sections(**options) -> dict
docs_search(query, *, limit=20, section=None, **options) -> dict
docs_read(path, **options) -> dict
```

`wait_task` is a separate opt-in helper, never part of `api_request`. It performs
only supported saved-task GETs. Its report preserves the last original envelope
under `response`, with `state`, `stop_reason`, `attempts`, `elapsed_seconds`,
`reported_cost_usd`, `reported_cost_complete`, and `cost_scope`. Reported cost is
from the last response; retrieval may repeat the original submission cost and
must not be added as another charge. Only pending statuses
20100/40601/40602 cause another GET. Errors, unknown statuses, empty results,
attempt limits, and elapsed bounds stop it; transport exceptions propagate.
See [queue workflow and precise bounds](WORKFLOWS.md).

`response_view` returns a separate view and never mutates the input. Dotted
selection uses numeric indexes (for example `tasks.0.result.0.items`). Summary
task metadata remains complete even when displayed result lists are limited.
`output_file` labels an already saved file; the Python view helper does not write
it. A missing reported cost is flagged, not labelled free. Legacy `total_cost`
sums present costs and treats missing ones as zero; do not use it to claim a
complete bill when responses omit cost or repeat the same task's cost. The
optional cost ledger also records provider reports, not incremental charges.

Documentation options: `cache_dir=None`, `offline=False`, `refresh=False`,
`ttl=86400`, `timeout=20`. These helpers never load API credentials. Failures raise
`DocumentationError`. [Discovery and caching](DISCOVERY.md).

The 0.1.x router-native generation is the supported public baseline. It keeps
the positional `api_request(path, payload)` transport and timeout/consumer/confirm
keywords. Public defaults are single-account, all paid calls confirmed, logging
off, and task statuses caller-owned. Private account failover, workstation
installers, and PowerShell-specific wrappers are not part of the public API.
Pin a release or full commit. Breaking changes will be documented and versioned.

## Research evidence

`legends_dataforseo.evidence` provides offline `export`, `verify`, and
`assess_reuse` helpers. The module CLI supports `export`, `verify`, `inventory`,
`find`, `reuse`, and `view`.
See [research memory](RESEARCH-MEMORY.md) for the complete handoff contract.
No provider calls, implicit vault writes, or request API changes are introduced.


Evidence navigation additions in 0.1.3:

```python
inventory(bank, *, workspace=None, endpoint=None, query=None, limit=50, offset=0)
view(package, *, summary=False, select=None, limit=None)
```

`inventory` verifies manifest identity only and never reads response files or
intake notes. Returned rows explicitly say `response_integrity="not_checked"`.
Search is local substring matching over manifest metadata, including request
settings. It is not semantic search. `view` verifies the complete package first;
its default includes the full response, with optional existing display views.
New exports hash README bytes through `note_sha256`; old exports still verify
with `note_integrity="not_recorded"`. See the research-memory guide for limits.
