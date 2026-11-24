"""Matrix family backup_notify: instance.backups.notify_failures (Go consumer handler; Beat publishes it
every 300 s, the Go scheduler in Go mode -- see test_p4_scheduling.ScheduledExceptions).

task-coverage.md row: wire the update runner access with restricted permissions, run a real runner
fixture and check deduplicated notices after a switch. The job workers read the scoped backup notices
socket, never the administration socket (contract: TESTS/handoff/test_p4_backup_notices.py): the stub
of TESTS/adapters/go/celery_consumer_backup_failures_process.py serves HTTP over
<ASSOZETA_UPDATER_NOTICES_DIRECTORY>/notices.sock with its own bearer token in <dir>/token (directory
0700, token 0600) and answers GET /backup-failures with the projection of the adapter's ``failures``
data: four failures to notice (op-created, op-recent, schedule:<at>, remote:<at>).
ASSOZETA_UPDATER_DIRECTORY names a directory without runner.sock. Every notice goes to the owner of
the instance's primary association through the instance/system SMTP (the CountingSMTP).
Both runtimes run with ASSOZETA_DEPLOYMENT_MODE=production and those two directories (Django worker
environment and the Go consumer env file).

Scenarios:
  instance_backups_notify_failures__{django,go,queued_forward,queued_reverse} (p4_matrix_kit.standard):
    one run sends exactly four notices to owner@example.test and reads the notices socket once with its
    bearer token (GET /backup-failures, Authorization: Bearer <token>); Go mode has no Django process.
  instance_backups_notify_failures__dedup_after_switch_{forward,reverse}: a run on the source sends
    the four notices; after the switch the same runner answer run on the target sends none (the
    dedup keys 'instance:backup-failure-notified:<key>', 7 days, live in the shared production cache:
    django-redis database 1 of the instance Redis, which the Go consumer shares); a new failure
    added afterwards is noticed exactly once on the target.
Harness seam (t-p4): the Django test settings must use the production cache (django-redis on the
private Redis, database 1) instead of the contract adapter's locmem cache; the dedup scenarios fail
with an explicit message until they do.
"""
import os
from datetime import datetime, timezone
import stat
from types import SimpleNamespace

from TESTS.handoff.p3_stack import ContractFailure, check, wait_for
from TESTS.handoff.p4_family_email import await_smtp, recipients, reset_people, switch_running
from TESTS.handoff.p4_matrix_kit import (Case, await_done, exclusive_go, identity, produce_django, produce_go_task,
                                          require_matrix_prerequisites, require_record, standard)

FAMILY = 'backup_notify'
TASK = 'instance.backups.notify_failures'
OWNER = 'owner@example.test'


def runner_module():
    from TESTS.adapters.go import celery_consumer_backup_failures_process as adapter
    return adapter


def runner(stack):
    """One runner stub per stack, on a short path (Unix socket length limit)."""
    if getattr(stack, 'p4_runner', None) is None:
        directory = stack.work / 'runner'
        directory.mkdir(mode=0o700, exist_ok=True)
        stack.p4_runner = runner_module().Runner(directory)
    return stack.p4_runner


def configure(stack, status, inventory):
    adapter, stub = runner_module(), runner(stack)
    stub.configure(SimpleNamespace(runner={}), status, inventory)
    os.chmod(stub.directory, 0o700)
    os.chmod(stub.directory / 'token', stat.S_IRUSR | stat.S_IWUSR)
    values = {'ASSOZETA_DEPLOYMENT_MODE': 'production', 'ASSOZETA_UPDATER_NOTICES_DIRECTORY': str(stub.directory),
              'ASSOZETA_UPDATER_DIRECTORY': str(stub.directory / 'no-runner')}
    stack.env.update(values)
    path = stack.go_consumer_env_file()
    lines = [line for line in path.read_text().splitlines() if line.split('=', 1)[0] not in values]
    path.write_text(''.join(line + '\n' for line in lines + [f'{k}={v}' for k, v in values.items()]))
    return adapter.NOTICES_TOKEN


