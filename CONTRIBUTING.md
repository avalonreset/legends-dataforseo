# Contributing

Bug fixes, clearer examples, and focused improvements are welcome. Start with an
issue for a substantial API or routing change so the scope can be discussed.

## Work locally

Use Python 3.11 or newer and a virtual environment, then run from the repository:

```sh
python -m pip install -e ".[dev]"
python -m pytest
python examples/offline.py
python -m legends_dataforseo doctor
python -m build
python -m twine check dist/*
python scripts/audit_public.py
python scripts/smoke_wheel.py
```

Tests and default examples run offline. Live provider calls are unnecessary for
routine contributions. If a live test is needed, use your own account with an
explicit budget and keep its responses private.

## Preserve the public contract

- Keep task IDs and raw per-task status envelopes, including pending and empty results.
- Never automatically retry a paid submission or switch accounts.
- Reject unconfirmed paid requests before HTTP and preserve the caller's budget checks.
- Keep secrets out of arguments, output, fixtures, commits, and issue reports.
- Use synthetic HTTP fixtures for regression tests and maintain Python 3.11 compatibility.
- Document behavior changes in [CHANGELOG.md](CHANGELOG.md) and [docs/API.md](docs/API.md).

Keep a pull request focused. Explain the user-visible result and relevant checks.
Be respectful and specific in discussion. The project is maintained on a
best-effort basis; there is no promised review or support response time.

Contributions are provided under the repository's [MIT license](LICENSE).
Identify the origin and license of any third-party material; do not add captured
customer responses or provider SDK code without reviewing its distribution terms.

See [SECURITY.md](SECURITY.md) for private reports and [docs/RELEASING.md](docs/RELEASING.md)
for the release process.
