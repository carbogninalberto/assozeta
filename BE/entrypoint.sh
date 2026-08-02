#!/bin/bash
set -e

echo "Running Django migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput --clear

echo "Starting Gunicorn with Uvicorn workers..."
exec gunicorn core.asgi:application \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers 4 \
  --threads 1 \
  --bind 0.0.0.0:8000 \
  --max-requests 500 \
  --max-requests-jitter 50 \
  --timeout 120 \
  --keep-alive 5 \
  --preload \
  --worker-tmp-dir /dev/shm \
  --log-level info \
  --access-logfile - \
  --error-logfile -