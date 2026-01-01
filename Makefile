# Makefile for Python Quality Framework

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip

# Default target
.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "Available targets:"
	@echo "  install        Install project dependencies"
	@echo "  dev            Install dev dependencies (if using requirements-dev.txt)"
	@echo "  lint           Run Ruff linting"
	@echo "  format         Run Ruff formatter"
	@echo "  typecheck      Run mypy type checking"
	@echo "  test           Run pytest"
	@echo "  coverage       Run tests with coverage and show report"
	@echo "  radon          Run radon complexity checks"
	@echo "  bandit         Run bandit security checks"
	@echo "  quality        Run lint, typecheck, radon, bandit, and tests"
	@echo "  check          Alias for 'quality'"
	@echo "  pre-commit     Run pre-commit on all files"
	@echo "  hooks          Install pre-commit git hooks"
	@echo "  ci             Run CI-style pipeline locally"


# ----------------------------
# Installation
# ----------------------------

.PHONY: install
install:
	$(PIP) install -r requirements.txt

.PHONY: dev
dev:
	@if [ -f requirements-dev.txt ]; then \
	  $(PIP) install -r requirements-dev.txt; \
	else \
	  echo "requirements-dev.txt not found, skipping dev install"; \
	fi


# ----------------------------
# Quality Checks (wrapped scripts)
# ----------------------------

.PHONY: lint
lint:
	./scripts/lint.sh

.PHONY: typecheck
typecheck:
	./scripts/typecheck.sh

.PHONY: radon
radon:
	./scripts/run_radon.sh

.PHONY: test
test:
	./scripts/test.sh

.PHONY: coverage
coverage:
	PYTHONPATH=. coverage run -m pytest
	coverage report


# ----------------------------
# Security & Formatting
# ----------------------------

.PHONY: bandit
bandit:
	bandit -q -r app

.PHONY: format
format:
	ruff format .


# ----------------------------
# Aggregated Gates
# ----------------------------

.PHONY: quality
quality: lint typecheck radon bandit test

.PHONY: check
check: quality

.PHONY: ci
ci: quality coverage


# ----------------------------
# Pre-commit
# ----------------------------

.PHONY: pre-commit
pre-commit:
	pre-commit run --all-files

.PHONY: hooks
hooks:
	pre-commit install
