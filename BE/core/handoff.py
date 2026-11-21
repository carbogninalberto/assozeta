"""Durable Celery task admission on instances with the handoff ledger."""
import json
import os
import tempfile
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction


# A self-retry hands off this exact attempt before its next delivery can run.
_running = ContextVar('handoff_running_attempt', default=None)


class AdmissionRejected(Exception):
    """A producer can retry against the effective runtime."""
    retryable = True


def _fetch(sql, parameters=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, parameters)
        return cursor.fetchone()


def enabled():
    return connection.vendor == 'postgresql' and _fetch(
        "SELECT to_regclass('public.assozeta_handoff_work') IS NOT NULL"
    )[0]


def barrier(point, key):
    directory = getattr(settings, 'ASSOZETA_HANDOFF_BARRIER_DIR', '')
    if not directory or Path(key).name != key:
        return
    root = Path(directory)
    with tempfile.NamedTemporaryFile(mode='w', dir=root, delete=False) as marker:
        json.dump({'pid': os.getpid(), 'point': point, 'key': key}, marker)
    os.replace(marker.name, root / f'arrived.{point}.{key}')
    deadline = time.monotonic() + 120
    while any((root / f'hold.{point}.{k}').exists() for k in (key, 'all')):
        if any((root / f'release.{point}.{k}').exists() for k in (key, 'all')) or time.monotonic() >= deadline:
            return
        time.sleep(0.025)


def admit(queue, payload, headers):
    stamp = datetime.fromtimestamp(headers.get('assozeta_published_at', 0), timezone.utc)
    outcome, = _fetch(
        """SELECT outcome FROM public.assozeta_handoff_admit('django',%s,%s,'broker',%s,%s,%s,%s,%s)""",
        (headers['id'], headers['task'], queue, payload, stamp, headers.get('eta'), headers.get('expires')),
    )
    if outcome == 'rejected':
        raise AdmissionRejected('Django task admission rejected')
    return outcome


def _dispatch(task_id, push):
    # The producer may outlive its business transaction. A switch that got
    # here first will find the original envelope in the ledger during staging.
    with transaction.atomic():
        row = _fetch("""
            SELECT w.task_id FROM public.assozeta_go_job_ownership o
            JOIN public.assozeta_handoff_work w ON w.owner_runtime=o.runtime AND w.owner_generation=o.generation
            WHERE o.singleton=1 AND o.runtime='django' AND o.admission='open' AND w.task_id=%s
              AND w.state IN ('pending','running') AND w.transport='broker' FOR SHARE OF o
        """, (task_id,))
        if row:
            push()


def install_producer():
    from kombu import Connection
    from kombu.transport.redis import Channel
    from kombu.utils.json import dumps

    if getattr(Channel.basic_publish, '_handoff', False):
        return
    original_publish, original_put = Channel.basic_publish, Channel._put
    publishing = ContextVar('handoff_publishing', default=None)

    def publish(channel, message, *args, **kwargs):
        token = publishing.set(message)
        try:
            return original_publish(channel, message, *args, **kwargs)
        finally:
            publishing.reset(token)

    def put(channel, queue, message, **kwargs):
        headers = message.get('headers') or {}
        if publishing.get() is not message or not headers.get('task') or not headers.get('id') or not enabled():
            return original_put(channel, queue, message, **kwargs)
        payload = dumps(message).encode()
        task_id = headers['id']
        running = _running.get()
        if running and running[0] == task_id:
            # Retire the old attempt and admit its successor together. Even a
            # zero-countdown retry can now be claimed as soon as it is pushed.
            with transaction.atomic():
                _finish(running[1], 'retried')
                outcome = admit(queue, payload, headers)
        else:
            outcome = admit(queue, payload, headers)
        if outcome != 'accepted':
            return
        if not connection.in_atomic_block:
            return _dispatch(task_id, lambda: original_put(channel, queue, message, **kwargs))

        # A transaction callback can outlive the producer's channel/pool lease.
        # Reopen the same broker on commit and push the exact admitted bytes.
        broker_options = channel.connection.client.info()
        key = channel._q_for_pri(queue, channel._get_message_priority(message))

        def push():
            with Connection(**broker_options) as conn, conn.channel() as fresh, fresh.conn_or_acquire() as client:
                client.lpush(key, payload)

        transaction.on_commit(lambda: _dispatch(task_id, push))

    publish._handoff = True
    Channel.basic_publish, Channel._put = publish, put


