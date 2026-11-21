"""Leased Beat publication on installations with the shared occurrence ledger."""
import json
import threading
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError, close_old_connections, connection, transaction
from django_celery_beat.models import PeriodicTask
from kombu import Producer
from kombu.utils.json import dumps

from core.handoff import admit, barrier


class ScheduleAuthority:
    def __init__(self, app):
        self.app = app
        self.holder = 'beat-' + str(uuid4())
        self.lease = getattr(settings, 'ASSOZETA_SCHEDULER_LEASE_SECONDS', 30)
        self.interval = getattr(settings, 'ASSOZETA_HANDOFF_HEARTBEAT_SECONDS', 1)
        self.stopped = threading.Event()
        self.state = self.heartbeat()
        self.thread = threading.Thread(target=self.renew, daemon=True, name='scheduler-lease')
        self.thread.start()

    def heartbeat(self):
        with transaction.atomic(), connection.cursor() as c:
            c.execute("SELECT operation_id FROM public.assozeta_handoff_heartbeat(%s,'django','scheduler')",
                      (self.holder,))
            operation, = c.fetchone()
            c.execute("SELECT * FROM public.assozeta_scheduler_acquire('django',%s,%s)", (self.holder, self.lease))
            outcome, generation = c.fetchone()
            if outcome == 'frozen':
                # Closing admission took the exclusive ownership lock. No previous
                # publication transaction is open, and new publications must fence.
                c.execute('SELECT public.assozeta_handoff_acknowledge_freeze(%s,%s)', (self.holder, operation))
            return outcome, generation

    def renew(self):
        try:
            while not self.stopped.wait(self.interval):
                try:
                    close_old_connections()
                    self.state = self.heartbeat()
                except DatabaseError:
                    self.state = ('unavailable', None)
                    connection.close()
        finally:
            connection.close()

    def close(self):
        self.stopped.set()
        self.thread.join()
        with connection.cursor() as c:
            c.execute("SELECT public.assozeta_scheduler_release('django',%s)", (self.holder,))

    def fence(self, cursor, generation):
        cursor.execute('SELECT public.assozeta_scheduler_fence(%s,%s)', (self.holder, generation))
        return cursor.fetchone()[0]

    @staticmethod
    def due(row, now):
        if not row.enabled or (row.start_time and now < row.start_time):
            return False
        if (row.expires and row.expires <= now) or (row.one_off and row.total_run_count):
            return False
        last = row.last_run_at or row.date_changed
        if row.last_run_at is None and row.start_time:
            last -= timedelta(days=365 * 30)
        schedule = row.schedule
        schedule.nowfun = lambda: now.astimezone(schedule.tz)
        return schedule.is_due(last).is_due

    def tick(self, now):
        outcome, generation = self.state
        if outcome not in ('acquired', 'renewed'):
            return outcome in ('frozen', 'not_owner')
        try:
            self.flush(generation)
            rows = PeriodicTask.objects.filter(enabled=True).select_related('interval', 'crontab', 'clocked', 'solar')
            for row in rows.order_by('id').iterator(chunk_size=128):
                if not self.due(row, now):
                    continue
                key = f'{generation}-{row.pk}'
                barrier('schedule:due', key)
                try:
                    published = self.publish(row.pk, now, generation)
                except DatabaseError as exc:
                    if getattr(exc.__cause__, 'pgcode', None) != 'AZF02':
                        raise
                    barrier('schedule:fenced', key)
                    return True
                if published:
                    barrier('schedule:committed', str(row.pk))
                    self.flush(generation)
        except DatabaseError as exc:
            if getattr(exc.__cause__, 'pgcode', None) != 'AZF02':
                raise
        return True

    def publish(self, pk, now, generation):
        with transaction.atomic(), connection.cursor() as c:
            owner = self.fence(c, generation)
            row = PeriodicTask.objects.select_for_update().filter(pk=pk).first()
            if row is None:
                return False
            # Lock the timing definition too. An inline admin edit can change its
            # fields without writing the PeriodicTask foreign key.
            field = next((f for f in ('interval', 'crontab', 'solar', 'clocked') if getattr(row, f + '_id')), None)
            if field is None:
                return False
            spec = getattr(row, field)
            locked = type(spec).objects.select_for_update().get(pk=spec.pk)
            setattr(row, field, locked)
            if not self.due(row, now):
                return False
            task_id = str(uuid4())
            self.admit_entry(row, task_id)
            c.execute("""INSERT INTO public.assozeta_go_periodic_occurrences
                (id,periodic_task_id,source_name,dispatch_sequence,runtime,task_id,scheduler_generation,
                 dispatched_at,ownership_generation,definition_sha256)
                SELECT %s,%s,%s,coalesce(max(dispatch_sequence),0)+1,'django',%s,%s,%s,%s,
                       public.assozeta_periodic_digest(%s)
                FROM public.assozeta_go_periodic_occurrences WHERE periodic_task_id=%s""",
                      (str(uuid4()), pk, row.name, task_id, generation, now, owner, pk, pk))
            PeriodicTask.objects.filter(pk=pk).update(last_run_at=now, total_run_count=row.total_run_count + 1,
                                                      enabled=not row.one_off, date_changed=now)
            return True

    def admit_entry(self, row, task_id):
        # Use a dedicated channel so Kombu performs its normal serialization,
        # routing and before_task_publish signals. Capture the final Redis
        # envelope at the transport boundary instead of pushing before commit.
        with self.app.connection_for_write() as broker, broker.channel() as channel:
            admitted = False

            def record(queue, message, **kwargs):
                nonlocal admitted
                if admitted:
                    raise ValueError('periodic definition routed to multiple queues')
                if admit(queue, dumps(message).encode(), message['headers']) != 'accepted':
                    raise RuntimeError('scheduled job admission failed')
                admitted = True

            channel._put = record
            options = {field: getattr(row, field) for field in ('queue', 'exchange', 'routing_key', 'priority')
                       if getattr(row, field) is not None}
            headers = json.loads(row.headers or '{}')
            headers['periodic_task_name'] = row.name
            task = self.app.signature(row.task, args=json.loads(row.args or '[]'), kwargs=json.loads(row.kwargs or '{}'))
            task.apply_async(task_id=task_id, headers=headers, expires=row.expires_, producer=Producer(channel), **options)
            if not admitted:
                raise ValueError('periodic definition has no queue binding')

    def flush(self, generation):
        while True:
            with transaction.atomic(), connection.cursor() as c:
                owner = self.fence(c, generation)
                c.execute("""SELECT w.task_id,w.queue,w.envelope,w.priority FROM public.assozeta_go_periodic_occurrences r
                    JOIN public.assozeta_handoff_work w USING (task_id)
                    WHERE r.runtime='django' AND r.ownership_generation=%s AND r.broker_pushed_at IS NULL
                      AND r.job_id IS NULL AND w.owner_runtime='django' AND w.owner_generation=%s AND w.state='pending'
                    ORDER BY r.dispatched_at,r.id LIMIT 1 FOR UPDATE OF r""", (owner, owner))
                item = c.fetchone()
                if not item:
                    return
                task_id, queue, payload, priority = item
                with self.app.connection_for_write() as broker, broker.channel() as ch, ch.conn_or_acquire() as client:
                    key = ch._q_for_pri(queue, priority or 0)
                    marker = 'assozeta:schedule:published:' + task_id
                    client.eval("if redis.call('exists',KEYS[2])==0 then redis.call('lpush',KEYS[1],ARGV[1]); "
                                "redis.call('set',KEYS[2],'1') end", 2, key, marker, bytes(payload))
                    c.execute('UPDATE public.assozeta_go_periodic_occurrences SET broker_pushed_at=clock_timestamp() '
                              'WHERE task_id=%s', (task_id,))
            # A crash before the SQL commit leaves the Redis marker for recovery.
            with self.app.connection_for_write() as broker, broker.channel() as ch, ch.conn_or_acquire() as client:
                client.delete(marker)
