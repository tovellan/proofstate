.PHONY: audit build example gate lint test

lint:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src/proofstate tests
	python3 scripts/check_repository.py

test:
	uv run pytest --cov=proofstate --cov-report=term-missing

build:
	uv build
	python3 scripts/check_distribution.py dist

audit:
	uv export --quiet --frozen --no-dev --no-emit-project --format requirements-txt | uv run pip-audit --requirement - --disable-pip --require-hashes

example:
	uv run python examples/basic/run.py

gate:
	bash scripts/local_gate.sh
