"""HTTP behaviour of the Cloud Logging and Cloud Trace clients.

urlopen is replaced throughout, so no network access or credentials are needed.
"""

import io
import json
import urllib.error
import urllib.request

import pytest

import gcp_client
from gcp_client import GcpClient, GcpError


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _ok(payload):
    return lambda req, timeout=None: _Response(json.dumps(payload).encode())


def _http_error(code, detail="boom"):
    return urllib.error.HTTPError(
        "https://example.googleapis.com", code, "err", {}, io.BytesIO(detail.encode())
    )


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Collapse the backoff so retry tests run instantly."""
    monkeypatch.setattr(gcp_client.time, "sleep", lambda _s: None)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", "token-1")
    return GcpClient("example-project")


def _headers(req):
    return {k.lower(): v for k, v in req.headers.items()}


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------


def test_token_is_taken_from_the_environment_when_present(client):
    assert client.token == "token-1"


def test_token_falls_back_to_the_gcloud_cli(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(gcp_client, "_gcloud_token", lambda: "from-gcloud")
    assert GcpClient("example-project").token == "from-gcloud"


def test_quota_project_defaults_to_the_target_project():
    assert GcpClient("example-project").quota_project == "example-project"
    assert GcpClient("example-project", quota_project="billing").quota_project == "billing"


def test_requests_carry_the_bearer_token_and_quota_project(client, monkeypatch):
    seen = {}

    def fake(req, timeout=None):
        seen.update(_headers(req))
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        return _Response(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    client._request("https://example.googleapis.com/v1/thing")

    assert seen["authorization"] == "Bearer token-1"
    assert seen["x-goog-user-project"] == "example-project"
    assert seen["method"] == "GET"


def test_a_body_is_sent_as_json_via_post(client, monkeypatch):
    seen = {}

    def fake(req, timeout=None):
        seen["data"] = req.data
        seen["method"] = req.get_method()
        seen["content_type"] = _headers(req).get("content-type")
        return _Response(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    client._request("https://example.googleapis.com/v1/thing", method="POST", body={"a": 1})

    assert json.loads(seen["data"]) == {"a": 1}
    assert seen["method"] == "POST"
    assert seen["content_type"] == "application/json"


def test_an_expired_token_is_refreshed_once(client, monkeypatch):
    monkeypatch.setattr(gcp_client, "_gcloud_token", lambda: "token-2")
    tokens_seen = []
    calls = []

    def fake(req, timeout=None):
        tokens_seen.append(_headers(req)["authorization"])
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(401, "token expired")
        return _Response(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake)

    assert client._request("https://example.googleapis.com/v1/thing") == {"ok": True}
    assert tokens_seen == ["Bearer token-1", "Bearer token-2"]


def test_a_second_401_is_not_retried_again(client, monkeypatch):
    monkeypatch.setattr(gcp_client, "_gcloud_token", lambda: "token-2")
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        raise _http_error(401, "still unauthorised")

    monkeypatch.setattr(urllib.request, "urlopen", fake)

    with pytest.raises(GcpError, match="HTTP 401"):
        client._request("https://example.googleapis.com/v1/thing")
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# retries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_failures_are_retried(client, monkeypatch, status):
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(status)
        return _Response(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake)

    assert client._request("https://example.googleapis.com/v1/thing") == {"ok": True}
    assert len(calls) == 2


def test_retries_are_bounded(client, monkeypatch):
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        raise _http_error(503)

    monkeypatch.setattr(urllib.request, "urlopen", fake)

    with pytest.raises(GcpError, match="HTTP 503"):
        client._request("https://example.googleapis.com/v1/thing")
    assert len(calls) == gcp_client._MAX_RETRIES + 1


def test_permission_errors_fail_immediately_and_surface_the_detail(client, monkeypatch):
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        raise _http_error(403, "caller lacks roles/logging.viewer")

    monkeypatch.setattr(urllib.request, "urlopen", fake)

    with pytest.raises(GcpError, match="roles/logging.viewer"):
        client._request("https://example.googleapis.com/v1/thing")
    assert len(calls) == 1


def test_network_errors_are_retried_then_reported(client, monkeypatch):
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(urllib.request, "urlopen", fake)

    with pytest.raises(GcpError, match="Network error"):
        client._request("https://example.googleapis.com/v1/thing")
    assert len(calls) == gcp_client._MAX_RETRIES + 1


def test_an_empty_response_body_parses_as_an_empty_object(client, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Response(b""))
    assert client._request("https://example.googleapis.com/v1/thing") == {}


# ---------------------------------------------------------------------------
# Cloud Logging
# ---------------------------------------------------------------------------


def test_iter_log_entries_follows_pagination(client, monkeypatch):
    bodies = []
    pages = [
        {"entries": [{"insertId": "a"}], "nextPageToken": "page-2"},
        {"entries": [{"insertId": "b"}]},
    ]

    def fake(req, timeout=None):
        bodies.append(json.loads(req.data))
        return _Response(json.dumps(pages[len(bodies) - 1]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)

    entries = list(client.iter_log_entries('logName="x"'))

    assert [e["insertId"] for e in entries] == ["a", "b"]
    assert bodies[0] == {
        "resourceNames": ["projects/example-project"],
        "filter": 'logName="x"',
        "orderBy": "timestamp desc",
        "pageSize": 1000,
    }
    assert bodies[1]["pageToken"] == "page-2"


def test_iter_log_entries_handles_an_empty_result(client, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _ok({}))
    assert list(client.iter_log_entries('logName="x"')) == []


# ---------------------------------------------------------------------------
# Cloud Trace
# ---------------------------------------------------------------------------


def test_iter_traces_requests_complete_spans_and_paginates(client, monkeypatch):
    urls = []
    pages = [
        {"traces": [{"traceId": "t1"}], "nextPageToken": "page-2"},
        {"traces": [{"traceId": "t2"}]},
    ]

    def fake(req, timeout=None):
        urls.append(req.full_url)
        return _Response(json.dumps(pages[len(urls) - 1]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)

    traces = list(
        client.iter_traces(
            start_time="2026-07-01T00:00:00Z",
            end_time="2026-07-08T00:00:00Z",
            trace_filter="span:generate_content",
        )
    )

    assert [t["traceId"] for t in traces] == ["t1", "t2"]
    assert "/v1/projects/example-project/traces?" in urls[0]
    assert "view=COMPLETE" in urls[0]
    assert "filter=span%3Agenerate_content" in urls[0]
    assert "startTime=2026-07-01T00%3A00%3A00Z" in urls[0]
    assert "pageToken=page-2" in urls[1]


def test_iter_traces_omits_an_empty_filter(client, monkeypatch):
    urls = []

    def fake(req, timeout=None):
        urls.append(req.full_url)
        return _Response(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake)

    list(client.iter_traces(start_time="a", end_time="b", trace_filter=""))
    assert "filter=" not in urls[0]
