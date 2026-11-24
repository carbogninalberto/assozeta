"""Scheduler liveness evidence, written by normal service operation, never by diagnostics."""
import json
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from django.conf import settings
from django.db import connection, transaction
from core.handoff import barrier
from .schedule_authority import ScheduleAuthority
from django.core.cache import cache
from django_celery_beat.schedulers import DatabaseScheduler


def periodic_declarations():
    from celery.schedules import crontab
    from core.celery import app

    entries = dict(app.conf.beat_schedule)
    entries['celery.backend_cleanup'] = {
        'task': 'celery.backend_cleanup', 'schedule': crontab(minute='0', hour='4'),
        'options': {'expire_seconds': 43200},
    }
    declarations = []
    for name, entry in sorted(entries.items()):
        schedule = entry['schedule']
        if isinstance(schedule, crontab):
            fields = ('minute', 'hour', 'day_of_week', 'day_of_month', 'month_of_year')
            spec = {field: str(getattr(schedule, '_orig_' + field)) for field in fields}
            spec.update(kind='crontab', timezone=str(app.timezone))
        else:
            spec = {'kind': 'interval', 'every': int(schedule), 'period': 'seconds'}
        options = dict.fromkeys(('queue', 'exchange', 'routing_key', 'priority', 'expire_seconds', 'expires'))
        options.update(headers={})
        options.update(entry.get('options', {}))
        declarations.append(dict(name=name, task=entry['task'], schedule=spec, args=list(entry.get('args', ())),
                                 kwargs=entry.get('kwargs', {}), options=options))
    return declarations


class InstanceScheduler(DatabaseScheduler):
    def setup_schedule(self):
        self._authoritative = False
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('public.assozeta_scheduler_lease') IS NOT NULL")
                self._authoritative = cursor.fetchone()[0]
        if not self._authoritative:
            return super().setup_schedule()

        self._last_tick = None
        with transaction.atomic(), connection.cursor() as cursor:
            if getattr(settings, 'ASSOZETA_HANDOFF_CLOCK_FILE', ''):
                cursor.execute("SELECT set_config('assozeta.scheduler_now', %s, true)", (self.scheduler_now().isoformat(),))
            cursor.execute("SELECT * FROM public.assozeta_seed_periodic_definitions('django', %s::jsonb)",
                           (json.dumps(periodic_declarations()),))

        self.authority = ScheduleAuthority(self.app)

    def close(self):
        if hasattr(self, 'authority'):
            self.authority.close()
        super().close()

    @staticmethod
    def scheduler_now():
        path = getattr(settings, 'ASSOZETA_HANDOFF_CLOCK_FILE', '')
        return datetime.fromisoformat(Path(path).read_text().strip()) if path else datetime.now(timezone.utc)

    def authority_tick(self):
        now = self.scheduler_now()
        key = str(int(round(now.timestamp() * 1000)))
        clock_file = getattr(settings, 'ASSOZETA_HANDOFF_CLOCK_FILE', '')
        generation = self.authority.state[1]
        token = (key, generation)
        if not clock_file or token != self._last_tick:
            if self.authority.tick(now):
                if clock_file:
                    barrier('schedule:ticked', key)
                self._last_tick = token
        return self.max_interval

    @staticmethod
    def publish_heartbeat(timestamp):
        try:
            cache.set('instance:scheduler:heartbeat', timestamp, timeout=90)
        except Exception:
            pass

    def tick(self, *args, **kwargs):
        interval = self.authority_tick() if getattr(self, '_authoritative', False) else super().tick(*args, **kwargs)
        # Monitoring must never delay scheduled work if cache/DNS stalls. Only
        # one daemon publisher can exist; a stuck publisher means stale evidence,
        # not an accumulating thread pool or a blocked scheduler. Record tick time,
        # not eventual write time, so a delayed write cannot claim fresh liveness.
        publisher = getattr(self, '_heartbeat_publisher', None)
        if publisher is None or not publisher.is_alive():
            self._heartbeat_publisher = threading.Thread(
                target=self.publish_heartbeat, args=(time.time(),), daemon=True,
                name='instance-scheduler-heartbeat',
            )
            try:
                self._heartbeat_publisher.start()
            except RuntimeError:
                # Resource pressure in monitoring must not stop the scheduler.
                pass
        return min(interval, 30)
