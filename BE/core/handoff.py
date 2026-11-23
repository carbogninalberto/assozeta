"""Durable Celery task admission on instances with the handoff ledger."""
import json
import os
import tempfile
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from threading import Condition, Thread
from uuid import uuid4

from billiard.einfo import ExceptionWithTraceback
from billiard.exceptions import WorkerLostError
from celery.exceptions import Ignore
from celery.worker.request import Request

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


# The SMTP data command is shared by Django's mail backend and the association
# mailer. An explicit negative reply is safe to retry. Losing the reply leaves
# an intent for reconciliation.
_mail = ContextVar('handoff_smtp_attempt', default=None)


def _install_mail():
    import smtplib
    from contextvars import copy_context
    from threading import Timer

    original_data = smtplib.SMTP.data
    original_start = Timer.start

    def data(client, msg):
        active = _mail.get()
        if active is None:
            return original_data(client, msg)
        _, task_id, retries, attempt, sequence, _ = active
        key = f'{task_id}:{retries}:smtp:{next(sequence)}'
        outcome, = _fetch("SELECT public.assozeta_handoff_effect_intent(%s,%s,'smtp')", (attempt, key))
        if outcome == 'confirmed':
            client.rset()
            return 250, b'accepted'
        if outcome != 'recorded':
            raise RuntimeError('SMTP effect needs reconciliation')
        try:
            code, reply = original_data(client, msg)
        except smtplib.SMTPDataError:
            _fetch("DELETE FROM public.assozeta_handoff_effects WHERE effect_key=%s AND state='intent' RETURNING 1", (key,))
            raise
        if code == 250:
            barrier('effect:accepted', task_id)
            with transaction.atomic():
                _fetch('SELECT public.assozeta_handoff_fence(%s)', (attempt,))
                _fetch('SELECT public.assozeta_handoff_effect_confirm(%s,NULL)', (key,))
        else:
            _fetch("DELETE FROM public.assozeta_handoff_effects WHERE effect_key=%s AND state='intent' RETURNING 1", (key,))
        return code, reply

    def start(timer):
        active = _mail.get()
        if active is None or active[0] not in ('send_email_html', 'send_reminders'):
            return original_start(timer)
        # The configured HTML sender schedules its SMTP call in a Timer. Keep
        # the logical attempt alive through that call and its confirmation.
        callback = timer.function
        context = copy_context()
        timer.function = lambda *a, **kw: context.run(callback, *a, **kw)
        original_start(timer)
        active[5].append(timer)

    smtplib.SMTP.data = data
    Timer.start = start


def install_producer():
    from kombu import Connection
    from kombu.transport.redis import Channel
    from kombu.utils.json import dumps

    if getattr(Channel.basic_publish, '_handoff', False):
        return
    _install_mail()
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
        task_id = headers['id']
        running = _running.get()
        if running and running[0] != task_id and not headers.get('parent_id'):
            headers['parent_id'] = running[0]
            if headers.get('root_id') in (None, task_id):
                headers['root_id'] = running[2]
        if running and running[0] == task_id:
            # Retire the old attempt and admit its successor together. Even a
            # zero-countdown retry can now be claimed as soon as it is pushed.
            with transaction.atomic():
                payload = dumps(message).encode()
                _finish(running[1], 'retried')
                outcome = admit(queue, payload, headers)
        else:
            payload = dumps(message).encode()
            if running:
                with transaction.atomic():
                    _fetch('SELECT public.assozeta_handoff_fence(%s)', (running[1],))
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
_managed_worker = False
_parent_pulse = None
_renew_interval = 10
_local_attempts = set()
_attempts_changed = Condition()
_local_monitor = None


def _renew(attempts):
    if attempts:
        _fetch("SELECT public.assozeta_handoff_renew(%s::uuid[])", (list(attempts),))


