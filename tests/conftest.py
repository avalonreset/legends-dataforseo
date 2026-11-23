from pathlib import Path
import sys

SRC = str(Path(__file__).resolve().parents[1] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import pytest
from legends_dataforseo import client, documentation, truth_gate


@pytest.fixture(autouse=True)
def isolate_credentials_and_network(monkeypatch):
    for name in ("DATAFORSEO_LOGIN", "DATAFORSEO_USERNAME", "DATAFORSEO_PASSWORD", "FIRECRAWL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FIRECRAWL_DISABLE_USER_ENV", "1")
    monkeypatch.setattr(client, "_windows_user_environment", lambda _: None)
    monkeypatch.setattr(client, "_open", lambda *_: pytest.fail("Unexpected network request"))
    monkeypatch.setattr(documentation, "_open", lambda *_: pytest.fail("Unexpected documentation network request"))
    monkeypatch.setattr(truth_gate, "_windows_user_env", lambda _: None)
    monkeypatch.setattr(truth_gate, "_open_firecrawl", lambda *_: pytest.fail("Unexpected Firecrawl network request"))