def seed(stack, data=None):
    from application.models import SportAssociation, User
    from instance.models import InstanceConfiguration
    reset_people(stack)
    owner, association = '00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b2'
    User.original_objects.bulk_create([User(user_id=owner, username='backup-owner', role=1, password='!private-fixture',
                                            email=OWNER)])
    SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=association, user_id=owner,
                                                                    denomination='ASD Backup', tax_code='00000000000')])
    InstanceConfiguration._base_manager.bulk_create([InstanceConfiguration(
        domain='istanza.example.test', name='Istanza', self_hosted=True, primary_association_id=association)])
    status, inventory = (data or runner_module().failures)(datetime.now(timezone.utc))
    token = configure(stack, status, inventory)
    return {'token': token, 'status': status, 'inventory': inventory}


def producer():
    def django(stack, context):
        import instance.tasks  # noqa: F401  registers the task in the driver (autodiscovery is lazy)
        context['produced_by'] = 'django'
        context['tasks'] = [produce_django(stack, TASK, headers={'periodic_task_name': 'notify-backup-failures'})]
        return context['tasks']

    def go(stack, context):
        context['produced_by'] = 'go'
        context['tasks'] = [produce_go_task(stack, TASK)]
        return context['tasks']
    return {'django': django, 'go': go}


def runner_reads(stack, context, who):
    requests = runner(stack).take()
    paths = [f'{r["method"]} {r["path"]}' for r in requests]
    check(paths == ['GET /backup-failures'], f'{who}: notices socket requests {paths} (expected one GET /backup-failures)')
    for request in requests:
        check(['authorization', f'Bearer {context["token"]}'] in request['headers'],
              f'{who}: the notices request carries the notices bearer token')


def verify(stack, context, runtime):
    context['executed_by'] = runtime
    await_smtp(stack, 4, f'{TASK} on {runtime}')
    check(stack.smtp.count() == 4 and set(recipients(stack)) == {OWNER},
          f'{TASK} on {runtime}: notices {recipients(stack)}, expected four to {OWNER}')
    runner_reads(stack, context, f'{TASK} on {runtime}')
    if runtime == 'go':
        exclusive_go(stack, context['tasks'])


CASE = Case(TASK, seed, producer(), verify, timeout=120)


def shared_cache_required():
    from django.conf import settings
    backend = settings.CACHES['default']['BACKEND']
    if 'locmem' in backend:
        raise ContractFailure('harness (t-p4): TESTS/handoff/p4_django_settings.py must use the production cache '
                              f'(django-redis on the private Redis, database 1) for cross-runtime dedup; it is {backend}')


def dedup_after_switch(source, target):
    def scenario(stack):
        require_matrix_prerequisites(stack)
        stack.set_authority(source, 1)
        context = seed(stack)
        stack.executors(source)
        first, = CASE.produce[source](stack, context)
        check(await_done(stack, first, f'the {source} executor') == 'completed', f'{TASK} on {source}')
        verify(stack, context, source)
        generation = switch_running(stack, source, 1, target)
        shared_cache_required()
        configure(stack, context['status'], context['inventory'])
        stack.executors(target)
        second, = CASE.produce[target](stack, context)
        check(await_done(stack, second, f'the {target} executor') == 'completed', f'{TASK} on {target}')
        runner_reads(stack, context, f'{TASK} on {target} after the switch')
        check(stack.smtp.count() == 4, f'{source} -> {target}: notices already sent were sent again '
                                       f'({stack.smtp.count()} accepted, expected 4)')
        status = dict(context['status'])
        status['history'] = status['history'] + [{'id': 'op-new', 'kind': 'backup', 'status': 'failed',
                                                  'updated_at': datetime.now(timezone.utc).isoformat(), 'error': 'nuovo'}]
        configure(stack, status, context['inventory'])
        third, = CASE.produce[target](stack, context)
        check(await_done(stack, third, f'the {target} executor (new failure)') == 'completed', f'{TASK} again on {target}')
        await_smtp(stack, 5, f'{TASK} on {target}: the new failure')
        check(stack.smtp.count() == 5, f'{source} -> {target}: a new failure is noticed exactly once: {stack.smtp.count()}')
        if target == 'go':
            exclusive_go(stack, [second, third])
        identity(stack, {first: 'source_completed', second: 'target_completed', third: 'target_completed'},
                 (source, 1), (target, generation), {source: 1, target: 2})
        require_record(TASK, FAMILY)
    return scenario


SCENARIOS = dict(standard(CASE, FAMILY))
SCENARIOS['instance_backups_notify_failures__dedup_after_switch_forward'] = dedup_after_switch('django', 'go')
SCENARIOS['instance_backups_notify_failures__dedup_after_switch_reverse'] = dedup_after_switch('go', 'django')
