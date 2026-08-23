#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required" >&2
  exit 1
}
command -v gitleaks >/dev/null 2>&1 || {
  echo "gitleaks is required" >&2
  exit 1
}

gate_tmp=$(mktemp -d)
cleanup() {
  rm -rf -- "$gate_tmp"
}
trap cleanup EXIT

python3 scripts/check_repository.py
python3 scripts/check_distribution.py --source-only
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src/proofstate tests
uv run pytest --cov=proofstate --cov-report=term-missing
uv build --out-dir "$gate_tmp/dist"
python3 scripts/check_distribution.py "$gate_tmp/dist"
python3 scripts/verify_release.py --dist "$gate_tmp/dist"

uv export --quiet \
  --frozen \
  --no-dev \
  --no-emit-project \
  --format requirements-txt \
  --output-file "$gate_tmp/requirements.txt"
uv run pip-audit \
  --requirement "$gate_tmp/requirements.txt" \
  --disable-pip \
  --require-hashes

wheel=$(find "$gate_tmp/dist" -maxdepth 1 -type f -name 'proofstate-*.whl' -print -quit)
sdist=$(find "$gate_tmp/dist" -maxdepth 1 -type f -name 'proofstate-*.tar.gz' -print -quit)
test -n "$wheel"
test -n "$sdist"
uv venv --python 3.11 "$gate_tmp/wheel-venv"
uv pip install --python "$gate_tmp/wheel-venv/bin/python" "$wheel"
"$gate_tmp/wheel-venv/bin/proofstate" --version
"$gate_tmp/wheel-venv/bin/proofstate" conformance
"$gate_tmp/wheel-venv/bin/proofstate" conformance --export "$gate_tmp/wheel-conformance"
"$gate_tmp/wheel-venv/bin/python" examples/basic/run.py

uv venv --python 3.11 "$gate_tmp/sdist-venv"
uv pip install --python "$gate_tmp/sdist-venv/bin/python" "$sdist"
"$gate_tmp/sdist-venv/bin/proofstate" --version
"$gate_tmp/sdist-venv/bin/proofstate" conformance
"$gate_tmp/sdist-venv/bin/proofstate" conformance --export "$gate_tmp/sdist-conformance"
"$gate_tmp/sdist-venv/bin/python" examples/basic/run.py

git diff --check
gitleaks git --redact --no-banner .

echo "local release gate passed"