def _stamp_execution(request, **kwargs):
    # This id belongs to a local pool invocation, including when two deliveries
    # share the same task id and Redis delivery tag. Never trust a wire value.
    props = dict(request.request_dict.get('properties') or {})
    props['_handoff_execution'] = str(uuid4())
    request.request_dict['properties'] = props


def _watch_parent():
    # A prefork child can outlive its parent. Normally the parent's existing
    # heartbeat connection renews the entire pool. Only take over database
    # renewal here if that heartbeat stopped.
    while True:
        with _attempts_changed:
            _attempts_changed.wait_for(lambda: _local_attempts)
        time.sleep(_renew_interval)
        with _attempts_changed:
            local = tuple(_local_attempts)
        if local and (_parent_pulse is None or time.monotonic() - _parent_pulse.value >= _renew_interval + 2):
            try:
                _renew(local)
            except Exception:
                pass  # retry on the next tick; the claim/finish fence is authoritative
            finally:
                connection.close()


@contextmanager
def _executing(attempt):
    global _local_monitor
    with _attempts_changed:
        _local_attempts.add(str(attempt))
        if _local_monitor is None:
            _local_monitor = Thread(target=_watch_parent, name='handoff-executor', daemon=True)
            _local_monitor.start()
        _attempts_changed.notify()
    try:
        yield
    finally:
        with _attempts_changed:
            _local_attempts.discard(str(attempt))


class ActiveDelivery(Ignore):
    """Return the pool slot; the consumer still owns the unacked reservation."""


class BrokerRequest(Request):
    def on_failure(self, exc_info, *args, **kwargs):
        from celery.worker.state import task_ready

        exc = exc_info.exception
        if isinstance(exc, ExceptionWithTraceback):
            exc = exc.exc
        if isinstance(exc, ActiveDelivery):
            # The pool slot is free. Kombu retains the Redis reservation until
            # normal visibility restore (including if this consumer now dies).
            task_ready(self)
            return
        if isinstance(exc, WorkerLostError) and self.task.acks_late and self.task.reject_on_worker_lost:
            execution = (self.request_dict.get('properties') or {}).get('_handoff_execution')
            if execution and _managed_worker:
                # The parent has proof that this exact pool invocation died.
                # Expire its lease before Celery rejects/requeues its delivery.
                # If the DB is unavailable, normal lease expiry still applies.
                from django.db import DatabaseError
                try:
                    _fetch("""UPDATE public.assozeta_handoff_attempts a SET lease_until=clock_timestamp()
                        FROM public.assozeta_go_job_ownership o WHERE a.attempt_id=%s AND a.outcome='running'
                        AND a.consumer_id=%s AND o.singleton=1 AND a.runtime=o.runtime AND a.generation=o.generation
                        RETURNING a.attempt_id""", (execution, _consumer_id))
                except DatabaseError:
                    connection.close()
        return super().on_failure(exc_info, *args, **kwargs)


def broker_strategy(task, app, consumer, **options):
    from celery.worker.strategy import default

    class RestoredTask:
        # A redelivered early-ack task must also keep its broker copy until the
        # claim is decided. Only this delivery's consumer-side ACK policy changes.
        acks_late = True

        def __getattr__(self, name):
            return getattr(task, name)

    regular = default(task, app, consumer, **options)
    restored = default(RestoredTask(), app, consumer, **options)

    def receive(message, *args, **kwargs):
        if _managed_worker and (message.delivery_info or {}).get('redelivered'):
            return restored(message, *args, **kwargs)
        return regular(message, *args, **kwargs)

    return receive


