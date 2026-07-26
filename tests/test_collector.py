"""Log-entry and trace-span parsing, including wrapper-span removal."""

import datetime as dt

import collector


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------


def test_parse_rfc3339_handles_a_plain_timestamp():
    parsed = collector.parse_rfc3339("2026-07-01T09:30:00Z")
    assert parsed == dt.datetime(2026, 7, 1, 9, 30, tzinfo=dt.timezone.utc)


def test_parse_rfc3339_truncates_nanosecond_precision():
    """Cloud Trace emits nine fractional digits; datetime accepts at most six."""
    parsed = collector.parse_rfc3339("2026-07-01T09:30:00.123456789Z")
    assert parsed is not None
    assert parsed.microsecond == 123456


def test_parse_rfc3339_returns_none_for_missing_or_invalid_input():
    assert collector.parse_rfc3339(None) is None
    assert collector.parse_rfc3339("") is None
    assert collector.parse_rfc3339("yesterday") is None


def test_sortable_normalises_to_utc_seconds():
    assert collector._sortable("2026-07-01T11:30:00+02:00") == "2026-07-01T09:30:00"
    assert collector._sortable(None) is None


def test_latency_ms():
    assert collector._latency_ms("2026-07-01T09:30:00Z", "2026-07-01T09:30:01Z") == 1000
    assert collector._latency_ms("2026-07-01T09:30:00Z", None) is None


# ---------------------------------------------------------------------------
# Cloud Logging
# ---------------------------------------------------------------------------


def test_build_log_filter_targets_streamassist_turns():
    f = collector.build_log_filter("example-project")
    assert 'logName="projects/example-project/logs/' in f
    assert "gemini_enterprise_user_activity" in f
    assert 'jsonPayload.logMetadata.methodName="StreamAssist"' in f
    assert "engines/" not in f


def test_build_log_filter_can_scope_to_one_engine():
    f = collector.build_log_filter("example-project", engine_id="my-app_123")
    assert 'jsonPayload.logMetadata.name:"engines/my-app_123"' in f


def _log_entry(**overrides):
    entry = {
        "trace": "projects/example-project/traces/0af7651916cd43dd8448eb211c80319c",
        "timestamp": "2026-07-01T09:30:00.123456789Z",
        "insertId": "insert-1",
        "resource": {"labels": {"location": "global"}},
        "jsonPayload": {
            "userIamPrincipal": "dana@example.com",
            "logMetadata": {
                "methodName": "StreamAssist",
                "name": "projects/1234/locations/global/collections/default_collection"
                "/engines/support-app_1700000000000/assistants/default_assistant",
            },
            "request": {
                "query": {
                    "parts": [
                        {"text": "What is the status of order 4471?"},
                        {"capability": {"name": "a2ui"}},
                    ]
                }
            },
            "response": {
                "agentInfo": {
                    "agent": "projects/1234/locations/global/collections/default_collection"
                    "/engines/support-app_1700000000000/assistants/default_assistant"
                    "/agents/8899001122",
                    "displayName": "Order Support",
                },
                "answer": {
                    "name": "projects/1234/locations/global/collections/default_collection"
                    "/engines/support-app_1700000000000/sessions/1755/answers/2001"
                },
            },
        },
    }
    entry.update(overrides)
    return entry


def test_parse_turn_extracts_identity_and_routing():
    row = collector.parse_turn(_log_entry())
    assert row is not None
    assert row["trace_id"] == "0af7651916cd43dd8448eb211c80319c"
    assert row["user_principal"] == "dana@example.com"
    assert row["query_text"] == "What is the status of order 4471?"
    assert row["agent_id"] == "8899001122"
    assert row["agent_display_name"] == "Order Support"
    assert row["engine_id"] == "support-app_1700000000000"
    assert row["session_id"] == "1755"
    assert row["location"] == "global"
    assert row["insert_id"] == "insert-1"
    assert row["ts"] == "2026-07-01T09:30:00"


def test_parse_turn_keeps_only_text_parts_of_the_query():
    """A turn carries capability parts alongside the prompt; only text is kept."""
    entry = _log_entry()
    entry["jsonPayload"]["request"]["query"]["parts"] = [
        {"text": "line one"},
        {"capability": {"name": "a2ui"}},
        {"text": "line two"},
    ]
    assert collector.parse_turn(entry)["query_text"] == "line one\nline two"


def test_parse_turn_without_text_parts_yields_no_query():
    entry = _log_entry()
    entry["jsonPayload"]["request"]["query"]["parts"] = [{"capability": {"name": "a2ui"}}]
    assert collector.parse_turn(entry)["query_text"] is None


