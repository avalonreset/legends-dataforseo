# Operating legends-dataforseo

This is an on-demand module recipe, not a registered skill. The central `cto-legends` router owns discovery (`skills/cto-legends/SKILL.md`).

## Readiness

1. Run `legends-dataforseo doctor` (or `python -m legends_dataforseo doctor`). This is offline, free, and verifies Python 3.11+, the local route registry, and whether `DATAFORSEO_LOGIN` (or `DATAFORSEO_USERNAME`) and `DATAFORSEO_PASSWORD` are configured in the environment.
2. Explore operations and baseline cost estimates offline before any live call:
   - `legends-dataforseo routes`
   - `legends-dataforseo route <operation>`
   - `legends-dataforseo estimate <operation> [--tasks N] [--depth D] [--items I]`
3. For authorized live requests, ensure `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` are set in the environment, then verify authentication with the no-charge account probe: `legends-dataforseo doctor --live`.
4. All named CLI commands (`serp`, `maps`, `business`, `demand`) and `call` preview by default (`preview: true`) and make zero network requests unless `--execute` is passed. Paid POST requests additionally require `--estimated-cost-usd <USD>` and `--max-cost-usd <USD>`.

## CLI and Python Workflow Reference

| Goal | CLI Command | Python API | Notes |
|---|---|---|---|
| Offline readiness check | `legends-dataforseo doctor` | `credential_status()`, `load_routes()` | Returns `0` offline; reports Python, route count, and credential presence without secret values |
| Live auth check (free) | `legends-dataforseo doctor --live` | `api_request("/appendix/user_data")` | Verifies account auth via `/appendix/user_data` without printing login/account body |
| List route catalog | `legends-dataforseo routes` | `load_routes()` | Lists all registered DataForSEO v3 operations |
| Inspect single route | `legends-dataforseo route <op>` | `route_for(op)` | Shows path, HTTP method, charged flag, and baseline estimate model |
| Estimate baseline cost | `legends-dataforseo estimate <op> --tasks N --depth D` | `estimate_cost(op, tasks=N, depth=D, items=I)` | Offline advisory baseline; check live pricing before execution |
| Google Organic SERP | `legends-dataforseo serp "<query>" [--location-code 2840] [--depth 10]` | `serp(keyword, location_code=2840, depth=10, ...)` | Previews offline by default; add `--execute --estimated-cost-usd X --max-cost-usd Y` to send |
| Google Maps Live SERP | `legends-dataforseo maps "<query>" --location-coordinate "<lat,lng,z>"` | `maps(keyword, location_coordinate=..., depth=100, ...)` | Coordinate-pinned Maps SERP; add execution and cost flags to send |
| Google Business Profile | `legends-dataforseo business "<cid:ID\|place_id:ID\|query>"` | `business(keyword=..., target=..., location_code=2840, ...)` | Live `my_business_info` lookup; queued GBP uses `business-task-post` / `business-task-get` |
| Keyword Demand Overview | `legends-dataforseo demand "<kw1>" "<kw2>" ...` | `demand(keywords=[...], location_code=2840, ...)` | DataForSEO Labs keyword overview (volume, CPC, competition, intent) |
| Generic v3 Endpoint | `legends-dataforseo call <path> [--body-file tasks.json]` | `api_request(path, payload, confirm=True, ...)` | Supports any documented v3 relative path (and explicit `.ai` suffix) |
| Bounded Task Polling | `legends-dataforseo wait <task_get_path> [--execute]` | `wait_task(path, max_attempts=5, interval=5, ...)` | Performs only GET retrievals on saved task IDs; never submits or retries POSTs |
| Inspect Saved Response | `legends-dataforseo view <file.json> [--summary] [--select PATH] [--limit N]` | `response_view(data, summary=..., select=..., limit=...)` | Offline inspection and projection of saved JSON responses |
| Official Docs Discovery | `legends-dataforseo docs sections\|index\|search\|read [--offline]` | `docs_sections()`, `docs_index()`, `docs_search()`, `docs_read()` | Fetches or reads cached official `docs.dataforseo.com/v3` Markdown |
| Research Evidence Bank | `legends-dataforseo evidence export\|verify\|inventory\|find\|view\|reuse` | `legends_dataforseo.evidence` (`export`, `verify`, `assess_reuse`, `inventory`, `view`) | Content-addressed offline evidence packages with SHA-256 verification |

## EDR and Security Guardrails

1. **Zero Hidden Consoles or Bypass Wrappers**: Never spawn hidden consoles, execution-policy bypass shell wrappers, or background localhost daemons. Invoke `legends-dataforseo` or `python -m legends_dataforseo` directly as a standard foreground process.
2. **Strict Environment-Only Credential Loading**: Never read local browser profile databases, DPAPI stores, or third-party credential files outside the project workspace. Read `DATAFORSEO_LOGIN` (or `DATAFORSEO_USERNAME`) and `DATAFORSEO_PASSWORD` strictly from the environment.
3. **Zero Secret or Private Account Exposure**: Never print credential values, raw `/appendix/user_data` account bodies, or private client queries into public logs or committed Markdown receipts.
4. **Single-Request Transport & Fail-Closed File Output**: Never retry a paid POST request automatically or hide a returned task ID. `--output <path>` reserves a single new file before HTTP and refuses existing files or symlinks (`FileExistsError`), preventing accidental overwrite bursts or wasted paid calls.
5. **HTTPS-Only No-Redirect Transport**: All API traffic targets `https://api.dataforseo.com/v3` with HTTP redirects refused (`_NoRedirect`) so Basic Auth headers can never follow a redirect to another host.
