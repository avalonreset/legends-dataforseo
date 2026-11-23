# Changelog

## Unreleased

- **Independent SERP Truth Gate (`src/legends_dataforseo/truth_gate.py`, `serp_integrity.py`, `client.py`, `cli.py`)**: closes the residual partial-decoy and `from_web == 0` / `from_web > 0` provider-only blind spots by verifying every protected Google organic SERP against an independent search view (default: stdlib HTTPS-only Firecrawl `/v2/search` using `FIRECRAWL_API_KEY` from the environment, or caller-supplied `independent_sites` / `independent_items` / `consensus_provider`).
- Provider-only signals (`provider_from_web`, `double_pull_stable`, `provider_clean_no_decoy_signals`) are no longer sufficient for `serp_integrity.verified = true`. Verified envelopes carry `verified_basis` in `VERIFIED_BASES` (`independent_consensus` or `independent_consensus_repaired`); raw `api_request` and queued `task_get` envelopes stay `verified = false` until checked with `verify_serp_envelope()`.
- Disagreement triggers bounded DataForSEO re-pulls within `max_poison_retries` and cumulative `max_cost_usd`, then AIO-corroborated independent repair when Google's AI Overview (`>= 3` cited sites) confirms DFS is poisoned (`<= 0.60`) and the independent view matches (`>= 0.65`), else fails closed with `UnverifiedSerpError`.
- Added `--consensus-provider`, `--country`, and `--local-maps-check` (`local_maps_check` / `maps_sites` diagnostic corroboration) to `legends-dataforseo serp`.
- Truth Gate hardening: a Firecrawl HTTP 200 with a non-object payload, `success: false`, or zero usable sites now returns `ok: false` (never burns paid retries on an empty view); independent item URLs are filtered to valid `http(s)` URLs excluding Google `/goto` and `/url` wrappers; AIO-corroborated repairs re-assess `serp_integrity` on the repaired items instead of reusing stale pre-repair counts; `verify_serp_envelope()` normalizes unstamped organic blocks instead of skipping them; `stamp_verified()` fails closed unless `organic_count` is a strict `int > 0`.
- `firecrawl_search_view()` validates an explicit `api_key` and non-empty `keyword` before HTTP (same strip/control-byte rules as environment keys, never echoing the key), closes `HTTPError` responses, and catches `http.client.HTTPException`. `serp()` and the `serp` CLI validate `country` (`^[A-Za-z]{2}$`), `independent_sites`, `independent_items`, and `consensus_thresholds` before any paid DataForSEO request.
- Depth < 20 AIO behavior: on pulls whose effective depth (`depth` or `max_crawl_pages * 10`; a returned top 20 is always complete) is below 20, `aio_organic_disjoint` is diagnostic only when independent consensus passes (recorded with `top20_complete: false` and `suppressed_reason`); it still fires without passing consensus or at depth >= 20.
- Documented `FIRECRAWL_API_KEY` / `FIRECRAWL_DISABLE_USER_ENV=1` and the outbound `https://api.firecrawl.dev/v2/search` default consensus call across install/first-use/CLI/agent docs and the README (including the `truth_gate` preview block); `doctor` now reports `firecrawl_api_key_configured` without printing the key.

## 0.1.14 - 2026-11-22

- Sync vendored `cto-legends` router skill pin (`69ddc4f68faa`), enforce LF line endings via `.gitattributes`, and standardize release artifact checksums.

## 0.1.13 - 2026-11-21

- **Outlined Legends Font `r1` SVG Card Suite & README Refresh (`scripts/render_cards.py`, `assets/cards/r1/*.svg`, `README.md`)**: add deterministic outlined SVG design cards (`slot-1-agent-handoff.svg`, `slot-2-three-pillars.svg`, `slot-3-plain-english-chains.svg`, `slot-4-decoy-defense.svg`, `slot-5-raw-vs-dataforseo.svg`, `slot-collection-star-shrine.svg`, and `slot-7-message-avalon-reset.svg`) with all glyph runs converted to vector `<path>` outlines, the 40-keyword Decoy SERP defense benchmark narrative, 53-route v3 catalog overview, and collapsible `cto-legends` agent setup and CLI/Python reference drawers.

