"""Compare ordinary-job restore admission with the actual Celery task base."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import subprocess

from .accounts import ROOT
from .scoped_resources import uid


def run(work, log, tool_env):
    from auditlog.context import disable_auditlog
    from celery import current_app
    from celery.exceptions import Retry
    from django.test import override_settings
    from instance.models import DataRestore
    from instance.restore.locking import operation_lock
    from instance.restore.task_base import CoordinatedTask, StaleDataTask

    out = ROOT / 'quality-reports/go-migration/job-executor'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'django-admission-results.json').write_text('{"success":false}\n')
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    def stamp(value): return value.isoformat() if value is not None else None
    def row(state, completed=None): return {'state': state, 'completed': stamp(completed)}
    cases = [
        {'name': 'empty', 'rows': [], 'expected': 'executed'},
        {'name': 'inactive receipts', 'rows': [row('review', now + timedelta(days=1)), row('failed', now + timedelta(days=1))], 'expected': 'executed'},
        {'name': 'completed without timestamp', 'rows': [row('completed')], 'expected': 'executed'},
        {'name': 'published after restore', 'rows': [row('completed', now - timedelta(days=1))], 'expected': 'executed'},
        {'name': 'equal cutoff allowed', 'rows': [row('completed', now)], 'expected': 'executed'},
        {'name': 'one microsecond stale', 'rows': [row('completed', now + timedelta(microseconds=1))], 'expected': 'stale'},
        {'name': 'latest completion wins', 'rows': [row('completed', now - timedelta(days=1)), row('completed', now + timedelta(days=1))], 'expected': 'stale'},
        {'name': 'queued precedes stale check', 'rows': [row('queued'), row('completed', now + timedelta(days=1))], 'expected': 'deferred'},
        {'name': 'running precedes stale check', 'rows': [row('running'), row('completed', now + timedelta(days=1))], 'expected': 'deferred'},
        {'name': 'exclusive lock', 'rows': [], 'locked': True, 'expected': 'deferred'},
        {'name': 'unavailable volume', 'rows': [], 'unavailable': True, 'expected': 'deferred'},
        {'name': 'disabled pending and stale checks', 'rows': [row('queued'), row('completed', now + timedelta(days=1))], 'configured': False, 'expected': 'executed'},
        {'name': 'failed completion ignored', 'rows': [row('completed', now - timedelta(days=1)), row('failed', now + timedelta(days=1))], 'expected': 'executed'},
    ]
    for case in cases:
        case.setdefault('configured', True)
        case.setdefault('locked', False)
        case.setdefault('unavailable', False)
        case['published'] = stamp(now)
    ids = [uid(88300), uid(88301)]
    assert not DataRestore.objects.filter(id__in=ids).exists(), 'job receipt fixture IDs collide'
    DataRestore.objects.bulk_create([DataRestore(id=pk, owner_id=uid(8), association_id=uid(80), state='review', sha256='0'*64) for pk in ids])
    calls = []
    class Probe(CoordinatedTask):
        name = 'contract.go_job_restore_admission'
        def run(self):
            calls.append('executed')
            return 'executed'
    assert Probe.name not in current_app.tasks, 'task fixture name collides'
    task = current_app.register_task(Probe())
    try:
        for case in cases:
            DataRestore.objects.filter(id__in=ids).update(state='review', completed_at=None)
            for pk, receipt in zip(ids, case['rows']):
                DataRestore.objects.filter(pk=pk).update(state=receipt['state'], completed_at=receipt['completed'])
            lock = '' if not case['configured'] else str(work) if case['unavailable'] else str(work / 'job-admission.lock')
            calls.clear()
            with ExitStack() as stack:
                stack.enter_context(override_settings(DATA_RESTORE_LOCK_PATH=lock))
                if case['locked']:
                    stack.enter_context(operation_lock(exclusive=True))
                task.push_request(id='fixture-job', args=(), kwargs={}, retries=3, called_directly=False,
                                  is_eager=True, headers={'assozeta_published_at': now.timestamp()})
                try:
                    actual = task()
                except Retry as exc:
                    actual = 'deferred'
                    assert exc.when == 15 and exc.sig.options['retries'] == 3, 'maintenance spent retry budget'
                    assert exc.sig.options['headers']['assozeta_published_at'] == now.timestamp(), 'publication timestamp was refreshed'
                except StaleDataTask:
                    actual = 'stale'
                finally:
                    task.pop_request()
            assert actual == case['expected'], (case['name'], actual)
            assert calls == (['executed'] if actual == 'executed' else []), 'denied task had effects'
        fixture = work / 'job-admission.json'
        fixture.write_text(json.dumps(cases))
        completed = subprocess.run(['go', 'test', '-race', '-tags=jobsintegration', './internal/service', '-run', '^TestJobRestoreAdmissionDjango$', '-json'],
                       cwd=ROOT / 'BE_GO', env={**tool_env, 'ASSOZETA_JOB_ADMISSION_FIXTURE': str(fixture),
                       'ASSOZETA_JOB_TEST_DSN': f'host={work} user=contracts dbname=assozeta_contract_accounts sslmode=disable'},
                       capture_output=True, text=True, timeout=90)
        (out / 'django-admission-go.jsonl').write_text(completed.stdout)
        events = [json.loads(line) for line in completed.stdout.splitlines()]
        for event in events:
            if event.get('Output'):
                print(event['Output'], end='')
        if completed.stderr:
            print(completed.stderr)
        completed.check_returncode()
        expected = {'TestJobRestoreAdmissionDjango/' + case['name'].replace(' ', '_') for case in cases}
        passed = {event.get('Test') for event in events if event['Action'] == 'pass' and '/' in event.get('Test', '')}
        assert passed == expected and not any(event['Action'] == 'skip' for event in events), 'missing or skipped Django/Go admission scenario'
        report = {'success': True, 'cases_per_backend': len(cases), 'django': 'actual CoordinatedTask with eager Retry; no broker publication',
                  'go': 'actual executor, job repository and shared restore lease', 'scope': 'ordinary task admission; not task bodies or scheduler ownership'}
        (out / 'django-admission-results.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report), flush=True)
        return len(cases)
    finally:
        current_app.tasks.pop(Probe.name, None)
        with disable_auditlog():
            DataRestore.objects.filter(id__in=ids).delete()
