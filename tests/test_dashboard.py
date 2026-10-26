"""Offline HTML rendering."""

import json
import re

import pytest

import store
from dashboard import _short_model, render_dashboard


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(str(tmp_path / "t.db"))
    store.upsert_turns(
        connection,
        [
            {
                "trace_id": "trace-a",
                "ts": "2026-07-01T09:00:00",
                "user_principal": "dana@example.com",
                "query_text": "  What is the status of order 4471?\nAny update?  ",
                "agent_display_name": "Order Support",
            }
        ],
    )
    store.upsert_model_calls(
        connection,
        [
            {
                "span_id": "trace-a:1",
                "trace_id": "trace-a",
                "start_time": "2026-07-01T09:00:00",
                "platform": "gcp.gemini_enterprise",
                "model": "projects/p/locations/global/publishers/google/models/gemini-3-pro",
                "input_tokens": 1200,
                "output_tokens": 340,
            }
        ],
    )
    yield connection
    connection.close()


def _payload(html):
    match = re.search(r"const DATA = (\{.*?\});\n", html, re.S)
    assert match, "embedded DATA blob not found"
    return json.loads(match.group(1))


def test_short_model_strips_the_publisher_path():
    assert (
        _short_model("projects/p/locations/global/publishers/google/models/gemini-3-pro")
        == "gemini-3-pro"
    )
    assert _short_model("gemini-2.5-flash") == "gemini-2.5-flash"
    assert _short_model(None) == "(unknown)"
    assert _short_model("") == "(unknown)"


def test_every_placeholder_is_substituted(conn):
    html = render_dashboard(conn, project="example-project")
    assert not re.search(r"__[A-Z]+__", html)


def test_output_has_no_external_references(conn):
    """The page must open on a workstation with no network access."""
    html = render_dashboard(conn, project="example-project")

    assert "<link" not in html
    assert "<script src" not in html
    assert "@import" not in html
    # The only absolute URL is the SVG XML namespace, which is never fetched.
    urls = set(re.findall(r"https?://[^\"'\s)]+", html))
    assert urls <= {"http://www.w3.org/2000/svg"}


def test_rows_are_embedded_with_the_joined_user_and_model(conn):
    html = render_dashboard(conn, project="example-project")
    (row,) = _payload(html)["rows"]

    assert row["u"] == "dana@example.com"
    assert row["m"] == "gemini-3-pro"  # shortened for display
    assert row["a"] == "Order Support"
    assert row["i"] == 1200
    assert row["o"] == 340
    assert row["f"] == "Gemini Enterprise"
    assert row["at"] == 1
    assert row["v"] == "t"


def test_a_session_attributed_call_is_marked_as_such(conn):
    store.upsert_turns(
        conn,
        [
            {
                "trace_id": "trace-a",
                "ts": "2026-07-01T08:59:00",
                "user_principal": "dana@example.com",
                "session_id": "s-1",
            }
        ],
    )
    store.upsert_model_calls(
        conn,
        [
            {
                "span_id": "sub:1",
                "trace_id": "sub",  # no matching turn; session carries the link
                "start_time": "2026-07-01T09:02:00",
                "session_id": "s-1",
                "input_tokens": 10,
                "output_tokens": 2,
            }
        ],
    )
    rows = {r["r"]: r for r in _payload(render_dashboard(conn, project="p"))["rows"]}

    assert rows["sub"]["u"] == "dana@example.com"
    assert rows["sub"]["at"] == 1
    assert rows["sub"]["v"] == "s"


def test_the_agent_dimension_folds_the_deep_research_family(conn):
    store.upsert_model_calls(
        conn,
        [
            {
                "span_id": "dr:1",
                "trace_id": "dr-detached",
                "start_time": "2026-07-01T09:05:00",
                "agent_name": "deep_research_child_3",
                "input_tokens": 10,
                "output_tokens": 2,
            }
        ],
    )
    rows = {r["r"]: r for r in _payload(render_dashboard(conn, project="p"))["rows"]}
    assert rows["dr-detached"]["a"] == "Deep Research"