_consumer_id = None


def worker_identity(**kwargs):
    """Pool children inherit the identity of the main process that reserves work."""
    global _consumer_id
    from uuid import uuid4
    _consumer_id = f'django-{uuid4()}'


def _finish(attempt, outcome):
    # A self-republication may already have retired this attempt in the
    # admission transaction. Its successor owns any later result.
    _fetch("""SELECT public.assozeta_handoff_finish(%s,%s) WHERE NOT EXISTS (
                SELECT 1 FROM public.assozeta_handoff_attempts a JOIN public.assozeta_go_job_ownership o
                  ON o.singleton=1 AND o.runtime=a.runtime AND o.generation=a.generation
                WHERE a.attempt_id=%s AND a.outcome='retried' FOR SHARE OF o)""",
           (attempt, outcome, attempt))


def execute(task, guarded, run, args, kwargs):
    from celery.exceptions import Ignore, Retry
    from django.db import DatabaseError

    task_id = task.request.id
    barrier('reservation:confirmed', task_id)
    outcome, attempt = _fetch(
        "SELECT outcome,attempt_id FROM public.assozeta_handoff_claim('django',%s,%s,%s,NULL)",
        (_consumer_id, task_id, task.name),
    )
    if outcome != 'claimed':
        barrier('claim:refused', task_id)
        raise Ignore()
    barrier('claim:granted', task_id)

    def database_effect(*args, **kwargs):
        with transaction.atomic():
            _fetch('SELECT public.assozeta_handoff_fence(%s)', (attempt,))
            result = run(*args, **kwargs)
            _finish(attempt, 'completed')
            return result

    token = _running.set((task_id, attempt))
    try:
        try:
            if task.name == 'reset_communication_daily_email_balance':
                # Restore deferral can publish a retry before business execution.
                return guarded(database_effect, *args, **kwargs)
            result = guarded(run, *args, **kwargs)
        except Retry:
            _finish(attempt, 'retried')
            raise
        except Exception:
            _finish(attempt, 'failed')
            raise
        _finish(attempt, 'completed')
        return result
    except DatabaseError as exc:
        if getattr(exc.__cause__, 'pgcode', None) != 'AZF01':
            raise
        _fetch('SELECT public.assozeta_handoff_record_fenced(%s)', (attempt,))
        barrier('attempt:fenced', task_id)
        raise Ignore() from None
    finally:
        _running.reset(token)


def start_heartbeat(sender=None, **kwargs):
    from threading import Event, Thread
    from django.db import close_old_connections

    if not enabled():
        return
    queues = tuple(sender.task_consumer.queues)
    interval = getattr(settings, 'ASSOZETA_HANDOFF_HEARTBEAT_SECONDS', 1.0)
    acknowledged = None
    consuming = True
    pending = None

    def set_consuming(active):
        for queue in queues:
            if active:
                sender.app.amqp.queues.select_add(queue)
                sender.add_task_queue(queue.name)
            else:
                sender.cancel_task_queue(queue.name)

    def synchronize(in_worker_thread):
        nonlocal consuming, acknowledged, pending
        runtime, _, admission, operation = _fetch(
            "SELECT * FROM public.assozeta_handoff_heartbeat(%s,'django','broker')", (_consumer_id,),
        )
        active = runtime == 'django' and admission == 'open'
        if pending is not None:
            target, finished, errors = pending
            if not finished.is_set():
                return
            consuming = None if errors else target
            pending = None
        if active != consuming:
            if in_worker_thread:
                set_consuming(active)
                consuming = active
            else:
                # A busy solo consumer may only service this callback after its
                # task returns. Keep heartbeating and wait for the actual result
                # before acknowledging; queue at most one update at a time.
                finished = Event()
                errors = []

                def update():
                    try:
                        set_consuming(active)
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        finished.set()

                sender.call_soon(update)
                pending = active, finished, errors
                return
        if runtime == 'django' and operation and operation != acknowledged and not consuming:
            result, = _fetch('SELECT public.assozeta_handoff_acknowledge_freeze(%s,%s)', (_consumer_id, operation))
            if result == 'acknowledged':
                acknowledged = operation
                barrier('freeze:acknowledged', _consumer_id)

    def loop():
        while True:
            time.sleep(interval)
            try:
                close_old_connections()
                synchronize(False)
            except Exception:
                connection.close()

    synchronize(True)
    Thread(target=loop, name='handoff-heartbeat', daemon=True).start()
