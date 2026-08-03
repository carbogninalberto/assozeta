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
export ASSOZETA_WEB_IMAGE=invalid.example.invalid/conflicting-web
export ASSOZETA_RENDERER_IMAGE=invalid.example.invalid/conflicting-renderer
export POSTGRES_IMAGE=invalid.example.invalid/conflicting-postgres

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "$status" -ne 0 ] && [ -f "$ENV_FILE" ]; then
        compose ps >&2 || true
        compose logs --no-color postgres redis minio renderer >&2 || true
    fi
    if [ -f "$ENV_FILE" ]; then
        ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" uninstall --volumes --yes >/dev/null 2>&1 || true
    fi
    rm -rf "$TEMPORARY"
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
    curl --fail --silent --show-error --retry 20 --retry-all-errors --retry-delay 1 \
        http://localhost:58080/api/readyz >/dev/null
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

ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" configure --domain localhost:58080 --version test
set_value COMPOSE_PROJECT_NAME assozeta-production-smoke
set_value ASSOZETA_BACKEND_IMAGE assozeta-backend
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

ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" start

curl --fail --silent --show-error --retry 20 --retry-all-errors --retry-delay 1 \
    http://localhost:58080/ >/dev/null
curl --fail --silent --show-error --retry 20 --retry-all-errors --retry-delay 1 \
    http://localhost:58080/api/readyz >/dev/null
curl --fail --silent --show-error --retry 20 --retry-all-errors --retry-delay 1 \
    http://localhost:58080/api/instance/status >/dev/null
curl --fail --silent --show-error http://localhost:58080/static/css/bootstrap.min.css >/dev/null
curl --fail --silent --show-error http://localhost:58080/static/rest_framework/css/default.css >/dev/null

ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" migrate
curl_ready

setup_status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --request POST \
    --header 'content-type: application/json' \
    --data '{}' \
    http://localhost:58080/api/instance/configure)
case "$setup_status" in
    401|403) ;;
    *)
        printf 'Expected unauthenticated setup to be denied, got HTTP %s.\n' "$setup_status" >&2
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

entitlement_count=$(db_query "SELECT count(*) FROM instance_instanceconfiguration cfg JOIN application_sportassociation sa ON cfg.primary_association_id = sa.sport_association_id JOIN bakney_user u ON sa.user_id = u.user_id JOIN billing_subscription bs ON bs.user_id = u.user_id JOIN billing_plan bp ON bs.billing_plan_id = bp.billing_plan_id WHERE cfg.self_hosted IS TRUE AND u.email = '$owner_email' AND bs.auto_renewal IS TRUE AND bs.renewal_type = 2 AND bs.ends_on > now() + interval '30000 days';")
[ "$entitlement_count" = 1 ] || {
    printf 'Expected one long-lived self-host billing entitlement for owner, got %s.\n' "$entitlement_count" >&2
    exit 1
}

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

write_db_sentinel after-backup
write_object_sentinel after-backup
assert_db_sentinel after-backup
assert_object_sentinel after-backup

corrupt_db_archive="$TEMPORARY/backups/assozeta-corrupt-db.tar.gz"
create_corrupt_db_archive "$archive" "$corrupt_db_archive"
if ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" restore "$corrupt_db_archive" --yes; then
    printf 'Expected corrupt production database backup to be rejected.\n' >&2
    exit 1
fi
curl_ready
assert_db_sentinel after-backup
assert_object_sentinel after-backup

corrupt_object_archive="$TEMPORARY/backups/assozeta-corrupt-objects.tar.gz"
create_corrupt_object_archive "$archive" "$corrupt_object_archive"
if ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" restore "$corrupt_object_archive" --yes; then
    printf 'Expected corrupt production object backup to be rejected.\n' >&2
    exit 1
fi
curl_ready
assert_db_sentinel after-backup
assert_object_sentinel after-backup

ASSOZETA_ENV_FILE="$ENV_FILE" "$CLI" restore "$archive" --yes

curl_ready
assert_db_sentinel before-backup
assert_object_sentinel before-backup

printf 'Production self-host smoke test passed.\n'
