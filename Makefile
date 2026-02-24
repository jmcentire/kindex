.PHONY: install dev test lint clean help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install kindex (kin CLI)
	pip install -e .

dev: ## Install with dev + LLM dependencies
	pip install -e ".[dev,llm]"

test: ## Run test suite
	python -m pytest tests/ -x -q

test-verbose: ## Run tests with full output
	python -m pytest tests/ -v

lint: ## Run basic checks
	python -m py_compile src/kindex/cli.py
	python -m py_compile src/kindex/store.py
	python -m py_compile src/kindex/config.py

clean: ## Remove build artifacts
	rm -rf build/ dist/ *.egg-info src/*.egg-info .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

version: ## Show current version
	@python -c "from kindex import __version__; print(__version__)"
