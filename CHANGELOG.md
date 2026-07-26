# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.0.0]: https://github.com/abdulzedan/ge-usage-analytics/releases/tag/v1.0.0
