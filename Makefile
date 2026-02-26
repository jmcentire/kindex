.PHONY: install dev test test-verbose test-coverage lint check clean docs help all

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

all: install test ## Install and run tests

install: ## Install kindex (kin CLI)
	pip install -e .

dev: ## Install with dev + LLM dependencies
	pip install -e ".[dev,llm]"

test: ## Run test suite
	python -m pytest tests/ -x -q

test-verbose: ## Run tests with full output
	python -m pytest tests/ -v

test-coverage: ## Run tests with coverage report
	python -m pytest tests/ --cov=kindex --cov-report=term-missing -q

lint: ## Run basic checks
	python -m py_compile src/kindex/cli.py
	python -m py_compile src/kindex/store.py
	python -m py_compile src/kindex/config.py
	python -m py_compile src/kindex/extract.py
	python -m py_compile src/kindex/ingest.py
	python -m py_compile src/kindex/analytics.py
	python -m py_compile src/kindex/reminders.py
	python -m py_compile src/kindex/notify.py

check: lint test ## Lint + test combined

clean: ## Remove build artifacts
	rm -rf build/ dist/ *.egg-info src/*.egg-info .pytest_cache .coverage htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

docs: ## Open landing page in browser
	open docs/index.html 2>/dev/null || xdg-open docs/index.html 2>/dev/null || echo "Open docs/index.html in your browser"

version: ## Show current version
	@python -c "from kindex import __version__; print(__version__)"
