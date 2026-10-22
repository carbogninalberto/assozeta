#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
CLI="$ROOT/selfhost/bin/assozeta"
TEMPORARY=$(mktemp -d)
ENV_FILE="$TEMPORARY/production.env"

export COMPOSE_PROJECT_NAME=assozeta-conflicting-export
export HTTP_PORT=59999
export HTTPS_PORT=59998
export DBPASSWORD=conflicting-db-password
export ASSOZETA_BACKEND_IMAGE=invalid.example.invalid/conflicting-backend
export ASSOZETA_BACKEND_GO_IMAGE=invalid.example.invalid/conflicting-go-backend
export ASSOZETA_WEB_IMAGE=invalid.example.invalid/conflicting-web
export ASSOZETA_RENDERER_IMAGE=invalid.example.invalid/conflicting-renderer
export POSTGRES_IMAGE=invalid.example.invalid/conflicting-postgres

phase() {
    printf '\n=== %s ===\n' "$1" >&2
}

remove_temporary_directory() {
    if [ -f "$ENV_FILE" ]; then
        # A Compose one-off after uninstall can recreate the project's network
        # and volumes. Use only its resolved image for this final file cleanup.
        cleanup_image=$(compose --profile tools config --format json | python3 -c \
            'import json,sys; print(json.load(sys.stdin)["services"]["minio-init"]["image"])') || cleanup_image=
        if [ -n "$cleanup_image" ]; then
            docker run --rm --network none --user 0 \
                -v "$TEMPORARY:/cleanup:rw,z" --entrypoint /bin/sh \
                "$cleanup_image" -c 'rm -rf /cleanup' >/dev/null 2>&1 || true
        fi
    fi
    rm -rf "$TEMPORARY"
}

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "$status" -ne 0 ] && [ -f "$ENV_FILE" ]; then
        printf '\n=== production smoke failure diagnostics ===\n' >&2
        compose ps >&2 || true
        compose logs --no-color api api-go web worker beat postgres redis minio renderer >&2 || true
    fi
    if [ -f "$ENV_FILE" ]; then
        ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" uninstall --volumes --yes >/dev/null 2>&1 || true
    fi
    remove_temporary_directory
    exit "$status"
}
trap cleanup EXIT HUP INT TERM

set_value() {
    key=$1
    value=$2
    temporary="$ENV_FILE.tmp"
    awk -v key="$key" -v value="$value" '
        index($0, key "=") == 1 { print key "=" value; next }
        { print }
    ' "$ENV_FILE" > "$temporary"
    mv "$temporary" "$ENV_FILE"
}

env_value() {
    key=$1
    awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1); exit }' "$ENV_FILE"
}

compose() {
    (
        while IFS='=' read -r name _; do
            case "$name" in
                ''|[0-9]*|*[!A-Za-z0-9_]*) ;;
                *) unset "$name" ;;
            esac
        done < "$ENV_FILE"

        ASSOZETA_ENV_FILE=$ENV_FILE; export ASSOZETA_ENV_FILE
        docker compose --env-file "$ENV_FILE" -f "$ROOT/selfhost/compose.yml" "$@"
    )
}

curl_ready() {
    for api_prefix in /api /api/v1 /api/v2; do
        curl --fail --silent --show-error --retry 20 --retry-all-errors --retry-delay 1 \
            "http://localhost:58080$api_prefix/readyz" >/dev/null
    done
}

go_probe() {
    python3 "$ROOT/selfhost/tests/go-production-probe.py" "$@" \
        --env-file "$ENV_FILE" --login-json "$TEMPORARY/owner-login.json"
}

db_sql() {
    sql=$1
    compose exec -T postgres psql \
        -U "$(env_value DBUSER)" \
        -d "$(env_value DBNAME)" \
        -v ON_ERROR_STOP=1 \
        -c "$sql" >/dev/null
}

db_query() {
    sql=$1
    compose exec -T postgres psql \
        -U "$(env_value DBUSER)" \
        -d "$(env_value DBNAME)" \
        -v ON_ERROR_STOP=1 \
        -At \
        -c "$sql"
}

