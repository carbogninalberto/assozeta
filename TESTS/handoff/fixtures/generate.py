"""Regenerate sanitized Phase 0 state and observe the historical Compose topology.

Run from the repository root with Python, OpenSSL and the Docker Compose CLI.
The archived distribution runs through the existing fake Docker harness.
"""
import argparse
import ast
import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import uuid

from TESTS.handoff.topology import Installation, ROOT

DIRECTORY = Path(__file__).resolve().parent
REVISION = '2284ed467908b5f90f6330fae4c18de3b275036f'
CAPTURED_AT = '2026-11-20T12:00:00Z'
CAPTURED_TIMESTAMP = int(datetime.fromisoformat(CAPTURED_AT).timestamp())
FUTURE_ETA = '2026-11-20T12:30:00Z'
GENERATE = 'python3 -B -m TESTS.handoff.fixtures.generate'
FIXTURE_NAMESPACE = uuid.UUID('633da484-983d-4b1c-a106-13778c716181')
ZERO_UUID = str(uuid.UUID(int=0))
CONFIG_CACHE = 'assozeta_instance_config'
OVERRIDE = 'assozeta_api_override'
REPLACEMENTS = {
    'ADMIN_URL': 'fixture-admin/',
    'AWS_SECRET_ACCESS_KEY': 'fixture-storage',
    'CRM_API_TOKEN': 'fixture-crm',
    'DBPASSWORD': 'fixture-database',
    'DOCUMENT_BYPASS_TOKEN': 'fixture-document',
    'INSTANCE_SETUP_TOKEN': 'fixture-setup',
    'JWT_PUBLIC_KEY_B64': 'fixture-jwt-public',
    'JWT_SECRET_KEY_B64': 'fixture-jwt-private',
    'MINIO_ROOT_PASSWORD': 'fixture-storage',
    'REDIS_PASSWORD': 'fixture-redis',
    'SECRET_KEY': 'fixture-django',
    'SENJA_WEBHOOK_SECRET': 'fixture-webhook',
}


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n'


def fixture_uuid(label):
    return str(uuid.uuid5(FIXTURE_NAMESPACE, label))


def routed_names():
    source = subprocess.check_output(
        ['git', 'show', f'{REVISION}:selfhost/updater/go_tasks.py'], cwd=ROOT, text=True)
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'GO_TASK_NAMES'
                                               for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('pinned revision has no GO_TASK_NAMES')


def browser(name, version=None, override=None):
    local = {CONFIG_CACHE: json.dumps({'features': {'uiApiVersion': version}})} if version else {}
    session = {OVERRIDE: override} if override else {}
    return {'name': name, 'localStorage': local, 'sessionStorage': session}


def pending(fixture, label, task, queue='celery', *, args=(), kwargs=None, eta=None, expires=None,
            retries=0, priority=0):
    task_id = fixture_uuid(f'{fixture}:{label}')
    kwargs = kwargs or {}
    body = json.dumps([list(args), kwargs, dict(callbacks=None, errbacks=None, chain=None, chord=None)])
    return {
        'body': base64.b64encode(body.encode()).decode('ascii'),
        'content-type': 'application/json',
        'content-encoding': 'utf-8',
        'headers': {
            'lang': 'py', 'task': task, 'id': task_id, 'shadow': None, 'eta': eta,
            'expires': expires, 'group': None, 'group_index': None, 'retries': retries,
            'timelimit': [None, None], 'root_id': task_id, 'parent_id': None,
            'argsrepr': repr(args), 'kwargsrepr': repr(kwargs), 'origin': 'fixture@localhost',
            'ignore_result': False, 'replaced_task_nesting': 0, 'stamped_headers': None,
            'stamps': {}, 'assozeta_published_at': CAPTURED_TIMESTAMP,
        },
        'properties': {
            'correlation_id': task_id, 'reply_to': fixture_uuid(f'{fixture}:reply'),
            'delivery_mode': 2, 'delivery_info': {'exchange': '', 'routing_key': queue},
            'priority': priority, 'body_encoding': 'base64',
            'delivery_tag': fixture_uuid(f'{fixture}:{label}:delivery'),
        },
    }


def beat_row(identifier, name, task, *, enabled=True, interval_id=None, crontab_id=None):
    return dict(id=identifier, name=name, task=task, enabled=enabled, interval_id=interval_id,
                crontab_id=crontab_id, solar_id=None, clocked_id=None, args='[]', kwargs='{}',
                queue=None, exchange=None, routing_key=None, headers='{}', priority=None, expires=None,
                expire_seconds=None, one_off=False, start_time=None, last_run_at=None,
                total_run_count=0, date_changed=CAPTURED_AT, description='Synthetic handoff definition')


