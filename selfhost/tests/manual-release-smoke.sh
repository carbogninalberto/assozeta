#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
exec python3 "$ROOT/selfhost/tests/manual-release-smoke.py" "$@"
