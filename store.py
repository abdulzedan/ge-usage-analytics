"""Local SQLite store for Gemini Enterprise usage telemetry.

Four fact tables:

  turns                one row per user chat turn      (source: Cloud Logging)
  model_calls          one row per LLM call            (source: Cloud Trace span attributes)
  tool_calls           one row per tool execution      (source: Cloud Trace span attributes)
  notebooklm_activity  one row per NotebookLM action   (source: Cloud Logging; no tokens exist)

The `usage` view joins model_calls back to the account that initiated them, on
trace id first and on session id where the trace id breaks. The session key
re-attaches work that ran under its own traces but stamped the Gemini
Enterprise session on its spans; work that stamped nothing (Deep Research's
detached sub-agent calls, as observed live, and custom agents by default)
stays unattributed rather than being guessed. Both joins are exact keys.

Cloud Trace and the _Default log bucket both expire records after 30 days, so a
database populated on a schedule retains history beyond the platform's own
retention window.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    trace_id            TEXT PRIMARY KEY,
    ts                  TEXT,
    user_principal      TEXT,
    query_text          TEXT,
    agent_id            TEXT,
    agent_display_name  TEXT,
    engine_id           TEXT,
    session_id          TEXT,
    location            TEXT,
    insert_id           TEXT
);
CREATE INDEX IF NOT EXISTS idx_turns_ts      ON turns(ts);
CREATE INDEX IF NOT EXISTS idx_turns_user    ON turns(user_principal);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);

CREATE TABLE IF NOT EXISTS model_calls (
    span_id        TEXT PRIMARY KEY,
    trace_id       TEXT,
    start_time     TEXT,
    end_time       TEXT,
    span_name      TEXT,
    platform       TEXT,
    resource_id    TEXT,
    model          TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    agent_name     TEXT,
    session_id     TEXT,
    engine_id      TEXT,
    latency_ms     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_calls_trace ON model_calls(trace_id);
CREATE INDEX IF NOT EXISTS idx_calls_start ON model_calls(start_time);

CREATE TABLE IF NOT EXISTS tool_calls (
    span_id     TEXT PRIMARY KEY,
    trace_id    TEXT,
    start_time  TEXT,
    end_time    TEXT,
    tool_name   TEXT,
    tool_type   TEXT,
    latency_ms  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tools_trace ON tool_calls(trace_id);

CREATE TABLE IF NOT EXISTS notebooklm_activity (
    insert_id       TEXT PRIMARY KEY,
    ts              TEXT,
    user_principal  TEXT,
    action          TEXT,
    notebook_id     TEXT,
    query_text      TEXT,
    location        TEXT
);
CREATE INDEX IF NOT EXISTS idx_nblm_ts   ON notebooklm_activity(ts);
CREATE INDEX IF NOT EXISTS idx_nblm_user ON notebooklm_activity(user_principal);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

"""

