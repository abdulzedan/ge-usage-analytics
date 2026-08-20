# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] 2026-08-20

Driven by a live 30-day run: Deep Research resolved to a named user for only a
minority of its model calls, and NotebookLM Enterprise did not appear at all.

### Added

- Session-id attribution. A model call whose trace id matches no logged turn is
  now resolved through the Gemini Enterprise session its spans carry
  (`gen_ai.conversation.id`) before being declared unattributed. This is the
  key an agent team controls: stamp the session, or propagate `traceparent`,
  and the agent's calls resolve with no tool change. Deep Research — the case
  that prompted the work — turns out not to be recoverable yet: observed live,
  its planner and some sub-agent calls share the request trace and resolve,
  while its detached sub-agent calls carry neither key, so they now surface
  under their own agent names instead of vanishing into an anonymous bucket.
  Both joins are exact keys; nothing is ever attributed by time proximity.
- `attributed_via` on the `usage` view (`trace`, `session`, or NULL) and a
  `usage_by_agent` view splitting each agent's calls by attribution method, so a
  partially-attributed agent is visible as such instead of quietly inflating the
  unattributed bucket. `collect` prints the same split.
- NotebookLM Enterprise collection. `collect` reads the
  `notebooklm_enterprise_user_activity` log — a separate log from the Gemini
  Enterprise one, off by default and enabled per project — into a
  `notebooklm_activity` table with a `notebooklm_by_user` rollup, surfaced in
  `stats` and as a dashboard card. Its entries record who did what, and the
  prompt for chat actions, but no token counts exist for NotebookLM on any
  surface, so it is reported as activity and never mixed into token figures.
- The demo generator produces both new populations, so the behaviour is
  visible without a Google Cloud project.

## [1.0.0] 2026-07-26

First stable release.

### Added

- Collection from Cloud Logging and Cloud Trace, joined on W3C trace id, into a
  local SQLite database.
- `ge_usage.py` with `collect`, `stats`, `query`, `sql`, `dashboard` and `serve`.
- Offline HTML dashboard: no external references, cross-chart filtering, a table
  view per chart, light and dark themes, `--redact-queries`.
- `turns`, `model_calls` and `tool_calls` tables, a `usage` view resolving each
  model call to the account behind it, and a `usage_by_user` rollup.
- Idempotent collection, so a scheduled job accumulates history past the
  platform's 30-day retention.
- Wrapper-span removal, so an ADK agent's `call_llm`/`generate_content` pair is
  not double counted, without discarding genuine nested calls.
- Surface labelling, separating core-assistant traffic from custom agents.
- `tools/make_demo_db.py` and `tools/capture_screenshots.py`.
- 105 tests; CI runs ruff, pytest on Python 3.9 to 3.13, and an end-to-end job.

### Fixed

- Prompt text containing `</script>` truncated the dashboard. An HTML parser
  ends a script element at the first literal occurrence regardless of JavaScript
  quoting, and `json.dumps` does not escape angle brackets, so everything below
  the header was parsed as text. Angle brackets and ampersands are now `\uXXXX`
  escapes.

### Known limitations

The dashboard embeds every row and recomputes in the browser, so it is the
binding constraint on volume: comfortable to roughly 50,000 model calls. Model
calls from custom agents arrive without a user, because Gemini Enterprise does
not propagate trace context into them; token totals are unaffected. Cloud Trace
and the `_Default` log bucket retain 30 days. See the README.

[1.1.0]: https://github.com/abdulzedan/ge-usage-analytics/releases/tag/v1.1.0
[1.0.0]: https://github.com/abdulzedan/ge-usage-analytics/releases/tag/v1.0.0