def test_notebooklm_rollup_is_embedded_and_counted(conn):
    store.upsert_notebooklm_activity(
        conn,
        [
            {"insert_id": "n1", "ts": "2026-07-01T09:00:00",
             "user_principal": "dana@example.com",
             "action": "GenerateFreeFormStreamed",
             "notebook_id": "nb-1", "query_text": "secret prompt"},
        ],
    )
    html = render_dashboard(conn, project="example-project")
    payload = _payload(html)

    (entry,) = payload["notebooklm"]
    assert entry["u"] == "dana@example.com"
    assert entry["n"] == 1
    assert "1 NotebookLM activities" in html

    (recent,) = payload["notebooklmRecent"]
    assert recent["ac"] == "GenerateFreeFormStreamed"
    assert recent["nb"] == "nb-1"
    assert recent["q"] == "secret prompt"


def test_redaction_covers_notebooklm_prompts_too(conn):
    store.upsert_notebooklm_activity(
        conn,
        [
            {"insert_id": "n1", "ts": "2026-07-01T09:00:00",
             "user_principal": "dana@example.com",
             "action": "GenerateFreeFormStreamed",
             "notebook_id": "nb-1", "query_text": "secret prompt"},
        ],
    )
    html = render_dashboard(conn, project="example-project", redact_queries=True)

    assert "secret prompt" not in html
    (recent,) = _payload(html)["notebooklmRecent"]
    assert recent["q"] is None
    assert recent["ac"] == "GenerateFreeFormStreamed"  # the action itself stays


def test_without_notebooklm_data_the_payload_and_subtitle_stay_quiet(conn):
    html = render_dashboard(conn, project="example-project")

    assert _payload(html)["notebooklm"] == []
    assert _payload(html)["notebooklmRecent"] == []
    assert "NotebookLM activities" not in html


def test_antigravity_rollups_are_embedded_and_counted(conn):
    store.upsert_antigravity_requests(
        conn,
        [
            {"insert_id": "a1", "ts": "2026-07-01T09:00:00",
             "user_principal": "sam@example.com", "client_name": "antigravity_cli",
             "client_version": "1.2.14", "model": "gemini-3.5-flash-lite",
             "model_provider": None,
             "input_tokens": 91, "cached_tokens": None, "output_tokens": 3,
             "thinking_tokens": None, "total_tokens": 94, "trajectory_id": None},
            {"insert_id": "a2", "ts": "2026-07-01T09:00:40",
             "user_principal": "sam@example.com", "client_name": "antigravity_cli",
             "client_version": "1.2.14", "model": "gemini-3.1-pro-preview",
             "model_provider": None,
             "input_tokens": 12534, "cached_tokens": 7388, "output_tokens": 19,
             "thinking_tokens": 274, "total_tokens": 12827, "trajectory_id": "traj-1"},
            # After an upgrade, the same model arrives with a provider label.
            {"insert_id": "a3", "ts": "2026-07-01T09:01:10",
             "user_principal": "sam@example.com", "client_name": "antigravity_cli",
             "client_version": "1.4.5", "model": "gemini-3.1-pro-preview",
             "model_provider": "Google",
             "input_tokens": 1000, "cached_tokens": 600, "output_tokens": 10,
             "thinking_tokens": 90, "total_tokens": 1100, "trajectory_id": "traj-1"},
        ],
    )
    html = render_dashboard(conn, project="example-project")
    payload = _payload(html)

    (user,) = payload["antigravity"]
    assert (user["u"], user["n"], user["c"]) == ("sam@example.com", 3, 1)
    assert (user["i"], user["ca"], user["o"], user["th"], user["k"]) == (
        13625, 7988, 32, 364, 14021,
    )
    assert payload["antigravityTotals"] == {
        "requests": 3, "users": 1, "conversations": 1,
        "input": 13625, "cached": 7988, "output": 32, "thinking": 364, "tokens": 14021,
    }
    assert payload["antigravityDaily"] == [
        {"d": "2026-07-01", "n": 3, "u": 1, "i": 13625, "ca": 7988,
         "o": 32, "th": 364, "k": 14021},
    ]
    # One row per model, whichever client versions reported it.
    assert [(m["m"], m["p"]) for m in payload["antigravityModels"]] == [
        ("gemini-3.1-pro-preview", "Google"), ("gemini-3.5-flash-lite", None),
    ]
    assert [(c["c"], c["v"], c["n"]) for c in payload["antigravityClients"]] == [
        ("antigravity_cli", "1.2.14", 2), ("antigravity_cli", "1.4.5", 1),
    ]
    assert "3 Antigravity requests" in html

    # The Gemini Enterprise rows, and therefore every filtered chart, are untouched.
    assert len(payload["rows"]) == 1
    assert sum(r["i"] + r["o"] for r in payload["rows"]) == 1540


