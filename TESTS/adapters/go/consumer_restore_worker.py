"""Starts the Django Celery worker of the consumer-restore harnesses with the prefork pool.

core.celery selects the solo pool on macOS and the worker CLI keeps a configured pool over ``-P prefork``; the
production Linux worker uses prefork, whose time limits and worker-loss handling the harnesses compare against.
"""
import sys

from core.celery import app

if __name__ == '__main__':
    app.conf.worker_pool = 'prefork'
    app.worker_main(['worker', *sys.argv[1:]])
