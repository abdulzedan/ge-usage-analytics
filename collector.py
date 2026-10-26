"""Collect Gemini Enterprise usage telemetry from Cloud Logging and Cloud Trace.

Cloud Logging supplies the account (`jsonPayload.userIamPrincipal`), prompt,
agent, and session from `gemini_enterprise_user_activity`. Cloud Trace supplies
token counts (`gen_ai.usage.*`) and model names (`gen_ai.request.model`).

This module parses both sources. The `usage` view in store.py joins them by
trace ID, then by session ID from `gen_ai.conversation.id` when available.

NotebookLM Enterprise and Antigravity write separate logs that need no join.
Antigravity entries include the account, model, and token counts.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from typing import Any

from gcp_client import GcpClient

USER_ACTIVITY_LOG = (
    "discoveryengine.googleapis.com%2Fgemini_enterprise_user_activity"
)

# NotebookLM activity logging must be enabled per project. Entries include
# accounts, actions, and chat prompts; this collector reads no NotebookLM tokens.
NOTEBOOKLM_ACTIVITY_LOG = (
    "discoveryengine.googleapis.com%2Fnotebooklm_enterprise_user_activity"
)

# Antigravity on a Gemini Enterprise license is served by the Business AI Code
# API. Metadata logging, off by default, writes one InferenceResponseLog entry
# per request with the user, model, and token counts. Prompts-and-responses
# logging adds InferenceRequestLog entries with the prompt; the payload-type
# filter excludes them.
ANTIGRAVITY_RESOURCE_TYPE = "businessaicode.googleapis.com/BAICInstance"
ANTIGRAVITY_RESPONSE_LOG_TYPE = (
    "type.googleapis.com/google.cloud.businessaicode.logging.v1.InferenceResponseLog"
)

_ENGINE_RE = re.compile(r"engines/([^/]+)")
_SESSION_RE = re.compile(r"sessions/([^/]+)")
_AGENT_RE = re.compile(r"agents/([^/]+)")
_NOTEBOOK_RE = re.compile(r"notebooks/([^/]+)")
_MEMBER_TYPE_RE = re.compile(r"^(?:user|serviceAccount):")


# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------


def parse_rfc3339(value: str | None) -> dt.datetime | None:
    """Parse RFC3339, tolerating the 9-digit nanosecond precision GCP emits."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    # Python only accepts up to microseconds; truncate any extra digits.
    match = re.match(r"^(.*\.\d{6})\d*(\+\d{2}:\d{2})$", text)
    if match:
        text = match.group(1) + match.group(2)
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _sortable(value: str | None) -> str | None:
    """Normalise a timestamp to 'YYYY-MM-DDTHH:MM:SS' UTC for lexicographic sorting."""
    parsed = parse_rfc3339(value)
    if not parsed:
        return None
    return parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _latency_ms(start: str | None, end: str | None) -> int | None:
    a, b = parse_rfc3339(start), parse_rfc3339(end)
    if not a or not b:
        return None
    return int((b - a).total_seconds() * 1000)


def _first(pattern: re.Pattern[str], *candidates: str | None) -> str | None:
    for candidate in candidates:
        if not candidate:
            continue
        match = pattern.search(candidate)
        if match:
            return match.group(1)
    return None


# --------------------------------------------------------------------------
# Cloud Logging -> turns
# --------------------------------------------------------------------------


def build_log_filter(project: str, engine_id: str | None = None) -> str:
    """Filter for Gemini Enterprise chat turns (StreamAssist calls)."""
    parts = [
        f'logName="projects/{project}/logs/{USER_ACTIVITY_LOG}"',
        'jsonPayload.logMetadata.methodName="StreamAssist"',
    ]
    if engine_id:
        parts.append(f'jsonPayload.logMetadata.name:"engines/{engine_id}"')
    return " AND ".join(parts)


