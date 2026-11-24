"""Matrix family retention: cleanup_old_exports, delete_old_audit_logs, instance.restore.cleanup,
instance.sso.tasks.maintain_bakney_pairings.

task-coverage.md: cover retention cutoffs, deletion scope, pairing revocation/status calls, restore
locks and object references; roll back mid-batch without repeating external calls or restoring
deleted items by mistake.

Fixtures: ExportFixture, RetentionFixture, RestoreFixture, MaintenanceFixture (TESTS/adapters/go/
export_cleanup_, audit_, data_restore_cleanup_, bakney_maintenance_worker_process.py), compared by
identity with the in-process Django control on the same seed (p4_family_collectors.Controlled):
  - rows on both sides of each cutoff (365-day audit, 30-day/3-per-association exports, 24-hour
    restore reviews) equal the control, deletion scope included (other associations untouched);
  - the MinIO listing equals the control: deleted objects stay deleted, retained ones and the
    unrelated sentinel stay;
  - the Bakney peer stub receives exactly the control's requests (path + body, in order): each
    revocation 'disconnect' and the 'status' check once.

Scenarios: standard four boundaries per task, plus
  cleanup_old_exports__rollback_mid_batch, instance_sso_tasks_maintain_bakney_pairings__rollback_mid_batch
    Django -> Go, the job runs on Go and is killed at batch:committed '<task>-1'; the owner rolls
    back (Go -> Django, generation 3) and Django finishes the job: rows, objects and peer requests
    equal the control (no external call repeated, no deleted object restored).
  instance_restore_cleanup__restore_lock_then_switch_forward / _reverse
    an exclusive restore lock (fcntl LOCK_EX on DATA_RESTORE_LOCK_PATH) is held: the source
    occurrence completes with no effect (Django's Busy -> return); the lock is released, the
    runtime switches and the next occurrence on the target performs the control's cleanup once.

SEAMS: barrier 'batch:committed' key '<task name>-<n>' (as in p4_family_collectors): one unit =
  one export document, one restore receipt, one revocation/pairing request.
  Bakney peer stub: the adapter's stub server; both runtimes read BAKNEY_SSO_API_BASE,
  BAKNEY_SSO_UI_ORIGIN and APP_URL from the environment (Go env file / Django worker env, aligned
  here) and share SECRET_KEY (pairing secrets are encrypted with it). The stub is plain http: the
  Django worker gets ASSOZETA_HANDOFF_DEVELOPMENT_HTTP=1 (p4_django_settings: DEBUG=True,
  ASSOZETA_DEPLOYMENT_MODE=development) for this scenario only. Go-side need: the Go worker must
  accept the same http peer when its env file says DEBUG=True and ASSOZETA_DEPLOYMENT_MODE=development
  (as bakney_maintenance_worker_process does); the matrix stack must give the Go worker those values for
  the pairing scenarios only (the shared Go env file has no per-scenario restore today).
  Restore lock: DATA_RESTORE_LOCK_PATH of both runtimes is <stack work>/contract-lifecycle.lock.
"""
import fcntl
import json
import threading

from TESTS.handoff.p3_stack import check
from TESTS.handoff.p4_family_collectors import (switch_stopping, Controlled, align_environment, clean_business, kill_executors, listing,
                                                 model_state, normal_state, quiet_django, s3_settings, scrub, storage)
from TESTS.handoff.p4_matrix_kit import (await_done, exclusive_go, identity, now, produce_django, produce_go_job,
                                          require_matrix_prerequisites, require_record, standard)

FAMILY = 'retention'
_PEER = {}


# -- cleanup_old_exports --------------------------------------------------------------------------------
def export_make(stack):
    from TESTS.adapters.go import export_cleanup_worker_process as adapter
    align_environment(stack)
    return adapter.ExportFixture(storage(stack, adapter))


def export_control(stack, fixture):
    from application import tasks
    with s3_settings(stack):
        quiet_django(tasks.cleanup_old_exports_task.run)


EXPORTS = Controlled('cleanup_old_exports', FAMILY,
                     ('application.SportAssociationDocumentsArchive', 'docmanager.Document', 'application.InstructorHours',
                      'application.MedicalCertificate', 'auditlog.LogEntry', 'application.AuditLogIndex'),
                     export_make, lambda stack, fixture: fixture.seed(), export_control, externals=('storage',))


# -- delete_old_audit_logs --------------------------------------------------------------------------------
def audit_make(stack):
    from TESTS.adapters.go import audit_worker_process as adapter
    return adapter.RetentionFixture()


