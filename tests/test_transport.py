import io
import json
from urllib.error import HTTPError, URLError

import pytest

from legends_dataforseo import (API_ROOT, ApiError, CostLimitError, CredentialError,
                               Credentials, RouteError, api_request, business, demand, maps, serp)
from legends_dataforseo import client

AUTH = Credentials("fixture-login", "fixture-password")
LIVE = "/serp/google/maps/live/advanced"
QUEUE = "/serp/google/maps/task_post"
GET = "/serp/google/maps/task_get/advanced/synthetic-task-id"


def transport(monkeypatch, result):
    calls = []
    def open_request(request, timeout):
        calls.append((request, timeout))
        return io.BytesIO(json.dumps(result).encode())
    monkeypatch.setattr(client, "_open", open_request)
    return calls


@pytest.mark.parametrize("status", [20000, 20100, 40102, 40601, 40602, 40101, 40401, 40200])
@pytest.mark.parametrize("path,payload", [(LIVE, [{}]), (QUEUE, [{}, {}]), (GET, None)])
def test_task_envelopes_are_preserved(monkeypatch, status, path, payload):
    expected = {"status_code": 20000, "tasks_error": 1, "cost": 0.002,
                "tasks": [{"id": "synthetic-task-id", "status_code": status, "result": None}]}
    calls = transport(monkeypatch, expected)
    result = api_request(path, payload, timeout=17, confirm=payload is not None,
                         credentials=AUTH, consumer="legends-geogrid")
    assert result == expected
    assert len(calls) == 1
    request, timeout = calls[0]
    assert request.full_url == API_ROOT + path
    assert request.method == ("POST" if payload else "GET")
    assert timeout == 17
    assert (json.loads(request.data) if request.data else None) == payload


def test_method_body_alias_and_v3_prefix(monkeypatch):
    calls = transport(monkeypatch, {"status_code": 20000})
    api_request("/v3" + QUEUE, method="POST", body=[{"keyword": "example"}],
                credentials=AUTH, confirm=True)
    assert calls[0][0].full_url == API_ROOT + QUEUE
    assert json.loads(calls[0][0].data) == [{"keyword": "example"}]


@pytest.mark.parametrize("path", ["https://example.com", "//example.com", "/../secret",
                                   "/x?query=secret", "/%2e%2e/", "/x#fragment", "/", "/x\\y"])
def test_rejects_unsafe_paths_before_credentials(path):
    with pytest.raises(RouteError):
        api_request(path, confirm=True)


@pytest.mark.parametrize("kwargs", [
    {"payload": {}}, {"payload": []}, {"payload": [None]},
    {"payload": [{}], "body": [{}]}, {"method": "POST"},
    {"method": "GET", "body": [{}]}, {"method": "DELETE"}, {"method": 1},
    {"payload": [{"depth": float("nan")}]},
])
def test_invalid_inputs_before_credentials(kwargs):
    with pytest.raises(RouteError):
        api_request(LIVE, confirm=True, **kwargs)


def test_post_confirmation_and_unknown_get_gate():
    with pytest.raises(CostLimitError):
        api_request(LIVE, [{}])
    with pytest.raises(CostLimitError):
        api_request("/future/unclassified")


@pytest.mark.parametrize("limit,estimate", [(0.01, 0.02), (0.01, None)])
def test_budget_refuses_before_credentials(limit, estimate):
    with pytest.raises(CostLimitError):
        api_request(QUEUE, [{}], confirm=True, max_cost_usd=limit, estimated_cost_usd=estimate)


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "bad"])
@pytest.mark.parametrize("field", ["estimated_cost_usd", "max_cost_usd", "timeout"])
def test_invalid_cost_values(field, value):
    with pytest.raises(ValueError):
        api_request(QUEUE, [{}], confirm=True, **{field: value})


def test_reviewed_ceiling_allows_one_request(monkeypatch):
    calls = transport(monkeypatch, {"status_code": 20000, "cost": 0.02})
    result = api_request(QUEUE, [{}], credentials=AUTH, confirm=True,
                         estimated_cost_usd=0.01, max_cost_usd=0.01)
    assert len(calls) == 1
    # Actual provider cost can exceed the estimate; never discard the response.
    assert result["cost"] == 0.02