def test_without_antigravity_data_the_payload_and_subtitle_stay_quiet(conn):
    html = render_dashboard(conn, project="example-project")
    payload = _payload(html)

    assert payload["antigravity"] == []
    assert payload["antigravityDaily"] == []
    assert payload["antigravityModels"] == []
    assert payload["antigravityClients"] == []
    assert payload["antigravityTotals"]["requests"] == 0
    assert "Antigravity requests" not in html


def test_prompt_text_is_normalised_to_a_single_line(conn):
    queries = _payload(render_dashboard(conn, project="example-project"))["queries"]
    assert queries["trace-a"] == "What is the status of order 4471? Any update?"


def test_redaction_removes_prompt_text_but_keeps_the_metrics(conn):
    html = render_dashboard(conn, project="example-project", redact_queries=True)
    payload = _payload(html)

    assert payload["queries"] == {}
    assert "order 4471" not in html
    assert len(payload["rows"]) == 1


def test_long_prompts_are_truncated(conn):
    store.upsert_turns(
        conn,
        [{"trace_id": "trace-a", "query_text": "x" * 5000, "user_principal": "dana@example.com"}],
    )
    queries = _payload(render_dashboard(conn, project="example-project"))["queries"]
    assert len(queries["trace-a"]) == 300


def test_subtitle_reports_the_project_and_volume(conn):
    html = render_dashboard(conn, project="example-project", generated_at="2026-07-01 12:00 UTC")
    assert "Project example-project" in html
    assert "1 model calls" in html
    assert "1 logged turns" in html
    assert "generated 2026-07-01 12:00 UTC" in html
    assert "Cloud Logging + Cloud Trace" in html


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_theme_is_applied_to_the_root_element(conn, theme):
    html = render_dashboard(conn, project="example-project", theme=theme)
    assert f'data-theme="{theme}"' in html


def test_an_empty_database_still_renders(tmp_path):
    conn = store.connect(str(tmp_path / "empty.db"))
    html = render_dashboard(conn, project="example-project")

    assert _payload(html)["rows"] == []
    assert "no data yet" in html
    assert not re.search(r"__[A-Z]+__", html)
    conn.close()


def test_a_call_with_no_matching_turn_renders_as_unattributed(tmp_path):
    conn = store.connect(str(tmp_path / "t.db"))
    store.upsert_model_calls(
        conn,
        [
            {
                "span_id": "s1",
                "trace_id": "orphan",
                "start_time": "2026-07-01T09:00:00",
                "platform": "gcp.agent_engine",
                "input_tokens": 10,
                "output_tokens": 2,
            }
        ],
    )
    (row,) = _payload(render_dashboard(conn, project="example-project"))["rows"]

    assert row["u"] == "(unattributed)"
    assert row["at"] == 0
    assert row["f"] == "Agent Engine"
    conn.close()


def test_html_special_characters_in_data_do_not_break_the_document(tmp_path):
    conn = store.connect(str(tmp_path / "t.db"))
    store.upsert_turns(
        conn,
        [
            {
                "trace_id": "t1",
                "user_principal": "dana@example.com",
                "query_text": "</script><b>not markup</b>",
            }
        ],
    )
    store.upsert_model_calls(
        conn,
        [
            {
                "span_id": "s1",
                "trace_id": "t1",
                "start_time": "2026-07-01T09:00:00",
                "input_tokens": 1,
                "output_tokens": 1,
            }
        ],
    )
    html = render_dashboard(conn, project="example-project")

    # The HTML tokeniser ends a script element at the first literal `</script>`,
    # regardless of JavaScript quoting. Only the template's own closing tag may
    # appear literally; the prompt's copy must be escaped.
    assert html.count("</script>") == 1
    assert "\\u003c/script\\u003e" in html

    # ...and the escaping is lossless once JSON-decoded.
    assert _payload(html)["queries"]["t1"] == "</script><b>not markup</b>"
    conn.close()
