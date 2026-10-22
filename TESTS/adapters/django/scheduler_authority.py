"""Measure the pinned, actual Django Beat implementation in private PostgreSQL.

Only django-celery-beat migrations run. Synthetic schedules never execute a
business task; publication uses an unconsumed in-memory broker. Frozen clocks
exercise ModelEntry directly, separately from DatabaseScheduler's heap filters.
This is current-schema authority evidence, not historical restore or handoff.
"""
import ast
from datetime import datetime
import importlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
OUTPUT = ROOT / 'quality-reports/go-migration/scheduler-authority'
FIXTURE = ROOT / 'TESTS/fixtures/scheduler-authority.json'


def timestamp(value):
    return datetime.fromisoformat(value)


def declarations(path, target):
    """Inspect actual declarations without executing a second configuration."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not any(ast.unparse(t) == target for t in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            raise AssertionError('Schedule declaration is no longer a dictionary')
        result = {}
        for key, value in zip(node.value.keys, node.value.values):
            fields = {ast.literal_eval(k): v for k, v in zip(value.keys, value.values)}
            raw = fields['schedule']
            if isinstance(raw, ast.Call) and ast.unparse(raw.func) == 'crontab':
                schedule = {'kind': 'cron', **dict.fromkeys(
                    ['minute', 'hour', 'day_of_week', 'day_of_month', 'month_of_year'], '*')}
                schedule.update({item.arg: ast.literal_eval(item.value) for item in raw.keywords})
            else:
                schedule = {'kind': 'interval', 'seconds': ast.literal_eval(raw)}
            result[ast.literal_eval(key)] = {
                'source': str(path.relative_to(ROOT)), 'task': ast.literal_eval(fields['task']),
                'schedule': schedule, 'args': list(ast.literal_eval(fields['args'])) if 'args' in fields else [],
            }
        return result
    raise AssertionError(f'Missing declaration {target}')


def observe(fixture):
    import django
    with patch('environ.Env.read_env'):
        django.setup()
    from core.celery import app
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection
    from django.apps import apps
    from django.core.exceptions import ValidationError
    from django_celery_beat.models import (
        PeriodicTask, PeriodicTasks, IntervalSchedule, CrontabSchedule,
        ClockedSchedule, SolarSchedule,
    )
    from django_celery_beat.schedulers import ModelEntry
    from instance.scheduler import InstanceScheduler
    from celery.beat import SchedulingError
    from kombu import Exchange, Queue

    observations = {}
    effective = app.conf.beat_schedule
    declared = {**declarations(ROOT / 'BE/core/celery.py', 'app.conf.beat_schedule'),
                **declarations(ROOT / 'BE/core/settings.py', 'CELERY_BEAT_SCHEDULE')}
    observations['configuration_authority'] = {
        'declared_count': len(declared), 'declarations': declared,
        'effective_names': sorted(effective),
        'effective_task': effective['bakney-pairing-maintenance']['task'],
        'effective_interval_seconds': effective['bakney-pairing-maintenance']['schedule'],
        'timezone': str(app.timezone), 'enable_utc': app.conf.enable_utc,
        'rate_annotations': {name: value['rate_limit'] for name, value in app.conf.task_annotations.items()},
    }
    # Preserve schedule authority; replace only external transports after measuring
    # actual core.settings/core.celery. Never touch developer .env or Redis.
    settings.CELERY_BROKER_URL = 'memory://'
    settings.CELERY_RESULT_BACKEND = 'cache+memory://'
    settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    app.conf.update(CELERY_BROKER_URL='memory://', CELERY_RESULT_BACKEND='cache+memory://',
                    CELERY_TASK_ALWAYS_EAGER=False)
    app.set_current()
    app.set_default()
    call_command('migrate', 'django_celery_beat', interactive=False, verbosity=0)
    schedulers = []

    def scheduler():
        value = InstanceScheduler(app=app, lazy=False)
        schedulers.append(value)
        return value

    def clear():
        for value in schedulers:
            value.close()
            value._finalize.cancel()
        schedulers.clear()
        PeriodicTask.objects.all().delete()

    def interval(every=60):
        return IntervalSchedule.objects.create(every=every, period='seconds')

    def row(name, **fields):
        fields.setdefault('interval', interval())
        fields.setdefault('task', 'contracts.scheduler.capture')
        return PeriodicTask.objects.create(name=name, **fields)

    fixed = timestamp('2026-10-22T12:00:00+00:00')
    try:
        scheduler()
        cleanup = PeriodicTask.objects.get(name='celery.backend_cleanup')
        observations['cold_bootstrap'] = {
            'names': list(PeriodicTask.objects.order_by('name').values_list('name', flat=True)),
            'cleanup_hour': cleanup.crontab.hour, 'cleanup_minute': cleanup.crontab.minute,
            'cleanup_expire_seconds': cleanup.expire_seconds,
        }
        clear()
        row('historical-business', task='renew_memberships_payments')
        row('custom', task='custom.unknown.task')
        configured = row('bakney-pairing-maintenance', task='previous.task', enabled=False,
                         total_run_count=7, one_off=True, start_time=timestamp('2026-10-23T12:00:00+00:00'),
                         args='[9]', kwargs='{"old":true}', queue='previous')
        scheduler()
        configured.refresh_from_db()
        observations['persisted_reconciliation'] = {
            'custom_retained': PeriodicTask.objects.filter(name='custom').exists(),
            'historical_business_retained': PeriodicTask.objects.filter(name='historical-business').exists(),
            'configured_task': configured.task, 'configured_interval_seconds': configured.interval.every,
            'enabled': configured.enabled, 'last_run_at': configured.last_run_at,
            'total_run_count': configured.total_run_count, 'one_off': configured.one_off,
            'start_time': configured.start_time.isoformat(), 'args': json.loads(configured.args),
            'kwargs': json.loads(configured.kwargs), 'queue': configured.queue,
        }
        clear()
        # These are direct ModelEntry tests; no claim about heap inclusion is made.
        for name, specification in fixture['cases'].items():
            data = specification.get('input')
            if not data:
                continue
            now = timestamp(data['now'])
            fields = dict(task='contracts.scheduler.capture', last_run_at=timestamp(data['last']),
                          enabled=data.get('enabled', True), one_off=data.get('one_off', False),
                          total_run_count=data.get('count', 0))
            if data['kind'] == 'interval':
                fields['interval'] = interval(data['every'])
            elif data['kind'] == 'cron':
                fields['crontab'] = CrontabSchedule.objects.create(
                    minute=data['minute'], hour=data.get('hour', '*'),
                    timezone=data.get('timezone', 'UTC'), day_of_month=data.get('day_of_month', '*'),
                    day_of_week=data.get('day_of_week', '*'))
            else:
                fields['clocked'] = ClockedSchedule.objects.create(clocked_time=timestamp(data['clocked']))
            for source, target in [('start', 'start_time'), ('expires', 'expires')]:
                if source in data:
                    fields[target] = timestamp(data[source])
            if 'expire_seconds' in data:
                fields['expire_seconds'] = data['expire_seconds']
            model = PeriodicTask.objects.create(name=name, **fields)
            with patch.object(ModelEntry, '_default_now', return_value=now):
                entry = ModelEntry(model, app=app)
                entry.schedule.nowfun = lambda now=now, zone=entry.schedule.tz: now.astimezone(zone)
                due = entry.is_due()
            model.refresh_from_db()
            observations[name] = {'due': due.is_due, 'next_seconds': due.next,
                                  'enabled': model.enabled, 'count': model.total_run_count}
        clear()
        original = row('initial')
        changed = original.date_changed
        first = ModelEntry(original, app=app)
        starting = row('starting', start_time=fixed)
        changed_start = starting.date_changed
        second = ModelEntry(starting, app=app)
        observations['initial_cursor'] = {
            'uses_date_changed': first.last_run_at == changed,
            'start_shifts_days': (changed_start - second.last_run_at).days,
        }
        clear()
        bad_args = row('bad-args', args='{')
        ModelEntry(bad_args, app=app)
        bad_headers = row('bad-headers', headers='{')
        scheduled = scheduler().all_as_schedule()
        bad_args.refresh_from_db()
        bad_headers.refresh_from_db()
        observations['malformed_rows'] = {
            'bad_args_enabled': bad_args.enabled, 'bad_headers_enabled': bad_headers.enabled,
            'bad_headers_in_schedule': bad_headers.name in scheduled,
        }
        clear()
        solar = SolarSchedule.objects.create(event='sunrise', latitude=45, longitude=9)
        solar_error, missing_module = None, None
        try:
            solar.schedule
        except ModuleNotFoundError as error:
            solar_error, missing_module = type(error).__name__, error.name
        observations['solar_dependency'] = {
            'model_available': SolarSchedule.objects.filter(pk=solar.pk).exists(),
            'schedule_error': solar_error, 'missing_module': missing_module,
        }
        clock = ClockedSchedule.objects.create(clocked_time=fixed)
        invalid = PeriodicTask(name='invalid-clock', task='contracts.scheduler.capture', clocked=clock)
        requires_one_off = False
        try:
            invalid.validate_unique()
        except ValidationError as error:
            requires_one_off = 'one_off' in str(error)
        observations['clocked_validation'] = {'requires_one_off': requires_one_off}

        @app.task(name='contracts.scheduler.capture', ignore_result=True)
        def capture(*args, **kwargs):
            raise AssertionError('The scheduler oracle must never execute a task')

        clear()
        failed = row('failed', last_run_at=timestamp('2026-10-22T11:00:00+00:00'))
        engine = scheduler()
        error_name = None
        # The real reserve and finally/sync run; only the external publisher fails.
        with patch.object(ModelEntry, '_default_now', return_value=fixed), \
             patch.object(capture, 'apply_async', side_effect=OSError('fixture publisher unavailable')) as publish:
            try:
                engine.apply_async(engine.schedule[failed.name])
            except SchedulingError as error:
                error_name = type(error).__name__
        failed.refresh_from_db()
        if publish.call_count != 1:
            raise AssertionError('Expected exactly one attempted publication')
        observations['publication_failure'] = {
            'error': error_name, 'persisted_count': failed.total_run_count,
            'persisted_last': failed.last_run_at.isoformat(), 'successful_publications': 0,
        }
        clear()
        ticking = row('tick-failed', last_run_at=timestamp('2026-10-22T11:00:00+00:00'))
        engine = scheduler()
        # Keep the actual loaded row and heap algorithm. Freeze only clocks and
        # the external publisher/cache heartbeat, not is_due/reserve/sync.
        for entry in engine.schedule.values():
            entry.schedule.nowfun = lambda zone=entry.schedule.tz: fixed.astimezone(zone)
        with patch.object(ModelEntry, '_default_now', return_value=fixed), \
             patch.object(app, 'now', return_value=fixed), \
             patch.object(capture, 'apply_async', side_effect=OSError('fixture publisher unavailable')) as publish, \
             patch.object(InstanceScheduler, 'publish_heartbeat') as heartbeat:
            returned = engine.tick()
            engine._heartbeat_publisher.join(timeout=5)
            if engine._heartbeat_publisher.is_alive():
                raise AssertionError('Fixture heartbeat did not stop')
        ticking.refresh_from_db()
        observations['tick_publication_failure'] = {
            'returned': returned, 'persisted_count': ticking.total_run_count,
            'persisted_last': ticking.last_run_at.isoformat(),
            'publication_attempts': publish.call_count, 'successful_publications': 0,
            'heartbeat_called': heartbeat.call_count == 1,
        }
        clear()
        routed = row('routing', args='["fixture",3]', kwargs='{"flag":true}',
                     queue='scheduler-proof', exchange='scheduler-proof', routing_key='scheduler.proof',
                     priority=4, headers='{"proof":"kept","periodic_task_name":"spoofed"}', expire_seconds=45)
        engine = scheduler()
        queue = Queue('scheduler-proof', Exchange('scheduler-proof'), routing_key='scheduler.proof')
        with app.connection_for_write() as broker:
            bound = queue(broker)
            bound.declare()
            with broker.Producer() as producer:
                engine.apply_async(engine.schedule[routed.name], producer=producer)
            message = bound.get(no_ack=False)
            if message is None:
                raise AssertionError('No message reached the actual memory broker')
            payload = message.payload
            routed.refresh_from_db()
            observations['publication_routing'] = {
                'task': message.headers['task'], 'args': payload[0], 'kwargs': payload[1],
                'exchange': message.delivery_info['exchange'], 'routing_key': message.delivery_info['routing_key'],
                'priority': message.properties['priority'], 'header': message.headers['proof'],
                'periodic_task_name': message.headers['periodic_task_name'],
                'published_at_present': isinstance(message.headers.get('assozeta_published_at'), (int, float)),
                'expiration': message.properties['expiration'], 'count': routed.total_run_count,
            }
            message.ack()
            bound.delete()
        clear()
        updated = row('reload')
        engine = scheduler()
        before = engine.schedule['reload'].schedule.run_every.total_seconds()
        engine.schedule_changed()  # establish the pre-change marker
        updated.interval = interval(120)
        updated.save()
        after = engine.schedule['reload'].schedule.run_every.total_seconds()
        observations['database_change_reload'] = {'before': before, 'after': after}
        clear()
        aliases = ['auto_move_users_to_base_plan', 'application.tasks.auto_move_users_to_base_plan',
                   'nurturing_warm_leads', 'application.tasks.nurturing_warm_leads']
        for index, task in enumerate(aliases):
            row(f'retired-{index}', task=task)
        business = row('business', task='renew_memberships_payments')
        custom = row('custom', task='unregistered.custom')
        previous = PeriodicTasks.last_change()
        migration = importlib.import_module('application.migrations.0430_retire_software_plan_expiry')
        with connection.schema_editor() as editor:
            migration.disable_expiry_schedule(apps, editor)
        business.refresh_from_db()
        custom.refresh_from_db()
        observations['retired_aliases_only'] = {
            'retired_disabled': PeriodicTask.objects.filter(task__in=aliases, enabled=False).count(),
            'business_enabled': business.enabled, 'custom_enabled': custom.enabled,
            'marker_changed': PeriodicTasks.last_change() > previous,
        }
        clear()
        overdue = row('overdue', last_run_at=timestamp('2026-10-22T11:00:00+00:00'))
        with patch.object(ModelEntry, '_default_now', return_value=fixed):
            advanced = next(ModelEntry(overdue, app=app))
            advanced.save()
            advanced.schedule.nowfun = lambda: fixed
            due = advanced.is_due()
        overdue.refresh_from_db()
        observations['dispatch_cursor_no_catchup'] = {
            'count': overdue.total_run_count, 'last': overdue.last_run_at.isoformat(),
            'due_after_dispatch': due.is_due, 'next_seconds': due.next,
        }
        return observations
    finally:
        clear()


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    from TESTS.contracts.test_scheduler_authority import validate_report
    fixture = json.loads(FIXTURE.read_text())
    runtime = {name: version(name) for name in fixture['runtime']}
    if runtime != fixture['runtime']:
        raise RuntimeError('Pinned Celery/Beat dependencies required; no skipped oracle')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT / 'results.json'
    report_path.write_text(json.dumps({'success': False, 'phase': 'started'}) + '\n')
    postgres = shutil.which('postgres')
    if not postgres:
        candidate = Path('/opt/homebrew/opt/postgresql@16/bin/postgres')
        postgres = str(candidate) if candidate.exists() else None
    if not postgres:
        raise RuntimeError('Private PostgreSQL required')
    binaries = Path(postgres).parent
    tool_env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME', 'TMPDIR')}
    tool_env['LC_ALL'] = 'C'
    report = None
    with tempfile.TemporaryDirectory(prefix='az-beat-', dir='/tmp') as directory:
        work = Path(directory)
        startup_attempted = False
        with (OUTPUT / 'services.log').open('w') as log:
            def run(arguments):
                subprocess.run([str(arg) for arg in arguments], check=True, timeout=180,
                               stdout=log, stderr=subprocess.STDOUT, env=tool_env)
            try:
                run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts',
                     '--no-sync', '--no-locale', '-E', 'UTF8'])
                # pg_ctl can start its child before reporting failure or timing out.
                startup_attempted = True
                run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log',
                     '-o', f"-k {work} -h '' -F", '-w', 'start'])
                run([binaries / 'createdb', '-h', work, '-U', 'contracts', 'assozeta_contract_scheduler'])
                import environ
                os.environ.clear()
                os.environ.update(tool_env)
                environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
                os.environ.update(DJANGO_SETTINGS_MODULE='core.settings', DEBUG='False',
                    JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='', JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture',
                    DBTYPE='POSTGRES', DBHOST=str(work), DBPORT='5432', DBNAME='assozeta_contract_scheduler',
                    DBUSER='contracts', DBPASSWORD='fixture', USE_PGBOUNCER='False', STORE_ON_DISK='True',
                    CURRENT_HOST='http://127.0.0.1:9', CURRENT_HOST_PUPPETEER='http://127.0.0.1:9')
                cases = observe(fixture)
                report = {'schema_version': 1, 'runtime': runtime, 'cases': cases, 'scope': fixture['scope']}
                # Keep actual measurements even on mismatch; never rewrite expectations from them.
                (OUTPUT / 'observations.json').write_text(json.dumps(report, indent=2) + '\n')
                validate_report(report, fixture)
            finally:
                try:
                    from django.db import connections
                    from django.conf import settings
                    if settings.configured:
                        connections.close_all()
                finally:
                    try:
                        if (work / 'postgres.log').exists():
                            shutil.copyfile(work / 'postgres.log', OUTPUT / 'postgres.log')
                    finally:
                        # Diagnostic writes must never bypass private-service cleanup.
                        if startup_attempted:
                            try:
                                run([binaries / 'pg_ctl', '-D', work / 'postgres',
                                     '-m', 'immediate', '-w', 'stop'])
                            except subprocess.CalledProcessError:
                                # A startup failure may leave no server to stop.
                                # A remaining PID file is not a successful cleanup.
                                if (work / 'postgres/postmaster.pid').exists():
                                    raise
    report['success'] = True
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'success': True, 'cases': len(report['cases']), 'scope': report['scope']}))


if __name__ == '__main__':
    main()
