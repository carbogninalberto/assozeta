"""Inspect a mixed installation before backup, including pre-ledger databases."""
import json
from pathlib import Path
import sqlite3
import subprocess

import job_handoff
from common import SHARED_SERVICES, atomic_write, compose_command, compose_environment, read_env
from runtime_schedules import schedule_blockers

LEGACY_KEYS = {'ASSOZETA_GO_TASKS', 'GO_TASK_QUEUE_TASKS', 'ASSOZETA_GO_WHATSAPP_OUTBOX', 'ASSOZETA_REALTIME_RELAY_BACKEND',
               'ASSOZETA_DJANGO_RELAY_REPLICAS', 'ASSOZETA_GO_RELAY_REPLICAS',
               'ASSOZETA_BAKNEY_PROTOCOL_BACKEND', 'ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM'}


def retire_preferences(env_file, destination=None):
    file = Path(env_file)
    lines = file.read_bytes().splitlines(keepends=True)
    keys = {key.encode() for key in LEGACY_KEYS}
    kept = [line for line in lines if line.partition(b'=')[0] not in keys]
    if destination or kept != lines:
        atomic_write(destination or file, b''.join(kept))


class AdoptionInterrupted(Exception):
    pass


def previous_adoption(root):
    path = Path(root) / '.updater/operations.sqlite3'
    if not path.exists():
        return None
    # A preflight against the old distribution must never initialise a journal.
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        for (raw,) in db.execute('SELECT record FROM operations ORDER BY rowid DESC'):
            record = json.loads(raw)
            if record.get('adoption'):
                return record
    return None


def preflight(root, env_file, *, report_inventory=True):
    store = job_handoff.compose_store(root, env_file)
    tables = {r['tablename'] for r in store.rows(
        "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public'")}
    owner = {}
    if 'assozeta_go_job_ownership' in tables:
        rows = store.rows("SELECT runtime,generation,to_jsonb(o)->>'operation_id' AS op "
                          "FROM public.assozeta_go_job_ownership o WHERE singleton=1")
        owner = rows[0] if rows else {}
    previous = previous_adoption(root)
    if previous and previous['status'] in ('running', 'queued', 'recovery_required'):
        raise AdoptionInterrupted()
    required = not (owner.get('op') or (previous and previous['status'] == 'succeeded')
                    or Path(f'{env_file}.exclusive-install').is_file()
                    or (Path(root) / '.operations/exclusive-install').is_file())
    if not required and not report_inventory:
        return {'adoption_required': False, 'requested_runtime': owner.get('runtime', 'django'), 'blockers': []}
    setting = None
    if required and 'instance_instanceconfiguration' in tables:
        setting = store.scalar("SELECT to_jsonb(c)->>'ui_api_version' FROM public.instance_instanceconfiguration c "
                               "ORDER BY id LIMIT 1")
        if setting not in ('v1', 'v2'):
            setting = None
    target = ('go' if setting == 'v2' else 'django') if required else owner.get('runtime', 'django')
    command = compose_command(root, env_file) + ['--profile', '*', 'ps', '--status', 'running', '--services']
    services = subprocess.check_output(command, env=compose_environment(root, env_file), text=True, timeout=30)
    queues = dict.fromkeys(job_handoff.QUEUES, 0)
    problems = {}
    broker = job_handoff.compose_broker(root, env_file)
    catalog = job_handoff._task_catalog()
    for queue, _, _, _, raw in job_handoff._items(broker, 500):
        queues[queue] += 1
        if required:
            task, message = job_handoff._message(raw)
            reason = job_handoff._reason(message, catalog, target)
            if reason:
                problems[task] = reason
    jobs = 0
    if 'assozeta_go_jobs' in tables:
        where = "FROM public.assozeta_go_jobs WHERE state IN ('pending','running')"
        jobs = int(store.scalar('SELECT count(*) ' + where))
        if required:
            for row in store.rows('SELECT id,kind,version ' + where):
                if row['kind'] not in catalog or row['version'] != 1:
                    problems[row['id']] = 'unknown_task' if row['kind'] not in catalog else 'unsupported_option'
    outbox = (int(store.scalar("SELECT count(*) FROM public.communications_whatsappdelivery WHERE status='queued'"))
              if 'communications_whatsappdelivery' in tables else 0)
    if required and target == 'go' and 'django_celery_beat_periodictask' in tables:
        problems.update((row['task_id'], row['reason']) for row in schedule_blockers(store, catalog))
    if required and 'assozeta_handoff_work' in tables:
        record = {'id': '00000000-0000-0000-0000-000000000000', 'adoption': True, 'target': target,
                  'source_generation': owner['generation'], 'previous_runtime': owner['runtime']}
        problems.update((r['task_id'], r['reason']) for r in
                        job_handoff.handoff_steps(store, broker)['preflight'](record)['blockers'])
    return {'requested_runtime': target, 'decided_by': 'instance_setting' if setting else 'default',
            'instance_setting': setting, 'adoption_required': required,
            'legacy': {'services': sorted(set(services.splitlines()) - SHARED_SERVICES), 'queues': queues,
                       'sql_jobs': jobs, 'whatsapp_outbox': outbox,
                       'go_task_routing': bool(read_env(env_file).get('GO_TASK_QUEUE_TASKS', '').strip())},
            'blockers': [{'ref': task, 'reason': reason} for task, reason in sorted(problems.items())]}
