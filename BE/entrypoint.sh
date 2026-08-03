#!/bin/sh
set -eu

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
