"""Phase 4 handoff stack: the Phase 3 stack (TESTS/handoff/p3_stack.py) plus schedulers, a
controllable scheduler clock, the WhatsApp provider stub and the realtime relays.

Reuses everything of p3_stack.Stack (private PostgreSQL with the Django schema and every
BE_GO operational migration, private Redis, counting SMTP, Django workers, compiled Go
worker/consumer/probe with -tags handoff, the file barriers, the P3 controller seams) and adds:

- Beat: ``celery -A core beat -S instance.scheduler.InstanceScheduler`` subprocesses on
  TESTS.handoff.p4_django_settings, with ASSOZETA_HANDOFF_CLOCK_FILE in their environment.
- Go scheduler: ``cmd/scheduler`` (-tags handoff, race build) with test-speed bounds, the
  Phase 4 flags --lease and --handoff-heartbeat, and ASSOZETA_HANDOFF_CLOCK_FILE.
- Clock: ``set_clock(instant)`` writes the scheduler clock file atomically (one ISO 8601 line
  with an explicit offset); ``tick(instant)`` sets it and waits for the active scheduler's
  ``schedule:ticked`` barrier arrival for that instant (key = epoch milliseconds), the
  positive evidence that a full evaluation pass at that instant (publications committed)
  finished. No timed sleep decides a schedule outcome.
- WhatsApp provider stub (Part B) and the relays (Part B) are added by p4_outbox.py.

Nothing here is a production default; the contract is in test_p4_scheduling.py.
"""
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from TESTS.handoff.p3_stack import (DB_TASK, REDIS_PASSWORD, ROOT, ContractFailure, Stack, check, wait_for)

# Read at import: Stack.init_django replaces os.environ with the private stack environment.
PROBE = os.environ.get('ASSOZETA_HANDOFF_P4_PROBE') == '1'
BEAT_APP = 'handoff-django-beat'
GO_SCHEDULER_APP = 'assozeta-go-scheduler'
P4_MIGRATION = '000007_schedule_authority'
SEED_TABLE = 'assozeta_periodic_seed'
LEASE_TABLE = 'assozeta_scheduler_lease'
RECEIPTS = 'assozeta_go_periodic_occurrences'
P4A_COLUMNS = {
    SEED_TABLE: {'name', 'task', 'definition', 'definition_sha256', 'seeded_at', 'seeded_by'},
    LEASE_TABLE: {'singleton', 'runtime', 'holder', 'scheduler_generation', 'ownership_generation', 'acquired_at',
                  'heartbeat_at', 'lease_until'},
    RECEIPTS: {'id', 'periodic_task_id', 'source_name', 'dispatch_sequence', 'job_id', 'dispatched_at',
               'ownership_generation', 'definition_sha256', 'runtime', 'task_id', 'scheduler_generation'},
}
P4A_FUNCTIONS = ('assozeta_seed_periodic_definitions(text,jsonb)',
                 'assozeta_scheduler_acquire(text,text,double precision)',
                 'assozeta_scheduler_fence(text,bigint)')
BEAT_TABLES = ('django_celery_beat_periodictask', 'django_celery_beat_intervalschedule',
               'django_celery_beat_crontabschedule', 'django_celery_beat_clockedschedule',
               'django_celery_beat_solarschedule', 'django_celery_beat_periodictasks')


def utc(text):
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def clock_key(instant):
    return str(int(round(instant.timestamp() * 1000)))


