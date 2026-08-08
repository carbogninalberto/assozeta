#!/bin/sh
set -eu

SELF=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ASSOZETA="$SELF/selfhost/bin/assozeta"

use_coverage=true
use_parallel=false
open_report=false
pytest_args=""

while [ $# -gt 0 ]; do
    case "$1" in
        --help|-h)
            cat <<'USAGE'
Usage: ./run_tests.sh [OPTIONS] [PYTEST_ARGS...]

Run the backend test suite inside the Docker Compose development environment.
Requires Docker Compose and the development images built (make dev or
make dev-rebuild after adding test dependencies).

Options:
  --parallel, -n     Run tests in parallel using pytest-xdist (-n auto)
  --no-coverage      Disable coverage collection
  --open             Open HTML coverage report after tests (macOS)
  -k EXPR            Filter tests by expression (pytest -k)
  -v, -vv, -vvv      Increase verbosity
  --help             Show this help

Extra positional arguments are forwarded to pytest. Use filesystem-style paths
(e.g. application/tests/test_auth_login.py).

Examples:
  ./run_tests.sh
  ./run_tests.sh --parallel
  ./run_tests.sh -k test_login
  ./run_tests.sh --no-coverage -v application/tests/test_auth_login.py
  ./run_tests.sh instance/tests/
  ./run_tests.sh --open
  make dev-test                # Convenience: runs tests then UI CSS verify
USAGE
            exit 0
            ;;
        --parallel|-n)
            use_parallel=true
            shift
            ;;
        --no-coverage)
            use_coverage=false
            shift
            ;;
        --open)
            open_report=true
            shift
            ;;
        -k)
            pytest_args="$pytest_args -k \"$2\""
            shift 2
            ;;
        -v|-vv|-vvv)
            pytest_args="$pytest_args $1"
            shift
            ;;
        --)
            shift
            while [ $# -gt 0 ]; do
                pytest_args="$pytest_args $1"
                shift
            done
            break
            ;;
        *)
            pytest_args="$pytest_args $1"
            shift
            ;;
    esac
done

if [ "$use_coverage" = true ]; then
    pytest_args="--cov --cov-report=term $pytest_args"
    if [ "$open_report" = true ]; then
        pytest_args="--cov-report=html $pytest_args"
    fi
fi

if [ "$use_parallel" = true ]; then
    pytest_args="-n auto $pytest_args"
fi

printf '[run_tests] ensuring development environment is ready\n'
"$ASSOZETA" dev-compose up -d --wait postgres redis minio >/dev/null

printf '[run_tests] running tests\n'
"$ASSOZETA" dev-compose run --rm api sh -c "python -m pytest $pytest_args"
exit_code=$?

if [ "$open_report" = true ] && [ "$use_coverage" = true ] && [ "$exit_code" -eq 0 ]; then
    if command -v open >/dev/null 2>&1; then
        open "$SELF/BE/htmlcov/index.html" 2>/dev/null || true
    fi
fi

exit "$exit_code"
