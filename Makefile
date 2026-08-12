COMPOSE := docker compose -f deploy/docker-compose.yml

# Prefer a project virtualenv if one exists, so `make demo` works without the
# reader having activated anything.
PY := $(shell test -x .venv/bin/python && echo .venv/bin/python || command -v python3 || echo python)

# Credentials for the throwaway demo database, matching deploy/docker-compose.yml.
# They live here rather than as defaults in config.py: a password baked into
# application code is a password someone eventually ships. The container is
# ephemeral and holds nothing but generated data.
DEMO_ENV := DB_HOST=localhost DB_PORT=5432 DB_NAME=cybersum DB_USER=cybersum DB_PASS=cybersum

.DEFAULT_GOAL := help
.PHONY: help demo db-up db-wait seed report demo-down test test-all lint typecheck check scan record

help:  ## Show available targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ── demo ─────────────────────────────────────────────────────────────────────

demo: db-up db-wait seed report  ## Clone to briefing, no API key needed

db-up:
	@$(COMPOSE) up -d

db-wait:
	@printf 'waiting for postgres'
	@until $(COMPOSE) exec -T postgres pg_isready -U cybersum -q 2>/dev/null; do \
		printf '.'; sleep 1; done
	@echo ' ready'

seed:  ## Write one scenario window (window 4: service paused, DDoS critical)
	@$(DEMO_ENV) $(PY) -m cybersum.cli seed --windows 4 --trend-days 14 --seed 42

seed-all:  ## Write all five windows with full history (for the experiment)
	@$(DEMO_ENV) $(PY) -m cybersum.cli seed --trend-days 90 --seed 42

report:  ## Generate a briefing beside the facts it was given
	@$(DEMO_ENV) $(PY) -m cybersum.cli daily-report --window 4 --show-ground-truth

record:  ## Re-record the offline cassette (needs OPENAI_API_KEY)
	@$(DEMO_ENV) $(PY) -m cybersum.cli record --window 4

demo-down:  ## Stop the database and discard its data
	@$(COMPOSE) down -v

# ── checks ───────────────────────────────────────────────────────────────────

test:  ## Unit tests: no database, no network
	@$(PY) -m pytest tests -q

test-all: db-up db-wait  ## Unit and integration tests
	@$(DEMO_ENV) $(PY) -m pytest tests -q -m ""

lint:
	@$(PY) -m ruff check src tests adapters evaluation

typecheck:
	@$(PY) -m mypy src/cybersum

scan:  ## Fail if any internal identifier reached the tree
	@$(PY) -m pytest tests/unit/test_no_internal_identifiers.py -q

check: lint typecheck test scan  ## Everything CI runs