assert_go_operational_schema() {
    actual=$(db_query "SELECT version || ':' || dirty FROM public.assozeta_go_schema_migrations;")
    [ "$actual" = '2:false' ] || {
        printf 'Expected clean Go operational schema 2, got %s.\n' "$actual" >&2
        exit 1
    }
    actual=$(db_query "SELECT runtime || ':' || generation FROM public.assozeta_go_job_ownership WHERE singleton=1;")
    [ "$actual" = 'django:1' ] || {
        printf 'Ordinary lifecycle changed default Django execution ownership: %s.\n' "$actual" >&2
        exit 1
    }
}

go_intent_digest() {
    db_query "SELECT md5(row_to_json(j)::text) FROM public.assozeta_go_jobs j WHERE id='11111111-1111-4111-8111-111111111111';"
}

assert_go_intent() {
    expected=$1
    actual=$(go_intent_digest)
    [ -n "$actual" ] && [ "$actual" = "$expected" ] || {
        printf 'Lifecycle did not preserve the exact Go operational intent.\n' >&2
        exit 1
    }
    assert_go_operational_schema
}

write_db_sentinel() {
    value=$1
    db_sql "CREATE TABLE IF NOT EXISTS selfhost_smoke_sentinel (id integer PRIMARY KEY, value text NOT NULL); INSERT INTO selfhost_smoke_sentinel (id, value) VALUES (1, '$value') ON CONFLICT (id) DO UPDATE SET value = EXCLUDED.value;"
}

read_db_sentinel() {
    db_query "SELECT value FROM selfhost_smoke_sentinel WHERE id = 1;"
}

assert_db_sentinel() {
    expected=$1
    actual=$(read_db_sentinel)
    [ "$actual" = "$expected" ] || {
        printf 'Expected DB sentinel %s, got %s.\n' "$expected" "$actual" >&2
        exit 1
    }
}

write_object_sentinel() {
    value=$1
    printf '%s' "$value" > "$TEMPORARY/object-sentinel"
    compose --profile tools run --rm -T \
        -v "$TEMPORARY/object-sentinel:/sentinel:ro,z" \
        --entrypoint /bin/sh \
        minio-init -c \
        'mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc cp /sentinel "local/$AWS_STORAGE_BUCKET_NAME/smoke/sentinel.txt" >/dev/null' >/dev/null
}

read_object_sentinel() {
    rm -f "$TEMPORARY/object-read"
    compose --profile tools run --rm -T \
        -v "$TEMPORARY:/smoke:z" \
        --entrypoint /bin/sh \
        minio-init -c \
        'mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc cp "local/$AWS_STORAGE_BUCKET_NAME/smoke/sentinel.txt" /smoke/object-read >/dev/null' >/dev/null
    cat "$TEMPORARY/object-read"
}

assert_object_sentinel() {
    expected=$1
    actual=$(read_object_sentinel)
    [ "$actual" = "$expected" ] || {
        printf 'Expected object sentinel %s, got %s.\n' "$expected" "$actual" >&2
        exit 1
    }
}

create_corrupt_db_archive() {
    source_archive=$1
    corrupt_archive=$2
    corrupt_directory="$TEMPORARY/corrupt-archive"

    rm -rf "$corrupt_directory"
    mkdir -p "$corrupt_directory"
    tar -xzf "$source_archive" -C "$corrupt_directory"
    printf '%s\n' 'not a PostgreSQL custom-format dump' > "$corrupt_directory/postgres.dump"
    tar -C "$corrupt_directory" -czf "$corrupt_archive" .
    rm -rf "$corrupt_directory"
}

create_corrupt_object_archive() {
    source_archive=$1
    corrupt_archive=$2
    corrupt_directory="$TEMPORARY/corrupt-object-archive"

    rm -rf "$corrupt_directory"
    mkdir -p "$corrupt_directory"
    tar -xzf "$source_archive" -C "$corrupt_directory"
    rm -rf "$corrupt_directory/objects"
    printf '%s\n' 'not an object-storage directory' > "$corrupt_directory/objects"
    printf '%s\n' 'DBNAME=archive-config-must-not-be-trusted' > "$corrupt_directory/config.env"
    tar -C "$corrupt_directory" -czf "$corrupt_archive" .
    rm -rf "$corrupt_directory"
}