## 0.1.12 - 2026-11-21

Adversarial and battle-test hardening (2026-11-21):

- `docs` discovery uses the certifi-backed TLS context (`HTTPSHandler(context=_ssl_context())`), fixing `CERTIFICATE_VERIFY_FAILED` against `docs.dataforseo.com` on Windows Python 3.12, and `docs search` includes an actionable `legends-dataforseo docs read` parameter lookup hint when `llms.txt` yields zero matches.
- `resolve_goto_urls` is no longer forced by the transport. `serp(resolve_goto_urls=None)` sends it for `depth <= 10` (page-1 pulls, about 2-3 s) and omits it for deeper rank tracking (redirect unwrapping can add about 40 s; breadcrumb / `website_name` domain extraction is free). CLI: `--resolve-goto-urls` / `--no-resolve-goto-urls`. Raw `api_request` never injects it.
- Organic path split and case-normalized routing: `normalize_path` lowercases v3 endpoint segments while preserving `task_get` ID case, `_is_organic_serp_request_path` enforces `resolve_poisoned_results: true` on live pulls and standard-queue `task_post`, and `_is_organic_serp_result_path` normalizes and stamps live pulls and standard-queue `task_get`. URL-encoded `udm=14` / `udm=web` parameters are detected and rejected, including invalid `udm` + `resolve_poisoned_results=true` pairs under `LEGENDS_DFS_ALLOW_POISONED=1`.
- Google organic 5x search-operator surcharge guard (`SERP_OPERATOR_COST_MULTIPLIER = 5`, `has_serp_cost_multiplier_operator`, `estimate --keyword`): detects all 16 billed Google operators (`site:`, `inurl:`, `intitle:`, `filetype:`, `cache:`, `allin*`, etc., including parenthesized/quoted/encoded syntax and `url` `q=` parameters) and floors organic preflight estimates in `serp()`, `api_request()`, and `call` previews against task depth (`depth` / `max_crawl_pages`), mandatory resolve pages, and operator multipliers.
- Closed `WEBSITE_NAME_PLATFORMS` allowlist (`website_name_target`) and international `/goto` handling: a goto item whose breadcrumb is engagement text (`30+ comments · 2 years ago`) and whose `website_name` is `Reddit · r/Phoenixville` resolves to `resolved_domain: reddit.com`, `breadcrumb_path_hint: r/Phoenixville`, `resolution_basis: website_name`. Regional Google `/goto` hosts (`google.co.uk`, `google.ca`, `google.com.au`, etc.) are unmasked while unwrapped URLs with stale `google.com` domains keep `goto_wrapped: false` and legitimate Google product subdomains (`developers.google.com`, `cloud.google.com`) retain their site keys. Business names are never slug-guessed into domains.
- Clean single pulls verify while zero-organic / unparsed HTML blocks fail closed. `stamp_verified` rejects unprotected tasks, `organic_count == 0` blocks, non-clean blocks, any fired decoy signal and disagreeing or failed verification pulls, and stamps `verified_basis` as `provider_from_web`, `double_pull_stable` or `provider_clean_no_decoy_signals`. `incumbent_sites` is evaluated per attempt in `serp()` so surface-clean decoys trigger bounded retries before optional `verify_no_swap` pulls.
- `serp(verify_no_swap=False)` is the new default so clean SERPs no longer double cost; pass `verify_no_swap=True` / `--verify-no-swap` for the double pull. When the cap cannot cover the second pull, verification is `failed` (`max_cost_usd_insufficient_for_second_pull`) and the SERP fails closed.
- Route catalog grows from 30 to 53 routes, each with `section` and `mode`, with `llm_mentions` routes aligned to current DataForSEO v3 paths (`/search_mentions/live`, `/top_mentioned_domains/live`, `/top_mentioned_pages/live`, `/target_metrics/live`) plus aliases: `serp-task-post`, `serp-task-get`, `local-finder`, `ai-mode`, `serp-ai-summary`, `serp-screenshot`, `keywords-for-site`, `historical-serp`, `domain-intersection`, `subdomains`, `ai-keyword-volume`, `llm-mentions-top-domains`, `llm-mentions-top-pages`, `llm-mentions-aggregated`, `llm-responses-chatgpt`, `llm-responses-claude`, `llm-responses-gemini`, `llm-responses-perplexity`, `referring-domains`, `anchors`, `backlinks-list`, `content-parsing`, `lighthouse`.

