from pathlib import Path
import sys

SRC = str(Path(__file__).resolve().parents[1] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import pytest
from legends_dataforseo import client, documentation


@pytest.fixture(autouse=True)
def isolate_credentials_and_network(monkeypatch):
    for name in ("DATAFORSEO_LOGIN", "DATAFORSEO_USERNAME", "DATAFORSEO_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(client, "_windows_user_environment", lambda _: None)
    monkeypatch.setattr(client, "_open", lambda *_: pytest.fail("Unexpected network request"))
    monkeypatch.setattr(documentation, "_open", lambda *_: pytest.fail("Unexpected documentation network request"))
