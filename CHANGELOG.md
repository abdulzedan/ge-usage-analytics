# Changelog

This project is a demonstration for local log collection, not a production tool.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] 2026-09-22

### Added

- Antigravity collection. With Metadata logging switched on in Gemini
  Enterprise's AI developer tools settings (it is off by default), every
  Antigravity request served through the Business AI Code API writes an
  `InferenceResponseLog` entry naming the account, the model, the client and
  its version, the conversation, and the input, output and thinking token
  counts. `collect` reads those entries into an `antigravity_requests` table
  with an `antigravity_by_user` rollup, surfaced in `stats` and as dashboard
  cards for users, models and client versions. The filter matches the
  response entries only, so prompts logged under the separate
  prompts-and-responses setting are never downloaded, and response text on
  those entries is discarded. The token counts come from a different source
  than the Cloud Trace spans, so they are reported on their own and never
  added to the Gemini Enterprise token figures. The entry format is not in
  Google's documentation; the field names were checked against live entries.
- The demo generator produces Antigravity requests, including the Flash Lite
  background calls and a client upgrade in progress.

### Changed

- `stats` reports whichever sources hold data. It used to stop at "No data
  yet" unless Gemini Enterprise model calls existed, which hid a
  NotebookLM-only database and would have hidden an Antigravity-only one.

## [1.1.0] 2026-08-20

Adds session-based attribution and NotebookLM activity collection after a
30-day project run showed gaps in both.

### Added

- Session-ID attribution through `gen_ai.conversation.id` when a model call's
  trace ID does not match a logged turn. Both joins use exact keys. In the
  observed Deep Research run, some sub-agents had neither matching key and
  remained unattributed, listed under their own agent names.
- `attributed_via` on the `usage` view (`trace`, `session`, or NULL) and a
  `usage_by_agent` view showing each agent's attribution coverage. `collect`
  prints the same breakdown.
- NotebookLM Enterprise collection from `notebooklm_enterprise_user_activity`,
  which must be enabled per project. Rows are stored in `notebooklm_activity`
  and summarised in `notebooklm_by_user`, `stats`, and the dashboard. Recent
  actions include prompts unless `--redact-queries` is used. NotebookLM reporting
  is limited to activity counts and is separate from token totals.
- Synthetic examples of session attribution and NotebookLM activity in the demo
  generator.
- `agent_group` on the `usage` view groups Deep Research's planner and
  `deep_research_child_N` sub-agents for dashboard filtering and reporting.
  Individual agents remain available in `agent` and `usage_by_agent`.

### Changed

- Moved the dashboard's attribution explanation to the README's Attribution
  coverage section.

## [1.0.0] 2026-07-26

Initial release.

### Added

- Collection from Cloud Logging and Cloud Trace, joined on W3C trace id, into a
  local SQLite database.
- `ge_usage.py` with `collect`, `stats`, `query`, `sql`, `dashboard` and `serve`.
- Offline HTML dashboard: no external references, cross-chart filtering, a table
  view per chart, light and dark themes, `--redact-queries`.
- `turns`, `model_calls` and `tool_calls` tables, a `usage` view resolving each
  model call to the account behind it, and a `usage_by_user` rollup.
- Repeated collection updates matching rows without duplicates and preserves
  local history beyond source retention.
- Wrapper-span removal avoids counting an ADK agent's
  `call_llm`/`generate_content` pair twice while retaining separate nested calls.
- Surface labelling, separating core-assistant traffic from custom agents.
- `tools/make_demo_db.py` and `tools/capture_screenshots.py`.
- 105 tests; CI runs ruff, pytest on Python 3.9 to 3.13, and an end-to-end job.

### Fixed

- Prompt text containing `</script>` truncated the dashboard because the HTML
  parser treated it as the end of the script. Angle brackets and ampersands in
  embedded JSON are now escaped as `\uXXXX` sequences.

### Known limitations

The dashboard embeds every row and recalculates charts in the browser. Earlier
measurements showed slower loading and filtering around 50,000 model calls.
Calls without matching user identifiers remain unattributed but contribute to
collected token totals. Source retention limits the available history. See the
README for details.

[1.2.0]: https://github.com/abdulzedan/ge-usage-analytics/releases/tag/v1.2.0
[1.1.0]: https://github.com/abdulzedan/ge-usage-analytics/releases/tag/v1.1.0
[1.0.0]: https://github.com/abdulzedan/ge-usage-analytics/releases/tag/v1.0.0