phase "configure production environment"
ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" configure --domain localhost:58080 --version test
set_value COMPOSE_PROJECT_NAME assozeta-production-smoke
set_value ASSOZETA_BACKEND_IMAGE assozeta-backend
set_value ASSOZETA_BACKEND_GO_IMAGE assozeta-backend-go
set_value ASSOZETA_WEB_IMAGE assozeta-web
set_value ASSOZETA_RENDERER_IMAGE assozeta-renderer
set_value HTTPS_PORT 58443

[ "$(env_value APP_URL)" = http://localhost:58080 ] || {
    printf 'Expected generated APP_URL to use localhost:58080.\n' >&2
    exit 1
}
[ "$(env_value HTTP_PORT)" = 58080 ] || {
    printf 'Expected generated HTTP_PORT to be 58080.\n' >&2
    exit 1
}

mkdir -p "$ROOT/selfhost/.lifecycle.lock"
if ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" stop; then
    printf 'Expected a stale lifecycle lock to require manual recovery.\n' >&2
    exit 1
fi
rm -rf "$ROOT/selfhost/.lifecycle.lock"

phase "start production stack"
ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" start

phase "verify ordinary startup applies only additive Go operational migrations"
assert_go_operational_schema
django_migration_count=$(db_query 'SELECT count(*) FROM django_migrations;')
[ "$django_migration_count" -gt 0 ]
django_migration_digest=$(db_query 'SELECT md5(json_agg(m ORDER BY id)::text) FROM django_migrations m;')
db_sql "INSERT INTO public.assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at) VALUES ('11111111-1111-4111-8111-111111111111','00000000-0000-0000-0000-000000000000','fixture.lifecycle',1,'production-smoke','{\"checkpoint\":\"before-backup\",\"integer\":9007199254740993}',3,clock_timestamp());"
go_before_backup=$(go_intent_digest)

phase "verify public production endpoints"
compose exec -T api python -c 'import os; from pathlib import Path; assert os.getuid() == 10001; root = Path("/app/manuale"); assert not (root / "development").exists(); (root / ".production-smoke").write_text("persistent-manual")'
compose up -d --wait --force-recreate api
compose exec -T api python -c 'from pathlib import Path; assert Path("/app/manuale/.production-smoke").read_text() == "persistent-manual"'
curl --fail --silent --show-error --retry 20 --retry-all-errors --retry-delay 1 \
    http://localhost:58080/ >/dev/null
curl --fail --silent --show-error --retry 20 --retry-all-errors --retry-delay 1 \
    http://localhost:58080/api/readyz >/dev/null
curl --fail --silent --show-error --retry 20 --retry-all-errors --retry-delay 1 \
    http://localhost:58080/api/instance/status >/dev/null
curl --fail --silent --show-error http://localhost:58080/static/css/bootstrap.min.css >/dev/null
curl --fail --silent --show-error http://localhost:58080/static/rest_framework/css/default.css >/dev/null

phase "run migrations"
ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" migrate
curl_ready
assert_go_intent "$go_before_backup"
[ "$(db_query 'SELECT count(*) FROM django_migrations;')" = "$django_migration_count" ]
[ "$(db_query 'SELECT md5(json_agg(m ORDER BY id)::text) FROM django_migrations m;')" = "$django_migration_digest" ]

phase "reject dirty operational history before resuming application services"
db_sql 'UPDATE public.assozeta_go_schema_migrations SET dirty=true;'
if ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" migrate; then
    printf 'Expected dirty Go operational history to reject lifecycle migration.\n' >&2
    exit 1
fi
for service in api api-go worker beat; do
    [ -z "$(compose ps --status running --services "$service")" ] || {
        printf 'Service %s resumed after failed Go migration.\n' "$service" >&2
        exit 1
    }
done
maintenance_status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' http://localhost:58080/api/oauth2/login)
[ "$maintenance_status" = 503 ]
# Fixture-only repair of the injected dirty flag; the product never forces history.
db_sql 'UPDATE public.assozeta_go_schema_migrations SET dirty=false;'
ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" start
curl_ready
assert_go_intent "$go_before_backup"

