"""Schema, migration and view behaviour of the local SQLite store."""

import sqlite3

import pytest

import store


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(str(tmp_path / "t.db"))
    yield connection
    connection.close()


def _names(conn, kind):
    return {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))
    }


def test_connect_creates_tables_and_views(conn):
    assert {"turns", "model_calls", "tool_calls", "meta"} <= _names(conn, "table")
    assert {"usage", "usage_by_user"} <= _names(conn, "view")


def test_connect_is_repeatable(tmp_path):
    path = str(tmp_path / "t.db")
    store.connect(path).close()
    # Views are dropped and recreated on every connect; doing it twice must work.
    conn = store.connect(path)
    assert {"usage", "usage_by_user"} <= _names(conn, "view")
    conn.close()


def test_upsert_returns_row_count_and_is_idempotent(conn):
    rows = [
        {"trace_id": "t1", "ts": "2026-07-01T10:00:00", "user_principal": "a@example.com"},
        {"trace_id": "t2", "ts": "2026-07-01T11:00:00", "user_principal": "b@example.com"},
    ]
    assert store.upsert_turns(conn, rows) == 2
    assert store.upsert_turns(conn, rows) == 2  # re-running reports the same batch size
    assert conn.execute("SELECT COUNT(*) c FROM turns").fetchone()["c"] == 2


def test_upsert_replaces_on_primary_key(conn):
    store.upsert_turns(conn, [{"trace_id": "t1", "user_principal": "old@example.com"}])
    store.upsert_turns(conn, [{"trace_id": "t1", "user_principal": "new@example.com"}])
    row = conn.execute("SELECT user_principal u FROM turns").fetchone()
    assert row["u"] == "new@example.com"


def test_upsert_empty_is_a_noop(conn):
    assert store.upsert_turns(conn, []) == 0
    assert store.upsert_model_calls(conn, []) == 0
    assert store.upsert_tool_calls(conn, []) == 0


def test_usage_view_attributes_calls_to_the_logged_user(conn):
    store.upsert_turns(
        conn,
        [{"trace_id": "trace-a", "user_principal": "dana@example.com", "query_text": "hello"}],
    )
    store.upsert_model_calls(
        conn,
        [
            {
                "span_id": "trace-a:1",
                "trace_id": "trace-a",
                "start_time": "2026-07-01T09:30:00",
                "platform": "gcp.gemini_enterprise",
                "model": "gemini-3-pro",
                "input_tokens": 100,
                "output_tokens": 25,
            },
            {
                "span_id": "trace-b:1",
                "trace_id": "trace-b",  # no matching turn
                "start_time": "2026-07-01T09:40:00",
                "platform": "gcp.agent_engine",
                "model": "gemini-2.5-flash",
                "input_tokens": 400,
                "output_tokens": 60,
            },
        ],
    )

    rows = {r["span_id"]: r for r in conn.execute("SELECT * FROM usage")}

    attributed = rows["trace-a:1"]
    assert attributed["attributed"] == 1
    assert attributed["user_principal"] == "dana@example.com"
    assert attributed["query_text"] == "hello"
    assert attributed["total_tokens"] == 125
    assert attributed["surface"] == "Gemini Enterprise"
    assert attributed["day"] == "2026-07-01"
    assert attributed["hour"] == "09"

    orphan = rows["trace-b:1"]
    assert orphan["attributed"] == 0
    assert orphan["user_principal"] == "(unattributed)"
    assert orphan["surface"] == "Agent Engine"


def test_usage_view_falls_back_to_the_raw_platform_label(conn):
    store.upsert_model_calls(
        conn,
        [
            {
                "span_id": "s1",
                "trace_id": "t1",
                "start_time": "2026-07-01T09:30:00",
                "platform": "gcp.cloud_run",
                "input_tokens": 1,
                "output_tokens": 1,
            }
        ],
    )
    assert conn.execute("SELECT surface FROM usage").fetchone()["surface"] == "gcp.cloud_run"


