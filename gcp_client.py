"""REST clients for Cloud Logging and Cloud Trace.

Implemented against the Python standard library only (urllib, json, subprocess)
so the tool runs on any machine with the gcloud CLI installed and requires no
package installation.

Authentication uses an OAuth access token from `gcloud auth print-access-token`,
or the GOOGLE_OAUTH_ACCESS_TOKEN environment variable when a token is supplied
externally. Tokens expire after roughly one hour; the client refreshes once on a
401 response and retries.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

LOGGING_ENDPOINT = "https://logging.googleapis.com/v2/entries:list"
TRACE_ENDPOINT = "https://cloudtrace.googleapis.com/v1/projects/{project}/traces"

# Retried with exponential backoff. 401 is handled separately (token refresh).
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 5


class GcpError(RuntimeError):
    """A non-retryable error from a Google Cloud API."""


def _gcloud_token() -> str:
    """Mint an access token via the gcloud CLI."""
    try:
        out = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise GcpError(
            "gcloud not found on PATH. Install the Google Cloud CLI, or set "
            "GOOGLE_OAUTH_ACCESS_TOKEN to a valid OAuth token."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise GcpError(
            f"`gcloud auth print-access-token` failed: {exc.stderr.strip()}\n"
            "Run `gcloud auth login` first."
        ) from exc
    return out.stdout.strip()


class GcpClient:
    """Minimal authenticated JSON client for the two APIs we need."""

    def __init__(self, project: str, quota_project: str | None = None) -> None:
        self.project = project
        self.quota_project = quota_project or project
        self._token: str | None = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN") or None

    # -- plumbing ---------------------------------------------------------

    @property
    def token(self) -> str:
        if not self._token:
            self._token = _gcloud_token()
        return self._token

    def _refresh(self) -> None:
        self._token = _gcloud_token()

    def _request(
        self, url: str, *, method: str = "GET", body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = json.dumps(body).encode() if body is not None else None
        attempt = 0
        refreshed = False

        while True:
            req = urllib.request.Request(url, data=payload, method=method)
            req.add_header("Authorization", f"Bearer {self.token}")
            req.add_header("X-Goog-User-Project", self.quota_project)
            if payload is not None:
                req.add_header("Content-Type", "application/json")

            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    return json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")

                # An expired token surfaces as a 401; retry once with a fresh one.
                if exc.code == 401 and not refreshed:
                    refreshed = True
                    self._refresh()
                    continue

                if exc.code in _RETRY_STATUS and attempt < _MAX_RETRIES:
                    time.sleep(2**attempt)
                    attempt += 1
                    continue

                raise GcpError(f"HTTP {exc.code} from {url}\n{detail[:2000]}") from exc
            except urllib.error.URLError as exc:
                if attempt < _MAX_RETRIES:
                    time.sleep(2**attempt)
                    attempt += 1
                    continue
                raise GcpError(f"Network error calling {url}: {exc.reason}") from exc

    # -- Cloud Logging ----------------------------------------------------

    def iter_log_entries(
        self, log_filter: str, *, page_size: int = 1000
    ) -> Iterator[dict[str, Any]]:
        """Yield log entries matching `log_filter`, following pagination."""
        page_token: str | None = None
        while True:
            body: dict[str, Any] = {
                "resourceNames": [f"projects/{self.project}"],
                "filter": log_filter,
                "orderBy": "timestamp desc",
                "pageSize": page_size,
            }
            if page_token:
                body["pageToken"] = page_token

            data = self._request(LOGGING_ENDPOINT, method="POST", body=body)
            yield from data.get("entries", [])

            page_token = data.get("nextPageToken")
            if not page_token:
                return

    # -- Cloud Trace ------------------------------------------------------

    def iter_traces(
        self,
        *,
        start_time: str,
        end_time: str,
        trace_filter: str = "span:generate_content",
        page_size: int = 100,
    ) -> Iterator[dict[str, Any]]:
        """Yield fully-populated traces (view=COMPLETE) in the time window.

        view=COMPLETE returns every span of a matching trace together with its
        labels, so a single list call per page yields token counts, tool calls
        and latency without an additional traces.get request per trace.
        """
        base = TRACE_ENDPOINT.format(project=self.project)
        page_token: str | None = None

        while True:
            params = {
                "view": "COMPLETE",
                "pageSize": str(page_size),
                "startTime": start_time,
                "endTime": end_time,
            }
            if trace_filter:
                params["filter"] = trace_filter
            if page_token:
                params["pageToken"] = page_token

            data = self._request(f"{base}?{urllib.parse.urlencode(params)}")
            yield from data.get("traces", [])

            page_token = data.get("nextPageToken")
            if not page_token:
                return
