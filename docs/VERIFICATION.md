# Release verification

Checked 2026-09-21. These observations apply to the covered release and the
listed workflows, not every DataForSEO product or an unlimited production load.

## Automated checks

- 225 offline tests: credential handling, confirmation and cost guards, raw
  transport compatibility, protocol failures, documentation caching and URL
  restrictions, saved output, selection, and bounded queue retrieval.
- 21 consumer tests passed against the candidate package across GeoGrid transport
  and GitHub research tests, including real-package transport fixtures.
- CI runs the suite and package build on Windows, Linux, and macOS with Python
  3.11 and 3.13. The release process requires every job to pass.
- Release checks include wheel/sdist metadata, private-artifact scanning, clean
  installed-wheel API/CLI smoke checks, and uploaded asset checksums.

## Paid provider acceptance

Each submission used a single small task and explicit execution authorization.
The private runner saved a started marker before HTTP and never resubmitted a
started task. Queries and provider response files are excluded from this repo.

| Operation | Observed result | Reported task cost, USD |
| --- | --- | ---: |
| Google organic live advanced | Success | 0.002 |
| Google organic live advanced `.ai` | Success; compact schema | 0.002* |
| Google Maps live advanced | Success | 0.002 |
| Labs keyword overview | Success | 0.01212 |
| Bing organic live advanced | Success | 0.002 |
| Labs related keywords | Success | 0.01236 |
| On-page instant, JavaScript/resources disabled | Success | 0.00015 |
| Google Maps normal queue | Created, pending, then completed | 0.0006 |
| Google organic normal queue | Created, pending, then completed | 0.0006 |

The unique submitted tasks reported **$0.03383** in total. These are observed
task costs for these requests, not current price guarantees or an account audit.

\* The `.ai` response contained root-level `id`, `status_code`, and `items` but no
cost. Standard retrieval of that same saved ID supplied the reported task cost.
No second paid submission was made to obtain that evidence.

Both standard queues initially reached a polling bound while pending. A later
retrieval using the saved IDs completed successfully. Completed GET responses
repeated the original submission cost, so the helper reports the last response's
cost and explicitly labels it **not incremental billing**. It never sums repeated
retrieval reports as new charges.

## Result size measurement

One successful standard Google organic response was serialized as compact UTF-8
JSON text with Python `json.dumps(..., separators=(',', ':'), ensure_ascii=False)`.
The same response was rendered locally in three ways:

| View | Characters |
| --- | ---: |
| Full response | 41,019 |
| First two items plus complete task summary | 1,646 |
| Task summary only | 256 |

The full saved response was unchanged. This measures one response's character
count, not model tokens, equal-information compression, or a benchmark against
the official MCP/CLI. The selected view intentionally includes less result data.
Missing information remains available in the saved file without a paid call.

## Documentation and upstream

Live official-documentation discovery parsed 614 index entries, found Maps queue
documentation, retrieved its Markdown, and reread it from the offline cache.
The index is mutable and its entry count may change.

Official npm baseline: `dataforseo-mcp-server` 3.1.1, source commit
`fde5554e7b57f40528e73e55d90c82c8300b726b`. See [UPSTREAM.md](UPSTREAM.md) for
primary sources and the comparison boundaries. No vendor runtime was installed.

## Remaining boundaries

The kit cannot enforce provider-side spending limits. It does not claim every
API endpoint has been exercised live. Saved response files may contain private
data. A killed process can leave partial output, and a network timeout cannot
prove a submitted task was rejected. No automatic paid retries, account switching,
or task submission occurs during recovery.