def test_usage_view_prefers_the_logged_agent_name(conn):
    store.upsert_turns(
        conn,
        [
            {
                "trace_id": "t1",
                "user_principal": "dana@example.com",
                "agent_display_name": "Claims Coordinator",
                "engine_id": "engine-from-log",
                "session_id": "session-from-log",
            }
        ],
    )
    store.upsert_model_calls(
        conn,
        [
            {
                "span_id": "s1",
                "trace_id": "t1",
                "start_time": "2026-07-01T09:30:00",
                "agent_name": "agent-from-span",
                "engine_id": "engine-from-span",
                "session_id": "session-from-span",
                "input_tokens": 1,
                "output_tokens": 1,
            }
        ],
    )
    row = conn.execute("SELECT * FROM usage").fetchone()
    assert row["agent"] == "Claims Coordinator"
    assert row["engine"] == "engine-from-log"
    assert row["session_id"] == "session-from-log"


def test_usage_by_user_aggregates(conn):
    store.upsert_turns(
        conn,
        [
            {"trace_id": "t1", "user_principal": "dana@example.com", "session_id": "s-1"},
            {"trace_id": "t2", "user_principal": "dana@example.com", "session_id": "s-1"},
        ],
    )
    store.upsert_model_calls(
        conn,
        [
            {
                "span_id": "t1:a",
                "trace_id": "t1",
                "start_time": "2026-07-01T09:00:00",
                "input_tokens": 100,
                "output_tokens": 50,
            },
            {
                "span_id": "t1:b",
                "trace_id": "t1",
                "start_time": "2026-07-01T09:05:00",
                "input_tokens": 200,
                "output_tokens": 100,
            },
            {
                "span_id": "t2:a",
                "trace_id": "t2",
                "start_time": "2026-07-02T09:00:00",
                "input_tokens": 300,
                "output_tokens": 150,
            },
        ],
    )

    row = conn.execute(
        "SELECT * FROM usage_by_user WHERE user_principal = 'dana@example.com'"
    ).fetchone()
    assert row["model_calls"] == 3
    assert row["turns"] == 2
    assert row["sessions"] == 1
    assert row["active_days"] == 2
    assert row["input_tokens"] == 600
    assert row["output_tokens"] == 300
    assert row["total_tokens"] == 900
    assert row["avg_tokens_per_call"] == 300
    assert row["first_seen"] == "2026-07-01T09:00:00"
    assert row["last_seen"] == "2026-07-02T09:00:00"


def test_migration_backfills_columns_added_after_the_first_release(tmp_path):
    path = str(tmp_path / "old.db")
    legacy = sqlite3.connect(path)
    legacy.execute(
        """CREATE TABLE model_calls (
               span_id TEXT PRIMARY KEY, trace_id TEXT, start_time TEXT, end_time TEXT,
               model TEXT, input_tokens INTEGER, output_tokens INTEGER,
               agent_name TEXT, session_id TEXT, engine_id TEXT, latency_ms INTEGER)"""
    )
    legacy.execute(
        "INSERT INTO model_calls (span_id, trace_id, start_time, input_tokens, output_tokens)"
        " VALUES ('s1', 't1', '2026-07-01T09:00:00', 10, 5)"
    )
    legacy.commit()
    legacy.close()

    conn = store.connect(path)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(model_calls)")}
    assert {"span_name", "platform", "resource_id"} <= columns

    # The pre-existing row survives and the view that references the new columns works.
    row = conn.execute("SELECT * FROM usage").fetchone()
    assert row["total_tokens"] == 15
    assert row["surface"] == "other"
    conn.close()


def test_meta_round_trip(conn):
    assert store.get_meta(conn, "project") is None
    assert store.get_meta(conn, "project", "fallback") == "fallback"
    store.set_meta(conn, "project", "example-project")
    assert store.get_meta(conn, "project") == "example-project"
    store.set_meta(conn, "project", "other-project")
    assert store.get_meta(conn, "project") == "other-project"
