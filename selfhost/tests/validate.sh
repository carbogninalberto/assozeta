#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
TEMPORARY=$(mktemp -d)
trap 'rm -rf "$TEMPORARY"' EXIT HUP INT TERM

sh -n "$ROOT/selfhost/bin/assozeta"
sh -n "$ROOT/selfhost/tests/smoke.sh"
sh -n "$ROOT/selfhost/tests/production-smoke.sh"

ASSOZETA_ENV_FILE="$TEMPORARY/prod.env" \
    "$ROOT/selfhost/bin/assozeta" configure --domain localhost --version test
ASSOZETA_DEV_ENV_FILE="$TEMPORARY/dev.env" \
    "$ROOT/selfhost/bin/assozeta" dev-config

COMPOSE_PROJECT_NAME=conflicting-project HTTP_PORT=59999 DBPASSWORD=conflicting-password \
    ASSOZETA_ENV_FILE="$TEMPORARY/prod.env" \
    "$ROOT/selfhost/bin/assozeta" validate

COMPOSE_PROJECT_NAME=conflicting-project DEV_UI_PORT=59999 DBPASSWORD=conflicting-password \
    ASSOZETA_DEV_ENV_FILE="$TEMPORARY/dev.env" \
    "$ROOT/selfhost/bin/assozeta" dev-compose --profile tools config --quiet

python3 -m compileall -q "$ROOT/BE"

printf 'Static self-host validation passed.\n'
