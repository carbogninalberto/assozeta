#!/bin/sh
# Verify the published storage pair without touching application containers.
set -eu
server_image=${1:-ghcr.io/carbogninalberto/minio:RELEASE.2025-04-22T22-12-26Z-assozeta.1@sha256:8d35f39307b12cbc9ba3eedd26587d77ab30c21c3e904aee552df8b17b600389}
client_image=${2:-ghcr.io/carbogninalberto/mc:RELEASE.2025-04-16T18-13-26Z-assozeta.1@sha256:c12985fd4e9fada3afd286d10ccacb86ebeb7894894360c96da18ed5c092b456}
network="assozeta-storage-smoke-$$"
server="$network-server"
cleanup() {
    docker rm -f "$server" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM
docker run --rm "$server_image" --version | grep -F 'RELEASE.2025-04-22T22-12-26Z'
docker run --rm "$client_image" --version | grep -F 'RELEASE.2025-04-16T18-13-26Z'
docker network create "$network" >/dev/null
docker run -d --name "$server" --network "$network" --network-alias storage \
    -e MINIO_ROOT_USER=source-smoke -e MINIO_ROOT_PASSWORD=source-smoke-password \
    "$server_image" server /data --console-address :9001 >/dev/null
docker run --rm --network "$network" --entrypoint /bin/sh "$client_image" -ec '
    attempts=0
    until curl --fail --silent http://storage:9000/minio/health/live >/dev/null; do
        attempts=$((attempts + 1))
        [ "$attempts" -lt 30 ] || exit 1
        sleep 1
    done
    mc alias set fixture http://storage:9000 source-smoke source-smoke-password >/dev/null
    mc mb fixture/source-smoke >/dev/null
    printf "source-build-roundtrip" | mc pipe fixture/source-smoke/check.txt >/dev/null
    [ "$(mc cat fixture/source-smoke/check.txt)" = source-build-roundtrip ]
    mc rm fixture/source-smoke/check.txt >/dev/null
'
printf 'PASS: pinned MinIO versions, readiness and object write/read/delete.\n'
