# Python contract - 0.1.x router-native baseline (stable request signatures)

Public import: `legends_dataforseo`. Package: `legends-dataforseo`.
`API_ROOT = "https://api.dataforseo.com/v3"`.

```python
api_request(
    path, payload=None, *, method=None, body=None,
    credentials=None, timeout=None, log_cost=False, confirm=False,
    consumer="python", estimated_cost_usd=None, max_cost_usd=None,
) -> dict

serp(keyword, *, location_code=2840, location_name=None,
     location_coordinate=None, language_code="en", depth=10,
     device=None, os=None, max_poison_retries=2, labs_fallback=False,
     resolve_poisoned_results=None, incumbent_sites=None,
     verify_no_swap=False, allow_unverified=False, resolve_goto_urls=None,
     consensus_provider="firecrawl", independent_sites=None,
     independent_items=None, country="US", local_maps_check=False,
     maps_sites=None, consensus_thresholds=None,
     **request_options) -> dict
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

`path` is relative to v3, optionally beginning `/v3/` (endpoint segments are
normalized to lowercase while preserving task ID case on `task_get`). Absolute
URLs, query strings, encoded path traversal, and redirects are rejected. No
alternate host option exists. TLS verification remains enabled.
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
`estimate_cost(operation, *, tasks=1, depth=0, items=0, keyword=None)`,
`has_serp_cost_multiplier_operator(keyword)`, `SERP_OPERATOR_COST_MULTIPLIER`,
`total_cost(responses)`, `ledger_path()`. `write_json(Path, payload)` explicitly saves caller-selected data.
Organic SERP integrity helpers: `is_goto_wrapped_url(url)`,
`extract_breadcrumb_target(breadcrumb, website_name=None)`,
`normalize_organic_item(item)`, `assess_serp_integrity(result_block, *, task_data=None)`,
`normalize_serp_envelope(envelope, *, endpoint=None)`.
Decoy SERP helpers (0.1.12): `DECOY_THRESHOLDS`, `site_key(domain_or_url)`,
`organic_item_site_key(item)`, `organic_site_keys(result_block, *, limit=10)`, `overlap_ratio(a, b)`,
`assess_decoy_signals(result_block, *, task_data=None, second_pull_sites=None,
incumbent_sites=None, thresholds=None)`,
`apply_decoy_assessment(integrity, decoy)`, `stamp_verified(integrity, *,
verification=None, unprotected=False)`, `stamp_envelope_verified(envelope, *,
verification=None, resolve_sent=None)`, `resolve_poisoned_default()`,
`allow_poisoned()`, `ALLOW_POISONED_ENV`, `SERP_TIMEOUT_S`,
`UnverifiedSerpError`, `WEBSITE_NAME_PLATFORMS`, `website_name_target(website_name)`,
and `site_traffic(sites, *, location_code=2840,
language_code="en", max_age_days=30, **request_options)` (paid Labs bulk
traffic estimation, cached locally; informational, not a decoy signal).
Independent SERP Truth Gate helpers: `TRUTH_GATE_THRESHOLDS`, `VERIFIED_BASES`,
`evaluate_independent_consensus(dfs_sites, independent_sites, *, from_web_count=0, thresholds=None)`,
`firecrawl_search_view(keyword, *, location_name=None, country="US", limit=10, timeout=30.0, api_key=None)`,
`load_firecrawl_api_key()`, and `verify_serp_envelope(envelope, ...)`.

## Decoy SERP defense (0.1.12, fail closed)

Google serves some automated clients a decoy SERP: plausible-looking organic
results that are not what a person sees. The 2026-11-20 bench (40 keywords)
found the default pull was a decoy on 35/40 and `resolve_poisoned_results` cut
that to 3/40, so the defense is mandatory and the library fails closed.

**Resolve is mandatory.** Every Google organic request (`live/advanced`,
`live/regular`, `live/html`, standard-queue `task_post`) carries
`resolve_poisoned_results: true`. `serp()` always sends it. Raw `api_request`
injects it when absent (and floors any supplied `estimated_cost_usd` against
the task depth/page count, mandatory resolve page, and 5x search-operator
surcharge across `keyword` and `url` `q=` parameters)
and raises `RouteError` for an explicit non-true value or for `udm=14` /
`udm=web` tasks (including URL-encoded variants and even when the escape hatch
is set alongside `resolve_poisoned_results=True`), which DataForSEO cannot combine with resolve. Organic result
envelopes (`live/*` and standard-queue `task_get/{advanced,regular,html}/{id}`)
are normalized and stamped; a `task_get` reads protection from the task data
echo. `resolve_goto_urls` is never injected by `api_request` (it adds redirect
unwrap latency); `serp(resolve_goto_urls=None)` sends it for `depth <= 10` and
omits it for deeper rank tracking, and `True` / `False` force it. The only escape
hatch is `LEGENDS_DFS_ALLOW_POISONED=1` (`ALLOW_POISONED_ENV`, checked by
`allow_poisoned()`, exact value `1`), plus an explicit
`resolve_poisoned_results=False`; the env alone never turns resolve off. Such
results are stamped `serp_integrity.integrity = "unprotected"` and
`verified = false`. `resolve_poisoned_default()` always returns True; the old
`LEGENDS_DATAFORSEO_RESOLVE_POISONED` opt-out is retired and ignored.
Resolved pulls can take 66 s or more, so SERP paths default to a 300 s timeout
(`SERP_TIMEOUT_S`; `timeout=None` selects it, other paths keep 90 s).

**Detector.** The provider resolve is fail-open: when its Web-mode reference
page cannot be fetched it returns the original results unchanged, so zero
`from_web` items is not proof of a clean page. Every organic `serp_integrity`
block carries a `decoy` assessment. Signals, with conservative thresholds in
`DECOY_THRESHOLDS`:

- `aio_organic_disjoint`: the AI Overview cites at least 3 sites
  (`aio_min_reference_sites`) and at most 60% of them appear in the organic
  top 20 (`aio_overlap_max = 0.6`). Sites match on brand label, so an AIO
  `reddit.com` matches a `website_name`-derived `reddit` key.
- `pull_instability`: a second pull's top-10 overlap is at most
  `pull_overlap_max = 0.5`.
- `incumbent_mismatch`: caller-known `incumbent_sites` overlap the top 10 at
  most `incumbent_overlap_max = 0.2`.

Result age and Labs traffic were tested on the bench and did not separate
decoys; they are not scored. Signals that cannot be computed are omitted and
never count as clean evidence. `decoy.provider_resolve` is `resolved`,
`requested_no_swap` or `not_requested`.

Google now wraps most organic URLs in `google.com/goto` with `domain:
google.com` and no `timestamp`. Site keys therefore come from
`organic_item_site_key(item)`: resolved domain, then breadcrumb host, then the
closed `WEBSITE_NAME_PLATFORMS` allowlist (reddit, youtube, facebook,
instagram, x, twitter, linkedin, tiktok, yelp, nextdoor, quora, pinterest,
tripadvisor, medium), then a `website_name` slug key, and never bare/www
`google.<tld>` wrapper hosts (while preserving real Google product subdomains
such as `developers.google.com` or `cloud.google.com`).
`extract_breadcrumb_target` and `normalize_organic_item` never turn engagement
breadcrumb text (`30+ comments · 2 years ago`) into `breadcrumb_path_hint`.
When the breadcrumb has no host, an allowlisted `website_name` such as
`Reddit · r/Phoenixville` sets `resolved_domain = "reddit.com"`,
`breadcrumb_path_hint = "r/Phoenixville"` and `resolution_basis =
"website_name"`; a platform domain without a hint is enriched from the
`website_name` qualifier. Business names are never slug-guessed into domains.

**Pipeline in `serp()`.** Resolve pull, then the detector. A `suspect_decoy`
SERP is re-pulled with resolve within `max_poison_retries` and the cumulative
`max_cost_usd`. A clean result with zero swaps gets one verification pull only
when `verify_no_swap=True` (off by default since it doubles cost on clean
SERPs; one more resolve-priced page, cap-checked, and a cap too small for it
records `verification = {"status": "failed", "reason":
"max_cost_usd_insufficient_for_second_pull"}` and fails closed): the
second pull is telemetry only and is never adopted as the result; agreement above
`pull_overlap_max` is `stable`, disagreement is `unstable` and fires
`pull_instability`. A SERP still suspect at the end gets
`status = "unverified_decoy"` (with `detected_status = "suspect_decoy"`) or
stays `suspect_poisoned`, plus `unresolved = true`; it is never relabelled
`clean`. Transient task code 40101 (seen on concurrent same-keyword requests;
cost 0) is retried with 2 s, 5 s and 10 s backoff, and `serp()` holds a
per-process lock per normalized keyword so same-keyword calls run one at a time.

**Verified stamp (Independent SERP Truth Gate).** `stamp_verified(integrity, *,
verification=None, unprotected=False)` sets `serp_integrity.verified`, `integrity`
(`protected`/`unprotected`), `verified_basis`, `signals_used` and
`unverified_reasons` (and `unverified = true` when not verified). Provider-only
signals (`from_web > 0`, `from_web == 0`, or same-provider double-pull stability)
are never sufficient on their own. Only independent cross-provider consensus
(`VERIFIED_BASES`: `independent_consensus` or `independent_consensus_repaired`)
grants `verified = true`. Raw `api_request` organic pulls and queued `task_get`
retrievals are stamped `verified = false` until verified with
`verify_serp_envelope()`. `serp()` runs the Truth Gate automatically via
`consensus_provider="firecrawl"` (stdlib HTTPS-only `/v2/search` using
`FIRECRAWL_API_KEY` from the environment) or caller-supplied `independent_sites` /
`independent_items`, re-pulling on disagreement and optionally repairing from an
AIO-corroborated independent view before failing closed with `UnverifiedSerpError`.

**Depth < 20 AIO diagnostic.** The `aio_organic_disjoint` signal compares AI
Overview citations against the organic top 20. On a pull whose effective depth
(`depth`, or `max_crawl_pages * 10`; a returned top 20 always counts as complete)
is below 20, citations ranking 11-20 are unobservable, so when independent
consensus passes the signal is diagnostic only: it is recorded with
`top20_complete: false`, `requested_depth`, and
`suppressed_reason: "insufficient_depth_for_top20_with_independent_consensus"`,
and does not by itself refuse the SERP. Without passing independent consensus,
or at effective depth >= 20, disjointness still fires and fails closed.
`verify_serp_envelope()` normalizes unstamped organic blocks first (as
`/serp/google/organic/live/advanced`) instead of skipping them, and
`stamp_verified()` fails closed unless `organic_count` is a strict `int > 0`
(`False`, `0`, `None`, and non-`int` values never verify). `serp()` validates
`country` (`^[A-Za-z]{2}$`), `independent_sites`, `independent_items`, and
`consensus_thresholds` before any paid DataForSEO request.

**Fail closed.** When the final SERP is not verified, `serp()` raises
`UnverifiedSerpError` (an `ApiError` with the stamped envelope on `.response`
and `request_may_have_completed = true`). Pass `allow_unverified=True` only to
inspect it. Unprotected escape-hatch pulls are returned stamped rather than
raised. The envelope's `legends_serp` block carries `integrity`, `verified`,
`verification`, every attempt (task IDs, task status codes, resolve flag,
provider-reported cost, latency, status), `final_status`, `cost_usd_total` and
`latency_ms_total`. Downstream consumers (legends-firecrawl `scrape-serp`,
legends-prospector `--serp-file`) refuse any SERP whose
`serp_integrity.verified` is not true unless the operator overrides.

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
