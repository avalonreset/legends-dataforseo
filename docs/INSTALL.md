# Install legends-dataforseo

Python 3.10 or newer is required. Release wheels work on Windows, Linux, and
macOS, with no runtime dependencies or Git requirement.

## Create a project environment

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install "https://github.com/avalonreset/legends-dataforseo/releases/download/v0.1.5/legends_dataforseo-0.1.5-py3-none-any.whl"
.venv\Scripts\python.exe -m legends_dataforseo doctor
```

macOS or Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install "https://github.com/avalonreset/legends-dataforseo/releases/download/v0.1.5/legends_dataforseo-0.1.5-py3-none-any.whl"
.venv/bin/python -m legends_dataforseo doctor
```

Activating the environment is optional when using its Python path directly.
In an activated environment, `legends-dataforseo` and
`python -m legends_dataforseo` expose the same commands.

## Source installs and dependency pins

The [release page](https://github.com/avalonreset/legends-dataforseo/releases/latest)
contains a wheel, source distribution, and SHA-256 checksums. GitHub also supplies
ZIP and tar archives of the tagged repository. A source ZIP can be installed
directly with pip; Git is not needed:

```sh
python -m pip install "https://github.com/avalonreset/legends-dataforseo/archive/refs/tags/v0.1.5.zip"
```

For reproducible consumer dependencies, use the release's full commit in a
requirements file:

```text
legends-dataforseo @ https://github.com/avalonreset/legends-dataforseo/archive/<FULL_RELEASE_COMMIT>.zip
```

Replace the placeholder with the verified 40-character commit from the release
notes. Existing commit pins remain supported by the same request contract.
Source installation may download build tools; these are not runtime dependencies.

To work on the code, clone the repository and run
`python -m pip install -e ".[dev]"`. See [CONTRIBUTING.md](../CONTRIBUTING.md).

## Verify a download

Download the wheel and `SHA256SUMS.txt` from the same release. Compare the wheel's
SHA-256 hash with its exact filename in the checksums file:

```powershell
Get-FileHash -Algorithm SHA256 .\legends_dataforseo-0.1.5-py3-none-any.whl
```

```sh
sha256sum legends_dataforseo-0.1.5-py3-none-any.whl
# macOS alternative: shasum -a 256 legends_dataforseo-0.1.5-py3-none-any.whl
```

Then install that local file with `python -m pip install ./<wheel-filename>`.
Checksums detect mismatched bytes; they are not a separate publisher signature.

## Credentials

Use the API credentials from your DataForSEO account, supplied through a secret
manager or the environment. Do not put real values in command-line arguments,
source files, issue reports, or chat.

| Environment variable | Purpose |
| --- | --- |
| `DATAFORSEO_LOGIN` | API login; preferred name |
| `DATAFORSEO_USERNAME` | Login alias for existing integrations |
| `DATAFORSEO_PASSWORD` | API password |

The loader uses a complete process environment pair first, then a complete
Windows user environment pair. It never combines credentials across scopes or
reads a dotenv file. An incomplete process override is rejected instead of
silently combining it with another account. Restart the shell or application
after changing persistent environment settings.

```sh
legends-dataforseo doctor
legends-dataforseo doctor --live
```

The default doctor is offline and reports credential presence without values.
`--live` sends a no-charge authentication check. For charged requests, continue
with [first use](FIRST-USE.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Command not found | Use the installed environment's `python -m legends_dataforseo`. |
| Missing credentials | Supply a complete login/password pair in one supported scope. |
| Authentication rejected | Verify the API credentials in the provider account; do not paste them into an issue. |
| Request confirmation required | Preview first, then explicitly authorize with `confirm=True` or `--execute`. |
| Cost limit error | Supply a reviewed estimate no greater than the request ceiling. |
| Pending or empty results | Inspect each task's status and saved ID; see [API.md](API.md). |

To upgrade, install the chosen release wheel with `python -m pip install --upgrade <wheel-url>`.
To uninstall, run `python -m pip uninstall legends-dataforseo`. Uninstalling
the package does not clear credentials or private files you saved separately.

## Upgrading from the former distribution name

In the same virtual environment, uninstall `legends-dataforseo-kit` before
installing the new wheel above. Both distributions provide the same import and
CLI; keeping both installed can make a later uninstall remove shared files.
Existing documentation caches and cost ledgers are reused in place.
