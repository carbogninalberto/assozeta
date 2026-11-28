#!/bin/sh
# Installed Django runtime, real S3 services and a private disposable database.
set -eu
version=${1:?Usage: storage-roundtrip.sh RELEASE_VERSION}
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
installation=$(mktemp -d)
project=assozeta-storage-test-$$
port=${ASSOZETA_STORAGE_TEST_PORT:-58152}
cli=$installation/bin/assozeta
export ASSOZETA_INSTALL_ROOT=$installation ASSOZETA_ENV_FILE=$installation/.env
unset COMPOSE_PROJECT_NAME
cp -R "$root/bin" "$root/config" "$root/caddy" "$root/updater" "$root/compose.yml" "$installation/"
compose() {
    docker compose -p "$project" --env-file "$ASSOZETA_ENV_FILE" -f "$installation/compose.yml" "$@"
}
cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ -f "$ASSOZETA_ENV_FILE" ]; then
        compose -f "$installation/config/compose.garage.yml" --profile '*' down -v --remove-orphans >/dev/null
    fi
    rm -rf "$installation"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

"$cli" configure --storage garage --domain "localhost:$port" --version "$version"
sed -i "s/^COMPOSE_PROJECT_NAME=.*/COMPOSE_PROJECT_NAME=$project/; s/^HTTPS_PORT=.*/HTTPS_PORT=$((port+1))/" "$ASSOZETA_ENV_FILE"
"$cli" start
app() { compose --profile django exec -T api python manage.py shell -c "$1"; }
write_files() {
    # A delete + save uses the same key even on installations that disallow overwrites.
    app "from django.core.files.base import ContentFile
from django.core.files.storage import default_storage as s
for name in ['instance/logo.svg', 'qa/remove-me', 'qa/final.html']:
    s.delete(name)
s.save('instance/logo.svg', ContentFile(b'<svg>$1</svg>'))
s.save('qa/final.html', ContentFile(b'<html>$1</html>'))
if '$1' == 'first': s.save('qa/remove-me', ContentFile(b'delete me'))
s.delete('qa/added-on-garage')
if '$1' == 'second': s.save('qa/added-on-garage', ContentFile(b'new attachment'))
s.bucket.Object(s.location+'/qa/headers').put(Body=b'same', ContentType='application/pdf',
    CacheControl='private', ContentDisposition='attachment', Metadata={'revision':'$1'})"
}
check_files() {
    [ "$(curl -fsS "http://localhost:$port/api/instance/logo.png")" = "<svg>$1</svg>" ]
    app "from django.core.files.storage import default_storage as s
import requests
assert s.exists('qa/remove-me') == ('$1' == 'first')
assert s.open('qa/final.html').read() == b'<html>$1</html>'
assert s.exists('qa/added-on-garage') == ('$1' == 'second')
obj = s.bucket.Object(s.location+'/qa/headers')
assert (obj.content_type, obj.cache_control, obj.content_disposition, obj.metadata) == (
    'application/pdf', 'private', 'attachment', {'revision':'$1'})
r = requests.post('http://renderer:3000/render', json={'type':'pdf','url':s.url('qa/final.html')}, timeout=65)
r.raise_for_status()
assert r.content.startswith(b'%PDF-')"
}
check_store() {
    running=$(docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Label "com.docker.compose.service"}}')
    [ "$(printf '%s\n' "$running" | grep -E '^(minio|garage)$')" = "$1" ]
}
rehearse_restore() {
    "$cli" backup "$installation/$1-backups"
    set -- "$installation/$1-backups/"*.tar.gz "$2"
    write_files damaged
    "$cli" restore "$1" --yes
    check_files "$2"
}

check_store garage
"$cli" storage-switch minio
check_store minio
write_files first
rehearse_restore minio first
"$cli" storage-switch garage
check_store garage
check_files first
write_files second
rehearse_restore garage second
# Reject the destination application key after the final copy to the retained
# MinIO volume. Garage must serve the current files until a retry succeeds.
sed -i 's/^MINIO_APP_ACCESS_KEY=/MINIO_APP_ACCESS_KEY=invalid-/' "$ASSOZETA_ENV_FILE"
if "$cli" storage-switch minio; then
    echo 'Switch unexpectedly accepted invalid application credentials' >&2
    exit 1
fi
check_store garage
check_files second
sed -i 's/^MINIO_APP_ACCESS_KEY=invalid-/MINIO_APP_ACCESS_KEY=/' "$ASSOZETA_ENV_FILE"
"$cli" storage-switch minio
check_store minio
check_files second
docker volume inspect "${project}_garage_data" "${project}_minio_data" >/dev/null
printf '%s\n' 'PASS: storage round trip, application reads, PDF rendering and full restores.'
