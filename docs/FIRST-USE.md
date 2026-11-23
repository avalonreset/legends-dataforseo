# Your first provider request

Start with the [installation guide](INSTALL.md). Discovery, estimates, and CLI
previews run offline. Provider requests require your DataForSEO API credentials.
A protected `serp` pull also runs the independent SERP Truth Gate by default:
set `FIRECRAWL_API_KEY` (process environment, or the Windows user environment
unless `FIRECRAWL_DISABLE_USER_ENV=1`) so the default consensus check can call
`https://api.firecrawl.dev/v2/search` (2 Firecrawl credits). Without it, the
SERP fails closed as unverified unless you supply your own independent view in
Python. `legends-dataforseo doctor` reports `firecrawl_api_key_configured`
without printing the key.

## 1. Preview

```sh
legends-dataforseo serp "technical SEO" --location-code 2840 --depth 10
legends-dataforseo estimate serp --tasks 1 --depth 10
```

The first command prints the exact endpoint and task body with `preview: true`.
The second provides a local baseline, not a current provider quote. Verify the
operation, location, depth, and [current pricing](https://dataforseo.com/pricing).

## 2. Verify credentials

```sh
legends-dataforseo doctor --live
```

This account authentication probe reports status and cost without printing the
account response. It is not a paid search.

## 3. Execute deliberately

After reviewing the provider price, substitute your estimate and ceiling for
the placeholders below. The estimate covers this whole request in USD:

```text
legends-dataforseo serp "technical SEO" --location-code 2840 --depth 10 --execute --estimated-cost-usd <REVIEWED_ESTIMATE> --max-cost-usd <REQUEST_CEILING>
```

`--execute` authorizes the paid request. No further interactive prompt appears.
The local check compares the supplied estimate with the ceiling before HTTP;
it cannot enforce provider billing. CLI output is provider JSON and can include
your query. Store it privately if you need to retain it. For `serp`, `--execute`
also authorizes the outbound Truth Gate call to
`https://api.firecrawl.dev/v2/search` (keyword, location, and 2-letter
`--country`, default `US`); the preview shows this in its `truth_gate` block and
makes no network request.

## 4. Inspect the response

Check the envelope's `status_code`, each task's `status_code`, task IDs, and
reported `cost`. A successful envelope does not mean every task succeeded.
The transport preserves task statuses for your application to interpret.

For Maps standard queues, save the task IDs from `/serp/google/maps/task_post`
and retrieve them through `/serp/google/maps/task_get/advanced/{id}`. Pending
tasks are not a reason to submit another paid task. Your application owns the
polling interval, total budget, and response storage.

The kit never retries a request automatically. A timeout can occur after the
provider accepted a paid request; reconcile its state before resubmitting.

[Queue integration](INTEGRATIONS.md) · [Python contract](API.md) · [CLI reference](CLI.md)