phase "configure instance and verify protections"
setup_status=$(curl --silent --show-error --output "$TEMPORARY/unauthenticated-setup-response" --write-out '%{http_code}' \
    --request POST \
    --header 'content-type: application/json' \
    --data '{}' \
    http://localhost:58080/api/instance/configure)
case "$setup_status" in
    401|403) ;;
    *)
        printf 'Expected unauthenticated setup to be denied, got HTTP %s.\n' "$setup_status" >&2
        cat "$TEMPORARY/unauthenticated-setup-response" >&2
        exit 1
        ;;
esac

setup_token=$(awk -F= '$1 == "INSTANCE_SETUP_TOKEN" { print substr($0, index($0, "=") + 1); exit }' "$ENV_FILE")
owner_email=owner-smoke@example.invalid
setup_response="$TEMPORARY/setup-response.json"
setup_status=$(curl --silent --show-error --output "$setup_response" --write-out '%{http_code}' \
    --request POST \
    --header 'content-type: application/json' \
    --header "X-Setup-Token: $setup_token" \
    --data-binary @- \
    http://localhost:58080/api/instance/configure <<EOF
{"domain":"localhost:58080","oem":{"name":"Smoke Club","abbreviation":"SMK","primaryColor":"#351DC2","supportEmail":"$owner_email","logo":""},"oauth":{},"stripe":{},"initialization":{"type":"fresh","associationName":"Smoke Association","ownerEmail":"$owner_email","ownerPassword":"SmokePassword123!"}}
EOF
)
[ "$setup_status" = 200 ] || {
    printf 'Expected first-run setup to succeed, got HTTP %s.\n' "$setup_status" >&2
    cat "$setup_response" >&2 || true
    exit 1
}

owner_count=$(db_query "SELECT count(*) FROM instance_instanceconfiguration cfg JOIN application_sportassociation sa ON cfg.primary_association_id = sa.sport_association_id JOIN bakney_user u ON sa.user_id = u.user_id WHERE cfg.self_hosted IS TRUE AND u.email = '$owner_email';")
[ "$owner_count" = 1 ] || {
    printf 'Expected one configured association owner, got %s.\n' "$owner_count" >&2
    exit 1
}

assert_no_software_subscription() {
    entitlement_count=$(db_query "SELECT count(*) FROM billing_subscription bs JOIN bakney_user u ON bs.user_id = u.user_id WHERE u.email = '$owner_email';")
    [ "$entitlement_count" = 0 ] || {
        printf 'Expected no software subscription for owner, got %s.\n' "$entitlement_count" >&2
        exit 1
    }
}
assert_no_software_subscription

login_response="$TEMPORARY/owner-login.json"
login_status=$(curl --silent --show-error --output "$login_response" --write-out '%{http_code}' \
    --request POST --header 'content-type: application/json' \
    --data "{\"username\":\"$owner_email\",\"password\":\"SmokePassword123!\"}" \
    http://localhost:58080/api/oauth2/login)
[ "$login_status" = 200 ] || {
    printf 'Expected owner login without a software subscription, got HTTP %s.\n' "$login_status" >&2
    exit 1
}
owner_token=$(python3 -c 'import json, sys; token = json.load(open(sys.argv[1]))["access_token"]; assert token; print(token)' "$login_response")
access_status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --header "Authorization: Bearer $owner_token" http://localhost:58080/api/collaborators/list)
[ "$access_status" = 200 ] || {
    printf 'Expected owner access without a software subscription, got HTTP %s.\n' "$access_status" >&2
    exit 1
}
billing_status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --header "Authorization: Bearer $owner_token" http://localhost:58080/api/billing/active-plan)
[ "$billing_status" = 410 ] || {
    printf 'Expected retired software billing endpoint, got HTTP %s.\n' "$billing_status" >&2
    exit 1
}
assert_no_software_subscription
unset owner_token

setup_status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --request POST \
    --header 'content-type: application/json' \
    --header "X-Setup-Token: $setup_token" \
    --data '{}' \
    http://localhost:58080/api/instance/configure)
case "$setup_status" in
    401|403) ;;
    *)
        printf 'Expected setup token to be rejected after configuration, got HTTP %s.\n' "$setup_status" >&2
        exit 1
        ;;
esac

