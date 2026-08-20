"""Argument parsing and command behaviour of the ge_usage entry point."""

import argparse
import json
import subprocess

import pytest

import ge_usage
import store

_PROJECT_ENV = ("GE_PROJECT_ID", "PROJECT_ID", "GOOGLE_CLOUD_PROJECT")


@pytest.fixture(autouse=True)
def _clear_project_env(monkeypatch):
    for key in _PROJECT_ENV:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def db(tmp_path):
    """A populated database, returned as a path string."""
    path = str(tmp_path / "usage.db")
    conn = store.connect(path)
    store.upsert_turns(
        conn,
        [
            {
                "trace_id": "trace-a",
                "ts": "2026-07-01T09:00:00",
                "user_principal": "dana@example.com",
                "query_text": "status of order 4471",
                "session_id": "s-1",
            }
        ],
    )
    store.upsert_model_calls(
        conn,
        [
            {
                "span_id": "trace-a:1",
                "trace_id": "trace-a",
                "start_time": "2026-07-01T09:00:00",
                "platform": "gcp.gemini_enterprise",
                "model": "gemini-3-pro",
                "input_tokens": 1200,
                "output_tokens": 340,
            }
        ],
    )
    conn.close()
    return path


# ---------------------------------------------------------------------------
# project resolution
# ---------------------------------------------------------------------------


def test_an_explicit_project_wins(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "from-env")
    assert ge_usage.resolve_project("from-flag") == "from-flag"


@pytest.mark.parametrize("key", _PROJECT_ENV)
def test_each_environment_variable_is_honoured(monkeypatch, key):
    monkeypatch.setenv(key, "from-env")
    assert ge_usage.resolve_project(None) == "from-env"


def test_ge_project_id_takes_precedence(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "third")
    monkeypatch.setenv("PROJECT_ID", "second")
    monkeypatch.setenv("GE_PROJECT_ID", "first")
    assert ge_usage.resolve_project(None) == "first"


def test_project_falls_back_to_the_active_gcloud_configuration(monkeypatch):
    monkeypatch.setattr(
        ge_usage.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="from-gcloud\n", stderr=""),
    )
    assert ge_usage.resolve_project(None) == "from-gcloud"


def test_an_unset_gcloud_project_is_not_accepted(monkeypatch):
    monkeypatch.setattr(
        ge_usage.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="(unset)\n", stderr=""),
    )
    with pytest.raises(SystemExit, match="No project"):
        ge_usage.resolve_project(None)


