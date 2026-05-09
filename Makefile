UV ?= uv
PYTHON ?= 3.12
IMAGE ?= bedrock-strands-agent:dev
PORT ?= 8000

.DEFAULT_GOAL := help

.PHONY: help install lock sync run dev test cov lint format typecheck scan audit \
        hooks load-test clean docker-build docker-run check ci

help: ## Show this help message
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install Python and sync all extras + dev group (frozen lockfile)
	$(UV) python install $(PYTHON)
	$(UV) sync --all-extras --all-groups --frozen

lock: ## Refresh uv.lock
	$(UV) lock

sync: ## Sync dependencies from uv.lock (no resolve)
	$(UV) sync --all-extras --all-groups --frozen

run: ## Start the FastAPI server (production mode)
	$(UV) run python -m bedrock_strands_agent serve

dev: ## Start the server with auto-reload
	$(UV) run uvicorn bedrock_strands_agent.api.app:create_app \
		--factory --host 0.0.0.0 --port $(PORT) --reload

test: ## Run tests
	$(UV) run pytest

cov: ## Run tests with HTML coverage report
	$(UV) run pytest --cov-report=html

lint: ## Ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Apply ruff format and lint --fix
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## Run mypy strict
	$(UV) run mypy

scan: ## Bandit security scan
	$(UV) run bandit -c pyproject.toml -r src

hooks: ## Install + run pre-commit on all files (parity with the CI pre-commit job)
	$(UV) run pre-commit install --install-hooks
	$(UV) run pre-commit run --all-files

audit: ## pip-audit against the synced venv, skipping the editable project itself
	# Auditing the live venv (instead of -r requirements.txt) avoids:
	#   * "Dependency not found on PyPI" for the editable project
	#   * pip-audit's internal venv creation, which can fail bootstrapping
	#     ensurepip on uv-managed Python builds
	# --skip-editable drops the editable project from the audit set.
	$(UV) run pip-audit --skip-editable --progress-spinner=off

load-test: ## Run k6 load test (set BASE_URL/API_KEY for non-localhost)
	docker run --rm -i \
		-e BASE_URL=$${BASE_URL:-http://host.docker.internal:8000} \
		-e API_KEY=$${API_KEY:-} \
		-v $(PWD)/tests/load:/scripts \
		grafana/k6 run /scripts/extract.js

clean: ## Remove caches and build artefacts
	rm -rf .ruff_cache .mypy_cache .pytest_cache .coverage coverage.xml htmlcov \
	       build dist *.egg-info src/*.egg-info requirements.audit.txt

docker-build: ## Build container image
	docker build -t $(IMAGE) .

docker-run: ## Run container locally
	docker run --rm -p $(PORT):8000 --env-file .env $(IMAGE)

check: lint typecheck test scan hooks ## Full local quality gate

ci: install check audit ## CI-equivalent run