login_throttled=false
login_attempt=1
while [ "$login_attempt" -le 7 ]; do
    login_status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
        --request POST \
        --header 'content-type: application/json' \
        --data '{"username":"missing@example.invalid","password":"invalid"}' \
        http://localhost:58080/api/oauth2/login)
    if [ "$login_status" = 429 ]; then
        login_throttled=true
        break
    fi
    login_attempt=$((login_attempt + 1))
done
[ "$login_throttled" = true ] || {
    printf 'Expected repeated login attempts to be rate-limited.\n' >&2
    exit 1
}

write_db_sentinel before-backup
write_object_sentinel before-backup
phase "verify shipped Go mutation and Django readback before backup"
go_probe write --checkpoint before-backup --receipt "$TEMPORARY/go-before-backup.json"

phase "create and validate backup"
ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" backup "$TEMPORARY/backups"
archive=
archive_count=0
for candidate in "$TEMPORARY"/backups/assozeta-*.tar.gz; do
    [ -f "$candidate" ] || continue
    archive=$candidate
    archive_count=$((archive_count + 1))
done
[ "$archive_count" = 1 ] || {
    printf 'Expected one production backup, found %s.\n' "$archive_count" >&2
    exit 1
}

phase "save a fixture backup with dirty Go operational history"
db_sql 'UPDATE public.assozeta_go_schema_migrations SET dirty=true;'
ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" backup "$TEMPORARY/dirty-history-backups"
db_sql 'UPDATE public.assozeta_go_schema_migrations SET dirty=false;'
dirty_history_archive=
dirty_archive_count=0
for candidate in "$TEMPORARY"/dirty-history-backups/assozeta-*.tar.gz; do
    [ -f "$candidate" ] || continue
    dirty_history_archive=$candidate
    dirty_archive_count=$((dirty_archive_count + 1))
done
[ "$dirty_archive_count" = 1 ]
assert_go_intent "$go_before_backup"

write_db_sentinel after-backup
write_object_sentinel after-backup
db_sql "UPDATE public.assozeta_go_jobs SET payload=' {\"checkpoint\":\"after-backup\",\"integer\":9007199254740993}' WHERE id='11111111-1111-4111-8111-111111111111';"
go_after_backup=$(go_intent_digest)
[ "$go_after_backup" != "$go_before_backup" ]
phase "write a later Go preference and indexed audit"
go_probe write --checkpoint after-backup --receipt "$TEMPORARY/go-after-backup.json"
assert_db_sentinel after-backup
assert_object_sentinel after-backup

corrupt_db_archive="$TEMPORARY/backups/assozeta-corrupt-db.tar.gz"
create_corrupt_db_archive "$archive" "$corrupt_db_archive"
phase "reject corrupt database backup and preserve live data"
if ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" restore "$corrupt_db_archive" --yes; then
    printf 'Expected corrupt production database backup to be rejected.\n' >&2
    exit 1
fi
curl_ready
assert_db_sentinel after-backup
assert_object_sentinel after-backup
go_probe verify --receipt "$TEMPORARY/go-after-backup.json"
assert_go_intent "$go_after_backup"

corrupt_object_archive="$TEMPORARY/backups/assozeta-corrupt-objects.tar.gz"
create_corrupt_object_archive "$archive" "$corrupt_object_archive"
phase "reject corrupt object backup and preserve live data"
if ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" restore "$corrupt_object_archive" --yes; then
    printf 'Expected corrupt production object backup to be rejected.\n' >&2
    exit 1
fi
curl_ready
assert_db_sentinel after-backup
assert_object_sentinel after-backup
go_probe verify --receipt "$TEMPORARY/go-after-backup.json"
assert_go_intent "$go_after_backup"

phase "roll back live data after restored Go history rejects migration"
if ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" restore "$dirty_history_archive" --yes; then
    printf 'Expected restored dirty Go operational history to reject migration.\n' >&2
    exit 1
fi
curl_ready
assert_db_sentinel after-backup
assert_object_sentinel after-backup
go_probe verify --receipt "$TEMPORARY/go-after-backup.json"
assert_go_intent "$go_after_backup"

ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" restore "$archive" --yes

phase "verify valid restore"
curl_ready
assert_db_sentinel before-backup
assert_object_sentinel before-backup
go_probe verify --receipt "$TEMPORARY/go-before-backup.json"
assert_go_intent "$go_before_backup"

printf 'Production self-host smoke test passed.\n'