def worker_identity(sender, **kwargs):
    """Pool children inherit the identity of the main process that reserves work."""
    from multiprocessing.sharedctypes import RawValue
    from instance.restore.task_base import CoordinatedTask

    global _consumer_id, _parent_pulse
    if issubclass(sender.app.Task, CoordinatedTask):
        sender.app.Task.Strategy = 'core.handoff:broker_strategy'
        sender.app.Task.Request = 'core.handoff:BrokerRequest'
    _parent_pulse = RawValue('d', time.monotonic())
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
    task_id = task.request.id
    barrier('reservation:confirmed', task_id)
    properties = task.request.properties or {}
    outcome, attempt = _fetch(
        "SELECT outcome,attempt_id FROM public.assozeta_handoff_claim('django',%s,%s,%s,NULL,%s,%s,%s)",
        (_consumer_id, task_id, task.name, properties.get('delivery_tag'),
         bool((task.request.delivery_info or {}).get('redelivered')), properties.get('_handoff_execution')),
    )
    if outcome == 'active':
        barrier('claim:refused', task_id)
        raise ActiveDelivery()
    if outcome != 'claimed':
        if outcome == 'expired':
            task.backend.mark_as_revoked(task_id, 'expired', request=task.request)
        barrier('claim:refused', task_id)
        raise Ignore()
    with _executing(attempt):
        return _execute_claim(task, guarded, run, args, kwargs, attempt)


def _execute_claim(task, guarded, run, args, kwargs, attempt):
    from celery.exceptions import Ignore, Retry
    from django.db import DatabaseError

    task_id = task.request.id
    barrier('claim:granted', task_id)

    def database_effect(*args, **kwargs):
        with transaction.atomic():
            _fetch('SELECT public.assozeta_handoff_fence(%s)', (attempt,))
            result = run(*args, **kwargs)
            _finish(attempt, 'completed')
            return result

    from itertools import count
    smtp_tasks = ('core.tasks.send_mail_async', 'send_email_html', 'send_email_template', 'send_email_text',
                  'send_expiring_certificate_email', 'send_expired_certificate_email', 'send_reminders')
    timers = []
    mail = _mail.set((task.name, task_id, task.request.retries, attempt, count(), timers) if task.name in smtp_tasks else None)
    token = _running.set((task_id, attempt, task.request.root_id or task_id))
    try:
        try:
            if task.name == 'reset_communication_daily_email_balance':
                # Restore deferral can publish a retry before business execution.
                return guarded(database_effect, *args, **kwargs)
            try:
                result = guarded(run, *args, **kwargs)
            finally:
                for timer in timers:
                    timer.join()
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
        _mail.reset(mail)
        _running.reset(token)


def record_revoked(request=None, expired=False, **kwargs):
    # Celery revokes expired deliveries in its consumer, before Task.__call__.
    if request is None or not enabled():
        return
    _fetch("""UPDATE public.assozeta_handoff_work w SET state=%s, updated_at=clock_timestamp()
        FROM public.assozeta_go_job_ownership o WHERE w.task_id=%s AND w.owner_runtime='django'
        AND w.owner_runtime=o.runtime AND w.owner_generation=o.generation AND w.state='pending'
        RETURNING w.task_id""", ('expired' if expired else 'revoked', request.id))


def start_heartbeat(sender=None, **kwargs):
    global _managed_worker
    from threading import Event, Thread
    from django.db import close_old_connections

    if not enabled():
        return
    from celery.signals import task_received
    from celery.worker.state import active_requests
    task_received.connect(_stamp_execution)
    _managed_worker = True
    next_renewal = time.monotonic() + _renew_interval
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
        nonlocal consuming, acknowledged, pending, next_renewal
        runtime, _, admission, operation = _fetch(
            "SELECT * FROM public.assozeta_handoff_heartbeat(%s,'django','broker')", (_consumer_id,),
        )
        if time.monotonic() >= next_renewal:
            ids = [r.request_dict['properties']['_handoff_execution'] for r in tuple(active_requests)
                   if (r.request_dict.get('properties') or {}).get('_handoff_execution')]
            _renew(ids)
            next_renewal = time.monotonic() + _renew_interval
            if _parent_pulse is not None:
                _parent_pulse.value = time.monotonic()
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