def test_parse_turn_accepts_a_bare_trace_id():
    row = collector.parse_turn(_log_entry(trace="abc123"))
    assert row["trace_id"] == "abc123"


def test_parse_turn_skips_entries_with_no_trace():
    assert collector.parse_turn(_log_entry(trace="")) is None
    entry = _log_entry()
    del entry["trace"]
    assert collector.parse_turn(entry) is None


def test_parse_turn_tolerates_a_sparse_payload():
    row = collector.parse_turn({"trace": "abc", "jsonPayload": {}})
    assert row["trace_id"] == "abc"
    assert row["user_principal"] is None
    assert row["query_text"] is None
    assert row["agent_id"] is None


class _FakeClient:
    """Stands in for GcpClient; records the arguments it was called with."""

    project = "example-project"

    def __init__(self, entries=(), traces_by_filter=None):
        self._entries = list(entries)
        self._traces_by_filter = traces_by_filter or {}
        self.log_filters = []
        self.trace_filters = []

    def iter_log_entries(self, log_filter, page_size=1000):
        self.log_filters.append(log_filter)
        return iter(self._entries)

    def iter_traces(self, *, start_time, end_time, trace_filter="", page_size=100):
        self.trace_filters.append(trace_filter)
        return iter(self._traces_by_filter.get(trace_filter, []))


def test_collect_turns_windows_the_filter_and_drops_anonymous_entries():
    anonymous = _log_entry(trace="projects/p/traces/no-user")
    anonymous["jsonPayload"]["userIamPrincipal"] = None
    client = _FakeClient(entries=[_log_entry(), anonymous])

    rows = collector.collect_turns(
        client,
        start=dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc),
        end=dt.datetime(2026, 7, 8, tzinfo=dt.timezone.utc),
    )

    assert [r["user_principal"] for r in rows] == ["dana@example.com"]
    sent = client.log_filters[0]
    assert 'timestamp>="2026-07-01T00:00:00Z"' in sent
    assert 'timestamp<="2026-07-08T00:00:00Z"' in sent


def test_collect_turns_deduplicates_by_trace_id():
    client = _FakeClient(entries=[_log_entry(), _log_entry()])
    rows = collector.collect_turns(
        client,
        start=dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc),
        end=dt.datetime(2026, 7, 8, tzinfo=dt.timezone.utc),
    )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Cloud Trace
# ---------------------------------------------------------------------------


def _span(span_id, name, *, parent=None, tokens=None, labels=None, start=None, end=None):
    span = {
        "spanId": span_id,
        "name": name,
        "startTime": start or "2026-07-01T09:30:00Z",
        "endTime": end or "2026-07-01T09:30:02Z",
        "labels": dict(labels or {}),
    }
    if parent:
        span["parentSpanId"] = parent
    if tokens:
        span["labels"]["gen_ai.usage.input_tokens"] = str(tokens[0])
        span["labels"]["gen_ai.usage.output_tokens"] = str(tokens[1])
    return span


def test_parse_spans_reads_token_counts_and_metadata():
    trace = {
        "traceId": "trace-1",
        "spans": [
            _span(
                "1",
                "generate_content gemini-3-pro",
                tokens=(1200, 340),
                start="2026-07-01T09:30:00Z",
                end="2026-07-01T09:30:02Z",
                labels={
                    "gen_ai.request.model": "gemini-3-pro",
                    "cloud.platform": "gcp.gemini_enterprise",
                    "cloud.resource.id": "projects/1234/locations/global/collections/c"
                    "/engines/support-app_1700000000000/agents/8899001122",
                    "gen_ai.conversation.id": "projects/1234/locations/global/collections/c"
                    "/engines/support-app_1700000000000/sessions/1755",
                },
            )
        ],
    }
    models, tools = collector.parse_spans(trace)

    assert tools == []
    (call,) = models
    assert call["span_id"] == "trace-1:1"
    assert call["trace_id"] == "trace-1"
    assert call["span_name"] == "generate_content"
    assert call["model"] == "gemini-3-pro"
    assert call["input_tokens"] == 1200
    assert call["output_tokens"] == 340
    assert call["platform"] == "gcp.gemini_enterprise"
    assert call["engine_id"] == "support-app_1700000000000"
    assert call["session_id"] == "1755"
    assert call["agent_name"] == "8899001122"
    assert call["latency_ms"] == 2000


