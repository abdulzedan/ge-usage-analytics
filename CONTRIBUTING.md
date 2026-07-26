# Contributing

```bash
pip install '.[dev]'
make check          # ruff + pytest
```

Everything runs offline. `urlopen` is replaced in the tests and the Google Cloud
calls are stubbed, so no credentials or network access are needed. CI runs the
same checks on Python 3.9 to 3.13.

## Constraints

**No runtime dependencies.** Standard library only, so the directory can be
copied onto a locked-down host and run in place. Anything extra goes behind an
optional extra in `tools/`, as `capture_screenshots.py` does with Pillow.

**Never commit collected data.** `usage.db` and `dashboard.html` hold real email
addresses and prompt text. Both are gitignored and CI fails if either is
tracked, but `git add -f` still gets past the first of those.

**The dashboard stays self-contained.** No CDN, no external stylesheet, no
fetch. A unit test and a CI step both assert it.

**Wrapper-span removal is load-bearing.** The rule in `collector.py` is "a
*direct child* reports the *same* token counts". Broadening it to "is an
ancestor of a token-bearing span" looks like a simplification and is a bug:
under agent-as-tool nesting the outer call is a different call, and both sets of
tokens were really consumed. Four tests pin this down.

**Views are rebuilt on every connect; tables are not.** Changing a view needs
nothing else. Adding a column needs an entry in `_ADDED_COLUMNS` so existing
databases get it before any view references it.

**Timestamps are ISO-8601 UTC strings** compared lexicographically, which is why
`day` and `hour` are `substr()` expressions.

## Screenshots

Generated from synthetic data only:

```bash
pip install '.[docs]'                        # Pillow; also needs Chrome
python3 tools/make_demo_db.py --out demo.db
python3 tools/capture_screenshots.py --db demo.db
```

The generator is deterministic, so a regenerated image differs only where the
rendering changed. If a crop lands mid-card, adjust `--splits`.

## Commits

Conventional-commit subject, then a body explaining why.
