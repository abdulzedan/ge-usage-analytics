PYTHON ?= python3
PROJECT ?= $(GE_PROJECT_ID)
DAYS ?= 30

.PHONY: help
help:
	@echo "collect     pull telemetry into usage.db      (PROJECT=... DAYS=30)"
	@echo "stats       print the per-user summary"
	@echo "dashboard   render dashboard.html and open it"
	@echo "serve       render and serve on 127.0.0.1:8777"
	@echo "demo        build a synthetic demo.db and open its dashboard"
	@echo "test        run the test suite"
	@echo "lint        run ruff"
	@echo "check       lint + test"
	@echo "clean       remove generated databases, dashboards and caches"

.PHONY: collect
collect:
	$(PYTHON) ge_usage.py collect --project $(PROJECT) --days $(DAYS)

.PHONY: stats
stats:
	$(PYTHON) ge_usage.py stats

.PHONY: dashboard
dashboard:
	$(PYTHON) ge_usage.py dashboard --open

.PHONY: serve
serve:
	$(PYTHON) ge_usage.py serve --open

.PHONY: demo
demo:
	$(PYTHON) tools/make_demo_db.py --out demo.db
	$(PYTHON) ge_usage.py --db demo.db dashboard --out demo-dashboard.html --project demo-project --open

.PHONY: test
test:
	$(PYTHON) -m pytest

.PHONY: lint
lint:
	$(PYTHON) -m ruff check .

.PHONY: check
check: lint test

.PHONY: clean
clean:
	rm -f usage.db demo.db dashboard.html demo-dashboard.html collect.log
	rm -rf __pycache__ tests/__pycache__ .pytest_cache .ruff_cache
