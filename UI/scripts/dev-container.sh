#!/bin/sh
# Development container entrypoint. node_modules is a named volume, which Docker
# seeds from the image only once; resync it whenever package-lock.json changes.
set -eu

stamp=node_modules/.assozeta-package-lock.sha256
current=$(sha256sum package-lock.json | cut -d' ' -f1)

if [ "$(cat "$stamp" 2>/dev/null || true)" != "$current" ]; then
    echo "package-lock.json changed; installing UI dependencies"
    # npm ci cannot remove a mounted node_modules directory; install reconciles
    # the existing tree to the lockfile instead.
    npm install --no-audit --no-fund
    echo "$current" > "$stamp"
fi

exec npm run dev:selfhosted -- --host 0.0.0.0