def test_parse_spans_accepts_the_agent_engine_spelling_of_resource_id():
    """Agent Engine labels it cloud.resource_id; Gemini Enterprise uses cloud.resource.id."""
    trace = {
        "traceId": "trace-1",
        "spans": [
            _span(
                "1",
                "call_llm",
                tokens=(10, 5),
                labels={"cloud.resource_id": "projects/1234/locations/us-central1/agents/77"},
            )
        ],
    }
    (call,), _ = collector.parse_spans(trace)
    assert call["resource_id"].endswith("/agents/77")
    assert call["agent_name"] == "77"


def test_parse_spans_collects_tool_invocations():
    trace = {
        "traceId": "trace-1",
        "spans": [
            _span(
                "2",
                "execute_tool lookup_order",
                start="2026-07-01T09:30:00Z",
                end="2026-07-01T09:30:00.500000Z",
                labels={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": "lookup_order",
                    "gen_ai.tool.type": "function",
                },
            )
        ],
    }
    models, tools = collector.parse_spans(trace)
    assert models == []
    (tool,) = tools
    assert tool["span_id"] == "trace-1:2"
    assert tool["tool_name"] == "lookup_order"
    assert tool["tool_type"] == "function"
    assert tool["latency_ms"] == 500


def test_wrapper_span_is_discarded_to_avoid_double_counting():
    """An ADK agent wraps each Gemini call in call_llm; both report the same tokens."""
    trace = {
        "traceId": "trace-1",
        "spans": [
            _span("1", "call_llm", tokens=(900, 120)),
            _span("2", "generate_content gemini-2.5-flash", parent="1", tokens=(900, 120)),
        ],
    }
    models, _ = collector.parse_spans(trace)

    assert [m["span_id"] for m in models] == ["trace-1:2"]
    assert sum(m["input_tokens"] for m in models) == 900


def test_nested_agent_as_tool_calls_are_both_kept():
    """An outer call whose inner call has *different* counts is not a wrapper.

    Discarding every token-bearing ancestor would silently drop the outer call
    here, even though both sets of tokens were genuinely consumed.
    """
    trace = {
        "traceId": "trace-1",
        "spans": [
            _span("1", "generate_content gemini-3-pro", tokens=(500, 100)),
            _span(
                "2",
                "execute_tool sub_agent",
                parent="1",
                labels={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "sub_agent"},
            ),
            _span("3", "generate_content gemini-2.5-flash", parent="2", tokens=(200, 40)),
        ],
    }
    models, tools = collector.parse_spans(trace)

    assert sorted(m["span_id"] for m in models) == ["trace-1:1", "trace-1:3"]
    assert sum(m["input_tokens"] + m["output_tokens"] for m in models) == 840
    assert [t["tool_name"] for t in tools] == ["sub_agent"]


def test_a_grandchild_with_matching_counts_is_not_treated_as_a_wrapper():
    """Only a *direct* child with equal counts marks a span as a wrapper."""
    trace = {
        "traceId": "trace-1",
        "spans": [
            _span("1", "generate_content", tokens=(300, 60)),
            _span("2", "execute_tool t", parent="1", labels={"gen_ai.tool.name": "t"}),
            _span("3", "generate_content", parent="2", tokens=(300, 60)),
        ],
    }
    models, _ = collector.parse_spans(trace)
    assert sorted(m["span_id"] for m in models) == ["trace-1:1", "trace-1:3"]


def test_spans_without_usage_or_tool_labels_are_ignored():
    trace = {"traceId": "trace-1", "spans": [_span("1", "StreamAssist")]}
    assert collector.parse_spans(trace) == ([], [])


def test_parse_spans_tolerates_a_trace_with_no_spans():
    assert collector.parse_spans({"traceId": "trace-1"}) == ([], [])


def test_collect_spans_deduplicates_across_filters():
    """The two default filters can match the same trace; span ids collapse it."""
    trace = {
        "traceId": "trace-1",
        "spans": [
            _span("1", "generate_content", tokens=(100, 10)),
            _span("2", "execute_tool t", labels={"gen_ai.tool.name": "t"}),
        ],
    }
    client = _FakeClient(
        traces_by_filter={"span:generate_content": [trace], "span:call_llm": [trace]}
    )

    models, tools = collector.collect_spans(
        client,
        start=dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc),
        end=dt.datetime(2026, 7, 8, tzinfo=dt.timezone.utc),
        trace_filters=["span:generate_content", "span:call_llm"],
    )

    assert len(models) == 1
    assert len(tools) == 1
    assert client.trace_filters == ["span:generate_content", "span:call_llm"]


def test_summarise_totals_tokens():
    assert collector.summarise(
        [
            {"input_tokens": 100, "output_tokens": 20},
            {"input_tokens": 5, "output_tokens": None},
            {},
        ]
    ) == (105, 20)


def test_summarise_of_nothing_is_zero():
    assert collector.summarise([]) == (0, 0)