def audit_control(stack, fixture):
    from application import tasks
    quiet_django(tasks.delete_old_audit_logs.run)


AUDIT = Controlled('delete_old_audit_logs', FAMILY, ('auditlog.LogEntry', 'application.AuditLogIndex'),
                   audit_make, lambda stack, fixture: fixture.seed(), audit_control)


# -- instance.restore.cleanup -----------------------------------------------------------------------------
def lock_path(stack):
    return stack.work / 'contract-lifecycle.lock'


def restore_make(stack):
    from TESTS.adapters.go import data_restore_cleanup_worker_process as adapter
    align_environment(stack)
    return adapter.RestoreFixture(storage(stack, adapter), lock_path(stack))


def restore_control(stack, fixture):
    from instance import tasks
    with s3_settings(stack, DATA_RESTORE_LOCK_PATH=str(lock_path(stack))):
        quiet_django(tasks.cleanup_data_restores.run)


RESTORE = Controlled('instance.restore.cleanup', FAMILY, ('instance.DataRestore',), restore_make,
                     lambda stack, fixture: fixture.seed(), restore_control,
                     externals=('storage',))


# -- instance.sso.tasks.maintain_bakney_pairings ------------------------------------------------------------
def peer(stack):
    """The Bakney authority stub (bakney_maintenance_worker_process._StubServer), one per driver process."""
    from TESTS.adapters.go import bakney_maintenance_worker_process as adapter
    if 'server' not in _PEER:
        server = adapter._StubServer(('127.0.0.1', 0), adapter._StubHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _PEER.update(server=server, authority=f'http://127.0.0.1:{server.server_address[1]}/api')
    return adapter, _PEER['authority']


def peer_settings(stack):
    from django.conf import settings
    adapter, authority = peer(stack)
    return {'BAKNEY_SSO_API_BASE': authority, 'BAKNEY_SSO_UI_ORIGIN': adapter.UI, 'APP_URL': adapter.ORIGIN,
            'SECRET_KEY': settings.SECRET_KEY}


def pairing_make(stack):
    adapter, authority = peer(stack)
    from django.conf import settings
    align_environment(stack, ASSOZETA_HANDOFF_DEVELOPMENT_HTTP='1',
                      **{k: v for k, v in peer_settings(stack).items() if k != 'SECRET_KEY'})
    adapter.SECRET_KEY = settings.SECRET_KEY  # the fixture encrypts pairing secrets with the stack's key
    # The Go worker accepts the plain-http peer stub only in development mode (the kit restores go.env afterwards).
    path = stack.go_env_file()
    lines = [l for l in path.read_text().splitlines() if l.split('=', 1)[0] not in ('DEBUG', 'ASSOZETA_DEPLOYMENT_MODE')]
    path.write_text('\n'.join(lines + ['DEBUG=True', 'ASSOZETA_DEPLOYMENT_MODE=development']) + '\n')
    return adapter.MaintenanceFixture(authority)


class _ReplyByRequest:
    """The stub's script indexed by request, not by position: a job resumed after a kill sends again the
    requests of the units it had not committed (the stub handler reads script[n - 1] for the n-th request)."""

    def __init__(self, adapter, exchanges):
        self.adapter = adapter
        self.replies = {json.dumps(body).encode(): reply for body, reply in exchanges}

    def __len__(self):
        return len(self.adapter.STUB['log'])

    def __getitem__(self, index):
        return self.replies.get(self.adapter.STUB['log'][index]['body'], (500, {'error': 'unexpected'}))


def _without_effect(reply):
    """Replies after which the Django and Go maintenance keep the unit (busy, still pending): resending is safe."""
    status, payload = reply
    return status >= 500 or (status == 200 and isinstance(payload, dict) and payload.get('status') == 'pending')


def pairing_seed(stack, fixture):
    adapter, _ = peer(stack)
    fixture.seed('main')
    exchanges = fixture.script('main')
    _PEER['harmless'] = {json.dumps(body).encode() for body, reply in exchanges if _without_effect(reply)}
    with adapter.STUB['lock']:
        adapter.STUB['log'], adapter.STUB['script'] = [], _ReplyByRequest(adapter, exchanges)


def pairing_control(stack, fixture):
    from django.test.utils import override_settings
    from instance.sso import tasks
    values = peer_settings(stack)
    with override_settings(DEBUG=True, ASSOZETA_DEPLOYMENT_MODE='development', **values):
        quiet_django(tasks.maintain_bakney_pairings.run)


def peer_requests(stack):
    """Distinct authority requests in first-sent order, plus every request sent again although its unit had an
    effect (must stay empty: a committed revocation or status update is never replayed)."""
    adapter, _ = peer(stack)
    with adapter.STUB['lock']:
        log = list(adapter.STUB['log'])
    seen, distinct, replayed = set(), [], []
    for entry in log:
        if entry['body'] in seen:
            if entry['body'] not in _PEER.get('harmless', ()):
                replayed.append(json.loads(entry['body'] or b'null'))
            continue
        seen.add(entry['body'])
        distinct.append((entry['path'], json.loads(entry['body'] or b'null')))
    return {'requests': distinct, 'replayed_after_effect': replayed}


PAIRINGS = Controlled('instance.sso.tasks.maintain_bakney_pairings', FAMILY,
                      ('instance.BakneyPairing', 'instance.BakneyRevocation', 'instance.BakneyChallenge',
                       'instance.BakneyNonce', 'instance.BakneyLogin'),
                      pairing_make, pairing_seed, pairing_control, extra={'peer_requests': peer_requests})


# -- boundaries ------------------------------------------------------------------------------------------
def rollback_mid_batch(controlled):
    """Django -> Go; the job is killed on Go at its first committed unit; Go -> Django; Django finishes it."""
    case = controlled.case()

    def scenario(stack):
        require_matrix_prerequisites(stack)
        context = case.seed(stack)
        generation = stack.switch('django', 1, 'go')
        key = f'{controlled.task}-1'
        stack.barrier.hold('batch:committed', key)
        stack.executors('go')
        produced = case.produce['go'](stack, context)
        arrival = stack.barrier.wait_arrived('batch:committed', key, f'the Go worker after the first committed unit of '
                                             f'{controlled.task} (contract: barrier batch:committed)', timeout=45)
        kill_executors(stack, 'go', arrival)
        stack.barrier.reset()
        generation = stack.switch('go', generation, 'django')
        stack.executors('django')
        for task_id in produced:
            check(await_done(stack, task_id, 'Django after the rollback', 120) == 'completed',
                  f'{controlled.task}: the interrupted Go job completes on Django after the rollback')
        case.verify(stack, context, 'django')
        children = case.children(stack, context)
        identity(stack, {t: 'target_completed' for t in produced} | children, ('go', 2), ('django', generation),
                 {'django': len(produced) + sum(1 for v in children.values() if v == 'target_completed'), 'go': 0})
        require_record(controlled.task, FAMILY)
    return scenario


def restore_lock_then_switch(source, target):
    case = RESTORE.case()

    def scenario(stack):
        require_matrix_prerequisites(stack)
        stack.set_authority(source, 1)
        context = case.seed(stack)
        seeded_objects = listing(stack)
        held = lock_path(stack).open('a')
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stack.executors(source)
            first = case.produce[source](stack, context)[0]
            check(await_done(stack, first, f'the {source} executor while a restore holds the lock') == 'completed',
                  'a busy restore lock ends the occurrence without effect (Django: Busy -> return)')
            unchanged = normal_state(context['before'], model_state(RESTORE.labels), context['since'], now())
            check(all(not any(part.values()) for part in unchanged.values()) and listing(stack) == seeded_objects,
                  f'{source}: the cleanup touched receipts or objects while the restore lock was held: {unchanged}')
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)
            held.close()
        generation = switch_stopping(stack, source, 1, target)
        stack.executors(target)
        second = case.produce[target](stack, context)[0]
        context['produced'] = [first, second]
        check(await_done(stack, second, f'the {target} executor after the lock and the switch') == 'completed', 'second run')
        case.verify(stack, context, target)
        if target == 'go':
            exclusive_go(stack, [second])
        identity(stack, {first: 'source_completed', second: 'target_completed'}, (source, 1), (target, generation),
                 {source: 1, target: 1})
        require_record('instance.restore.cleanup', FAMILY)
    return scenario


SCENARIOS = {}
for _controlled in (EXPORTS, AUDIT, RESTORE, PAIRINGS):
    SCENARIOS.update(standard(_controlled.case(), FAMILY))
for _controlled in (EXPORTS, PAIRINGS):
    SCENARIOS[f'{_controlled.task.replace(".", "_")}__rollback_mid_batch'] = rollback_mid_batch(_controlled)
SCENARIOS['instance_restore_cleanup__restore_lock_then_switch_forward'] = restore_lock_then_switch('django', 'go')
SCENARIOS['instance_restore_cleanup__restore_lock_then_switch_reverse'] = restore_lock_then_switch('go', 'django')
