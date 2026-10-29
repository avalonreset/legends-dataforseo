# Upstream tracking

Baseline reviewed **2026-09-21**:

| Source | Reviewed value |
| --- | --- |
| Official npm package | `dataforseo-mcp-server` |
| npm `latest` version | `3.1.1` |
| Published package `gitHead` | `fde5554e7b57f40528e73e55d90c82c8300b726b` |
| DataForSEO API used by this kit | HTTP API v3 |

The kit is an independent Python implementation calling DataForSEO directly.
It does not embed, fork, install, or depend on the official MCP server. The kit
version and official npm version describe separate products; matching their
numbers would not establish compatibility.

DataForSEO's current universal server exposes four tools: `docs_index`,
`docs_list_sections`, `docs_search`, and `api_request`. It also offers a standalone
CLI. The old 80-plus-tool server is a deprecated generation and is not the basis
for current comparisons. See the [official announcement](https://dataforseo.com/help-center/dataforseo-new-mcp-server-and-cli-connection-a-quickstart-guide)
and [the reviewed package manifest](https://github.com/dataforseo/mcp-server-typescript/blob/fde5554e7b57f40528e73e55d90c82c8300b726b/package.json).

## What this kit provides

| Capability | legends-dataforseo |
| --- | --- |
| Runtime and embedding | Python 3.11+, no runtime dependencies, Python functions and CLI |
| Documentation | Official index discovery, local index search, Markdown reads, explicit offline cache |
| API responses | Original Python task envelopes; optional CLI summary, field selection, display limits, and full-response files |
| `.ai` endpoints | Explicit `.ai` request paths accepted; standard response paths remain the default |
| Execution | Offline previews, explicit execution, caller estimates and ceilings before paid requests |
| Existing queued tasks | Optional bounded GET polling; original pending/error statuses retained |

The official CLI has documentation lookup and response filtering too, and its
reviewed implementation defaults to `.ai` mode. Those are not unique kit
advantages. Its MCP and HTTP transport integrations serve a different deployment
need. Review [the official CLI reference at the baseline commit](https://github.com/dataforseo/mcp-server-typescript/blob/fde5554e7b57f40528e73e55d90c82c8300b726b/SKILL.md)
for its current contract.

`.ai` is an explicit provider-format opt-in in this kit. A live verification of
the organic advanced `.ai` endpoint returned a root-level `id`, `status_code`,
`status_message`, and `items`, rather than the standard `tasks` envelope, and
omitted reported cost. The kit does not reshape that response into a standard
envelope. Do not substitute `.ai` paths into integrations or polling workflows
that expect the existing schema. Use standard paths with local `--summary` or
`--select` when you need smaller displayed output while retaining the original
task envelope and reported billing fields. An absent cost is unknown, not zero.

We do not claim complete endpoint parity, lower token usage than the official
tool, or that every MCP client loads all tools into every session. Context use
depends on the client, discovery behavior, selected output, and research task.
The kit's output controls reduce the displayed data when requested; a fair
cross-tool comparison requires measuring equivalent successful workflows.
Estimates and ceilings are local preflight checks, not provider-enforced billing
limits. Offline tests do not establish live success for every API endpoint.

## Check for upstream changes

From a source checkout:

```sh
python scripts/check_upstream.py
```

This unauthenticated, read-only check fetches the npm `latest` metadata and prints
JSON containing the tracked and observed version/commit, source URL, check time,
and a `changed` boolean. It does not install packages, update the baseline, modify
the checkout, access credentials, or call a paid API. Exit code 0 means the check
completed (including when a change is detected); exit code 2 means it could not
complete. A detected change is a review signal, not an automatic upgrade request.

Run it before a release and when reviewing provider changes. When upstream
changes, inspect the published version's source and relevant official API docs,
identify concrete behavior differences, add suitable fixtures or bounded live
verification, and then update this baseline and the script constants together.
The npm version check does not detect every independently published API or
documentation change. Capability claims should remain scoped to tested behavior.
