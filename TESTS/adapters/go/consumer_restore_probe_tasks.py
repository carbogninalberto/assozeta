"""Probe tasks for the Go consumer's prefork capabilities (acks_late, reject_on_worker_lost, time limits).

Loaded only by celery_consumer_capabilities_process.py: in the harness process (to publish) and in the
Django prefork worker it starts (``-I``). The Go consumer registers the same names only when built with
``-tags consumerprobe`` (BE_GO/cmd/celery-consumer/probe_tasks.go). ``CONSUMER_PROBE_DIR`` holds the gate
files the harness uses to observe a running task.
"""
import os
import signal
import time
from pathlib import Path

from celery.exceptions import SoftTimeLimitExceeded

from core.celery import app


def _directory():
    path = os.environ.get('CONSUMER_PROBE_DIR', '')
    if not path:
        raise RuntimeError('CONSUMER_PROBE_DIR is required')
    return Path(path)


def _gate(value):
    directory = _directory()
    (directory / f'started-{value}').write_text(str(os.getpid()))
    deadline = time.monotonic() + 30
    while not (directory / f'release-{value}').exists():
        if time.monotonic() > deadline:
            raise RuntimeError('probe gate timed out')
        time.sleep(0.05)
    return value


def _run(task, mode, value):
    if mode == 'gate':
        return _gate(value)
    if mode == 'value':
        return value
    if mode == 'fail':
        raise ValueError('probe failure', value)
    if mode == 'retry':
        raise task.retry(exc=ValueError('probe retry'), countdown=600)
    if mode == 'kill':
        os.kill(os.getpid(), signal.SIGKILL)
    if mode == 'kill-once':
        if not (task.request.delivery_info or {}).get('redelivered'):
            os.kill(os.getpid(), signal.SIGKILL)
        return 'redelivered'
    if mode == 'soft-catch':
        try:
            time.sleep(20)
        except SoftTimeLimitExceeded:
            return 'soft'
        return 'slept'
    if mode == 'soft-raise':
        time.sleep(20)
        return 'slept'
    if mode == 'hard':
        try:
            time.sleep(20)
        except SoftTimeLimitExceeded:
            time.sleep(20)
        return 'slept'
    raise KeyError(mode)


@app.task(bind=True, name='consumer_restore.probe', acks_late=True, reject_on_worker_lost=True,
          soft_time_limit=1, time_limit=3, max_retries=2)
def probe(self, mode, value=None):
    return _run(self, mode, value)


@app.task(bind=True, name='consumer_restore.probe_lost', acks_late=True, soft_time_limit=1, time_limit=3)
def probe_lost(self, mode, value=None):
    return _run(self, mode, value)


@app.task(bind=True, name='consumer_restore.probe_early', soft_time_limit=1, time_limit=3)
def probe_early(self, mode, value=None):
    return _run(self, mode, value)
