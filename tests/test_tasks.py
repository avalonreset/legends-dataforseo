"""Synthetic queue lifecycle coverage: no credentials, HTTP, or real sleeps."""

import pytest

from legends_dataforseo import ApiError, RouteError
from legends_dataforseo import tasks

PATH = "/serp/google/maps/task_get/advanced/saved-task-id"


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 0.0
        sleeps = []

        def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(tasks.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(tasks.time, "sleep", clock.sleep)
    return clock


def envelope(code, cost=0):
    return {"status_code": 20000, "cost": cost,
            "tasks": [{"id": "saved-task-id", "status_code": code, "result": None}]}


def mock_calls(monkeypatch, responses):
    calls = []
    responses = iter(responses)

    def request(path, **kwargs):
        calls.append((path, kwargs))
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(tasks.client, "api_request", request)
    return calls


def test_pending_to_completed_keeps_raw_response(monkeypatch, clock):
    raw = envelope(20000, 0.003)
    calls = mock_calls(monkeypatch, [envelope(20100, 0.003), envelope(40601, 0.003), envelope(40602), raw])
    result = tasks.wait_task(PATH, interval=2, consumer="fixture")
    assert result["response"] is raw
    assert result["state"] == "completed"
    assert result["attempts"] == 4
    assert result["reported_cost_usd"] == 0.003
    assert result["reported_cost_complete"]
    assert result["cost_scope"] == "last_response_not_incremental_billing"
    assert clock.sleeps == [2, 2, 2]
    assert all(path == PATH and options["method"] == "GET" for path, options in calls)
    assert all("payload" not in options and "body" not in options for _, options in calls)


@pytest.mark.parametrize("code,state", [(40102, "empty"), (40401, "stopped"),
                                       (50000, "stopped"), (None, "stopped"),
                                       ("40602", "stopped"), ([], "stopped")])
def test_terminal_or_unknown_never_polled(monkeypatch, clock, code, state):
    raw = envelope(code)
    calls = mock_calls(monkeypatch, [raw])
    result = tasks.wait_task(PATH)
    assert result["state"] == state
    assert result["response"] is raw
    assert len(calls) == 1 and not clock.sleeps


@pytest.mark.parametrize("task_list", [None, [], [None], [{}, {}]])
def test_malformed_task_list_stops(monkeypatch, clock, task_list):
    raw = {"tasks": task_list}
    mock_calls(monkeypatch, [raw])
    result = tasks.wait_task(PATH)
    assert result["state"] == "stopped"
    assert not result["reported_cost_complete"]


def test_attempt_limit_returns_pending_without_extra_sleep(monkeypatch, clock):
    raw = envelope(40602, 0.0006)
    calls = mock_calls(monkeypatch, [raw] * 3)
    result = tasks.wait_task(PATH, max_attempts=3, interval=1)
    assert result["state"] == "pending" and result["stop_reason"] == "max_attempts"
    assert result["response"] is raw
    assert result["reported_cost_usd"] == 0.0006
    assert len(calls) == 3 and clock.sleeps == [1, 1]


@pytest.mark.parametrize("cost", [None, -1, float("inf"), float("nan"), True, "invalid"])
def test_invalid_latest_cost_does_not_retain_earlier_cost(monkeypatch, clock, cost):
    mock_calls(monkeypatch, [envelope(40602, 0.0006), envelope(20000, cost)])
    result = tasks.wait_task(PATH)
    assert result["reported_cost_usd"] is None
    assert not result["reported_cost_complete"]


def test_missing_latest_cost_does_not_retain_earlier_cost(monkeypatch, clock):
    raw = envelope(20000)
    del raw["cost"]
    mock_calls(monkeypatch, [envelope(40602, 0.0006), raw])
    result = tasks.wait_task(PATH)
    assert result["reported_cost_usd"] is None
    assert not result["reported_cost_complete"]


def test_latest_valid_cost_replaces_missing_earlier_cost(monkeypatch, clock):
    mock_calls(monkeypatch, [envelope(40602, None), envelope(20000, 0.0006)])
    result = tasks.wait_task(PATH)
    assert result["reported_cost_usd"] == 0.0006
    assert result["reported_cost_complete"]


def test_zero_attempts_has_unknown_cost(monkeypatch):
    ticks = iter([0, 2, 2])
    monkeypatch.setattr(tasks.time, "monotonic", lambda: next(ticks))
    calls = mock_calls(monkeypatch, [])
    result = tasks.wait_task(PATH, max_elapsed=1)
    assert not calls
    assert result["attempts"] == 0 and result["response"] is None
    assert result["reported_cost_usd"] is None
    assert not result["reported_cost_complete"]


def test_elapsed_limit_caps_socket_timeout_and_sleep(monkeypatch, clock):
    calls = mock_calls(monkeypatch, [envelope(40602)])
    result = tasks.wait_task(PATH, max_elapsed=2, timeout=90, interval=5)
    assert calls[0][1]["timeout"] == 2
    assert clock.sleeps == [2]
    assert result["stop_reason"] == "max_elapsed"
    assert result["attempts"] == 1


def test_request_time_counts_against_elapsed(monkeypatch, clock):
    def slow_request(*args, **kwargs):
        clock.now += 3
        return envelope(40602)

    monkeypatch.setattr(tasks.client, "api_request", slow_request)
    result = tasks.wait_task(PATH, max_elapsed=2)
    assert result["stop_reason"] == "max_elapsed"
    assert result["attempts"] == 1 and clock.sleeps == []


@pytest.mark.parametrize("error", [ApiError("transport timeout"),
                                   ApiError("rejected", response={"status_code": 50000})])
def test_transport_failure_propagates_without_retry(monkeypatch, clock, error):
    calls = mock_calls(monkeypatch, [error])
    with pytest.raises(ApiError) as caught:
        tasks.wait_task(PATH)
    assert caught.value is error
    assert len(calls) == 1 and not clock.sleeps


@pytest.mark.parametrize("path", ["/serp/google/maps/task_post", "/appendix/user_data",
    "/made_up/task_get/id", "/serp/google/maps/task_get/advanced/", PATH + "?x=1",
    "https://api.dataforseo.com/v3" + PATH, PATH + "/extra", PATH + ".ai"])
def test_invalid_path_blocked_before_http(monkeypatch, path):
    calls = mock_calls(monkeypatch, [])
    with pytest.raises(RouteError):
        tasks.wait_task(path)
    assert not calls


@pytest.mark.parametrize("options", [{"max_attempts": True}, {"max_attempts": 0},
    {"max_attempts": 1001}, {"max_attempts": 1.5}, {"interval": -1},
    {"interval": float("inf")}, {"timeout": 0}, {"timeout": float("nan")},
    {"max_elapsed": 0}, {"max_elapsed": False}])
def test_invalid_bounds_blocked_before_http(monkeypatch, options):
    calls = mock_calls(monkeypatch, [])
    with pytest.raises(ValueError):
        tasks.wait_task(PATH, **options)
    assert not calls


@pytest.mark.parametrize("path", [PATH, "/v3" + PATH,
    "/serp/google/organic/task_get/advanced/id", "/serp/bing/organic/task_get/regular/id",
    "/business_data/google/reviews/task_get/id",
    "/business_data/google/my_business_info/task_get/id"])
def test_known_paths(path):
    assert tasks.retrieval_path(path) == path.removeprefix("/v3")
