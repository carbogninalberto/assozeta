#!/bin/sh
set -eu

# Validate the complete image-owned package before accepting requests. The
# persistent download volume cannot hide or replace these release assets.
python -m application.manuale.bundle --root /app/release-manual \
  --revision "$(cat /app/CODE_REVISION)" --release "$(cat /app/VERSION)" \
  --required "$(cat /app/MANUAL_REQUIRED)"

exec gunicorn core.asgi:application \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers "${WEB_CONCURRENCY:-4}" \
  --threads 1 \
  --bind 0.0.0.0:8000 \
  --max-requests "${GUNICORN_MAX_REQUESTS:-500}" \
  --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-50}" \
  --timeout "${GUNICORN_TIMEOUT:-120}" \
  --keep-alive 5 \
  --preload \
  --worker-tmp-dir /dev/shm \
  --log-level info \
  --access-logfile - \
  --error-logfile -