Decoy SERP defense:

- Decoy SERP defense, fail closed. Google serves automated clients decoy SERPs (plausible organic results that are not what a person sees). On the 2026-11-20 bench (40 keywords, real logged-out Chrome page 1 as truth) the DataForSEO default pull was a decoy on 35/40 (88%, median top-10 overlap 0.10, #1 match 6/40); with `resolve_poisoned_results` on, 3/40 (median overlap 0.90, #1 match 31/40, all three near-misses). The CTO keyword subset went from 7/7 decoys to 0/7.
- `resolve_poisoned_results: true` is mandatory on every Google organic task (`live/advanced`, `live/regular`, `live/html`, `task_post`). `serp()` always sends it; raw `api_request` injects it when absent (estimate raised one page per injected task) and raises `RouteError` for an explicit non-true value or `udm=14` / `udm=web` tasks. The only escape hatch is `LEGENDS_DFS_ALLOW_POISONED=1` plus an explicit `resolve_poisoned_results=False` (`--no-resolve-poisoned`); those results are stamped `integrity: unprotected`, `verified: false`. The 0.1.12-draft opt-out `LEGENDS_DATAFORSEO_RESOLVE_POISONED` is retired.
- SERP paths default to a 300 s timeout (`SERP_TIMEOUT_S`; resolved pulls can take 66 s+). `api_request(timeout=None)` and the CLI `--timeout` default select it; other paths keep 90 s.
- New `suspect_decoy` detector (`assess_decoy_signals`, conservative `DECOY_THRESHOLDS`): AI Overview backstop (AIO cites at least 3 sites and at most 60% appear in organic top 20; P=R=1.0 on default bench rows, n=20; brand-label matching), second-pull instability (top-10 overlap at most 0.5) and caller incumbents (at most 0.2). Result age and Labs traffic did not separate decoys on the bench and are not scored. `suspect_poisoned` keeps precedence.
- Goto-wrapped organic items: `organic_item_site_key()` keys items by resolved domain, breadcrumb host, then `website_name` slug, never `google.com`.
- `serp()` pipeline: resolve pull, detector, re-pull a `suspect_decoy` with resolve (within `max_poison_retries` and cumulative `max_cost_usd`), and, with `verify_no_swap=True`, one cap-checked verification pull when resolve swapped nothing (the provider resolve is fail-open). A SERP still suspect ends as `status: unverified_decoy` (`detected_status: suspect_decoy`, `unresolved: true`); it is never relabelled `clean`.
- Every organic `serp_integrity` block carries `verified`, `integrity`, `verified_basis` (`provider_from_web`, `double_pull_stable` or `provider_clean_no_decoy_signals`), `signals_used` and `unverified_reasons` (`stamp_verified`, `stamp_envelope_verified`). `serp()` raises `UnverifiedSerpError` (stamped envelope on `.response`) for any unverified protected SERP unless `allow_unverified=True` / `--allow-unverified`; the CLI exits 2 and prints the stamped response.
- Transient task code 40101 (seen on concurrent same-keyword requests, cost 0) is retried with 2 s / 5 s / 10 s backoff, and `serp()` serializes same-keyword calls with a per-process lock.
- `legends_serp` envelope block: `integrity`, `verified`, `verification`, per-attempt task IDs, task status codes, resolve flag, provider-reported cost, latency and status, `final_status`, totals.
- Removed before release: `decoy_traffic_check` and the age / filler-traffic signals. `site_traffic()` and the `bulk-traffic` route remain as informational Labs helpers.

## 0.1.11 - 2026-11-18

- Harden ancestor-chain symlink and reparse-point checks (`_safe_local_path` in `src/legends_dataforseo/client.py` and `_safe_path` in `src/legends_dataforseo/evidence.py`) to catch `OSError` (including `NotADirectoryError` / `ENOTDIR` on POSIX when a regular file sits in an ancestor component) while preserving symlink and junction refusal.

## 0.1.10 - 2026-11-11

- Enforce cumulative `max_cost_usd` spend ceilings across `serp()` SearchGuard poison retries and `labs_fallback` cross-verification, preserving the `$0.015` Labs floor so `CostLimitError` fires whenever remaining budget is insufficient.
- Enforce ancestor-chain symlink and Windows reparse-point refusal (`_safe_local_path` / `_safe_path`) across CLI inputs (`view`, `call --body-file`, `evidence --request-file`), `--output` reservation (`0600` permissions), documentation cache paths, and cost ledgers, plus bound HTTP response reads (`MAX_RESPONSE_BYTES`).
- Achieve 100% 1:1 CLI and Python API documentation parity across `docs/CLI.md` and `docs/API.md`, update `assets/manifest.json` for `social-preview.png`, and harden `scripts/check_release.py` with control-byte scanning and Gold Standard release checks.

## 0.1.9 - 2026-11-07

- Add `legends_dataforseo.serp_integrity` (`is_goto_wrapped_url`, `extract_breadcrumb_target`, `normalize_organic_item`, `assess_serp_integrity`, `normalize_serp_envelope`) to automatically unmask Google `/goto?url=` and `/url?q=` redirect wrappers from `breadcrumb` and `website_name`, preserve `raw_domain` / `raw_url` provenance, extract `breadcrumb_path_hint` slug hints, and flag degraded or poisoned SERP responses (`serp_integrity.status`).
- Upgrade `serp()` and `legends-dataforseo serp` with `--location-name`, `--device`, `--os`, automatic poison retry (`--max-poison-retries`, dropping `location_coordinate` on organic retries in favor of canonical `location_name` / `location_code`), and optional `--labs-fallback` cross-verification.
- Add first-class DataForSEO Labs helpers `competitors()`, `ranked_keywords()`, and `relevant_pages()` (`legends-dataforseo competitors`, `ranked-keywords`, `relevant-pages`) and register their routes in `routes.json`.
- Reconfigure CLI `stdout`/`stderr` streams to UTF-8 on Windows consoles so Unicode breadcrumb separators (`›`) and international glyphs never raise `UnicodeEncodeError`.

## 0.1.8 - 2026-11-05

- Add `docs/AGENT-RECIPE.md` with complete CLI/Python workflow reference and Windows Defender, EDR, and anti-ransomware guardrails, and expand `AGENTS.md` with explicit EDR security guardrails.
- Add `THIRD_PARTY_NOTICES.md`, `assets/manifest.json`, and `scripts/check_release.py` Gold Standard offline release verification gate (EDR forbidden regexes, forbidden file extensions, PNG/WebP authoring metadata checks, relative link verification, dash checks, and version parity).
- Expand `legends-dataforseo doctor` (`python -m legends_dataforseo doctor`) to report Python runtime version and explicit `dataforseo_login_configured` / `dataforseo_password_configured` boolean readiness fields alongside route count and credential source.

## 0.1.7 - 2026-11-04

- Align `business()` in `src/legends_dataforseo/client.py` and `docs/API.md` so callers can pass either `keyword` (positional or keyword) or `target` (keyword alias) along with optional `location_code`, `location_name`, `location_coordinate`, and `tag`.

## 0.1.6 - 2026-10-29

- Raise minimum supported Python runtime to Python 3.11 (`requires-python = ">=3.11"`) across package metadata, documentation, and multi-OS CI.
- Harden GitHub Actions workflows with 40-character commit SHA action pins and upgrade `pip>=26.2.2` before package installation.

## 0.1.5 - 2026-10-21

- Add first-class `business()` Python helper and `legends-dataforseo business` CLI subcommand for Google Business Profile lookups (`cid:<id>`, `place_id:<id>`, or query).
- Register queued Google Business Profile operations `business-task-post` (`/business_data/google/my_business_info/task_post`) and `business-task-get` (`/business_data/google/my_business_info/task_get/{id}`) in `routes.json` and `wait_task` retrieval validation for `legends-geogrid` business-name and profile audits.
- Document cross-module integration between `legends-dataforseo`, `legends-geogrid`, and `legends-jev` optical signage verification in `docs/INTEGRATIONS.md`.

## 0.1.4 - 2026-10-03

- Rename the repository, distribution and catalog identity to `legends-dataforseo`.
- Refresh install links, badges, package metadata and evidence producer names.
- Keep the Python import and CLI unchanged. Existing cache and ledger locations
  are reused when present; new installations use the canonical name.


## 0.1.3 - 2026-10-01

- Add offline evidence inventory/find, reuse assessment CLI and integrity-checked views through `legends-dataforseo evidence` and the existing module entry point.
- Bind new evidence intake notes with SHA-256 while keeping existing v1 packages readable with explicit unrecorded-note integrity.
- Preserve complete provider data and explicit client scope; list manifest metadata without reading raw responses. No implicit provider calls, vault promotion or default query truncation.
- Document portable client workspaces, explicit display limits and measured synthetic navigation checks.


## 0.1.2 - 2026-09-25

- Fix: package __version__ and the routes registry version join 0.1.2.
  The 0.1.1 bump missed the in-package version strings, failing the
  wheel smoke check.

## 0.1.1 - 2026-09-25

- Docs: reworded pre-reset version references (0.3.x/0.4.0/0.5.0) to the
  0.1.x router-native baseline and renamed Legends Obsidian references to
  legends-empire. No code changes.

## 0.1.0 - 2026-09-25

- Router-native generation reset: one registered skill (`cto-legends`) with a pinned copy vendored at `skills/cto-legends/SKILL.md` (router commit 6975dcbf073e5eec7a30ce6a04a6975238c61158); per-module skill registration removed; version reset to 0.1.0 with history preserved.

## 0.5.0 - 2026-09-23

- Ship the offline research-evidence module required by Legends Obsidian intake.
- Export immutable raw responses with workspace, request settings, source time,
  task statuses, reported costs and content identity.
- Verify package integrity; assess reuse against workspace, settings and freshness.
- Preserve pending, empty and failed responses without treating them as findings.
- Test the handoff from the installed wheel, without credentials or paid calls.

The Python transport contract is unchanged. Vault ingestion remains an explicit
Legends Obsidian transaction, not an automatic side effect of export.

## 0.4.0 - 2026-09-21

- Official documentation discovery, endpoint reading, search, and an offline cache.
- Save full responses with `--output`; inspect local JSON with summary, selection,
  and list limits without another provider request.
- Optional bounded GET polling of saved queue IDs, preserving original envelopes
  and reporting incomplete cost information.
- Explicit `.ai` endpoint support without changing standard paths or responses.
- Stronger failure handling: POST ambiguity flag, sanitized HTTP protocol errors,
  rejected task IDs retained in CLI output, and nonfatal ledger warnings.
- Dated upstream comparison and a read-only official-version check.

The existing `api_request` signatures and standard task status behavior remain
compatible with 0.3.x. No provider package or runtime dependency was added.

## 0.3.1 - 2026-09-21

- Legends banner, live badges, and matching social-preview artwork.
- Wheel-first quick start with verified output; installation, CLI, and first-request guides.
- Contributor guidance, issue forms, pull request template, and private security-reporting path.
- Searchable package metadata, documentation links, and branded source distribution.
- Repeatable checksum preparation and downloadable CI build artifacts.

The Python request signatures and transport behavior are unchanged from 0.3.0.
Existing immutable dependency pins continue to work.

## 0.3.0 - 2026-09-20

Initial public release.

- Portable Python package and JSON CLI; no runtime dependencies.
- Stable api_request transport, named serp/demand/maps helpers, and route registry.
- Maps live and standard queue POST/GET contracts; original task statuses retained.
- Environment-only credentials, redirects blocked, no automatic retries/failover.
- Explicit paid confirmation and advisory per-request budget prechecks.
- Offline examples and tests; cross-platform CI; MIT license and source provenance.

Operational limits: local estimates are not price guarantees; queue polling
and aggregate budgets belong to the caller. No PyPI upload, private account
routing, workstation-specific installer, or full first-class wrapper for every
DataForSEO endpoint. Use confirmed generic calls for documented v3 endpoints.