# Views are rebuilt on every connect so a schema change never needs a manual drop.
#
# Attribution is a two-key join, both keys exact:
#
#   1. trace id -- the span executed inside the same trace as the StreamAssist
#      log entry. Core-assistant calls resolve this way.
#   2. session id -- the span ran under its own trace but its
#      `gen_ai.conversation.id` label names the Gemini Enterprise session, and
#      a session belongs to exactly one signed-in account. Among the session's
#      turns, the latest one at or before the span is taken (the log entry can
#      be stamped after long-running work starts, so a call with no earlier
#      turn falls back to the session's first later one).
#
# A call matching neither key stays unattributed; nothing is inferred from time
# proximity across sessions.
#
# The "latest at-or-before, else earliest" preference is spelled as a COALESCE
# of two subqueries on purpose: only very recent SQLite builds resolve outer
# references inside a subquery's ORDER BY, and a single subquery ordering on
# `CASE WHEN t2.ts <= mc.start_time ...` fails with "no such column:
# mc.start_time" everywhere else (Ubuntu's and Apple's bundled builds
# included). Outer references in a subquery's WHERE work in every build.
VIEWS = """
DROP VIEW IF EXISTS usage;
CREATE VIEW usage AS
SELECT
    m.start_time                                        AS start_time,
    substr(m.start_time, 1, 10)                         AS day,
    substr(m.start_time, 12, 2)                         AS hour,
    m.trace_id                                          AS trace_id,
    m.span_id                                           AS span_id,
    COALESCE(t.user_principal, s.user_principal, '(unattributed)')
                                                        AS user_principal,
    CASE WHEN t.trace_id IS NULL AND s.trace_id IS NULL THEN 0 ELSE 1 END
                                                        AS attributed,
    CASE
        WHEN t.trace_id IS NOT NULL THEN 'trace'
        WHEN s.trace_id IS NOT NULL THEN 'session'
    END                                                 AS attributed_via,
    COALESCE(t.query_text, s.query_text)                AS query_text,
    COALESCE(t.agent_display_name, m.agent_name, s.agent_display_name)
                                                        AS agent,
    COALESCE(t.engine_id, m.engine_id, s.engine_id)     AS engine,
    COALESCE(t.session_id, m.session_id)                AS session_id,
    CASE
        WHEN m.platform = 'gcp.gemini_enterprise' THEN 'Gemini Enterprise'
        WHEN m.platform = 'gcp.agent_engine'      THEN 'Agent Engine'
        ELSE COALESCE(m.platform, 'other')
    END                                                 AS surface,
    m.model                                             AS model,
    m.input_tokens                                      AS input_tokens,
    m.output_tokens                                     AS output_tokens,
    m.input_tokens + m.output_tokens                    AS total_tokens,
    m.latency_ms                                        AS latency_ms
FROM (
    SELECT mc.*,
           COALESCE(
               (SELECT t2.trace_id FROM turns t2
                 WHERE mc.session_id IS NOT NULL AND mc.session_id != '-'
                   AND t2.session_id = mc.session_id
                   AND t2.ts <= mc.start_time
                 ORDER BY t2.ts DESC LIMIT 1),
               (SELECT t2.trace_id FROM turns t2
                 WHERE mc.session_id IS NOT NULL AND mc.session_id != '-'
                   AND t2.session_id = mc.session_id
                 ORDER BY t2.ts LIMIT 1)
           )                                            AS session_trace
    FROM model_calls mc
) m
LEFT JOIN turns t ON t.trace_id = m.trace_id
LEFT JOIN turns s ON t.trace_id IS NULL AND s.trace_id = m.session_trace;

DROP VIEW IF EXISTS usage_by_user;
CREATE VIEW usage_by_user AS
SELECT
    user_principal,
    COUNT(*)                        AS model_calls,
    COUNT(DISTINCT trace_id)        AS turns,
    COUNT(DISTINCT session_id)      AS sessions,
    COUNT(DISTINCT day)             AS active_days,
    SUM(input_tokens)               AS input_tokens,
    SUM(output_tokens)              AS output_tokens,
    SUM(total_tokens)               AS total_tokens,
    CAST(ROUND(AVG(total_tokens)) AS INTEGER) AS avg_tokens_per_call,
    MIN(start_time)                 AS first_seen,
    MAX(start_time)                 AS last_seen
FROM usage
GROUP BY user_principal;

DROP VIEW IF EXISTS usage_by_agent;
CREATE VIEW usage_by_agent AS
SELECT
    COALESCE(agent, '(none)')       AS agent,
    surface,
    COUNT(*)                        AS model_calls,
    COUNT(DISTINCT trace_id)        AS turns,
    SUM(attributed)                 AS attributed_calls,
    SUM(CASE WHEN attributed_via = 'trace'   THEN 1 ELSE 0 END) AS via_trace,
    SUM(CASE WHEN attributed_via = 'session' THEN 1 ELSE 0 END) AS via_session,
    SUM(input_tokens)               AS input_tokens,
    SUM(output_tokens)              AS output_tokens,
    SUM(total_tokens)               AS total_tokens,
    MIN(start_time)                 AS first_seen,
    MAX(start_time)                 AS last_seen
FROM usage
GROUP BY 1, 2;

DROP VIEW IF EXISTS notebooklm_by_user;
CREATE VIEW notebooklm_by_user AS
SELECT
    user_principal,
    COUNT(*)                          AS activities,
    COUNT(DISTINCT notebook_id)       AS notebooks,
    COUNT(DISTINCT substr(ts, 1, 10)) AS active_days,
    MIN(ts)                           AS first_seen,
    MAX(ts)                           AS last_seen
FROM notebooklm_activity
GROUP BY user_principal;
"""


# Columns added after the first release; ALTERed in for pre-existing databases.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "model_calls": {
        "span_name": "TEXT",
        "platform": "TEXT",
        "resource_id": "TEXT",
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)   # tables + indexes
    _migrate(conn)               # backfill columns before any view references them
    conn.executescript(VIEWS)    # views last
    return conn


def _upsert(conn: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = (
        f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})"
    )
    conn.executemany(sql, [[r.get(c) for c in cols] for r in rows])
    conn.commit()
    return len(rows)


def upsert_turns(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    return _upsert(conn, "turns", rows)


def upsert_model_calls(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    return _upsert(conn, "model_calls", rows)


def upsert_tool_calls(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    return _upsert(conn, "tool_calls", rows)


def upsert_notebooklm_activity(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    return _upsert(conn, "notebooklm_activity", rows)


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default