def parse_turn(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Map one user-activity log entry to a `turns` row."""
    trace = entry.get("trace") or ""
    trace_id = trace.rsplit("/", 1)[-1]  # bare hex, or projects/*/traces/<hex>
    if not trace_id:
        return None

    payload = entry.get("jsonPayload") or {}
    log_meta = payload.get("logMetadata") or {}
    request = payload.get("request") or {}
    response = payload.get("response") or {}
    agent_info = response.get("agentInfo") or {}
    answer = response.get("answer") or {}

    # The query carries an a2ui capability part alongside the prompt; keep text only.
    parts = ((request.get("query") or {}).get("parts")) or []
    query_text = "\n".join(p["text"] for p in parts if p.get("text")) or None

    return {
        "trace_id": trace_id,
        "ts": _sortable(entry.get("timestamp") or log_meta.get("timestamp")),
        "user_principal": payload.get("userIamPrincipal"),
        "query_text": query_text,
        "agent_id": _first(_AGENT_RE, agent_info.get("agent")),
        "agent_display_name": agent_info.get("displayName"),
        "engine_id": _first(
            _ENGINE_RE, log_meta.get("name"), agent_info.get("agent"), answer.get("name")
        ),
        "session_id": _first(_SESSION_RE, answer.get("name")),
        "location": ((entry.get("resource") or {}).get("labels") or {}).get("location"),
        "insert_id": entry.get("insertId"),
    }


def collect_turns(
    client: GcpClient, *, start: dt.datetime, end: dt.datetime, engine_id: str | None = None
) -> list[dict[str, Any]]:
    log_filter = build_log_filter(client.project, engine_id)
    log_filter += (
        f' AND timestamp>="{start.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
        f' AND timestamp<="{end.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
    )

    rows: dict[str, dict[str, Any]] = {}
    for entry in client.iter_log_entries(log_filter):
        row = parse_turn(entry)
        if row and row.get("user_principal"):
            rows[row["trace_id"]] = row
    return list(rows.values())


# --------------------------------------------------------------------------
# Cloud Logging -> notebooklm_activity
# --------------------------------------------------------------------------


def build_notebooklm_log_filter(project: str) -> str:
    """Filter for NotebookLM Enterprise user-activity entries.

    No method filter: unlike chat turns, every recorded action is of interest
    (queries, source uploads, sharing, audio overviews), and the method name is
    stored as the row's `action`.
    """
    return f'logName="projects/{project}/logs/{NOTEBOOKLM_ACTIVITY_LOG}"'


def parse_notebooklm_activity(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Map one NotebookLM user-activity log entry to a `notebooklm_activity` row.

    The log shares its envelope with the Gemini Enterprise one (same service
    prefix, same `userIamPrincipal`), but there is no trace to join on and no
    token fields to read: activity is all it records.
    """
    insert_id = entry.get("insertId")
    if not insert_id:
        return None

    payload = entry.get("jsonPayload") or {}
    log_meta = payload.get("logMetadata") or {}
    request = payload.get("request") or {}

    # Chat-style actions carry the question (`user_query` on
    # GenerateFreeFormStreamed, `free_form_action` on InteractSources);
    # management actions carry none. Both key spellings are accepted because
    # Cloud Logging renders proto fields in camelCase.
    query_text = None
    for key in ("userQuery", "user_query", "freeFormAction", "free_form_action"):
        value = request.get(key)
        if isinstance(value, str) and value:
            query_text = value
            break

    return {
        "insert_id": insert_id,
        "ts": _sortable(entry.get("timestamp") or log_meta.get("timestamp")),
        "user_principal": payload.get("userIamPrincipal"),
        "action": log_meta.get("methodName"),
        "notebook_id": _first(
            _NOTEBOOK_RE, log_meta.get("name"), request.get("name"), request.get("parent")
        ),
        "query_text": query_text,
        "location": ((entry.get("resource") or {}).get("labels") or {}).get("location"),
    }


def collect_notebooklm_activity(
    client: GcpClient, *, start: dt.datetime, end: dt.datetime
) -> list[dict[str, Any]]:
    """Collect NotebookLM Enterprise activity, keyed by insert id.

    When the project has not enabled NotebookLM user-activity logging (it is
    off by default) the filter simply matches nothing; that is not an error.
    """
    log_filter = build_notebooklm_log_filter(client.project)
    log_filter += (
        f' AND timestamp>="{start.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
        f' AND timestamp<="{end.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
    )

    rows: dict[str, dict[str, Any]] = {}
    for entry in client.iter_log_entries(log_filter):
        row = parse_notebooklm_activity(entry)
        if row and row.get("user_principal"):
            rows[row["insert_id"]] = row
    return list(rows.values())


# --------------------------------------------------------------------------
# Cloud Logging -> antigravity_requests
# --------------------------------------------------------------------------


def build_antigravity_log_filter() -> str:
    """Filter for Antigravity's per-request metadata entries.

    Matching the response payload type, rather than the resource alone, keeps
    the prompt-bearing request entries out of the download entirely.
    """
    return (
        f'resource.type="{ANTIGRAVITY_RESOURCE_TYPE}"'
        f' AND jsonPayload.@type="{ANTIGRAVITY_RESPONSE_LOG_TYPE}"'
    )


def _bare_principal(member: str | None) -> str | None:
    """`user:dana@example.com` -> `dana@example.com`.

    Antigravity labels the caller in IAM member form; the Gemini Enterprise and
    NotebookLM logs record the bare account. Dropping the type prefix makes the
    same person the same key in every table.
    """
    if not member:
        return None
    return _MEMBER_TYPE_RE.sub("", member)


def _count(fields: dict[str, Any], key: str) -> int | None:
    """An int64 count, which arrives as a JSON string; None when absent."""
    try:
        return int(fields[key])
    except (KeyError, TypeError, ValueError):
        return None


def parse_antigravity_request(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Map one InferenceResponseLog entry to an `antigravity_requests` row.

    Only metadata is read. With prompts-and-responses logging on, the same
    entry also carries the model's output; none of it is kept.

    The token fields follow the Gemini API's usage metadata: the total is the
    prompt, the response candidates and the thinking tokens added together,
    and a model that does not think omits `thoughtsTokenCount`. The cached
    count is the part of the prompt served from cache, already inside the
    prompt count, and is omitted when nothing was cached.
    """
    insert_id = entry.get("insertId")
    if not insert_id:
        return None

    payload = entry.get("jsonPayload") or {}
    labels = entry.get("labels") or {}
    metadata = payload.get("metadata") or {}

    return {
        "insert_id": insert_id,
        "ts": _sortable(entry.get("timestamp")),
        "user_principal": _bare_principal(labels.get("user_id")),
        "client_name": labels.get("client_name"),
        "client_version": labels.get("client_version"),
        "model": labels.get("model"),
        # Only newer clients label the provider.
        "model_provider": labels.get("model_provider"),
        "experience": payload.get("experience"),
        "input_tokens": _count(metadata, "promptTokenCount"),
        "cached_tokens": _count(metadata, "cachedContentTokenCount"),
        "output_tokens": _count(metadata, "candidatesTokenCount"),
        "thinking_tokens": _count(metadata, "thoughtsTokenCount"),
        "total_tokens": _count(metadata, "totalTokenCount"),
        "quota_type": metadata.get("quotaType"),
        # Background requests (Flash Lite, as observed) belong to no
        # conversation and carry no trajectory id.
        "request_id": labels.get("request_id"),
        "trajectory_id": labels.get("trajectory_id") or None,
        "location": ((entry.get("resource") or {}).get("labels") or {}).get("location"),
    }


def collect_antigravity_requests(
    client: GcpClient, *, start: dt.datetime, end: dt.datetime
) -> list[dict[str, Any]]:
    """Collect Antigravity requests, keyed by insert id.

    Unlike the activity logs, an entry is kept even without a user: each row
    carries tokens, and dropping it would understate consumption. A project
    that never enabled Metadata logging matches nothing; that is not an error.
    """
    log_filter = build_antigravity_log_filter()
    log_filter += (
        f' AND timestamp>="{start.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
        f' AND timestamp<="{end.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
    )

    rows: dict[str, dict[str, Any]] = {}
    for entry in client.iter_log_entries(log_filter):
        row = parse_antigravity_request(entry)
        if row:
            rows[row["insert_id"]] = row
    return list(rows.values())


# --------------------------------------------------------------------------
# Cloud Trace -> model_calls / tool_calls
# --------------------------------------------------------------------------


def _span_tokens(span: dict[str, Any]) -> tuple[int, int] | None:
    """(input, output) tokens for a span, or None if it reports no usage."""
    labels = span.get("labels") or {}
    if "gen_ai.usage.input_tokens" not in labels:
        return None
    return (
        int(labels.get("gen_ai.usage.input_tokens") or 0),
        int(labels.get("gen_ai.usage.output_tokens") or 0),
    )


def _wrapper_span_ids(spans: list[dict[str, Any]]) -> set[str]:
    """Find spans that repeat a direct child's token counts.

    An ADK agent on Agent Engine wraps every Gemini call in a `call_llm` span and
    emits `generate_content` beneath it. Both carry identical `gen_ai.usage.*`
    labels, so summing every token-bearing span double counts those calls.

    Only remove a span when a direct child reports the same counts. An outer
    `generate_content` can contain a separate model call beneath `execute_tool`;
    both calls consume tokens and must remain.
    """
    children: dict[str, list[dict[str, Any]]] = {}
    for span in spans:
        parent = span.get("parentSpanId")
        if parent:
            children.setdefault(parent, []).append(span)

    wrappers: set[str] = set()
    for span in spans:
        tokens = _span_tokens(span)
        if tokens is None:
            continue
        if any(_span_tokens(kid) == tokens for kid in children.get(span.get("spanId"), [])):
            wrappers.add(span["spanId"])
    return wrappers


def parse_spans(trace: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split one COMPLETE trace into model-call rows and tool-call rows."""
    trace_id = trace.get("traceId")
    spans = trace.get("spans") or []
    wrappers = _wrapper_span_ids(spans)
    model_calls: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []

    for span in spans:
        labels = span.get("labels") or {}
        start, end = span.get("startTime"), span.get("endTime")
        operation = labels.get("gen_ai.operation.name")

        if "gen_ai.usage.input_tokens" in labels:
            if span.get("spanId") in wrappers:
                continue  # a direct child reports the same tokens; don't double count
            conversation = labels.get("gen_ai.conversation.id")
            # Agent Engine spells it cloud.resource_id; Gemini Enterprise uses cloud.resource.id.
            resource = labels.get("cloud.resource.id") or labels.get("cloud.resource_id")
            model_calls.append(
                {
                    "span_id": f"{trace_id}:{span.get('spanId')}",
                    "trace_id": trace_id,
                    "start_time": _sortable(start),
                    "end_time": _sortable(end),
                    "span_name": (span.get("name") or "").split(" ")[0],
                    "platform": labels.get("cloud.platform"),
                    "resource_id": resource,
                    "model": labels.get("gen_ai.request.model"),
                    "input_tokens": int(labels.get("gen_ai.usage.input_tokens") or 0),
                    "output_tokens": int(labels.get("gen_ai.usage.output_tokens") or 0),
                    "agent_name": labels.get("gen_ai.agent.name")
                    or _first(_AGENT_RE, resource),
                    "session_id": _first(_SESSION_RE, conversation),
                    "engine_id": _first(_ENGINE_RE, conversation, resource),
                    "latency_ms": _latency_ms(start, end),
                }
            )
        elif operation == "execute_tool" or "gen_ai.tool.name" in labels:
            tool_calls.append(
                {
                    "span_id": f"{trace_id}:{span.get('spanId')}",
                    "trace_id": trace_id,
                    "start_time": _sortable(start),
                    "end_time": _sortable(end),
                    "tool_name": labels.get("gen_ai.tool.name"),
                    "tool_type": labels.get("gen_ai.tool.type"),
                    "latency_ms": _latency_ms(start, end),
                }
            )

    return model_calls, tool_calls


def collect_spans(
    client: GcpClient,
    *,
    start: dt.datetime,
    end: dt.datetime,
    trace_filters: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect token and tool spans across several Cloud Trace filters.

    Two filters are required because the surfaces name their spans differently:
    Gemini Enterprise emits `generate_content`, while an ADK agent on Agent Engine
    emits `call_llm`. Span ids are globally unique, so a trace matched by both
    filters de-duplicates on the way into the result dictionaries.
    """
    models: dict[str, dict[str, Any]] = {}
    tools: dict[str, dict[str, Any]] = {}

    for trace_filter in trace_filters:
        traces = client.iter_traces(
            start_time=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            end_time=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            trace_filter=trace_filter,
        )
        for trace in traces:
            m, t = parse_spans(trace)
            models.update({r["span_id"]: r for r in m})
            tools.update({r["span_id"]: r for r in t})

    return list(models.values()), list(tools.values())


def summarise(rows: Iterable[dict[str, Any]]) -> tuple[int, int]:
    rows = list(rows)
    return (
        sum(r.get("input_tokens") or 0 for r in rows),
        sum(r.get("output_tokens") or 0 for r in rows),
    )
