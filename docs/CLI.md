# CLI guide

Run `legends-dataforseo --help` or `python -m legends_dataforseo --help`.
Output is JSON. Successful commands exit `0`; input, credential, or transport
errors exit `2` and write a sanitized error to stderr. Returned per-task errors
remain in the provider envelope; inspect them even when the command exits `0`.

## Offline commands

```sh
legends-dataforseo doctor
legends-dataforseo routes
legends-dataforseo route maps-task-post
legends-dataforseo estimate maps --tasks 9 --depth 100
legends-dataforseo estimate serp --tasks 1 --depth 10 --keyword "site:reddit.com auto repair"
legends-dataforseo serp "technical SEO" --location-code 2840 --location-name "United States" --device desktop --os windows --max-poison-retries 2 --labs-fallback
legends-dataforseo demand "technical SEO" "local SEO"
legends-dataforseo competitors "technical SEO" "local SEO" --item-limit 20
legends-dataforseo ranked-keywords "example.com" --item-limit 100
legends-dataforseo relevant-pages "example.com" --item-limit 100
legends-dataforseo maps "coffee" --location-coordinate "40.7128,-74.0060,15z"
legends-dataforseo business "cid:1234567890123456789" --location-code 2840 --location-name "United States"
```

Named request commands preview by default. The registry includes endpoint
descriptions beyond the seven named helpers (`serp`, `maps`, `demand`,
`business`, `competitors`, `ranked-keywords`, and `relevant-pages`); discovery
is not a promise that every route has its own command or has been live-tested.

`docs search`, `docs index`, `docs sections`, and `docs read` discover official
documentation without credentials or charges. They use the network unless a
fresh cache exists or `--offline` is specified. [Discovery guide](DISCOVERY.md).

## Generic endpoints and queues

Preview the included synthetic task file from a source checkout:

```sh
legends-dataforseo call /serp/google/maps/task_post --body-file examples/maps-tasks.json
```

For your own request, supply a UTF-8 JSON array of task objects. Do not put
credentials in it. A body selects POST; no body selects GET. `--method` can make
that choice explicit. Only documented relative DataForSEO v3 paths are accepted.

After approval, add `--execute --estimated-cost-usd <ESTIMATE> --max-cost-usd <CEILING>`
with real reviewed amounts. For a known saved task, replace `<TASK_ID>` below:

```text
legends-dataforseo call /serp/google/maps/task_get/advanced/<TASK_ID> --execute
```

The CLI still needs `--execute` to send a retrieval request. Registered task
retrieval GETs do not require paid cost flags. Keep the returned task ID and
status; polling and retry decisions belong to the caller.

## Execution options

| Option | Meaning |
| --- | --- |
| `--execute` | Send this request; absent means preview. |
| `--estimated-cost-usd` | Reviewed estimate for the entire request. Required for paid POST execution. Google organic SERP calls floor this against the task depth, mandatory `resolve_poisoned_results` page, and any 5x search-operator surcharge (`site:`, `inurl:`, `intitle:`, `filetype:`, etc.). |
| `--max-cost-usd` | Local estimate ceiling, not a provider billing cap. Required for paid POST execution. |
| `--timeout` | Request timeout in seconds; default 300 on SERP paths (resolved pulls can take 66 s+), 90 elsewhere. |
| `--language-code` | Named-helper language code; default `en`. |
| `--location-code` | SERP/demand/business/Labs location code; default `2840`. |
| `--location-name` | Optional canonical location name for `serp` and `business`. |
| `--location-coordinate` | Required Maps coordinate and zoom value. |
| `--depth` | SERP/Maps result depth (`1..700`); defaults 10/100 respectively. |
| `--keyword` | Optional `estimate` keyword for Google organic SERP routes; applies the 5x search-operator surcharge when present. |
| `--device` | Optional `serp` device (`desktop` or `mobile`). |
| `--os` | Optional `serp` operating system filter. |
| `--max-poison-retries` | Bounded SearchGuard poison and `suspect_decoy` re-pulls for `serp` within cumulative `--max-cost-usd`; default 2. |
| `--no-resolve-poisoned` | UNPROTECTED. Send `resolve_poisoned_results: false` on `serp`. Refused unless `LEGENDS_DFS_ALLOW_POISONED=1`; results are stamped `integrity: unprotected`, `verified: false`. Resolve is mandatory since 0.1.12 (one extra SERP page per task). |
| `--allow-unverified` | Print an unverified `serp` result instead of failing. By default an unverified SERP exits 2 with `UnverifiedSerpError` and the stamped response; use only to inspect it. |
| `--verify-no-swap` | Add one verification pull on `serp` when resolve swapped nothing (`from_web = 0`); doubles cost on clean SERPs. Off by default. If `--max-cost-usd` cannot cover the second pull, verification is `failed` and the SERP fails closed. |
| `--resolve-goto-urls` / `--no-resolve-goto-urls` | Force or omit `resolve_goto_urls` on `serp`. Default: sent when `--depth` is 10 or less (page-1 pulls, about 2-3 s), omitted for deeper rank tracking (redirect unwrapping can add about 40 s; domains still come from breadcrumb and `website_name`). |
| `--labs-fallback` | Optional DataForSEO Labs competitor cross-verification for `serp` within cumulative `--max-cost-usd`. |
| `--item-limit` | Maximum Lab items (`1..1000`) for `competitors` (default 20), `ranked-keywords` (default 100), and `relevant-pages` (default 100). |
| `--output` | Save full JSON to a new file reserved before HTTP; print a summary. |
| `--summary` | Display IDs, statuses and costs without result bodies. |
| `--select` | Select a dotted field; repeat for multiple fields. |
| `--limit` | Limit lists in a labelled display view, preserving full saved JSON. |

`view FILE` applies the same output options to saved JSON with no network.
`wait PATH` previews supported saved-task retrieval; add `--execute` for bounded
GET polling. Bounds: `--attempts`, `--interval`, `--timeout`, `--max-elapsed`.
Returned pending/error task statuses remain visible; exit 0 does not mean a
task is complete. [Save and resume examples](WORKFLOWS.md).

Each subcommand's `--help` lists the options it accepts. Baseline estimates can
become outdated. Standard queue pricing needs a separately reviewed estimate.

[First live request](FIRST-USE.md) · [Credential setup](INSTALL.md#credentials) · [Python API](API.md)

## Offline evidence bank

`legends-dataforseo evidence --help` exposes export, verify, inventory, find,
reuse and view. `python -m legends_dataforseo evidence` and the older
`python -m legends_dataforseo.evidence` support the same commands.
Evidence reuse returns exit 2 when ineligible with explicit reasons; it never
submits or refreshes a request. Inventory errors are reported per package,
without turning incomplete metadata into verified response evidence.
See [the complete client-workspace workflow](RESEARCH-MEMORY.md).
