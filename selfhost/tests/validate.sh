#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
TEMPORARY=$(mktemp -d)
trap 'rm -rf "$TEMPORARY"' EXIT HUP INT TERM

sh -n "$ROOT/selfhost/bin/assozeta"
sh -n "$ROOT/selfhost/bin/update"
sh -n "$ROOT/selfhost/bin/prepare-storage"
sh -n "$ROOT/selfhost/tests/storage-smoke.sh"
sh -n "$ROOT/selfhost/tests/quality.sh"
sh -n "$ROOT/selfhost/tests/smoke.sh"
sh -n "$ROOT/selfhost/tests/production-smoke.sh"
sh -n "$ROOT/run_tests.sh"

if grep -Eq 'prod_compose up -d --wait( --force-recreate)? api worker beat web' "$ROOT/selfhost/bin/assozeta"; then
    printf 'Production lifecycle must not wait on worker/beat without health checks.\n' >&2
    exit 1
fi

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
python3 -m compileall -q "$ROOT/selfhost/updater"
python3 -m unittest discover -s "$ROOT/selfhost/tests" -p test_updater.py
python3 -m unittest discover -s "$ROOT/selfhost/tests" -p test_restart.py
python3 -m unittest discover -s "$ROOT/selfhost/tests" -p test_maintenance_lifecycle.py
python3 -m unittest discover -s "$ROOT/selfhost/tests" -p test_upgrade_preflight.py
python3 -m unittest discover -s "$ROOT/selfhost/tests" -p test_quality.py
python3 -m unittest discover -s "$ROOT/selfhost/tests" -p test_sync_repo.py
python3 -m unittest discover -s "$ROOT/selfhost/tests" -p test_operational_diagnostics.py

ASSOZETA_SELFHOST_DIR="$ROOT/selfhost" ASSOZETA_ENV_FILE="$TEMPORARY/prod.env" \
    ASSOZETA_UPDATER_API_VOLUME=assozeta_updater_api ASSOZETA_UPDATER_STATUS_VOLUME=assozeta_updater_status \
    docker compose --env-file "$TEMPORARY/prod.env" -f "$ROOT/selfhost/compose.updater.yml" \
    --project-name assozeta-updater config --format json | python3 -c '
import json, sys
config = json.load(sys.stdin)
assert config["name"] == "assozeta-updater"
assert config["volumes"]["updater_api"]["name"] == "assozeta_updater_api"
assert config["volumes"]["updater_api"]["external"]
assert config["volumes"]["updater_status"]["name"] == "assozeta_updater_status"
assert config["volumes"]["updater_status"]["external"]
'

ASSOZETA_ENV_FILE="$TEMPORARY/prod.env" \
    docker compose --env-file "$TEMPORARY/prod.env" -f "$ROOT/selfhost/compose.yml" config --format json | python3 -c '
import json, sys
config = json.load(sys.stdin)
for service in ("web", "api"):
    assert all("docker.sock" not in mount["target"] for mount in config["services"][service]["volumes"])
web = config["services"]["web"]["volumes"]
assert any(mount["source"] == "updater_status" and mount["read_only"] for mount in web)
assert not any(mount["source"] == "updater_api" for mount in web)
'

printf 'Self-host validation and updater regression tests passed.\n'

python3 -m unittest discover -s "$ROOT/selfhost/tests" -p test_storage_images.py