def database(mixed):
    configuration = dict(
        id=1, domain='localhost', configured_at=CAPTURED_AT, updated_at=CAPTURED_AT,
        name='Handoff fixture', abbreviation='Fixture', setup_provenance='legacy',
        self_hosted=True, ui_api_version='v2' if mixed else 'v1', go_tasks_enabled=mixed,
    )
    tasks = [
        beat_row(101, 'auto-mark-attendance', 'auto_mark_attendance', enabled=False, crontab_id=1),
        beat_row(102, 'fixture custom token sweep', 'clear_expired_subscription_tokens', interval_id=1),
    ]
    intervals = [dict(id=1, every=11, period='minutes')]
    if mixed:
        tasks.extend([
            dict(beat_row(103, 'deliver-whatsapp', 'communications.tasks.deliver_whatsapp', interval_id=2),
                 expire_seconds=10),
            dict(beat_row(104, 'reset-communication-daily-email-balance',
                          'reset_communication_daily_email_balance', crontab_id=2),
                 last_run_at=CAPTURED_AT, total_run_count=1),
        ])
        intervals.append(dict(id=2, every=10, period='seconds'))

    cron = [dict(id=1, minute='*/15', hour='*', day_of_week='*', day_of_month='*',
                 month_of_year='*', timezone='Europe/Rome')]
    if mixed:
        cron.append(dict(id=2, minute='0', hour='0', day_of_week='*', day_of_month='*',
                         month_of_year='*', timezone='Europe/Rome'))
    state = {
        'instance_instanceconfiguration': [configuration],
        'django_celery_beat_periodictask': tasks,
        'django_celery_beat_intervalschedule': intervals,
        'django_celery_beat_crontabschedule': cron,
    }
    if mixed:
        job_id = fixture_uuid('legacy-mixed:native-periodic-job')
        state.update({
            # The old four-name email switch does not change this SQL authority.
            'assozeta_go_job_ownership': [dict(singleton=1, runtime='django', generation=1)],
            'assozeta_go_jobs': [dict(
                id=job_id, association_id=ZERO_UUID,
                kind='reset_communication_daily_email_balance', version=1, payload={},
                idempotency_key='periodic:104:1', state='pending', attempts=0,
                max_attempts=3, available_at=CAPTURED_AT, created_at=CAPTURED_AT,
                finished_at=None, lease_owner='', lease_token=ZERO_UUID, lease_until=None,
                error_code='', result=None, ownership_generation=0,
            )],
            'communications_whatsappdelivery': [dict(
                id=fixture_uuid('legacy-mixed:whatsapp-delivery'), event_type='subscription_approved',
                recipient='+12025550123', body='Fixture subscription approved.', template_name='',
                template_language='', provider_message_id=None, status='queued', attempts=0,
                error_code='', error_message='', created_at=CAPTURED_AT,
                updated_at=CAPTURED_AT, next_attempt_at=CAPTURED_AT,
            )],
        })
    return state


def broker(name):
    if name == 'fresh':
        return {}
    celery = [pending(name, 'token-sweep', 'clear_expired_subscription_tokens')]
    if name == 'django-default':
        return {'celery': celery}
    celery.extend([
        pending(name, 'whatsapp-poll', 'communications.tasks.deliver_whatsapp',
                expires='2026-11-20T12:00:10Z'),
        pending(name, 'renewal-retry', 'renew_memberships_payments', eta=FUTURE_ETA,
                kwargs={'attempt': 2, 'max_attempts': 3}),
    ])
    mail = pending(name, 'smtp', 'core.tasks.send_mail_async', 'assozeta-go',
                   args=('Fixture reminder', 'Synthetic reminder', 'sender@example.invalid',
                         ['recipient@example.invalid']), eta=FUTURE_ETA, retries=1, priority=3)
    return {'celery': celery, 'assozeta-go': [mail]}


def observe(name, scratch):
    with tempfile.TemporaryDirectory(prefix='handoff-fixture-', dir=scratch) as work:
        install = Installation(work, revision=REVISION)
        if name == 'fresh':
            install.install()
            return None, sorted(install.running())
        install.cli('configure', '--domain', 'localhost', '--version', 'test')
        env = install.env()
        env.update({key: value for key, value in REPLACEMENTS.items() if key in env})
        if name == 'legacy-mixed':
            env.update(ASSOZETA_GO_TASKS='1', GO_TASK_QUEUE_TASKS=','.join(routed_names()))
        install.env_file.write_text(''.join(f'{key}={value}\n' for key, value in env.items()))
        install.cli('start')
        return env, sorted(install.running())


def generate(scratch):
    setups = {
        'fresh': ('Empty host and cold client; observed services are from stock CLI install.', [browser('cold')]),
        'django-default': (
            'Configured legacy v1 instance with default task routing, custom and disabled Beat definitions.',
            [browser('legacy-v1', 'v1')],
        ),
        'legacy-mixed': (
            'Configured legacy v2 instance with four email routes, pending work on both queues and persisted outbox work.',
            [browser('legacy-v2', 'v2'), browser('legacy-v1', 'v1', 'v1')],
        ),
    }
    manifest = {'format': 1, 'fixtures': {}}
    for name, (description, browsers) in setups.items():
        env, running = observe(name, scratch)
        state = {'env': env, 'database': database(name == 'legacy-mixed') if env else None,
                 'broker': broker(name), 'browser': browsers}
        files, digests = {}, {}
        for key, value in state.items():
            files[key] = None
            if value is not None:
                name_in_manifest = f'{name}/{key}.json'
                path = DIRECTORY / name_in_manifest
                path.parent.mkdir(exist_ok=True)
                data = json_text(value).encode('utf-8')
                path.write_bytes(data)
                files[key] = name_in_manifest
                digests[name_in_manifest] = hashlib.sha256(data).hexdigest()
        manifest['fixtures'][name] = dict(
            description=description, revision=REVISION, captured_at=CAPTURED_AT, generator=GENERATE,
            files=files, sha256=digests, observed={'running_services': running},
        )
    (DIRECTORY / 'manifest.json').write_text(json_text(manifest))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', type=Path, help='parent directory for disposable fixture installations')
    generate(parser.parse_args().scratch)