def test_batch_limits():
    with pytest.raises(RouteError):
        api_request(LIVE, [{}, {}], confirm=True)
    with pytest.raises(RouteError):
        api_request(QUEUE, [{}] * 101, confirm=True)


@pytest.mark.parametrize("status", [301, 302, 307, 400, 401, 402, 429, 500])
def test_http_error_preserves_body_without_retry_or_leaks(monkeypatch, status):
    evidence = {"status_code": 40200, "status_message": "fixture-password private-input"}
    calls = []
    def failure(request, timeout):
        calls.append(request)
        raise HTTPError(request.full_url, status, "fixture-password", {}, io.BytesIO(json.dumps(evidence).encode()))
    monkeypatch.setattr(client, "_open", failure)
    with pytest.raises(ApiError) as error:
        api_request(QUEUE, [{}], confirm=True, credentials=AUTH)
    assert len(calls) == 1
    assert error.value.response == evidence
    assert error.value.http_status == status
    assert "fixture-password" not in str(error.value)
    assert "private-input" not in repr(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("failure", [URLError("fixture-password"), TimeoutError("private-input")])
def test_network_failures_no_retry_or_details(monkeypatch, failure):
    calls = []
    def fail(*args):
        calls.append(args)
        raise failure
    monkeypatch.setattr(client, "_open", fail)
    with pytest.raises(ApiError) as error:
        api_request(QUEUE, [{}], confirm=True, credentials=AUTH)
    assert len(calls) == 1
    assert "fixture-password" not in str(error.value)
    assert "private-input" not in str(error.value)


@pytest.mark.parametrize("raw", [b"not-json", b"\xff", b"[]", b"null"])
def test_invalid_response(monkeypatch, raw):
    monkeypatch.setattr(client, "_open", lambda *_: io.BytesIO(raw))
    with pytest.raises(ApiError):
        api_request(GET, credentials=AUTH)


def test_rejected_top_envelope_preserves_response(monkeypatch):
    response = {"status_code": 40200, "status_message": "private-input"}
    transport(monkeypatch, response)
    with pytest.raises(ApiError) as error:
        api_request(GET, credentials=AUTH)
    assert error.value.response == response
    assert "private-input" not in str(error.value)


def test_redirect_handler_blocks_cross_host(monkeypatch):
    from urllib.request import Request
    assert client._NoRedirect().redirect_request(Request(API_ROOT + GET), None, 302,
                                                "Found", {}, "https://example.com") is None


def test_named_research_methods(monkeypatch):
    calls = transport(monkeypatch, {"status_code": 20000})
    opts = dict(credentials=AUTH, confirm=True, estimated_cost_usd=0.02, max_cost_usd=0.02)
    serp("example", **opts)
    demand(["one", "two"], **opts)
    maps("coffee", location_coordinate="40.7128,-74.0060,15z", **opts)
    business("cid:3865327066180748277", **opts)
    assert calls[0][0].full_url.endswith("/serp/google/organic/live/advanced")
    assert json.loads(calls[1][0].data)[0]["keywords"] == ["one", "two"]
    assert json.loads(calls[2][0].data)[0]["location_coordinate"] == "40.7128,-74.0060,15z"
    assert calls[3][0].full_url.endswith("/business_data/google/my_business_info/live")
    assert json.loads(calls[3][0].data)[0]["keyword"] == "cid:3865327066180748277"


def test_ledger_is_optional_redacts_task_ids_and_survives_disk_failure(monkeypatch, tmp_path):
    target = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("LEGENDS_DATAFORSEO_LEDGER", str(target))
    result = {"status_code": 20000, "cost": 0, "tasks": [{"data": {"keyword": "private-input"}}]}
    transport(monkeypatch, result)
    api_request(GET, credentials=AUTH)
    assert not target.exists()
    api_request(GET, credentials=AUTH, log_cost=True)
    text = target.read_text()
    assert "private-input" not in text and "synthetic-task-id" not in text and "fixture-password" not in text
    monkeypatch.setattr(client, "_append_ledger", lambda *_: (_ for _ in ()).throw(OSError("private-input")))
    with pytest.warns(RuntimeWarning, match="do not repeat"):
        assert api_request(GET, credentials=AUTH, log_cost=True) == result
