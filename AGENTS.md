# Agent instructions

Start with [docs/AGENT-RECIPE.md](docs/AGENT-RECIPE.md), [README.md](README.md), and [docs/API.md](docs/API.md).
This project is agent-neutral. Use Python and the CLI directly; no MCP server or particular coding agent is required.

## Core Operating Rules

1. **Single Router Skill**: Discovery belongs to `skills/cto-legends/SKILL.md`. Do not add secondary skills under `skills/`.
2. **Readiness First**: Run `legends-dataforseo doctor` (or `python -m legends_dataforseo doctor`) and `legends-dataforseo routes` offline before preparing requests.
3. **Offline Tests by Default**: Run `python scripts/check_release.py`, `python -m unittest discover -s tests -v`, and `python -m pytest` for changes; tests must stay offline by default.
4. **Preserve Task Envelopes**: Preserve raw task envelopes for pending, empty, and failed tasks. Never retry a paid request automatically or hide a returned task ID.
5. **Preview Paid Work**: Preview paid requests offline first and respect the user's execution authorization and budget (`--execute --estimated-cost-usd <USD> --max-cost-usd <USD>` on CLI, or `confirm=True` in Python).
6. **Zero Secret Exposure**: Credentials (`DATAFORSEO_LOGIN` / `DATAFORSEO_USERNAME` and `DATAFORSEO_PASSWORD`) belong in environment/secret management. Never print them, commit them, or include account responses, cost ledgers, or private queries in public files.

## EDR and Security Guardrails

- **No Hidden Console Spawning or Bypass Wrappers**: Never spawn hidden consoles, execution-policy bypass shell wrappers, or background localhost daemons. Do not ship PowerShell, batch, or VBS wrapper scripts.
- **No Browser Profile or DPAPI Credential Scanning**: Never read or scan local browser profile databases, DPAPI stores, or third-party credential stores outside the project workspace. Read DataForSEO credentials strictly from the environment.
- **Fail-Closed Single-File Output**: `--output <path>` reserves a single new file before HTTP and refuses existing files or symlinks. Evidence bank exports operate strictly inside the explicitly supplied target directory with symlink refusal.

Before release, run `python scripts/check_release.py`, `python -m unittest discover -s tests -v`, and `python -m pytest`, build wheel and sdist, run `scripts/audit_public.py` and `scripts/smoke_wheel.py`, and verify the offline CLI.
