# Changelog

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
