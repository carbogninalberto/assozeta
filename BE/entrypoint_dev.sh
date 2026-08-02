#!/bin/bash
# Development entrypoint for local macOS environment
# Usage: ./entrypoint_dev.sh [--skip-migrate] [--skip-static]

set -e

SKIP_MIGRATE=false
SKIP_STATIC=false

# Parse arguments
for arg in "$@"; do
    case $arg in
        --skip-migrate)
            SKIP_MIGRATE=true
            shift
            ;;
        --skip-static)
            SKIP_STATIC=true
            shift
            ;;
    esac
done

# Kill any pending processes from previous runs
kill_pending_processes() {
    echo "Cleaning up pending processes..."

    # Kill old Gunicorn processes
    if pgrep -f "gunicorn core.asgi:application" > /dev/null 2>&1; then
        echo "Killing old Gunicorn processes..."
        pkill -9 -f "gunicorn core.asgi:application" 2>/dev/null || true
        sleep 1
    fi

    # Kill old Celery workers
    if pgrep -f "celery -A core worker" > /dev/null 2>&1; then
        echo "Killing old Celery worker processes..."
        pkill -9 -f "celery -A core worker" 2>/dev/null || true
        sleep 1
    fi

    # Kill old Celery beat
    if pgrep -f "celery -A core beat" > /dev/null 2>&1; then
        echo "Killing old Celery beat processes..."
        pkill -9 -f "celery -A core beat" 2>/dev/null || true
        sleep 1
    fi

    # Kill any uvicorn workers
    if pgrep -f "uvicorn.workers.UvicornWorker" > /dev/null 2>&1; then
        echo "Killing old Uvicorn worker processes..."
        pkill -9 -f "uvicorn.workers.UvicornWorker" 2>/dev/null || true
        sleep 1
    fi

    # Kill old MCP server
    if pgrep -f "run_mcp_server" > /dev/null 2>&1; then
        echo "Killing old MCP server processes..."
        pkill -9 -f "run_mcp_server" 2>/dev/null || true
        sleep 1
    fi

    # Clean up stale celerybeat files
    rm -f celerybeat-schedule celerybeat.pid 2>/dev/null

    echo "Cleanup complete."
}

# Run cleanup before starting
kill_pending_processes

# Trap SIGINT (Ctrl+C) and SIGTERM for graceful shutdown
cleanup() {
    echo ""
    echo "Shutting down gracefully..."
    if [ -n "$CELERY_BEAT_PID" ]; then
        echo "Stopping Celery beat..."
        kill -TERM "$CELERY_BEAT_PID" 2>/dev/null
        wait "$CELERY_BEAT_PID" 2>/dev/null
    fi
    if [ -n "$CELERY_WORKER_PID" ]; then
        echo "Stopping Celery worker..."
        kill -TERM "$CELERY_WORKER_PID" 2>/dev/null
        wait "$CELERY_WORKER_PID" 2>/dev/null
    fi
    if [ -n "$MCP_SERVER_PID" ]; then
        echo "Stopping MCP server..."
        kill -TERM "$MCP_SERVER_PID" 2>/dev/null
        wait "$MCP_SERVER_PID" 2>/dev/null
    fi
    if [ -n "$GUNICORN_PID" ]; then
        echo "Stopping Gunicorn..."
        kill -TERM "$GUNICORN_PID" 2>/dev/null
        wait "$GUNICORN_PID" 2>/dev/null
    fi
    # Clean up celerybeat schedule file
    rm -f celerybeat-schedule celerybeat.pid 2>/dev/null
    echo "All services stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM

if [ "$SKIP_MIGRATE" = false ]; then
    echo "Running Django migrations..."
    python manage.py migrate --noinput
else
    echo "Skipping migrations (--skip-migrate)"
fi

if [ "$SKIP_STATIC" = false ]; then
    echo "Collecting static files..."
    python manage.py collectstatic --noinput --clear
else
    echo "Skipping static collection (--skip-static)"
fi

echo "Starting MCP SSE server on port 8081..."
python manage.py run_mcp_server --transport sse --port 8081 &
MCP_SERVER_PID=$!

echo "Starting Celery worker..."
celery -A core worker --loglevel=info &
CELERY_WORKER_PID=$!

echo "Starting Celery beat..."
celery -A core beat --loglevel=info &
CELERY_BEAT_PID=$!

echo "Starting Gunicorn with Uvicorn workers (dev mode)..."
echo "Press Ctrl+C to stop all services"
echo ""

# Use /tmp instead of /dev/shm (macOS compatible)
# Reduced workers for local development
# Added --reload for auto-reload on code changes
gunicorn core.asgi:application \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 1 \
    --threads 1 \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --keep-alive 5 \
    --worker-tmp-dir /tmp \
    --log-level debug \
    --access-logfile - \
    --error-logfile - \
    --reload &

GUNICORN_PID=$!

# Wait for gunicorn process
wait "$GUNICORN_PID"