def test_a_missing_gcloud_is_reported_as_a_missing_project(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("gcloud")

    monkeypatch.setattr(ge_usage.subprocess, "run", boom)
    with pytest.raises(SystemExit, match="No project"):
        ge_usage.resolve_project(None)


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def test_every_subcommand_is_registered():
    parser = ge_usage.build_parser()
    for command in ("collect", "stats", "query", "sql", "dashboard", "serve"):
        assert parser.parse_args([command] + (["x"] if command == "query" else [])).func


def test_collect_defaults():
    args = ge_usage.build_parser().parse_args(["collect"])
    assert args.days == 7
    assert args.since is None
    assert args.engine_id is None
    assert args.trace_filter is None  # filled in from DEFAULT_TRACE_FILTERS at run time


def test_trace_filter_is_repeatable():
    args = ge_usage.build_parser().parse_args(
        ["collect", "--trace-filter", "span:a", "--trace-filter", "span:b"]
    )
    assert args.trace_filter == ["span:a", "span:b"]


def test_default_trace_filters_cover_both_surfaces():
    assert ge_usage.DEFAULT_TRACE_FILTERS == ["span:generate_content", "span:call_llm"]


def test_a_subcommand_is_required():
    with pytest.raises(SystemExit):
        ge_usage.build_parser().parse_args([])


def test_dashboard_defaults():
    args = ge_usage.build_parser().parse_args(["dashboard"])
    assert args.theme == "light"
    assert args.redact_queries is False
    assert args.open is False


def test_serve_defaults_to_a_loopback_port():
    assert ge_usage.build_parser().parse_args(["serve"]).port == 8777


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def test_stats_prints_a_row_per_user(db, capsys):
    rc = ge_usage.cmd_stats(argparse.Namespace(db=db))
    out = capsys.readouterr().out

    assert rc == 0
    assert "dana@example.com" in out
    assert "1,540" in out  # 1200 + 340
    assert "TOTAL (1 users)" in out


def test_stats_appends_notebooklm_activity_when_present(db, capsys):
    conn = store.connect(db)
    store.upsert_notebooklm_activity(
        conn,
        [{"insert_id": "n1", "ts": "2026-07-02T10:00:00",
          "user_principal": "dana@example.com",
          "action": "GenerateFreeFormStreamed",
          "notebook_id": "nb-1"}],
    )
    conn.close()

    rc = ge_usage.cmd_stats(argparse.Namespace(db=db))
    out = capsys.readouterr().out

    assert rc == 0
    assert "NotebookLM Enterprise" in out
    assert "no token counts" in out
    assert "2026-07-02T10:00:00" in out


def test_stats_stays_quiet_without_notebooklm_data(db, capsys):
    ge_usage.cmd_stats(argparse.Namespace(db=db))
    assert "NotebookLM" not in capsys.readouterr().out


def test_stats_on_an_empty_database_reports_no_data(tmp_path, capsys):
    path = str(tmp_path / "empty.db")
    store.connect(path).close()

    rc = ge_usage.cmd_stats(argparse.Namespace(db=path))
    assert rc == 1
    assert "Run `collect` first" in capsys.readouterr().out


def test_query_renders_a_table(db, capsys):
    rc = ge_usage.cmd_query(
        argparse.Namespace(db=db, sql="SELECT user_principal FROM usage", format="table")
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "user_principal" in out
    assert "dana@example.com" in out
    assert "(1 rows)" in out


def test_query_renders_json(db, capsys):
    rc = ge_usage.cmd_query(
        argparse.Namespace(
            db=db, sql="SELECT user_principal, total_tokens FROM usage", format="json"
        )
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == [
        {"user_principal": "dana@example.com", "total_tokens": 1540}
    ]


def test_query_renders_csv(db, capsys):
    rc = ge_usage.cmd_query(
        argparse.Namespace(
            db=db, sql="SELECT user_principal, total_tokens FROM usage", format="csv"
        )
    )
    assert rc == 0
    assert capsys.readouterr().out.splitlines() == [
        "user_principal,total_tokens",
        "dana@example.com,1540",
    ]


def test_query_reports_no_rows(db, capsys):
    rc = ge_usage.cmd_query(
        argparse.Namespace(db=db, sql="SELECT * FROM usage WHERE 0", format="table")
    )
    assert rc == 0
    assert "(no rows)" in capsys.readouterr().out


def test_invalid_sql_is_reported_without_a_traceback(db, capsys):
    rc = ge_usage.cmd_query(
        argparse.Namespace(db=db, sql="SELECT * FROM nope", format="table")
    )
    assert rc == 1
    assert "SQL error" in capsys.readouterr().err


def test_dashboard_writes_a_self_contained_file(db, tmp_path, capsys):
    out = tmp_path / "out.html"
    rc = ge_usage.cmd_dashboard(
        argparse.Namespace(
            db=db, out=str(out), project="example-project",
            theme="light", redact_queries=False, open=False,
        )
    )

    assert rc == 0
    assert "self-contained" in capsys.readouterr().out
    assert "dana@example.com" in out.read_text()


def test_dashboard_honours_redaction(db, tmp_path):
    out = tmp_path / "out.html"
    ge_usage.cmd_dashboard(
        argparse.Namespace(
            db=db, out=str(out), project="example-project",
            theme="dark", redact_queries=True, open=False,
        )
    )
    text = out.read_text()

    assert "order 4471" not in text
    assert 'data-theme="dark"' in text


def test_dashboard_uses_the_project_recorded_at_collection_time(db, tmp_path):
    conn = store.connect(db)
    store.set_meta(conn, "project", "recorded-project")
    conn.close()

    out = tmp_path / "out.html"
    ge_usage.cmd_dashboard(
        argparse.Namespace(
            db=db, out=str(out), project=None,
            theme="light", redact_queries=False, open=False,
        )
    )
    assert "Project recorded-project" in out.read_text()


def test_dashboard_without_a_database_exits_nonzero(tmp_path, capsys):
    rc = ge_usage.cmd_dashboard(
        argparse.Namespace(
            db=str(tmp_path / "missing.db"), out=str(tmp_path / "o.html"),
            project=None, theme="light", redact_queries=False, open=False,
        )
    )
    assert rc == 1
    assert "Run `collect` first" in capsys.readouterr().err


def test_sql_shell_without_a_database_exits_nonzero(tmp_path, capsys):
    rc = ge_usage.cmd_sql(argparse.Namespace(db=str(tmp_path / "missing.db")))
    assert rc == 1
    assert "Run `collect` first" in capsys.readouterr().err


def test_sql_shell_reports_a_missing_sqlite3_binary(db, monkeypatch, capsys):
    def boom(*a, **k):
        raise FileNotFoundError("sqlite3")

    monkeypatch.setattr(ge_usage.subprocess, "call", boom)
    rc = ge_usage.cmd_sql(argparse.Namespace(db=db))

    assert rc == 1
    assert "sqlite3 CLI not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# collect, with the two Google Cloud calls stubbed out
# ---------------------------------------------------------------------------


def test_collect_writes_all_sources_and_summarises(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "new.db")

    monkeypatch.setattr(ge_usage, "GcpClient", lambda project: object())
    monkeypatch.setattr(
        ge_usage.collector,
        "collect_turns",
        lambda client, *, start, end, engine_id: [
            {"trace_id": "trace-a", "user_principal": "dana@example.com"}
        ],
    )
    monkeypatch.setattr(
        ge_usage.collector,
        "collect_spans",
        lambda client, *, start, end, trace_filters: (
            [
                {
                    "span_id": "trace-a:1",
                    "trace_id": "trace-a",
                    "start_time": "2026-07-01T09:00:00",
                    "platform": "gcp.gemini_enterprise",
                    "input_tokens": 1200,
                    "output_tokens": 340,
                }
            ],
            [{"span_id": "trace-a:2", "trace_id": "trace-a", "tool_name": "lookup_order"}],
        ),
    )
    monkeypatch.setattr(
        ge_usage.collector,
        "collect_notebooklm_activity",
        lambda client, *, start, end: [
            {
                "insert_id": "n1",
                "ts": "2026-07-01T10:00:00",
                "user_principal": "dana@example.com",
                "action": "GenerateFreeFormStreamed",
                "notebook_id": "nb-1",
            }
        ],
    )

    rc = ge_usage.cmd_collect(
        argparse.Namespace(
            db=path, project="example-project", days=7,
            since=None, engine_id=None, trace_filter=None,
        )
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "1 turns · 1 distinct users" in out
    assert "1 model calls · 1 tool calls" in out
    assert "1,200 input tokens · 340 output tokens" in out
    assert "1 activities · 1 distinct users (no token data exists)" in out
    assert "Gemini Enterprise" in out
    assert "attributed to a user: 1/1 model calls (100.0%)" in out
    assert "1 via trace id · 0 via session id" in out

    conn = store.connect(path)
    assert conn.execute("SELECT COUNT(*) c FROM usage").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM notebooklm_activity").fetchone()["c"] == 1
    assert store.get_meta(conn, "project") == "example-project"
    assert store.get_meta(conn, "last_collect_utc") is not None
    conn.close()


def test_collect_explains_a_shortfall_in_attribution(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "new.db")

    monkeypatch.setattr(ge_usage, "GcpClient", lambda project: object())
    monkeypatch.setattr(
        ge_usage.collector, "collect_turns", lambda client, **kw: []
    )
    monkeypatch.setattr(
        ge_usage.collector,
        "collect_spans",
        lambda client, **kw: (
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
            [],
        ),
    )
    monkeypatch.setattr(
        ge_usage.collector, "collect_notebooklm_activity", lambda client, **kw: []
    )

    ge_usage.cmd_collect(
        argparse.Namespace(
            db=path, project="example-project", days=7,
            since=None, engine_id=None, trace_filter=None,
        )
    )
    out = capsys.readouterr().out

    assert "attributed to a user: 0/1 model calls (0.0%)" in out
    assert "own trace id" in out
    # Nothing collected from NotebookLM points at the off-by-default logging.
    assert "off by default" in out


def test_collect_reports_the_session_recoveries_separately(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "new.db")

    monkeypatch.setattr(ge_usage, "GcpClient", lambda project: object())
    monkeypatch.setattr(
        ge_usage.collector,
        "collect_turns",
        lambda client, **kw: [
            {"trace_id": "trace-a", "ts": "2026-07-01T08:59:00",
             "user_principal": "dana@example.com", "session_id": "s-1"}
        ],
    )
    monkeypatch.setattr(
        ge_usage.collector,
        "collect_spans",
        lambda client, **kw: (
            [
                {"span_id": "trace-a:1", "trace_id": "trace-a",
                 "start_time": "2026-07-01T09:00:00", "session_id": "s-1",
                 "input_tokens": 10, "output_tokens": 2},
                {"span_id": "sub:1", "trace_id": "sub",
                 "start_time": "2026-07-01T09:01:00", "session_id": "s-1",
                 "input_tokens": 40, "output_tokens": 4},
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        ge_usage.collector, "collect_notebooklm_activity", lambda client, **kw: []
    )

    ge_usage.cmd_collect(
        argparse.Namespace(
            db=path, project="example-project", days=7,
            since=None, engine_id=None, trace_filter=None,
        )
    )
    out = capsys.readouterr().out

    assert "attributed to a user: 2/2 model calls (100.0%)" in out
    assert "1 via trace id · 1 via session id" in out


def test_collect_accepts_an_explicit_start_time(tmp_path, monkeypatch, capsys):
    seen = {}

    monkeypatch.setattr(ge_usage, "GcpClient", lambda project: object())
    monkeypatch.setattr(
        ge_usage.collector,
        "collect_turns",
        lambda client, *, start, end, engine_id: seen.update(start=start) or [],
    )
    monkeypatch.setattr(ge_usage.collector, "collect_spans", lambda client, **kw: ([], []))
    monkeypatch.setattr(
        ge_usage.collector, "collect_notebooklm_activity", lambda client, **kw: []
    )

    ge_usage.cmd_collect(
        argparse.Namespace(
            db=str(tmp_path / "n.db"), project="example-project", days=7,
            since="2026-06-01T00:00:00Z", engine_id=None, trace_filter=None,
        )
    )

    assert seen["start"].year == 2026
    assert seen["start"].month == 6
    assert seen["start"].day == 1


def test_collect_applies_the_default_trace_filters(tmp_path, monkeypatch):
    seen = {}

    monkeypatch.setattr(ge_usage, "GcpClient", lambda project: object())
    monkeypatch.setattr(ge_usage.collector, "collect_turns", lambda client, **kw: [])
    monkeypatch.setattr(
        ge_usage.collector,
        "collect_spans",
        lambda client, *, start, end, trace_filters: seen.update(f=trace_filters) or ([], []),
    )
    monkeypatch.setattr(
        ge_usage.collector, "collect_notebooklm_activity", lambda client, **kw: []
    )

    ge_usage.cmd_collect(
        argparse.Namespace(
            db=str(tmp_path / "n.db"), project="example-project", days=7,
            since=None, engine_id=None, trace_filter=None,
        )
    )

    assert seen["f"] == ge_usage.DEFAULT_TRACE_FILTERS


class _StubParser:
    """Returns a fixed namespace, so main() can be driven without real argv."""

    def __init__(self, func):
        self._func = func

    def parse_args(self):
        return argparse.Namespace(func=self._func)


def _main_with(monkeypatch, func):
    monkeypatch.setattr(ge_usage, "build_parser", lambda: _StubParser(func))
    return ge_usage.main()


def test_main_returns_the_command_status(monkeypatch):
    assert _main_with(monkeypatch, lambda _args: 0) == 0


def test_main_reports_api_errors_without_a_traceback(monkeypatch, capsys):
    from gcp_client import GcpError

    def boom(_args):
        raise GcpError("HTTP 403 from logging.googleapis.com")

    assert _main_with(monkeypatch, boom) == 2
    err = capsys.readouterr().err
    assert "Google Cloud API error" in err
    assert "HTTP 403" in err


def test_main_handles_an_interrupt_with_the_conventional_status(monkeypatch, capsys):
    def boom(_args):
        raise KeyboardInterrupt

    assert _main_with(monkeypatch, boom) == 130
    assert "interrupted" in capsys.readouterr().err
