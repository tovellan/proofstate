.PHONY: audit build example gate lint preflight test

preflight:
	python3 scripts/check_repository.py
	python3 scripts/check_distribution.py --source-only

lint: preflight
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src/proofstate tests

test: preflight
	uv run pytest --cov=proofstate --cov-report=term-missing

build: preflight
	uv build
	python3 scripts/check_distribution.py dist

audit: preflight
	@set -eu; \
	audit_requirements=$$(mktemp); \
	trap 'rm -f "$$audit_requirements"' EXIT HUP INT TERM; \
	uv export --quiet --frozen --no-dev --no-emit-project --format requirements-txt --output-file "$$audit_requirements"; \
	uv run pip-audit --requirement "$$audit_requirements" --disable-pip --require-hashes

example: preflight
	uv run python examples/basic/run.py

gate: preflight
	bash scripts/local_gate.sh
