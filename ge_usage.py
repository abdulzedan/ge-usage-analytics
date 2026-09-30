#!/usr/bin/env python3
"""Demo for collecting Gemini Enterprise logs and token usage locally.

Reads Cloud Logging and Cloud Trace into SQLite, links calls to users by trace
or session ID, and displays the results in SQL or an offline HTML dashboard.
Intended for quick inspection and learning, not production use.

    ./ge_usage.py collect   --project YOUR_PROJECT_ID --days 30
    ./ge_usage.py stats
    ./ge_usage.py dashboard --open
    ./ge_usage.py query     "SELECT user_principal, SUM(total_tokens) t
                             FROM usage GROUP BY 1 ORDER BY t DESC"
    ./ge_usage.py sql       # interactive sqlite3 shell

Standard library only. Requires the gcloud CLI for authentication.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collector  # noqa: E402
import store  # noqa: E402
from dashboard import render_dashboard  # noqa: E402
from gcp_client import GcpClient, GcpError  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "usage.db"
DEFAULT_HTML = HERE / "dashboard.html"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def resolve_project(arg: str | None) -> str:
    if arg:
        return arg
    for key in ("GE_PROJECT_ID", "PROJECT_ID", "GOOGLE_CLOUD_PROJECT"):
        if os.environ.get(key):
            return os.environ[key]
    try:
        out = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()
        if out and out != "(unset)":
            return out
    except Exception:  # pragma: no cover - best effort only
        pass
    raise SystemExit(
        "No project. Pass --project, set PROJECT_ID, or run `gcloud config set project ...`."
    )


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


DEFAULT_TRACE_FILTERS = ["span:generate_content", "span:call_llm"]


def cmd_collect(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    args.trace_filter = args.trace_filter or DEFAULT_TRACE_FILTERS
    end = utcnow()
    start = (
        dt.datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        if args.since
        else end - dt.timedelta(days=args.days)
    )

    print(f"project : {project}")
    print(f"window  : {start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC")
    print(f"database: {args.db}")

    client = GcpClient(project)
    conn = store.connect(args.db)

    print("\n[1/3] Cloud Logging: chat turns and user identity …", flush=True)
    turns = collector.collect_turns(
        client, start=start, end=end, engine_id=args.engine_id
    )
    n_turns = store.upsert_turns(conn, turns)
    users = {t["user_principal"] for t in turns if t.get("user_principal")}
    print(f"      {n_turns:,} turns · {len(users)} distinct users")

    print("\n[2/3] Cloud Trace: model spans and token counts …", flush=True)
    models, tools = collector.collect_spans(
        client, start=start, end=end, trace_filters=args.trace_filter
    )
    n_models = store.upsert_model_calls(conn, models)
    n_tools = store.upsert_tool_calls(conn, tools)
    tok_in, tok_out = collector.summarise(models)
    print(f"      {n_models:,} model calls · {n_tools:,} tool calls")
    print(f"      {tok_in:,} input tokens · {tok_out:,} output tokens")

    print("\n[3/3] Cloud Logging: NotebookLM Enterprise activity …", flush=True)
    nblm = collector.collect_notebooklm_activity(client, start=start, end=end)
    n_nblm = store.upsert_notebooklm_activity(conn, nblm)
    nblm_users = {r["user_principal"] for r in nblm if r.get("user_principal")}
    if n_nblm:
        print(f"      {n_nblm:,} activities · {len(nblm_users)} distinct users (no token data exists)")
    else:
        print(
            "      0 activities. NotebookLM Enterprise logging is off by default and has\n"
            "      to be enabled per project; see 'NotebookLM Enterprise' in the README."
        )

    store.set_meta(conn, "last_collect_utc", end.strftime("%Y-%m-%dT%H:%M:%SZ"))
    store.set_meta(conn, "project", project)

    print("\nby surface:")
    for r in conn.execute(
        "SELECT surface, COUNT(*) calls, SUM(attributed) attributed,"
        " SUM(total_tokens) tokens FROM usage GROUP BY surface ORDER BY tokens DESC"
    ):
        print(
            f"  {r['surface']:<20} {r['calls']:>6,} calls  "
            f"{r['attributed']:>6,} user-attributed  {r['tokens']:>12,} tokens"
        )

    matched = conn.execute("SELECT COALESCE(SUM(attributed),0) c FROM usage").fetchone()["c"]
    total = conn.execute("SELECT COUNT(*) c FROM usage").fetchone()["c"]
    via = {
        r["attributed_via"]: r["c"]
        for r in conn.execute(
            "SELECT attributed_via, COUNT(*) c FROM usage WHERE attributed = 1 GROUP BY 1"
        )
    }
    pct = (matched / total * 100) if total else 0.0
    print(f"\nattributed to a user: {matched:,}/{total:,} model calls ({pct:.1f}%)")
    if matched:
        print(
            f"  {via.get('trace', 0):,} via trace id · {via.get('session', 0):,} via session id"
        )
    if total and pct < 100:
        print(
            "  Calls without a matching trace or session ID remain unattributed.\n"
            "  Their tokens are included in the collected totals.\n"
            "  See 'Attribution coverage' in the README."
        )
    conn.close()
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    conn = store.connect(args.db)
    rows = conn.execute(
        "SELECT * FROM usage_by_user ORDER BY total_tokens DESC"
    ).fetchall()
    if not rows:
        print("No data yet. Run `collect` first.")
        return 1

    head = f"{'USER':<38}{'TURNS':>8}{'CALLS':>8}{'INPUT':>12}{'OUTPUT':>10}{'TOTAL':>12}"
    print(head)
    print("-" * len(head))
    for r in rows:
        user = (r["user_principal"] or "")[:37]
        print(
            f"{user:<38}{r['turns']:>8,}{r['model_calls']:>8,}"
            f"{r['input_tokens']:>12,}{r['output_tokens']:>10,}{r['total_tokens']:>12,}"
        )
    print("-" * len(head))
    tot = conn.execute(
        "SELECT COUNT(DISTINCT user_principal) u, COUNT(DISTINCT trace_id) t,"
        " COUNT(*) c, SUM(input_tokens) i, SUM(output_tokens) o FROM usage"
    ).fetchone()
    print(
        f"{'TOTAL (' + str(tot['u']) + ' users)':<38}{tot['t']:>8,}{tot['c']:>8,}"
        f"{tot['i']:>12,}{tot['o']:>10,}{tot['i'] + tot['o']:>12,}"
    )

    nblm = conn.execute(
        "SELECT * FROM notebooklm_by_user ORDER BY activities DESC"
    ).fetchall()
    if nblm:
        head = f"{'NOTEBOOKLM USER':<38}{'ACTIONS':>8}{'NOTEBOOKS':>10}{'DAYS':>6}  LAST SEEN"
        print("\nNotebookLM Enterprise — activity only; its log carries no token counts")
        print(head)
        print("-" * len(head))
        for r in nblm:
            user = (r["user_principal"] or "")[:37]
            print(
                f"{user:<38}{r['activities']:>8,}{r['notebooks']:>10,}"
                f"{r['active_days']:>6,}  {r['last_seen'] or ''}"
            )
    conn.close()
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    conn = store.connect(args.db)
    try:
        cur = conn.execute(args.sql)
    except sqlite3.Error as exc:
        print(f"SQL error: {exc}", file=sys.stderr)
        return 1

    rows = cur.fetchall()
    cols = [d[0] for d in cur.description] if cur.description else []

    if args.format == "json":
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    elif args.format == "csv":
        writer = csv.writer(sys.stdout)
        writer.writerow(cols)
        writer.writerows([[r[c] for c in cols] for r in rows])
    else:
        if not rows:
            print("(no rows)")
            return 0
        widths = [
            min(60, max(len(c), max((len(str(r[c])) for r in rows), default=0)))
            for c in cols
        ]
        print("  ".join(c.ljust(w)[:w] for c, w in zip(cols, widths)))
        print("  ".join("-" * w for w in widths))
        for r in rows:
            print("  ".join(str(r[c]).ljust(w)[:w] for c, w in zip(cols, widths)))
        print(f"\n({len(rows)} rows)")
    conn.close()
    return 0


def cmd_sql(args: argparse.Namespace) -> int:
    """Drop into the sqlite3 shell for ad-hoc exploration."""
    if not Path(args.db).exists():
        print("No database yet. Run `collect` first.", file=sys.stderr)
        return 1
    try:
        return subprocess.call(["sqlite3", "-column", "-header", args.db])
    except FileNotFoundError:
        print(
            "sqlite3 CLI not found. Use `ge_usage.py query \"SELECT ...\"` instead.",
            file=sys.stderr,
        )
        return 1


def cmd_dashboard(args: argparse.Namespace) -> int:
    if not Path(args.db).exists():
        print("No database yet. Run `collect` first.", file=sys.stderr)
        return 1
    conn = store.connect(args.db)
    project = args.project or store.get_meta(conn, "project") or "(unknown project)"
    html = render_dashboard(
        conn,
        project=project,
        theme=args.theme,
        redact_queries=args.redact_queries,
        generated_at=utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    )
    out = Path(args.out)
    out.write_text(html, encoding="utf-8")
    conn.close()

    size_kb = out.stat().st_size / 1024
    print(f"wrote {out}  ({size_kb:,.0f} KB, self-contained)")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Serve the dashboard on localhost when file URLs are unavailable."""
    import functools
    import http.server

    cmd_dashboard(args)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE))
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as httpd:
        url = f"http://127.0.0.1:{args.port}/{Path(args.out).name}"
        print(f"serving {url}   (ctrl-c to stop)")
        if args.open:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ge_usage.py",
        description="Collect Gemini Enterprise logs and token usage for local inspection.",
        epilog="Demonstration only. Not intended for production use.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--db", default=str(DEFAULT_DB), help=f"SQLite path (default: {DEFAULT_DB.name})")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="read Google Cloud logs and traces into SQLite")
    c.add_argument("--project", help="GCP project id")
    c.add_argument("--days", type=int, default=7, help="look back N days (default 7)")
    c.add_argument("--since", help="ISO start time, overrides --days")
    c.add_argument("--engine-id", help="restrict to one Gemini Enterprise app/engine id")
    c.add_argument(
        "--trace-filter",
        action="append",
        default=None,
        help="Cloud Trace list filter; repeatable. "
        "Default: 'span:generate_content' (Gemini Enterprise) and "
        "'span:call_llm' (ADK agents on Agent Engine).",
    )
    c.set_defaults(func=cmd_collect)

    s = sub.add_parser("stats", help="print a per-user summary table")
    s.set_defaults(func=cmd_stats)

    q = sub.add_parser("query", help="run SQL against the local database")
    q.add_argument("sql", help="SQL statement")
    q.add_argument("--format", choices=["table", "json", "csv"], default="table")
    q.set_defaults(func=cmd_query)

    sh = sub.add_parser("sql", help="open an interactive sqlite3 shell")
    sh.set_defaults(func=cmd_sql)

    d = sub.add_parser("dashboard", help="render the offline HTML dashboard")
    d.add_argument("--project")
    d.add_argument("--out", default=str(DEFAULT_HTML))
    d.add_argument("--theme", choices=["light", "dark"], default="light")
    d.add_argument("--redact-queries", action="store_true", help="omit prompt text")
    d.add_argument("--open", action="store_true", help="open in the default browser")
    d.set_defaults(func=cmd_dashboard)

    v = sub.add_parser("serve", help="render and serve the dashboard on localhost")
    v.add_argument("--project")
    v.add_argument("--out", default=str(DEFAULT_HTML))
    v.add_argument("--theme", choices=["light", "dark"], default="light")
    v.add_argument("--redact-queries", action="store_true")
    v.add_argument("--port", type=int, default=8777)
    v.add_argument("--open", action="store_true")
    v.set_defaults(func=cmd_serve)

    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except GcpError as exc:
        print(f"\nGoogle Cloud API error:\n{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
