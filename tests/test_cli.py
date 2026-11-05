import json
from pathlib import Path
import sys

SRC = str(Path(__file__).resolve().parents[1] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import pytest
from legends_dataforseo import __version__, estimate_cost, route_for
from legends_dataforseo import cli


@pytest.mark.parametrize("argv", [
    ["routes"], ["route", "maps-task-post"], ["route", "business-task-post"], ["doctor"],
    ["estimate", "maps", "--tasks", "9", "--depth", "100"],
    ["estimate", "business-task-post", "--tasks", "10"],
    ["serp", "example"], ["demand", "one", "two"], ["business", "cid:3865327066180748277"],
    ["maps", "coffee", "--location-coordinate", "40.7128,-74.0060,15z"],
    ["call", "/serp/google/maps/task_get/advanced/synthetic-id"],
    ["call", "/business_data/google/my_business_info/task_get/synthetic-id"],
])
def test_offline_cli_needs_no_credentials_or_network(capsys, argv):
    assert cli.main(argv) == 0
    assert isinstance(json.loads(capsys.readouterr().out), dict)


def test_paid_cli_requires_budget(capsys):
    assert cli.main(["serp", "example", "--execute"]) == 2
    assert "--max-cost-usd" in capsys.readouterr().err


def test_budget_cli_preflight(capsys):
    assert cli.main(["serp", "example", "--execute", "--estimated-cost-usd", "2",
                     "--max-cost-usd", "1"]) == 2
    assert "CostLimitError" in capsys.readouterr().err


def test_live_doctor_sanitizes_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "api_request", lambda *_: {"status_code": 20000, "cost": 0,
                       "tasks": [{"status_code": 20000, "result": [{"login": "private-account"}]}]})
    assert cli.main(["doctor", "--live"]) == 0
    result = capsys.readouterr().out
    assert "private-account" not in result
    assert json.loads(result)["live"] is True


def test_queue_call_previews_json_file(tmp_path, capsys):
    body = tmp_path / "tasks.json"
    body.write_text('[{"keyword":"coffee"}]', encoding="utf-8")
    assert cli.main(["call", "/serp/google/maps/task_post", "--body-file", str(body)]) == 0
    assert json.loads(capsys.readouterr().out)["body"] == [{"keyword": "coffee"}]


def test_registry_estimates_and_version():
    from legends_dataforseo import load_routes
    assert load_routes()["version"] == __version__
    assert route_for("maps-task-get")["method"] == "GET"
    assert estimate_cost("maps", tasks=9, depth=100)["estimated_cost_usd"] == 0.018
    assert estimate_cost("maps-task-post")["estimated_cost_usd"] is None
    assert estimate_cost("status")["estimated_cost_usd"] == 0


def test_doctor_reports_readiness_and_env_presence(monkeypatch, capsys):
    assert cli.main(["doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["version"] == __version__
    assert isinstance(payload["python"], str) and payload["python"]
    assert payload["dataforseo_login_configured"] is False
    assert payload["dataforseo_password_configured"] is False
    monkeypatch.setenv("DATAFORSEO_LOGIN", "fixture-login")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "fixture-secret")
    assert cli.main(["doctor"]) == 0
    configured = json.loads(capsys.readouterr().out)
    assert configured["dataforseo_login_configured"] is True
    assert configured["dataforseo_password_configured"] is True
    assert configured["credentials"] == {"present": True, "source": "process-env"}


import contextlib
import io
import os
import unittest
from unittest.mock import patch
from legends_dataforseo import client


class DoctorUnittestSuite(unittest.TestCase):
    def test_doctor_offline_readiness_and_credentials(self):
        with patch.dict(os.environ, {}, clear=False), patch.object(
            client, "_windows_user_environment", return_value=None
        ):
            for key in ("DATAFORSEO_LOGIN", "DATAFORSEO_USERNAME", "DATAFORSEO_PASSWORD"):
                os.environ.pop(key, None)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["doctor"])
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["version"], __version__)
            self.assertFalse(data["dataforseo_login_configured"])
            self.assertFalse(data["dataforseo_password_configured"])

            os.environ["DATAFORSEO_LOGIN"] = "fixture-login"
            os.environ["DATAFORSEO_PASSWORD"] = "fixture-secret"
            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                rc2 = cli.main(["doctor"])
            self.assertEqual(rc2, 0)
            data2 = json.loads(buf2.getvalue())
            self.assertTrue(data2["dataforseo_login_configured"])
            self.assertTrue(data2["dataforseo_password_configured"])

