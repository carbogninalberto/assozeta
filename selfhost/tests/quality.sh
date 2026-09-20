#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$ROOT"
scenario=${1:-legacy}
case "$scenario" in legacy|recovery) ;; *) echo 'Use legacy or recovery' >&2; exit 1 ;; esac
revision=$(git rev-parse HEAD)
export ASSOZETA_QUALITY_REPORT_DIR=${ASSOZETA_QUALITY_REPORT_DIR:-"$ROOT/quality-reports"}
mkdir -p "$ASSOZETA_QUALITY_REPORT_DIR"
# Build sequentially so two frontend builds do not exhaust a standard CI runner.
docker build --label "org.opencontainers.image.revision=$revision" -t assozeta-backend:goal-test BE
docker build --label "org.opencontainers.image.revision=$revision" -t assozeta-web:goal-test UI
docker build --label "org.opencontainers.image.revision=$revision" -t assozeta-renderer:test selfhost/renderer
docker build --label "org.opencontainers.image.revision=$revision" -t assozeta-updater:goal-test -f selfhost/updater/Dockerfile .
npm ci --prefix selfhost/tests/browser
npm exec --prefix selfhost/tests/browser -- playwright install --with-deps chromium
# The updater writes private host state as root. Inspect it in this disposable
# Linux runner with the same privileges; preserve Node/browser paths explicitly.
run_scenario() {
    if [ "$(uname -s)" = Linux ] && [ "$(id -u)" != 0 ]; then
        sudo -E env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$ROOT" python3 "$@"
    else
        python3 "$@"
    fi
}
if [ "$scenario" = legacy ]; then
    git rev-parse --verify 'v1.0.2^{commit}' >/dev/null
    run_scenario selfhost/tests/application-update-smoke.py --legacy --browser
else
    run_scenario selfhost/tests/application-update-smoke.py --browser
fi
