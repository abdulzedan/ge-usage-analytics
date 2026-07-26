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
    assert "no BigQuery" in html


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
