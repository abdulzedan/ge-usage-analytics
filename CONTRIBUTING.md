# Contributing

This project demonstrates how to collect and inspect Gemini Enterprise logs and
traces. It is intended for local experiments, not production use.

## Run the checks

```bash
pip install '.[dev]'
make check          # ruff + pytest
```

The tests mock Google Cloud calls, so they need no credentials or network access.
CI runs the same checks on Python 3.9 to 3.13.

## Constraints

**Use the standard library at runtime.** Keep dependencies for development and
`tools/` scripts in optional extras, such as Pillow for `capture_screenshots.py`.

**Do not commit collected data.** `usage.db` and `dashboard.html` contain email
addresses and prompt text. They are gitignored, and CI rejects them if tracked.
Check exports and files saved under other names too.

**Keep the dashboard offline.** Embed its data, styles, and charts in the HTML.
Tests and CI check for external references.

**Preserve the wrapper-span rule.** In `collector.py`, remove a span only when a
direct child reports the same token counts. Other nested calls can consume tokens
separately and must remain. The collector tests cover these cases.

**Match users by exact identifiers.** Attribution uses a matching trace ID or
session ID from the source data. Do not assign users by timing alone or other
guesses. Calls without a match stay `(unattributed)`.

**Migrate table changes.** Views are rebuilt on every connection. New table
columns need an entry in `_ADDED_COLUMNS` so existing databases receive them
before a view uses them.

**Keep timestamps in UTC.** The database compares ISO-8601 strings directly and
uses `substr()` to derive `day` and `hour`.

## Screenshots

Generated from synthetic data only:

```bash
pip install '.[docs]'                        # Pillow; also needs Chrome
python3 tools/make_demo_db.py --out demo.db
python3 tools/capture_screenshots.py --db demo.db
```

The generator produces repeatable data. If a screenshot cuts through a card,
adjust `--splits`.

## Commits

Use a conventional-commit subject, such as `docs: clarify demo setup`, and explain
the reason for the change in the body.
