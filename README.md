# Gemini Enterprise Usage Analytics

Pull Gemini Enterprise activity logs and token usage from Google Cloud into a
local SQLite database. Query the data with SQL or view it in an offline HTML
dashboard.

> **Demonstration only. Not intended for production use.** Use this project to
> learn how to pull Gemini Enterprise logs and traces for quick local inspection.

![Dashboard overview](docs/images/dashboard-overview.png)

---

## Contents

1. [What this is for](#1-what-this-is-for)
2. [Why two data sources are required](#2-why-two-data-sources-are-required)
3. [Requirements](#3-requirements)
4. [Installation](#4-installation)
5. [Try it without a Google Cloud project](#5-try-it-without-a-google-cloud-project)
6. [First run](#6-first-run)
7. [Command reference](#7-command-reference)
8. [The dashboard](#8-the-dashboard)
9. [Querying the data](#9-querying-the-data)
10. [Scheduling regular collection](#10-scheduling-regular-collection)
11. [Attribution coverage](#11-attribution-coverage)
12. [NotebookLM Enterprise](#12-notebooklm-enterprise)
13. [Data model](#13-data-model)
14. [Limits](#14-limits)
15. [Extending it](#15-extending-it)
16. [Security and data handling](#16-security-and-data-handling)
17. [Troubleshooting](#17-troubleshooting)
18. [How it works internally](#18-how-it-works-internally)
19. [Development](#19-development)

---

## 1. What this is for

Use this demo to collect a recent sample of Gemini Enterprise activity, inspect
token usage, and see how logs and traces can be linked to users. It reads from
Google Cloud and stores the results locally, without a BigQuery setup.

Start with [synthetic data](#5-try-it-without-a-google-cloud-project) to try the
dashboard, or follow [First run](#6-first-run) to collect from your project.
See [Limits](#14-limits) before interpreting the results.

---

## 2. Why two data sources are required

The demo uses Cloud Logging for activity and user identity, and Cloud Trace for
token counts.

| Field | Where it is recorded | Service |
|---|---|---|
| User account (`userIamPrincipal`) | `gemini_enterprise_user_activity` log | Cloud Logging |
| Prompt text, agent, session, engine | Same log entry | Cloud Logging |
| **Input and output token counts** | `gen_ai.usage.*` span attributes | **Cloud Trace** |
| Model name | `gen_ai.request.model` span attribute | Cloud Trace |
| Tool invocations and latency | `gen_ai.tool.name`, span timings | Cloud Trace |
| NotebookLM Enterprise actions | `notebooklm_enterprise_user_activity` log (opt-in) | Cloud Logging |

The user-activity log supplies activity counts, not token counts. This tool reads
tokens from Cloud Trace. The experimental `gen_ai.*` usage logs described in the
August 2026 release notes were not present in the projects used to develop it.

Matching trace IDs link the account and prompt to the model call and its tokens.
When trace IDs differ, the tool also checks for a matching session ID. See
[Attribution coverage](#11-attribution-coverage) for how the joins work.

NotebookLM uses a separate activity log, disabled by default. The demo reports
its actions separately from token usage. See
[NotebookLM Enterprise](#12-notebooklm-enterprise).

---

## 3. Requirements

| Requirement | Detail |
|---|---|
| Python | 3.9 or later. No third-party runtime packages. |
| Google Cloud CLI | `gcloud`, authenticated. Used to obtain access tokens. |
| APIs enabled | Cloud Logging API, Cloud Trace API. |
| IAM roles | `roles/logging.viewer` and `roles/cloudtrace.user` on the target project. |

The collector only reads from Google Cloud. The Cloud Trace User role also grants
permissions to manage Trace tasks and scopes; it is not a strictly read-only role.
See [Cloud Trace permissions](https://docs.cloud.google.com/iam/docs/roles-permissions/cloudtrace).

### Confirming prerequisites

```bash
python3 --version                 # expect 3.9+
gcloud --version
gcloud auth list                  # confirm an active account
gcloud config get-value project   # confirm the target project
```

If no account is active:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

---

## 4. Installation

Copy this directory to the machine that will run reports, then make the entry
point executable:

```bash
cd ge-usage-analytics
chmod +x ge_usage.py
python3 ge_usage.py --help
```

Running the tool requires no build step, virtual environment, or dependency
installation. Development and screenshot tools have optional dependencies.

---

## 5. Try it without a Google Cloud project

To try the dashboard with synthetic data:

```bash
make demo
```

This generates a month of synthetic activity, writes `demo.db` and
`demo-dashboard.html`, and opens the dashboard. It does not contact Google Cloud.
The generator produces the same data for the same options and seed.

To run the steps directly:

```bash
python3 tools/make_demo_db.py --out demo.db --turns 420 --days 30
python3 ge_usage.py --db demo.db dashboard --project demo --open
```

---

## 6. First run

Collect the last 30 days into a local database:

```bash
python3 ge_usage.py collect --project YOUR_PROJECT_ID --days 30
```

```
project : YOUR_PROJECT_ID
window  : 2026-06-24 18:06 → 2026-07-24 18:06 UTC
database: /path/to/ge-usage-analytics/usage.db

[1/3] Cloud Logging: chat turns and user identity …
      309 turns · 5 distinct users

[2/3] Cloud Trace: model spans and token counts …
      620 model calls · 379 tool calls
      3,231,449 input tokens · 369,803 output tokens

[3/3] Cloud Logging: NotebookLM Enterprise activity …
      0 activities. NotebookLM Enterprise logging is off by default and has
      to be enabled per project; see 'NotebookLM Enterprise' in the README.

by surface:
  Gemini Enterprise       223 calls     121 user-attributed     2,079,056 tokens
  Agent Engine            397 calls       0 user-attributed     1,522,196 tokens

attributed to a user: 121/620 model calls (19.5%)
  121 via trace id · 0 via session id
```

View a summary or open the dashboard:

```bash
python3 ge_usage.py stats             # per-user summary in the terminal
python3 ge_usage.py dashboard --open  # write dashboard.html and open it
```

Repeated collection updates matching rows without duplicating them. Records
outside the collection window stay in the local database.

Calls without a user match still contribute to token totals. Those totals cover
the spans collected, which may be limited by sampling, retention, and enabled
logging. See [Attribution coverage](#11-attribution-coverage).

---

## 7. Command reference

Place `--db PATH` before the command to use a database other than the default
`usage.db`, for example `python3 ge_usage.py --db demo.db stats`.

### `collect`

Reads from Google Cloud into the local database.

```bash
python3 ge_usage.py collect --project YOUR_PROJECT_ID --days 30
```

| Option | Purpose |
|---|---|
| `--project ID` | Target project. Falls back to `GE_PROJECT_ID`, `PROJECT_ID`, `GOOGLE_CLOUD_PROJECT`, then the active gcloud project. |
| `--days N` | Look back N days. Default 7. |
| `--since ISO` | Explicit start time, e.g. `2026-07-01T00:00:00Z`. Overrides `--days`. |
| `--engine-id ID` | Restrict to a single Gemini Enterprise application. |
| `--trace-filter F` | Cloud Trace filter; repeatable. Defaults to `span:generate_content` and `span:call_llm`. |

### `stats`

Prints a per-user summary table to the terminal, followed by a NotebookLM
activity table when that data exists.

### `query`

Runs SQL against the local database.

```bash
python3 ge_usage.py query "SELECT user_principal, SUM(total_tokens) FROM usage GROUP BY 1"
python3 ge_usage.py query "SELECT * FROM usage_by_user" --format csv > report.csv
```

`--format` accepts `table` (default), `json` or `csv`.

### `sql`

Opens an interactive `sqlite3` shell against the database.

### `dashboard`

Writes a self-contained HTML file.

| Option | Purpose |
|---|---|
| `--out PATH` | Output file. Default `dashboard.html`. |
| `--open` | Open in the default browser when finished. |
| `--theme dark` | Render with the dark palette. A toggle is also built into the page. |
| `--redact-queries` | Omit prompt text from the output. |

### `serve`

Renders the dashboard and serves it on `127.0.0.1:8777`, for hosts where opening a
local `file://` URL is blocked.

```bash
python3 ge_usage.py serve --port 8777 --open
```

---

## 8. The dashboard

`dashboard.html` contains its data, charts, and styles in one file. It needs
JavaScript but no network connection to view.

Filter token charts by date range, surface, user, model, or agent. Deep Research
and its sub-agents share one agent filter. Each chart has a **Table** button for
viewing the numbers. NotebookLM tables cover the full collected period and are
not affected by these filters.

![Breakdowns by agent and by hour](docs/images/dashboard-breakdowns.png)

Below the charts, tables show per-user totals and recent chat turns with their
prompts.

![Per-user detail and recent activity](docs/images/dashboard-tables.png)

Use **Toggle theme** to switch between light and dark themes.

![Dark theme](docs/images/dashboard-dark.png)

To regenerate these images after a change:

```bash
pip install '.[docs]'          # Pillow; also requires Google Chrome
python3 tools/make_demo_db.py --out demo.db
python3 tools/capture_screenshots.py --db demo.db
```

---

## 9. Querying the data

The `usage` view is the primary interface. Each row is one model call, with the
user, prompt and agent attached where available.

```sql
-- Token consumption by user
SELECT user_principal,
       SUM(input_tokens)  AS input_tokens,
       SUM(output_tokens) AS output_tokens,
       SUM(total_tokens)  AS total_tokens
FROM usage
WHERE attributed = 1
GROUP BY 1
ORDER BY total_tokens DESC;
```

```sql
-- Daily trend for one account
SELECT day,
       SUM(total_tokens)        AS tokens,
       COUNT(DISTINCT trace_id) AS turns
FROM usage
WHERE user_principal = 'user@example.com'
GROUP BY 1
ORDER BY 1;
```

```sql
-- Turns with the most tokens
SELECT trace_id,
       user_principal,
       SUM(total_tokens) AS tokens,
       MIN(query_text)   AS prompt
FROM usage
GROUP BY 1, 2
ORDER BY tokens DESC
LIMIT 10;
```

```sql
-- Model mix, split by surface
SELECT surface, model, COUNT(*) AS calls, SUM(total_tokens) AS tokens
FROM usage
GROUP BY 1, 2
ORDER BY tokens DESC;
```

```sql
-- Tool performance
SELECT tool_name,
       COUNT(*)                     AS invocations,
       CAST(AVG(latency_ms) AS INT) AS avg_ms
FROM tool_calls
GROUP BY 1
ORDER BY avg_ms DESC;
```

You can also open the database with a SQLite client or export query results as
CSV for another reporting tool.

---

## 10. Scheduling regular collection

These examples show how repeated collection can keep a local history during a
demo. They are not a production collection service.

[Cloud Trace retention](https://docs.cloud.google.com/trace/docs/quotas#trace_retention_periods)
is 30 days. The project's `_Default` log bucket also defaults to 30 days, but
[log retention is configurable](https://docs.cloud.google.com/logging/quotas#logs_retention_periods).
Collection cannot recover records that have expired.

**Linux / macOS (cron):** daily at 06:17, with a two-day window to cover one missed
daily run:

```cron
17 6 * * * cd /path/to/ge-usage-analytics && /usr/bin/python3 ge_usage.py collect --project YOUR_PROJECT_ID --days 2 >> collect.log 2>&1
```

**Windows (Task Scheduler)**

```
Program:   python
Arguments: ge_usage.py collect --project YOUR_PROJECT_ID --days 2
Start in:  C:\path\to\ge-usage-analytics
```

Scheduled runs need valid credentials. If using a service account key for the
demo, activate an account with the roles listed in [Requirements](#3-requirements):

```bash
gcloud auth activate-service-account --key-file=/path/to/key.json
```

---

## 11. Attribution coverage

Not every model call can be linked to a named user. Token totals include all
collected calls; per-user totals include only calls with a matching user.

A model call is attributed through one of two exact keys. The `usage` view
records which one matched in `attributed_via`.

**Via trace ID.** Core-assistant `generate_content` spans share the
`StreamAssist` request's trace ID, linking them to the account in the log entry.

**Via session ID.** A call with a different trace ID can match through
`gen_ai.conversation.id` when it contains the Gemini Enterprise session ID.
The tool uses the session's latest turn at or before the span. If none exists,
it uses the first later turn, since a long-running turn can be logged after its
work begins. Custom agents need to pass a matching trace or session ID for
this attribution to work.

**Unattributed.** Calls that match neither key remain `(unattributed)`. In the
projects used to develop this tool, custom agents ran under separate traces
without the Gemini Enterprise session ID. Their collected tokens are included,
but no user is assigned. The tool does not guess a user based on timing alone.

### Deep Research

In an August 2026 project run, the planner and some sub-agent calls shared the
logged `StreamAssist` trace and matched a user. Other sub-agents had separate
traces and no matching session ID. These calls remained unattributed under their
`deep_research_child_N` names, with their tokens included in the totals. Calls
that provide either matching key can use the existing joins.

Check attribution by agent:

```sql
SELECT agent, surface, model_calls, via_trace, via_session,
       model_calls - attributed_calls AS unattributed, total_tokens
FROM usage_by_agent
ORDER BY total_tokens DESC;
```

| Agent | Surface | Calls | Via trace | Via session | Unattributed | Tokens |
|---|---|---|---|---|---|---|
| Claims Coordinator | Agent Engine | 118 | 0 | 0 | 118 | 1,648,256 |
| core_assistant | Gemini Enterprise | 114 | 114 | 0 | 0 | 1,643,475 |
| Contract Review | Agent Engine | 78 | 0 | 78 | 0 | 1,057,286 |
| deep_research_child_2 | Gemini Enterprise | 14 | 3 | 0 | 11 | 618,508 |
| deep_research | Gemini Enterprise | 14 | 14 | 0 | 0 | 94,715 |

This synthetic example shows three cases: Claims Coordinator has no matching
key, Contract Review matches by session, and Deep Research matches only for
calls in the request trace.

Use `agent` to inspect individual sub-agents. Use `agent_group` to report on
Deep Research as a group, as the dashboard does:

```sql
-- Deep Research token usage by user, including unattributed calls
SELECT user_principal, COUNT(*) AS calls, SUM(total_tokens) AS tokens
FROM usage
WHERE agent_group = 'Deep Research'
GROUP BY 1
ORDER BY tokens DESC;
```

Changing the storage destination does not supply missing user identifiers.
Attribution requires a matching key in the source data; see
[Extending it](#15-extending-it).

---

## 12. NotebookLM Enterprise

Gemini Notebook Enterprise uses the log ID
`notebooklm_enterprise_user_activity`. The API and this tool retain the
NotebookLM name.

Logging is disabled by default and is enabled per project, rather than per app
or agent. Enabling it requires `roles/discoveryengine.agentspaceAdmin`; reading
it uses `roles/logging.viewer`.

The following command changes project settings and enables prompt logging.
An administrator should run it only when that data collection is appropriate.
The collector does not enable logging itself. See
[Google's setup instructions](https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/set-up-usage-audit-logs-for-nblme):

```bash
curl -X PATCH \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: PROJECT_ID" \
  "https://ENDPOINT_LOCATION-discoveryengine.googleapis.com/v1alpha/projects/PROJECT_ID?updateMask=customerProvidedConfig.notebooklmConfig.observabilityConfig" \
  -d '{
    "customerProvidedConfig": {
      "notebooklmConfig": {
        "observabilityConfig": {
          "observabilityEnabled": true,
          "sensitiveLoggingEnabled": true
        }
      }
    }
  }'
```

`ENDPOINT_LOCATION` is `us`, `eu`, or `global`. `sensitiveLoggingEnabled`
controls prompt capture. Only activity after logging is enabled is recorded.

The log records the account, action, notebook, and prompts for chat actions.
Examples include `GenerateFreeFormStreamed`, `UploadSourceFile`, `CreateNotebook`,
and sharing actions. Documentation prefixes action names with the service;
observed entries use the bare method name.

`collect` reads this log into `notebooklm_activity`. View counts by user in
`notebooklm_by_user` or `stats`, and recent actions with prompts in the dashboard.
`--redact-queries` removes these prompts from the generated HTML. The demo reports
NotebookLM activity only; it does not collect NotebookLM token counts or add
these actions to token totals.

---

## 13. Data model

Four activity tables and four reporting views, plus a metadata table.

| Table | Each row represents | Source |
|---|---|---|
| `turns` | One user chat turn | Cloud Logging |
| `model_calls` | One LLM call | Cloud Trace |
| `tool_calls` | One tool execution | Cloud Trace |
| `notebooklm_activity` | One NotebookLM action | Cloud Logging (opt-in) |

### View: `usage`

One row per model call. The primary reporting view.

| Column | Description |
|---|---|
| `start_time`, `day`, `hour` | UTC timestamp and derived parts |
| `trace_id`, `span_id` | Correlation identifiers |
| `user_principal` | Signed-in account, or `(unattributed)` |
| `attributed` | `1` if resolved to a named account, otherwise `0` |
| `attributed_via` | `trace`, `session`, or NULL: the key that resolved it |
| `query_text` | Prompt text, where available |
| `agent`, `engine`, `session_id` | Routing context |
| `agent_group` | Agent name, with Deep Research and its sub-agents grouped together |
| `surface` | `Gemini Enterprise` or `Agent Engine` |
| `model` | Model name |
| `input_tokens`, `output_tokens`, `total_tokens` | Recorded token counts |
| `latency_ms` | Call duration |

### View: `usage_by_user`

Totals per account: call and turn counts, session and active-day counts,
token totals, average per call, and first and last activity timestamps.

### View: `usage_by_agent`

Per agent and surface: calls, turns, attributed calls split by method
(`via_trace` / `via_session`), token totals, and first and last activity. A
partially attributed agent such as Deep Research can be inspected here.

### View: `notebooklm_by_user`

Per account: activity count, distinct notebooks, active days, first and last
seen. This view reports activity counts only.

All timestamps are **UTC**.

---

## 14. Limits

### Volume

Earlier synthetic-data measurements used roughly **675 bytes per model call**
in SQLite and **232 bytes per model call** in the dashboard. These are examples,
not capacity guarantees:

| Model calls | Database | Dashboard HTML | Verdict |
|---|---|---|---|
| 600 | 0.5 MB | 0.2 MB | a small team, one month |
| 7,300 | 4.8 MB | 1.7 MB | comfortable |
| 37,000 | 24 MB | 8.2 MB | comfortable |
| 148,000 | 96 MB | 33 MB | SQL fine, dashboard impractical |

In those measurements, SQL queries at 148,000 calls took 175 ms for
`usage_by_user` and 70 ms for a daily total. The dashboard slowed around 50,000
calls (~12 MB) because it embeds every row and recalculates charts in the browser.
Performance depends on the data and machine. For larger samples, use
`query --format csv` or a separate reporting system. See [Extending it](#15-extending-it).

### Source-side limits

- **Retention.** Expired records cannot be retrieved. Cloud Trace retains
  spans for 30 days; log retention depends on the bucket configuration. See
  [Scheduling regular collection](#10-scheduling-regular-collection).
- **Attribution.** User matches depend on trace and session IDs in the source
  data. See [section 11](#11-attribution-coverage).
- **NotebookLM.** This demo reports activity only, after logging is enabled.
  See [NotebookLM Enterprise](#12-notebooklm-enterprise).
- **Cloud Trace sampling.** If a project samples traces, token counts reflect the
  sampled population. This tool reports what the APIs return.

---

## 15. Extending it

The code separates collection from storage:

- `collector.py` reads and parses Google Cloud responses. `collect_turns()`,
  `collect_spans()`, and `collect_notebooklm_activity()` return dictionaries.
- `store.py` writes those rows to SQLite through `upsert_turns()`,
  `upsert_model_calls()`, `upsert_tool_calls()`, and `upsert_notebooklm_activity()`.
  Its `usage` view performs the trace and session joins.

The ideas below require additional implementation and testing. They do not make
this demo ready for production.

### Point it at your own database

To use another database, implement the storage interface and adapt the schema,
views, and queries used by the CLI and dashboard. Keeping the `upsert_*`
signatures helps reuse the collector, but replacing those functions alone is
not enough. A PostgreSQL upsert could follow this pattern (incomplete example):

```python
# store_postgres.py: sketch only; define columns, SQL, and connection handling
def upsert_model_calls(conn, rows):
    execute_values(conn.cursor(), """
        INSERT INTO model_calls (span_id, trace_id, start_time, model,
                                 input_tokens, output_tokens, ...)
        VALUES %s
        ON CONFLICT (span_id) DO UPDATE SET ...
    """, [tuple(r.get(c) for c in COLUMNS) for r in rows])
    return len(rows)
```

For larger datasets, use a reporting tool that queries the destination database
instead of embedding every row in an HTML file.

### Export instead of replacing

To export without changing the storage code:

```bash
python3 ge_usage.py query "SELECT * FROM usage" --format csv > usage.csv
sqlite3 usage.db ".dump" > usage.sql
```

The CSV contains the joined `usage` rows. The SQL dump includes the stored tables
and views; importing it elsewhere may require adapting SQLite syntax.

### Other directions

- **Multiple projects.** Add project identifiers to the four activity tables,
  keys, metadata, and joins before collecting several projects into one database.
- **Cost estimates.** Join a pricing table on `model` and `surface`. Validate the
  pricing rules and collection coverage before using the result; token totals
  here are not a billing record.
- **BI reports.** Export `usage` and `usage_by_user`, or use a reporting tool
  with a suitable SQLite connector.
- **Other log systems.** Send joined rows to an existing system such as Splunk,
  Elastic, or Datadog through an adapter you implement.
- **Attribution.** Where supported, pass W3C `traceparent` to custom agents or
  include the Gemini Enterprise session ID in `gen_ai.conversation.id`. Verify
  that it matches a collected turn. Other identifiers to investigate include
  `user.id` and `gemini_enterprise.assist_token`; this tool does not join on them.
- **Alerts.** A script can check query results against a threshold. Scheduling,
  delivery, and failure handling would need to be added.
- **Shared dashboards.** `serve` binds to `127.0.0.1` for local viewing. Shared
  hosting would need a separate design for authentication and data access.

---

## 16. Security and data handling

- **Cloud access.** The collector makes read requests only. The optional
  NotebookLM setup command changes project settings. IAM roles may grant more
  permissions than collection needs; see [Requirements](#3-requirements).
- **Data stays local.** Collected data is written only to the local SQLite file and
  the generated HTML. Nothing is transmitted anywhere else.
- **Sensitive content.** `usage.db` and `dashboard.html` contain user email
  addresses and prompt text. Treat both as confidential. The supplied `.gitignore`
  excludes them, and CI fails if either is ever committed.
- **Redaction.** `dashboard --redact-queries` removes prompt text from the HTML,
  including its embedded data. User identities remain, and the database is
  unchanged. Review the output before sharing it.
- **Credentials.** The tool keeps access tokens in memory and does not write them
  to the database or dashboard. Tokens come from `gcloud` or
  `GOOGLE_OAUTH_ACCESS_TOKEN`; `gcloud` manages its own stored credentials.

---

## 17. Troubleshooting

**`gcloud not found on PATH`**
Install the Google Cloud CLI, or set `GOOGLE_OAUTH_ACCESS_TOKEN` to a valid token.

**`gcloud auth print-access-token failed`**
Run `gcloud auth login`. For unattended hosts, use
`gcloud auth activate-service-account --key-file=…`.

**`HTTP 403` from either API**
The account lacks `roles/logging.viewer` or `roles/cloudtrace.user`, or the
relevant API is not enabled. The error includes the API's own message, which names
the missing permission.

**`HTTP 400` mentioning a quota project**

```bash
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

**Collection returns 0 turns**
Confirm the project serves Gemini Enterprise traffic and that the window covers a
period with activity. Widen it with `--days 30` and check the log bucket's retention.
User activity is only logged while the observability settings are on (for managed
agents such as Deep Research, the toggle sits on the agent itself).

**Collection returns turns but 0 model calls**
Cloud Trace may not be enabled, or no traced model calls occurred in the window:

```bash
gcloud logging read 'logName:"gemini_enterprise_user_activity"' --limit 5
```

**Attribution percentage looks low**
Expected when traffic is routed to custom agents. See
[Attribution coverage](#11-attribution-coverage).

**NotebookLM shows 0 activities**
Its logging is off by default and records nothing retroactively; history starts
when it is switched on. See
[NotebookLM Enterprise](#12-notebooklm-enterprise). If it is on and the window
covers real usage, confirm the account can read the log:

```bash
gcloud logging read 'logName:"notebooklm_enterprise_user_activity"' --limit 5
```

**Dashboard is blank or truncated**
Confirm the database has rows (`python3 ge_usage.py stats`), then regenerate. The
page requires JavaScript. If the database is very large, see
[Limits](#14-limits).

**Starting over**
Back up `usage.db` if you need its history, then delete it and re-run `collect`.
Expired source data cannot be collected again. Deleting the local database does
not change Google Cloud data.

---

## 18. How it works internally

1. **Cloud Logging** is queried via `entries:list` for
   `gemini_enterprise_user_activity` entries with method `StreamAssist`, producing
   one `turns` row per chat turn, keyed by trace id and carrying the session.

2. **Cloud Trace** is queried via `traces.list` with `view=COMPLETE`, which returns
   the spans and labels of matching traces in paginated responses.
   Spans carrying `gen_ai.usage.*` become `model_calls`; spans carrying
   `gen_ai.tool.name` become `tool_calls`.

3. **Wrapper spans are removed.** An ADK agent wraps each Gemini call in a
   `call_llm` span containing a `generate_content` child, and both report identical
   token counts. Counting both would overstate consumption. A span is discarded
   when a *direct child* reports the *same* token counts.

   Keep other nested model calls: an outer `generate_content` can contain a
   separate model call beneath an `execute_tool` span. Both calls consume tokens.

4. **Cloud Logging is queried once more** for `notebooklm_enterprise_user_activity`
   entries, producing `notebooklm_activity` rows keyed by insert id. A project
   with logging disabled returns no activity entries.

5. **Everything is written with `INSERT OR REPLACE`**, which makes repeated
   runs idempotent. The `usage` view joins model calls to turns on trace id,
   falling back to session id, at query time, so a turn collected later than
   its spans still attributes them.

### Source files

| File | Responsibility |
|---|---|
| `ge_usage.py` | Command-line interface |
| `gcp_client.py` | REST clients: authentication, retries, pagination |
| `collector.py` | Log and span parsing, wrapper removal |
| `store.py` | SQLite schema, migrations, views and the two-key join |
| `dashboard.py` | Offline HTML generation |
| `tools/make_demo_db.py` | Deterministic synthetic data |
| `tools/capture_screenshots.py` | Regenerates the images in this README |

Generated at runtime and never committed: `usage.db`, `dashboard.html`.

---

## 19. Development

```bash
pip install '.[dev]'
make check     # ruff + pytest
make test
make lint
```

Tests use mocked Google Cloud responses and need no credentials or network
access. CI also generates a synthetic database, checks that repeated generation
produces identical files, and verifies that the dashboard has no external
references.

Tests run on Python 3.9 through 3.13.

---

## License

Apache License 2.0. See [LICENSE](LICENSE).
