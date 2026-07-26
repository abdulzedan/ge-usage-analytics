# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-07-26

First stable release.

### Added

- **Collection** from Cloud Logging and Cloud Trace, joined on W3C trace id, into
  a local SQLite database. Read-only; requires `roles/logging.viewer` and
  `roles/cloudtrace.user` and no change to the target project.
- **`ge_usage.py`** with six subcommands: `collect`, `stats`, `query`, `sql`,
  `dashboard` and `serve`.
- **Offline HTML dashboard** with no external references — embedded JSON, inline
  SVG, one style element — so it opens on a host with no network access. Cross-
  chart filtering by range, surface, user, model and agent; a table view for every
  chart; light and dark palettes; `--redact-queries` for wider circulation.
- **Data model**: `turns`, `model_calls` and `tool_calls` tables, with a `usage`
  view that resolves each model call to the account that initiated it and flags
  whether the join succeeded, and a pre-aggregated `usage_by_user`.
- **Idempotent collection.** Overlapping windows can be re-run safely, so a
  scheduled job accumulates history past the platform's own 30-day retention.
- **Wrapper-span removal**, so an ADK agent's `call_llm`/`generate_content` pair is
  not counted twice, without discarding genuine nested calls under agent-as-tool
  execution.
- **Surface labelling**, separating Gemini Enterprise core-assistant traffic from
  custom agents on Agent Engine, so the two attribution populations can be reported
  apart.
- **`tools/make_demo_db.py`**, a deterministic synthetic data generator, and
  **`tools/capture_screenshots.py`**, which regenerates the README images from it.
- **Test suite** of 105 tests covering parsing, the store and its migration, client
  retry and pagination behaviour, rendering and the CLI. No credentials or network
  access required.
- **CI** running ruff, pytest on Python 3.9–3.13, and an end-to-end job that
  asserts the demo generator is byte-for-byte reproducible and the rendered
  dashboard carries no external reference.

### Fixed

- Prompt text containing `</script>` truncated the dashboard. An HTML parser ends a
  script element at the first literal occurrence regardless of JavaScript quoting,
  and the payload was embedded with a plain `json.dumps`, which does not escape
  angle brackets. Everything below the header was parsed as text and the page came
  up blank. Angle brackets and ampersands are now emitted as `\uXXXX` escapes,
  which JSON decodes back to the original characters.

### Known limitations

- The dashboard embeds every row and recomputes in the browser, which makes it the
  binding constraint on volume — comfortable to roughly 50,000 model calls, beyond
  which the database is still fast but the page is not. See "Limits" in the README.
- Model calls from custom agents arrive without a user, because Gemini Enterprise
  does not propagate trace context into them. Token totals are unaffected; only the
  per-user breakdown is. See "Attribution coverage" in the README.
- Cloud Trace and the `_Default` log bucket retain 30 days. Nothing older can be
  collected retrospectively.

[1.0.0]: https://github.com/abdulzedan/ge-usage-analytics/releases/tag/v1.0.0
