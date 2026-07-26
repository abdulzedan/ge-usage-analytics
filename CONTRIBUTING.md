# Contributing

## Getting set up

```bash
pip install '.[dev]'
make check          # ruff + pytest
```

The tool itself has **no runtime dependencies** and must stay that way. It is
written against the standard library so the directory can be copied onto a
locked-down reporting host and run in place, with no index access and no build
step. A pull request that adds an install-time dependency to the collection or
rendering path will not be accepted; put it behind an optional extra in `tools/`
instead, as `capture_screenshots.py` does with Pillow.

Everything runs offline. `urlopen` is replaced throughout the tests and the Google
Cloud calls are stubbed, so no credentials, project or network access are needed to
develop or to run CI.

## Before opening a pull request

```bash
make check
```

CI runs the same checks on Python 3.9 through 3.13, plus an end-to-end job that
builds a synthetic database, renders it, and asserts that the generator is
byte-for-byte reproducible and the output carries no external reference.

## Things worth knowing

**Never commit collected data.** `usage.db` and `dashboard.html` contain real email
addresses and real prompt text. They are gitignored, and CI fails if either is ever
tracked — but `git add -f` will still get past the first of those, so be careful.

**Screenshots come from synthetic data only.** If you change the dashboard and want
to refresh the images:

```bash
pip install '.[docs]'                              # Pillow; also needs Chrome
python3 tools/make_demo_db.py --out demo.db
python3 tools/capture_screenshots.py --db demo.db
```

The generator is deterministic, so a regenerated image differs only where the
rendering actually changed. If the layout moves enough that a crop lands mid-card,
adjust `--splits`.

**Wrapper-span removal is load-bearing.** The rule in `collector.py` is "a *direct
child* reports the *same* token counts", and it is narrow on purpose. Broadening it
to "is an ancestor of a token-bearing span" looks like a simplification and is a
bug: under agent-as-tool nesting, an outer `generate_content` contains a genuinely
different inner call, and both sets of tokens were really consumed. Four tests pin
this down; if you find yourself editing them to make a change pass, stop and
re-read them.

**The dashboard must stay self-contained.** No CDN, no external stylesheet, no
fetch. That property is the reason the format is useful, and both a unit test and a
CI step assert it.

**Views are rebuilt on every connect; tables are not.** Changing a view definition
in `store.py` needs nothing else. Adding a *column* needs an entry in
`_ADDED_COLUMNS` so existing databases get it via `ALTER TABLE` before any view
references it.

**Timestamps are ISO-8601 UTC strings** compared lexicographically. That is why
`day` and `hour` are `substr()` expressions rather than date functions.

## Commit messages

Conventional-commit subject line, then a body explaining *why* rather than what.
The diff already covers what.
