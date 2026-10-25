<a name="banner"></a>
<a name="legends-dataforseo"></a>

# ![legends-dataforseo](assets/banner.webp)

[![release](https://img.shields.io/github/v/release/avalonreset/legends-dataforseo?label=release&sort=date&style=flat-square&labelColor=000000&color=ff0000)](https://github.com/avalonreset/legends-dataforseo/releases/latest)
[![checks](https://img.shields.io/github/actions/workflow/status/avalonreset/legends-dataforseo/ci.yml?branch=main&label=checks&style=flat-square&labelColor=000000)](https://github.com/avalonreset/legends-dataforseo/actions/workflows/ci.yml)
[![license](https://img.shields.io/github/license/avalonreset/legends-dataforseo?label=license&style=flat-square&labelColor=000000&color=666666)](LICENSE)

**Search results, Google Maps rankings, and keyword research from Python or your terminal.**
legends-dataforseo connects scripts, applications, and coding agents to
DataForSEO API v3 with offline request previews and explicit paid execution.
Find official endpoint docs, save complete responses, inspect focused results,
and resume queued tasks without submitting them again.

Python 3.10+ · Windows, Linux, macOS · No runtime dependencies · MIT licensed

[Quick start](#quick-start) · [Python API](docs/API.md) · [CLI guide](docs/CLI.md) ·
[Agent setup](#agent-setup-via-cto-legends) · [Releases](https://github.com/avalonreset/legends-dataforseo/releases)

## Agent setup (via `cto-legends`)

Part of the [cto-legends](https://github.com/avalonreset/cto-legends) ecosystem. `cto-legends` is the only registered skill; this repo vendors a pinned copy at `skills/cto-legends/SKILL.md`.

The product, repository, Python distribution and catalog ID are `legends-dataforseo`.
The former catalog name remains a compatibility alias for existing commands.

Install with `cto-legends install legends-dataforseo`, then follow the module recipe the router loads. Do not register this module as its own skill.

## What you can build

| Workflow | Included interface | What you get |
| --- | --- | --- |
| SERP research | `serp()` / `legends-dataforseo serp` | Google organic results in the provider's JSON envelope |
| Local ranking scans | `maps()` and queue `api_request()` calls | Maps live results, standard task submission, and saved-task retrieval |
| Business profiles | `business()` / `legends-dataforseo business` | Google Business Profile (`my_business_info`) live lookup and standard queue tasks by `cid`, `place_id`, or keyword |
| Keyword research | `demand()` / `legends-dataforseo demand` | DataForSEO Labs keyword overview for a keyword list |
| Custom integrations | `api_request()` / `legends-dataforseo call` | GET/POST access to documented v3 endpoints |
| Request planning | `routes`, `route`, `estimate`, command previews | Local discovery and baseline estimates without credentials |
| Endpoint discovery | `docs search`, `docs read` | Official documentation with a local offline cache |
| Focused results | `--output`, `view`, `--select`, `--limit` | Complete saved JSON with concise, labelled display views |
| Research memory | `legends-dataforseo evidence` | Offline export, inventory, search, verified views, compatible reuse and optional vault intake |
| Saved task retrieval | `wait` / `wait_task()` | Opt-in GET polling with attempt and elapsed bounds |

The transport preserves task IDs and original pending, empty, and failed task
statuses. Your application decides how to interpret results and when to poll.
No MCP server or particular agent runtime is required.

## Install

```sh
python -m pip install "https://github.com/avalonreset/legends-dataforseo/releases/download/v0.1.5/legends_dataforseo-0.1.5-py3-none-any.whl"
legends-dataforseo --version
legends-dataforseo doctor
```

Use a virtual environment for a project install. If the executable is not on your
PATH, use `python -m legends_dataforseo` in its place. `doctor` checks the install
and credential presence locally; missing credentials do not block offline use.

The wheel requires no Git installation. [Install options](docs/INSTALL.md) cover
Windows and POSIX environments, source ZIPs, immutable dependency pins, upgrades,
and checksum verification. Releases are distributed through GitHub; the bare
package name is not a documented PyPI install route.

## Quick start

Preview a Google search request:

```sh
legends-dataforseo serp "technical SEO" --location-code 2840
```

```json
{
  "preview": true,
  "path": "/serp/google/organic/live/advanced",
  "method": "POST",
  "body": [{"language_code": "en", "keyword": "technical SEO", "depth": 10, "location_code": 2840}],
  "estimated_cost_usd": null,
  "max_cost_usd": null
}
```

This is real command output, formatted compactly. No request was sent. Explore
the registry, estimate a live Maps workload, or preview keyword research:

```sh
legends-dataforseo routes
legends-dataforseo estimate maps --tasks 9 --depth 100
legends-dataforseo demand "technical SEO" "local SEO"
```

Named commands preview by default. These commands make no network requests and
need no credentials. Baseline estimates are advisory and can become outdated.
Queue pricing requires a separately reviewed estimate.

The [first live request guide](docs/FIRST-USE.md) takes you from credential setup
to an explicitly approved request and result inspection. The
[offline example](examples/offline.py) demonstrates queue and empty-result
handling with synthetic data.

## Discover, save, and reuse

```sh
legends-dataforseo docs search "google maps task_post" --limit 3
legends-dataforseo docs read serp/google/maps/task_post
```

Docs downloads need internet, but no credentials or provider credits. Add
`--offline` to read the cached copy. Documentation is reference material; agents
should review it before preparing a request.

Add `--output results.json` to an approved request to save the full response and
print a short summary. Then inspect it as often as needed without another call:

```sh
legends-dataforseo view results.json --select tasks.0.result.0.items --limit 3
legends-dataforseo view results.json --summary
```

Existing output files are refused before HTTP. All task IDs and statuses remain
in summaries; displayed lists are labelled when shortened. The Python transport
still returns the untouched provider response. For saved queue IDs, `wait`
offers bounded GET retrieval with no paid resubmission.

[Research memory and vault handoff](docs/RESEARCH-MEMORY.md) ·
[Complete workflow](docs/WORKFLOWS.md) · [Documentation discovery](docs/DISCOVERY.md) ·
[Verification evidence](docs/VERIFICATION.md) · [Official tooling comparison](docs/UPSTREAM.md)

## Configure credentials

Set `DATAFORSEO_LOGIN` (or `DATAFORSEO_USERNAME`) and `DATAFORSEO_PASSWORD` using
your shell or secret manager. The loader reads a complete process environment
pair first, then the Windows user environment. It never mixes scopes, reads a
credential file, or prompts. Do not put credentials in source or command arguments.

`legends-dataforseo doctor --live` verifies authentication with a no-charge
request and prints only status, cost, and credential presence.

See [credential setup and troubleshooting](docs/INSTALL.md#credentials). A
DataForSEO account is needed for provider requests; the open-source license does
not include provider credits.

## Python API

```python
from legends_dataforseo import api_request

# This sends a paid request. Set confirm only after reviewing the scan budget.
response = api_request(
    "/serp/google/maps/task_post",
    [{"keyword": "coffee", "location_coordinate": "40.7128,-74.0060,15z",
      "language_code": "en", "depth": 100, "priority": 1}],
    confirm=True,
    consumer="my-app",
    estimated_cost_usd=0.01,  # Example reviewed allowance; not a price quote.
    max_cost_usd=0.01,
)
for task in response.get("tasks") or []:
    # Save task IDs; inspect status before polling. Never blindly resubmit.
    print(task.get("id"), task.get("status_code"))
```

Retrieve a saved task with
`api_request("/serp/google/maps/task_get/advanced/" + task_id)`. The caller controls
polling, interprets all task statuses, and owns the aggregate scan budget.
`40102` means an empty search result, not proof of no market demand.

`serp(keyword, ...)`, `demand(keywords, ...)`, and `maps(keyword, ...)` convenience
functions also accept `confirm`, `estimated_cost_usd`, `max_cost_usd`, and `timeout`.
See the [versioned API contract](docs/API.md) for signatures and error handling.

## Cost and privacy

Paid or unclassified requests require `confirm=True` in Python. Paid CLI calls
require `--execute`, `--estimated-cost-usd`, and `--max-cost-usd`. No interactive
prompt is repeated after these explicit options. The ceiling compares your
estimate before HTTP; it cannot enforce provider billing. Review
[current pricing](https://dataforseo.com/pricing) and reconcile `response.cost`.

Paid submissions are never retried or switched between accounts. The transport
makes one request; optional `wait_task` polls only supported saved-task GETs.
Responses remain in memory unless the caller saves them. Optional `log_cost=True` writes only cost
metadata to a user state directory. Provider responses can contain private
queries or account information; do not publish them.

## Development and agent use

```sh
python -m pip install -e ".[dev]"
python -m pytest
python examples/offline.py
python -m build
python -m twine check dist/*
```

CI tests Linux, Windows, and macOS without credentials or paid calls. Read
[AGENTS.md](AGENTS.md) or [the pinned router skill](skills/cto-legends/SKILL.md)
for agent-assisted install and operation. Consumer integration recipes for
[legends-geogrid](https://github.com/avalonreset/legends-geogrid) and
[legends-github](https://github.com/avalonreset/legends-github) are in
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

For an agent-assisted install, ask your agent to read this repository's
[AGENTS.md](AGENTS.md), install the wheel into your project's environment, run the
offline doctor, and preview your intended request. Give it a budget before paid
execution. The portable instruction files work with file-and-command-capable
agents; no separate model subscription is required by the kit.

## Architecture and how it works

```text
Caller (CLI or Python)
  -> Route registry & offline preview / cost ceiling check
  -> DataForSEO API v3 HTTPS transport (single request, no blind retries)
  -> Complete JSON envelope + optional research-memory bank (`evidence`)
```

- `legends_dataforseo/`: Core transport, route catalog, cost guardrails, and CLI entry points.
- `docs/`: API reference, CLI guide, discovery workflow, research memory, and [search strategy](docs/SEARCH-STRATEGY.md).
- `skills/cto-legends/SKILL.md`: Pinned copy of the central [`cto-legends`](https://github.com/avalonreset/cto-legends) router skill.

## FAQ

**Does `legends-dataforseo` make paid API calls by default?**
No. Named CLI commands (`serp`, `maps`, `business`, `demand`) preview the request payload offline by default. Paid execution requires `--execute`, `--estimated-cost-usd`, and `--max-cost-usd` on the CLI, or `confirm=True` in Python.

**Do I need DataForSEO credentials to explore routes or run tests?**
No. Route discovery (`routes`, `route`, `estimate`), offline previews, and the test suite run locally without credentials.

## Support and contributions

[Report a bug or request a feature](https://github.com/avalonreset/legends-dataforseo/issues/new/choose).
Include the version, a minimal example, and sanitized status codes.
[CONTRIBUTING.md](CONTRIBUTING.md) explains local checks and the transport rules
that integrations rely on. For credential leaks or vulnerabilities, use
[private security reporting](SECURITY.md).

## License

MIT licensed. [Provenance](docs/PROVENANCE.md). Independent project; not an
official DataForSEO SDK. The license covers this code, not the provider service,
data, or trademarks.