class P4Stack(Stack):
    def __init__(self, output):
        super().__init__(output)
        self.clock_file = self.work / 'clock'
        self.scheduler_processes = []

    # -- lifecycle --------------------------------------------------------------------------------
    def build_go(self):
        super().build_go()
        command = ['go', 'build', '-tags', 'handoff', '-race', '-o', str(self.work / 'scheduler'), './cmd/scheduler']
        self.run(command, cwd=ROOT / 'BE_GO', env=dict(self.base_env), timeout=600)

    def django_environment(self):
        values = super().django_environment()
        values.update(DJANGO_SETTINGS_MODULE='TESTS.handoff.p4_django_settings', ASSOZETA_HANDOFF_REDIS_PASSWORD=REDIS_PASSWORD,
                      ASSOZETA_SCHEDULER_LEASE_SECONDS='1')
        values.update(self.extra_django_environment())
        return values

    def extra_django_environment(self):
        return {}

    def reset(self):
        super().reset()
        self.scheduler_processes = []
        present = [t for t in (SEED_TABLE, LEASE_TABLE) if self.exists('public.' + t)]
        self.sql('TRUNCATE ' + ', '.join([*BEAT_TABLES, *('public.' + t for t in present)]) + ' RESTART IDENTITY CASCADE')
        with suppress(FileNotFoundError):
            self.clock_file.unlink()

    # -- schema -----------------------------------------------------------------------------------
    def require_handoff_ledger(self):
        """Phase 3 prerequisite (contract P3-A): a ContractFailure, never a crash, when absent."""
        for table in ('assozeta_handoff_work', 'assozeta_handoff_consumers'):
            self.require_table(table)
        check('admission' in self.columns('assozeta_go_job_ownership'),
              'contract: assozeta_go_job_ownership.admission is missing (migration 000006_job_handoff)')

    def require_schedule_schema(self):
        self.require_handoff_ledger()
        if PROBE:
            return  # diagnostic only: run past the schema gate to see the behavioural red (never CI evidence)
        text = (ROOT / 'BE_GO/migrations/migrations.go').read_text()
        for suffix in ('up', 'down'):
            check((ROOT / f'BE_GO/migrations/{P4_MIGRATION}.{suffix}.sql').exists(),
                  f'contract: BE_GO/migrations/{P4_MIGRATION}.{{up,down}}.sql missing (migration {P4_MIGRATION})')
        import re
        version = int(re.search(r'^const CurrentVersion = (\d+)$', text, re.M)[1])
        check(version >= 7, f'contract: migrations.CurrentVersion must be >= 7 (migration {P4_MIGRATION})')
        for table, columns in P4A_COLUMNS.items():
            check(self.exists('public.' + table), f'contract: public.{table} does not exist (migration {P4_MIGRATION})')
            missing = columns - self.columns(table)
            check(not missing, f'contract: public.{table} lacks columns {sorted(missing)} (migration {P4_MIGRATION})')
        for signature in P4A_FUNCTIONS:
            check(self.sql('SELECT to_regprocedure(%s) IS NOT NULL', ('public.' + signature,))[0][0],
                  f'contract: public.{signature} is missing (migration {P4_MIGRATION})')
        roles = self.sql("""SELECT pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid='public.assozeta_handoff_consumers'::regclass AND contype='c'""")
        check(any('scheduler' in row[0] for row in roles),
              f"contract: assozeta_handoff_consumers.role accepts 'scheduler' (migration {P4_MIGRATION})")

    # -- clock ------------------------------------------------------------------------------------
    def set_clock(self, instant):
        temporary = self.clock_file.with_suffix('.tmp')
        temporary.write_text(instant.astimezone(timezone.utc).isoformat() + '\n')
        os.replace(temporary, self.clock_file)

    def tick(self, instant, who='the active scheduler', timeout=30.0):
        """Set the scheduler clock and wait for one completed evaluation pass at that instant."""
        key = clock_key(instant)
        with suppress(FileNotFoundError):
            (self.barrier.directory / f'arrived.schedule:ticked.{key}').unlink()
        self.set_clock(instant)
        self.barrier.wait_arrived('schedule:ticked', key, who, timeout=timeout)

    # -- schedulers -------------------------------------------------------------------------------
    def beat(self, name='beat'):
        """A real Beat process (InstanceScheduler) on the private stack, clock seam enabled."""
        log = self.output / f'beat-{len(self.children) + 1}.log'
        stream = log.open('w')
        env = dict(self.env, ASSOZETA_HANDOFF_APPLICATION=BEAT_APP, ASSOZETA_HANDOFF_CLOCK_FILE=str(self.clock_file))
        process = subprocess.Popen([sys.executable, '-m', 'celery', '-A', 'core', 'beat', '-S',
                                    'instance.scheduler.InstanceScheduler', '-l', 'WARNING', '--pidfile=',
                                    '-s', str(self.work / f'{name}-{len(self.children)}-schedule')],
                                   cwd=ROOT / 'BE', env=env, stdout=stream, stderr=subprocess.STDOUT)
        self.children.append((process, stream, log))
        self.scheduler_processes.append(process)
        return process

    def go_scheduler(self, lease='1s'):
        """cmd/scheduler (-tags handoff, race build) with test bounds, the Phase 4 flags and the clock seam."""
        log = self.output / f'go-scheduler-{len(self.children) + 1}.log'
        stream = log.open('w')
        env = dict(self.base_env, ASSOZETA_HANDOFF_BARRIER_DIR=str(self.barrier.directory),
                   ASSOZETA_HANDOFF_CLOCK_FILE=str(self.clock_file))
        command = [str(self.work / 'scheduler'), '--env-file', str(self.go_env_file()), '--page-size', '64',
                   '--poll-interval', '50ms', '--tick-timeout', '3s', '--source-timeout', '1s',
                   '--lease', lease, '--handoff-heartbeat', '100ms']
        process = subprocess.Popen(command, env=env, stdout=stream, stderr=subprocess.STDOUT)
        self.children.append((process, stream, log))
        self.scheduler_processes.append(process)
        wait_for(lambda: 'scheduler process initialized' in log.read_text() or process.poll() is not None,
                 'Go scheduler startup', timeout=30)
        if process.poll() is not None:
            text = log.read_text()
            check('invalid scheduler arguments' not in text,
                  'contract: cmd/scheduler refused --lease/--handoff-heartbeat (Phase 4 scheduler lease flags missing)')
            raise ContractFailure('Go scheduler exited during startup: ' + text[-800:])
        return process

    def print_declarations(self):
        completed = subprocess.run([str(self.work / 'scheduler'), '--print-declarations'], env=dict(self.base_env),
                                   capture_output=True, text=True, timeout=60)
        check(completed.returncode == 0, 'contract: cmd/scheduler --print-declarations (the embedded periodic '
                                         f'declarations as JSON) failed: {(completed.stdout + completed.stderr)[-400:]}')
        try:
            return json.loads(completed.stdout)
        except ValueError:
            raise ContractFailure('contract: cmd/scheduler --print-declarations must print one JSON array') from None

    # -- definitions (Django ORM in the driver: the same tables both schedulers read) ---------------
    def beat_models(self):
        from django_celery_beat import models
        return models

    def periodic(self, name):
        return self.beat_models().PeriodicTask.objects.get(name=name)

    def changed(self):
        """Bulk edits bypass the model signals: bump the Beat change marker like an admin save."""
        self.beat_models().PeriodicTasks.update_changed()

    def disable_all(self, keep=()):
        models = self.beat_models()
        models.PeriodicTask.objects.exclude(name__in=keep).update(enabled=False)
        self.changed()

    def add_definition(self, name, *, task=DB_TASK, every=None, period='seconds', cron=None, clocked=None,
                       last_run_at=None, enabled=True, one_off=False, total_run_count=0, kwargs='{}', args='[]'):
        models = self.beat_models()
        fields = dict(task=task, enabled=enabled, one_off=one_off, total_run_count=total_run_count, args=args,
                      kwargs=kwargs, last_run_at=last_run_at)
        if every is not None:
            fields['interval'], _ = models.IntervalSchedule.objects.get_or_create(every=every, period=period)
        if cron is not None:
            values = dict(minute='*', hour='*', day_of_week='*', day_of_month='*', month_of_year='*', timezone='UTC')
            values.update(cron)
            fields['crontab'] = models.CrontabSchedule.objects.create(**values)
        if clocked is not None:
            fields['clocked'] = models.ClockedSchedule.objects.create(clocked_time=clocked)
        return models.PeriodicTask.objects.create(name=name, **fields)

    def definitions(self):
        """Every persisted definition, normalized (no ids, cursors or change stamps)."""
        result = {}
        for row in self.beat_models().PeriodicTask.objects.select_related('interval', 'crontab', 'clocked').order_by('name'):
            schedule = None
            if row.interval_id:
                schedule = ('interval', row.interval.every, row.interval.period)
            elif row.crontab_id:
                c = row.crontab
                schedule = ('crontab', c.minute, c.hour, c.day_of_week, c.day_of_month, c.month_of_year, str(c.timezone))
            elif row.clocked_id:
                schedule = ('clocked', row.clocked.clocked_time.isoformat())
            elif row.solar_id:
                schedule = ('solar', row.solar_id)
            result[row.name] = {
                'task': row.task, 'enabled': row.enabled, 'one_off': row.one_off, 'schedule': schedule,
                'args': json.loads(row.args or '[]'), 'kwargs': json.loads(row.kwargs or '{}'), 'queue': row.queue or None,
                'exchange': row.exchange or None, 'routing_key': row.routing_key or None, 'priority': row.priority,
                'headers': json.loads(row.headers or '{}'), 'expire_seconds': row.expire_seconds,
                'expires': row.expires.isoformat() if row.expires else None,
                'start_time': row.start_time.isoformat() if row.start_time else None}
        return result

    def cursor(self, name):
        row = self.periodic(name)
        return row.last_run_at, row.total_run_count, row.enabled

    def seed_rows(self):
        return {name: {'task': task, 'definition': definition, 'sha256': digest, 'seeded_by': by, 'seeded_at': at}
                for name, task, definition, digest, by, at in self.sql(
                    f'SELECT name, task, definition, definition_sha256, seeded_by, seeded_at FROM public.{SEED_TABLE}')}

    def receipts(self, source_name=None):
        clause, parameters = ('WHERE source_name=%s', (source_name,)) if source_name else ('', ())
        with self.connection.cursor() as cursor:
            cursor.execute(f'SELECT * FROM public.{RECEIPTS} {clause} ORDER BY dispatched_at, dispatch_sequence', parameters)
            return [dict(zip([c.name for c in cursor.description], row)) for row in cursor.fetchall()]

    def lease(self):
        with self.connection.cursor() as cursor:
            cursor.execute(f'SELECT * FROM public.{LEASE_TABLE} WHERE singleton=1')
            row = cursor.fetchone()
            return dict(zip([c.name for c in cursor.description], row)) if row else None

    # -- switch (the Phase 3 controller seams + the Phase 1 commit) --------------------------------
    def switch(self, source, generation, target, *, stop=(), staged=None, heartbeat_grace=1.0):
        """close_admission -> settle -> stop source processes -> stage_backlog -> commit -> activate."""
        import uuid
        operation = uuid.uuid4()
        record = self.record(operation, source, generation, target)
        steps = self.handoff_steps(heartbeat_grace=heartbeat_grace)
        steps['close_admission'](record)
        steps['settle'](record)
        for process in stop:
            self.stop(process)
        steps['stage_backlog'](record)
        if staged:
            staged(record)
        outcome = tuple(self.commit_authority(str(operation), source, generation, target))
        check(outcome == ('committed', target, generation + 1), f'authority commit {source}->{target}: {outcome}')
        steps['activate'](record)
        return generation + 1

    def freeze(self, source, generation, heartbeat_grace=1.0):
        """Only close admission (the freeze boundary); returns (record, steps) to continue the switch."""
        import uuid
        operation = uuid.uuid4()
        record = self.record(operation, source, generation, 'go' if source == 'django' else 'django')
        steps = self.handoff_steps(heartbeat_grace=heartbeat_grace)
        steps['close_admission'](record)
        return record, steps

    def finish_switch(self, record, steps, *, stop=()):
        steps['settle'](record)
        for process in stop:
            self.stop(process)
        steps['stage_backlog'](record)
        source, generation, target = record['previous_runtime'], record['source_generation'], record['target']
        outcome = tuple(self.commit_authority(record['id'], source, generation, target))
        check(outcome == ('committed', target, generation + 1), f'authority commit {source}->{target}: {outcome}')
        steps['activate'](record)
        return generation + 1

    # -- results backend ----------------------------------------------------------------------------
    def seed_results(self, rows):
        """django_celery_results rows {task_id: date_done} (status SUCCESS)."""
        from django_celery_results.models import TaskResult
        for task_id, done in rows.items():
            TaskResult.objects.create(task_id=task_id, status='SUCCESS', content_type='application/json',
                                      content_encoding='utf-8', result='null')
            self.sql('UPDATE django_celery_results_taskresult SET date_done=%s, date_created=%s WHERE task_id=%s',
                     (done, done, task_id))

    def result_ids(self):
        return {row[0] for row in self.sql('SELECT task_id FROM django_celery_results_taskresult')}


def day_start(days_ahead=1):
    """Midnight UTC `days_ahead` days after the real current day (always after any seeding stamp)."""
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=days_ahead)
