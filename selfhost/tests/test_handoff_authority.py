"""Phase 1 handoff: one runtime authority and one serialized controller operation.

Red-first contract for selfhost/updater/runtime_switch.py (to be written by the
implementer). Real PostgreSQL (private cluster, handoff_postgres.py) holds the
authority; the updater journal (.updater/operations.sqlite3) holds the
operation. No Docker command may run: Phase 1 never touches containers,
routing or Compose, and every rejected or blocked path must leave them alone.

Authority (BE_GO/migrations/000005_runtime_authority)
    public.assozeta_go_job_ownership(singleton=1): runtime 'django'|'go',
    generation bigint, operation_id uuid NULL (last committed operation),
    committed_at. Changed only by
    public.assozeta_commit_runtime_authority(op, expected_runtime,
    expected_generation, target) -> (outcome, runtime, generation, operation_id),
    outcome 'committed' | 'already_committed' | 'conflict'.
    Single read path for later phases: runtime_switch.read_authority(root, env)
    / PsqlAuthority.read() / ./bin/assozeta runtime-status; in-database
    processes read that row directly.

Module runtime_switch
    RUNTIMES = ('django', 'go')
    STEP_STAGES = ('preflight', 'close_admission', 'settle', 'stage_backlog', 'activate', 'verify')
    BARRIER_POINTS = ('stage:preflight', 'stage:close_admission', 'stage:settle',
        'stage:stage_backlog', 'stage:commit', 'authority:committed',
        'stage:activate', 'stage:verify', 'outcome:succeeded')
        barrier(point) is called right after the durable write it names
        ('authority:committed': the DB commit returned, journal not yet updated).
    OPERATOR_ACTOR = '00000000-0000-4000-8000-000000000001' (CLI requests)
    Authority(runtime, generation, operation_id)       namedtuple
    CommitResult(outcome, authority)                   namedtuple
    AuthorityUnavailable(Exception), SwitchUnavailable(Exception)
    PsqlAuthority(command): read() -> Authority; commit(operation_id,
        expected_runtime, expected_generation, target) -> CommitResult.
        command is a psql argv prefix reading SQL on stdin (-At, ON_ERROR_STOP).
    compose_authority(root, env_file) -> PsqlAuthority via
        docker compose ... exec -T postgres psql -U $DBUSER -d $DBNAME
    read_authority(root, env_file) -> Authority (compose_authority(...).read())
    submit(root, env_file, journal, authority, body, claim=False) -> (status, payload)
        body = {request_id (idempotency key, uuid), actor_id (uuid),
                target 'django'|'go', expected_generation int >= 1}
        200 no-op / 202 queued (or replay): {'operation': VIEW, 'authority': AUTH, 'replayed': bool}
        409 {'error', 'code', 'authority', 'active'} code in stale_generation,
            operation_in_progress, idempotency_conflict (+ 'operation'),
            recovery_required
        400 {'error', 'code': 'invalid_request'}; 503 {'error', 'code': 'authority_unavailable'}
        claim=True creates the record already 'running' for a caller that runs it.
    default_steps(root, env_file) -> {stage: callable(context)}; in Phase 1
        'preflight' raises SwitchUnavailable (no release can switch yet).
    run(root, env_file, journal, authority, operation, steps=None, barrier=None,
        caller_holds_locks=False) -> final journal record. Claims a queued
        record; holds .lifecycle.flock and .operations/lifecycle.lock
        (LOCK_EX|LOCK_NB, legacy .lifecycle.lock directory refused) from
        preflight until its last journal write.
    recover(root, env_file, journal, authority, steps=None, barrier=None,
        caller_holds_locks=False) -> list of changed records. Repairs from the
        DB authority: committed by this operation -> finish forward without a
        commit; DB still at the recorded source -> resume and commit once;
        anything else -> failed 'authority_diverged'. Lock busy -> [] (a live
        controller owns it).
    status(root, env_file, journal, authority) -> STATUS (below)
    __main__: status ROOT ENV | recover ROOT ENV | switch ROOT ENV TARGET

Journal record (kind 'runtime_switch'): the request fields plus previous_runtime,
source_generation, outcome (None|'committed'|'noop'|'failed'), counts
{'pending', 'transferred', 'blocked'}, committed_generation, error, error_code,
recovery, failed_stage. status: queued|running|succeeded|failed|recovery_required.
Journal.interrupt_running() leaves runtime_switch records to recover().
A private JSON-lines log .updater/logs/<operation id>.log has one line per
stage with at, operation_id, actor_id, previous_runtime, requested_runtime,
stage, counts, outcome. Nothing written anywhere carries credentials, env
values or job payloads.

Run: python3 -m unittest discover -s selfhost/tests -p 'test_handoff_*.py'
(Not yet in selfhost/tests/validate.sh: the implementer adds that line in the
change that turns this module green, so the existing PR job is not red meanwhile.)
"""
import fcntl
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
# Verification only: point at a disposable copy (mutation controls); defaults to this checkout.
UPDATER = Path(os.environ.get('ASSOZETA_HANDOFF_UPDATER_DIR') or ROOT / 'selfhost/updater').resolve()
sys.path[:0] = [str(UPDATER), str(ROOT / 'BE/instance'), str(Path(__file__).resolve().parent)]

from journal import Journal, Conflict  # noqa: E402
from backup_jobs import BackupRejected, SCHEDULE_FILE, create_backup, schedule_tick  # noqa: E402
from restore_jobs import start_restore  # noqa: E402
from server import validate_request, validate_restart_request  # noqa: E402
import handoff_postgres  # noqa: E402

CLI = Path(os.environ.get('ASSOZETA_HANDOFF_CLI') or ROOT / 'selfhost/bin/assozeta')
DEFINITIONS = CLI.read_text().split('\ncommand=${1:-help}\n')[0]
REAL_POPEN = subprocess.Popen
SECRETS = ('P1-DBPASSWORD-SENTINEL', 'P1-SECRET-KEY-SENTINEL', 'P1-JWT-SENTINEL', 'P1-RUNNER-TOKEN-SENTINEL',
           'P1-ADMIN-PASSWORD-SENTINEL', 'P1-JOB-PAYLOAD-SENTINEL')
OWNER, OTHER_OWNER = str(uuid4()), str(uuid4())
VIEW_KEYS = {'id', 'kind', 'actor_id', 'previous_runtime', 'requested_runtime', 'expected_generation', 'status',
             'stage', 'outcome', 'counts', 'committed_generation', 'error', 'error_code', 'recovery',
             'created_at', 'updated_at'}
STATUS_KEYS = {'protocol', 'effective_runtime', 'generation', 'last_operation_id', 'requested_runtime',
               'operation', 'active', 'authority_error', 'recovery_command'}
COUNTS = {'pending': 0, 'transferred': 0, 'blocked': 0}
ARCHIVE = 'assozeta-20261101T020000Z.tar.gz'
PG = None


def setUpModule():
    global PG
    PG = handoff_postgres.PrivatePostgres().start()


def tearDownModule():
    if PG is not None:
        PG.stop()


def rs(*names):
    """The contract module; a missing module or symbol fails with the contract it lacks."""
    try:
        module = importlib.import_module('runtime_switch')
    except ModuleNotFoundError as error:
        if error.name == 'runtime_switch':
            raise AssertionError('CONTRACT MISSING: selfhost/updater/runtime_switch.py (Phase 1 controller); '
                                 'needed: ' + ', '.join(names or ('module',))) from None
        raise
    missing = [name for name in names if not hasattr(module, name)]
    if missing:
        raise AssertionError('CONTRACT MISSING: runtime_switch.' + ', runtime_switch.'.join(missing))
    return module


def require_authority_schema():
    if not PG.has_runtime_authority():
        raise AssertionError('CONTRACT MISSING: BE_GO/migrations/000005_runtime_authority.up.sql '
                             '(operation_id column and assozeta_commit_runtime_authority)')


def parallel(count, call):
    """Start count calls together (a barrier, no sleeps); re-raise the first failure in the test."""
    start, results, errors = threading.Barrier(count), [None] * count, []

    def worker(index):
        try:
            start.wait()
            results[index] = call(index)
        except BaseException as error:  # noqa: BLE001 - surfaced below
            errors.append(error)
    threads = [threading.Thread(target=worker, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    if errors:
        raise errors[0]
    return results


class SimulatedKill(BaseException):
    """Process death at a barrier: the controller may only catch Exception."""


class Forbidden(AssertionError):
    pass


class HandoffCase(unittest.TestCase):
    """A disposable installation root, a fresh authority database and a command guard."""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.env = self.root / '.env'
        self.env.write_text('ASSOZETA_VERSION=1.4.0\nCOMPOSE_PROJECT_NAME=p1fixture\nDBUSER=assozeta\nDBNAME=assozeta\n'
                            f'DBPASSWORD={SECRETS[0]}\nSECRET_KEY={SECRETS[1]}\nJWT_SECRET_KEY={SECRETS[2]}\n'
                            'ASSOZETA_GO_TASKS=0\nGO_TASK_QUEUE_TASKS=\n')
        (self.root / 'compose.yml').write_text('services: {api: {}, worker: {}, beat: {}, api-go: {}}\n')
        (self.root / 'caddy').mkdir()
        (self.root / 'caddy/Caddyfile').write_text(':80 {\n  reverse_proxy api:8000\n}\n')
        (self.root / '.operations').mkdir()
        (self.root / 'backups').mkdir()
        (self.root / 'backups' / ARCHIVE).write_bytes(b'\x1f\x8b')
        self.journal = Journal(self.root / '.updater/operations.sqlite3')
        self.database = PG.fresh_database()
        self.commands = []
        guard = patch('subprocess.Popen', side_effect=self.guarded_popen)
        guard.start()
        self.addCleanup(guard.stop)

    def guarded_popen(self, args, *rest, **kwargs):
        argv = [str(arg) for arg in (args if isinstance(args, (list, tuple)) else [args])]
        # psql is the authority transport; the private cluster's own tools are fixtures.
        if Path(argv[0]).name == 'psql' or Path(argv[0]).parent == PG.bin or argv[0] == sys.executable:
            return REAL_POPEN(args, *rest, **kwargs)
        self.commands.append(argv)
        raise Forbidden('Phase 1 must not run host/container commands: ' + ' '.join(argv[:4]))

    def authority(self, database=None):
        require_authority_schema()
        return rs('PsqlAuthority').PsqlAuthority(PG.psql_command(database or self.database))

    def row(self):
        require_authority_schema()
        return PG.authority_row(self.database)

    def body(self, target='go', generation=1, key=None, actor=OWNER):
        return {'request_id': key or str(uuid4()), 'actor_id': actor, 'target': target, 'expected_generation': generation}

    def submit(self, body, **kwargs):
        module = rs('submit')
        return module.submit(self.root, self.env, self.journal, self.authority(), body, **kwargs)

    def noop_steps(self, calls=None):
        module = rs('STEP_STAGES')

        def step(stage):
            def call(context):
                if calls is not None:
                    calls.append(stage)
            return call
        return {stage: step(stage) for stage in module.STEP_STAGES}

    def run_op(self, operation, **kwargs):
        module = rs('run')
        kwargs.setdefault('steps', self.noop_steps())
        return module.run(self.root, self.env, self.journal, kwargs.pop('authority', None) or self.authority(), operation, **kwargs)

    def recover(self, **kwargs):
        module = rs('recover')
        kwargs.setdefault('steps', self.noop_steps())
        # A fresh Journal object: what a restarted runner or the CLI would open.
        return module.recover(self.root, self.env, Journal(self.journal.path),
                              kwargs.pop('authority', None) or self.authority(), **kwargs)

    def record(self, operation_id):
        return next(item for item in Journal(self.journal.path).records() if item['id'] == operation_id)

    def runtime_records(self):
        return [item for item in self.journal.records() if item.get('kind') == 'runtime_switch']

    def fixture_commit(self, runtime, generation, target, operation=None):
        """Trusted fixture: an authority change made outside the operation under test."""
        require_authority_schema()
        operation = operation or str(uuid4())
        PG.query(self.database, f"SELECT outcome FROM public.assozeta_commit_runtime_authority('{operation}', "
                                f"'{runtime}', {generation}, '{target}');")
        return operation

    def world(self):
        """Everything a blocked or rejected request must leave alone (besides the journal)."""
        files = {name: (self.root / name).read_bytes() for name in ('.env', 'compose.yml', 'caddy/Caddyfile')}
        return {'authority': self.row(), 'files': files,
                'maintenance': (self.root / '.operations/maintenance.flag').exists()}

    def assertNoCommands(self):
        self.assertEqual(self.commands, [], 'no Docker/host command may run in Phase 1')

    def queued(self, target='go', generation=1, actor=OWNER):
        status, payload = self.submit(self.body(target, generation, actor=actor))
        self.assertEqual(status, 202, payload)
        return self.record(payload['operation']['id'])


class SubmissionTests(HandoffCase):
    def test_submission_returns_operation_id_and_asynchronous_status_without_committing(self):
        key = str(uuid4())
        status, payload = self.submit(self.body('go', 1, key))
        self.assertEqual(status, 202)
        self.assertEqual(set(payload), {'operation', 'authority', 'replayed'})
        self.assertIs(payload['replayed'], False)
        self.assertEqual(payload['authority'], {'runtime': 'django', 'generation': 1, 'operation_id': None})
        view = payload['operation']
        self.assertEqual(set(view), VIEW_KEYS)
        UUID(view['id'])
        self.assertEqual({k: view[k] for k in ('kind', 'actor_id', 'previous_runtime', 'requested_runtime',
                                                'expected_generation', 'status', 'stage', 'outcome', 'counts',
                                                'committed_generation', 'error', 'error_code', 'recovery')},
                         {'kind': 'runtime_switch', 'actor_id': OWNER, 'previous_runtime': 'django',
                          'requested_runtime': 'go', 'expected_generation': 1, 'status': 'queued', 'stage': 'queued',
                          'outcome': None, 'counts': COUNTS, 'committed_generation': None, 'error': None,
                          'error_code': None, 'recovery': None})
        [record] = self.runtime_records()
        self.assertEqual((record['id'], record['request_id'], record['target'], record['source_generation']),
                         (view['id'], key, 'go', 1))
        self.assertEqual(self.row(), ('django', 1, None), 'submission must never touch the authority')
        self.assertNoCommands()

    def test_identical_submissions_with_one_key_return_one_operation_and_commit_once(self):
        body = self.body('go', 1)
        results = parallel(4, lambda index: self.submit(dict(body)))
        self.assertEqual({status for status, _ in results}, {202})
        self.assertEqual(len({payload['operation']['id'] for _, payload in results}), 1)
        self.assertEqual(sorted(payload['replayed'] for _, payload in results), [False, True, True, True])
        [record] = self.runtime_records()
        final = self.run_op(record)
        self.assertEqual((final['status'], final['outcome'], final['committed_generation']), ('succeeded', 'committed', 2))
        # The browser timed out and retries with the original body (expected generation 1).
        status, payload = self.submit(dict(body))
        self.assertEqual((status, payload['operation']['id'], payload['replayed']), (202, record['id'], True))
        self.assertEqual((payload['operation']['outcome'], payload['operation']['committed_generation']), ('committed', 2))
        self.assertEqual(self.row(), ('go', 2, record['id']), 'exactly one generation increment')
        self.assertEqual(len(self.runtime_records()), 1)

    def test_key_reused_for_a_different_request_is_rejected(self):
        body = self.body('go', 1)
        _, first = self.submit(body)
        for change in ({'target': 'django'}, {'expected_generation': 2}, {'actor_id': OTHER_OWNER}):
            with self.subTest(change=change):
                status, payload = self.submit({**body, **change})
                self.assertEqual(status, 409, payload)
                self.assertEqual(payload['code'], 'idempotency_conflict')
                self.assertEqual(payload['operation']['id'], first['operation']['id'])
                self.assertEqual(payload['authority'], {'runtime': 'django', 'generation': 1, 'operation_id': None})
        self.assertEqual(len(self.runtime_records()), 1)
        self.assertEqual(self.row(), ('django', 1, None))

    def test_journal_itself_refuses_a_reused_runtime_key_for_another_target(self):
        # Defence in depth below submit(): today the journal returns the old record.
        request = {'request_id': str(uuid4()), 'actor_id': OWNER, 'kind': 'runtime_switch', 'target': 'go',
                   'expected_generation': 1, 'release_id': None, 'tag': '1.4.0', 'source_version': '1.4.0'}
        self.journal.create(request)
        with self.assertRaises(Conflict):
            self.journal.create({**request, 'target': 'django'})
        with self.assertRaises(Conflict):
            self.journal.create({**request, 'expected_generation': 2})

    def test_stale_browser_generation_is_rejected_with_the_current_state(self):
        winner = self.fixture_commit('django', 1, 'go')
        before = self.world()
        status, payload = self.submit(self.body('django', 1))
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload['code'], 'stale_generation')
        self.assertEqual(payload['authority'], {'runtime': 'go', 'generation': 2, 'operation_id': winner})
        self.assertIsNone(payload['active'])
        self.assertEqual(self.runtime_records(), [])
        self.assertEqual(self.world(), before)
        self.assertNoCommands()

    def test_target_already_effective_returns_a_recorded_noop(self):
        body = self.body('django', 1)
        status, payload = self.submit(body)
        self.assertEqual(status, 200, payload)
        view = payload['operation']
        self.assertEqual((view['status'], view['stage'], view['outcome'], view['committed_generation']),
                         ('succeeded', 'completed', 'noop', None))
        status, again = self.submit(dict(body))
        self.assertEqual((status, again['operation']['id'], again['replayed']), (200, view['id'], True))
        self.assertEqual(self.row(), ('django', 1, None))
        # A no-op does not hold the controller.
        self.assertEqual(self.submit(self.body('go', 1))[0], 202)
        self.assertNoCommands()

    def test_effective_target_is_not_a_noop_while_runtime_recovery_is_pending(self):
        operation = self.queued('go', 1)
        self.journal.update(operation['id'], status='recovery_required', stage='recovery_required')
        status, payload = self.submit(self.body('django', 1))
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload['code'], 'recovery_required')
        self.assertEqual(self.row(), ('django', 1, None))

    def test_competing_requests_receive_the_active_operation_and_current_state(self):
        winner = self.queued('go', 1)
        before = self.world()
        for target, actor in (('django', OTHER_OWNER), ('go', OTHER_OWNER), ('go', OWNER)):
            with self.subTest(target=target, actor=actor):
                status, payload = self.submit(self.body(target, 1, actor=actor))
                self.assertEqual(status, 409, payload)
                self.assertEqual(payload['code'], 'operation_in_progress')
                self.assertEqual(payload['active']['id'], winner['id'])
                self.assertEqual(payload['authority'], {'runtime': 'django', 'generation': 1, 'operation_id': None})
        self.assertEqual([item['id'] for item in self.runtime_records()], [winner['id']])
        self.assertEqual(self.world(), before)
        self.run_op(winner)
        status, payload = self.submit(self.body('django', 1, actor=OTHER_OWNER))
        self.assertEqual((status, payload['code']), (409, 'stale_generation'))
        self.assertEqual(payload['authority'], {'runtime': 'go', 'generation': 2, 'operation_id': winner['id']})
        self.assertNoCommands()

    def test_concurrent_opposite_submissions_admit_one_operation(self):
        results = parallel(8, lambda index: self.submit(self.body('go' if index % 2 == 0 else 'django', 1,
                                                                  actor=str(uuid4()))))
        accepted = [payload for status, payload in results if status == 202]
        self.assertEqual(len(accepted), 1, results)
        winner = accepted[0]['operation']['id']
        for status, payload in results:
            self.assertIn(status, (200, 202, 409))
            if status == 409:
                self.assertEqual((payload['code'], payload['active']['id']), ('operation_in_progress', winner))
            if status == 200:
                self.assertEqual(payload['operation']['outcome'], 'noop')
        self.assertEqual(len([item for item in self.journal.records() if item['status'] in ('queued', 'running')]), 1)
        self.assertEqual(self.row(), ('django', 1, None))

    def test_malformed_requests_are_rejected_before_the_journal(self):
        valid = self.body('go', 1)
        for change in ({'extra': 1}, {'target': 'node'}, {'target': 'Go'}, {'expected_generation': True},
                       {'expected_generation': '1'}, {'expected_generation': 0}, {'expected_generation': -3},
                       {'request_id': 'owner'}, {'actor_id': 'owner'}):
            with self.subTest(change=change):
                status, payload = self.submit({**valid, **change})
                self.assertEqual((status, payload.get('code')), (400, 'invalid_request'), payload)
        for missing in ('request_id', 'actor_id', 'target', 'expected_generation'):
            with self.subTest(missing=missing):
                body = dict(valid)
                del body[missing]
                self.assertEqual(self.submit(body)[0], 400)
        self.assertEqual(self.journal.records(), [])

    def test_unreachable_authority_fails_closed(self):
        module = rs('PsqlAuthority', 'submit')
        authority = module.PsqlAuthority(PG.psql_command('handoff_database_that_does_not_exist'))
        status, payload = module.submit(self.root, self.env, self.journal, authority, self.body('go', 1))
        self.assertEqual((status, payload.get('code')), (503, 'authority_unavailable'), payload)
        self.assertEqual(self.journal.records(), [])
        self.assertNotIn(SECRETS[0], json.dumps(payload))


class ControllerTests(HandoffCase):
    def test_run_writes_every_stage_durably_and_commits_once(self):
        module = rs('BARRIER_POINTS', 'STEP_STAGES')
        operation = self.queued()
        calls, seen = [], []

        def barrier(point):
            record = self.record(operation['id'])
            seen.append((point, record['status'], record['stage'], self.row()))
        final = self.run_op(operation, steps=self.noop_steps(calls), barrier=barrier)
        self.assertEqual([point for point, *_ in seen], list(module.BARRIER_POINTS))
        self.assertEqual(calls, list(module.STEP_STAGES))
        source, target = ('django', 1, None), ('go', 2, operation['id'])
        for point, status, stage, row in seen:
            with self.subTest(point=point):
                if point.startswith('stage:'):
                    self.assertEqual((status, stage), ('running', point.split(':', 1)[1]))
                committed = point in ('authority:committed', 'stage:activate', 'stage:verify', 'outcome:succeeded')
                self.assertEqual(row, target if committed else source)
        self.assertEqual(seen[-1][1:3], ('succeeded', 'completed'))
        self.assertEqual((final['status'], final['stage'], final['outcome'], final['committed_generation']),
                         ('succeeded', 'completed', 'committed', 2))
        self.assertEqual(self.row(), target)
        self.assertNoCommands()

    def test_release_without_switch_support_refuses_before_any_change(self):
        operation = self.queued()
        before = self.world()
        final = self.run_op(operation, steps=None)
        self.assertEqual((final['status'], final['outcome'], final['error_code']), ('failed', 'failed', 'switch_unavailable'))
        self.assertEqual(final['failed_stage'], 'preflight')
        self.assertEqual(self.world(), before)
        self.assertNoCommands()

    def test_lifecycle_locks_are_held_from_preflight_through_the_commit(self):
        operation = self.queued()
        held = {}

        def barrier(point):
            for name in ('.lifecycle.flock', '.operations/lifecycle.lock'):
                with (self.root / name).open('a') as handle:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(handle, fcntl.LOCK_UN)
                        held[(point, name)] = False
                    except BlockingIOError:
                        held[(point, name)] = True
        self.run_op(operation, barrier=barrier)
        for point in ('stage:preflight', 'stage:commit', 'authority:committed', 'stage:verify'):
            for name in ('.lifecycle.flock', '.operations/lifecycle.lock'):
                self.assertTrue(held[(point, name)], f'{name} free at {point}: a CLI restore/backup/upgrade could interleave')

    def test_held_cli_or_restore_lock_blocks_the_switch_without_changes(self):
        for name in ('.lifecycle.flock', '.operations/lifecycle.lock', 'legacy'):
            with self.subTest(lock=name):
                operation = self.queued()
                before, calls = self.world(), []
                if name == 'legacy':
                    (self.root / '.lifecycle.lock').mkdir()
                    handle = None
                else:
                    handle = (self.root / name).open('a')
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    final = self.run_op(operation, steps=self.noop_steps(calls))
                finally:
                    if handle:
                        handle.close()
                    else:
                        (self.root / '.lifecycle.lock').rmdir()
                self.assertEqual((final['status'], final['error_code']), ('failed', 'lifecycle_busy'))
                self.assertEqual(calls, [])
                self.assertEqual(self.world(), before)
        self.assertNoCommands()

    def test_source_changed_after_submission_fails_without_a_commit(self):
        operation = self.queued('go', 1)
        other = self.fixture_commit('django', 1, 'go')
        final = self.run_op(operation)
        self.assertEqual((final['status'], final['error_code']), ('failed', 'stale_generation'))
        self.assertEqual(self.row(), ('go', 2, other))

    def test_step_failure_before_commit_is_redacted_and_retains_the_source(self):
        operation = self.queued()
        steps = self.noop_steps()

        def failing(context):
            raise RuntimeError(f'connection failed password={SECRETS[0]}')
        steps['settle'] = failing
        before = self.world()
        final = self.run_op(operation, steps=steps)
        self.assertEqual((final['status'], final['outcome'], final['failed_stage'], final['error_code']),
                         ('failed', 'failed', 'settle', 'step_failed'))
        self.assertTrue(final['error'])
        self.assertNotIn(SECRETS[0], json.dumps(self.journal.records()))
        self.assertEqual(self.world(), before)

    def test_failure_after_commit_requires_recovery_and_never_commits_again(self):
        operation = self.queued()
        steps = self.noop_steps()

        def failing(context):
            raise RuntimeError('target not ready')
        steps['activate'] = failing
        final = self.run_op(operation, steps=steps)
        self.assertEqual((final['status'], final['error_code'], final['committed_generation']),
                         ('recovery_required', 'post_commit_failed', 2))
        self.assertIn('runtime-recover', final['recovery'])
        self.assertEqual(self.row(), ('go', 2, operation['id']))
        self.assertTrue(self.journal.requiring_recovery(), 'updates and restores stay blocked meanwhile')
        self.recover()
        self.assertEqual((self.record(operation['id'])['status'], self.record(operation['id'])['outcome']), ('succeeded', 'committed'))
        self.assertEqual(self.row(), ('go', 2, operation['id']))

    def test_lost_commit_answer_is_resolved_from_the_database(self):
        module = rs('AuthorityUnavailable', 'PsqlAuthority')
        for applied in (True, False):
            with self.subTest(commit_applied=applied):
                self.database = PG.fresh_database()
                real = self.authority()
                operation = self.queued()

                class LostAnswer:
                    def read(self):
                        return real.read()

                    def commit(self, *args):
                        if applied:
                            real.commit(*args)
                        raise module.AuthorityUnavailable('connection reset')
                final = self.run_op(operation, authority=LostAnswer())
                self.assertEqual((final['status'], final['error_code']), ('recovery_required', 'authority_unknown'))
                self.assertEqual(self.row(), ('go', 2, operation['id']) if applied else ('django', 1, None))
                self.recover(authority=real)
                record = self.record(operation['id'])
                self.assertEqual((record['status'], record['outcome'], record['committed_generation']), ('succeeded', 'committed', 2))
                self.assertEqual(self.row(), ('go', 2, operation['id']), 'exactly one commit either way')

    def test_claimed_operation_is_never_run_twice(self):
        status, payload = self.submit(self.body('go', 1), claim=True)
        self.assertEqual(status, 202)
        record = self.record(payload['operation']['id'])
        self.assertEqual(record['status'], 'running', 'a claimed record is never visible as queued to the runner')
        stale_snapshot = {**record, 'status': 'queued', 'stage': 'queued'}
        calls = []
        result = self.run_op(stale_snapshot, steps=self.noop_steps(calls))
        self.assertEqual(calls, [])
        self.assertEqual(result['status'], 'running')
        self.assertEqual(self.row(), ('django', 1, None))


class SerializationTests(HandoffCase):
    def attempt_other_operations(self):
        """Every other deployment operation the runner accepts must be refused."""
        update = validate_request({'release_id': 3, 'tag': 'v1.4.1', 'source_version': 'v1.4.0',
                                   'request_id': str(uuid4()), 'actor_id': OWNER})
        with self.assertRaises(Conflict):
            self.journal.create(update)
        with self.assertRaises(Conflict):
            self.journal.create(validate_restart_request({'request_id': str(uuid4()), 'actor_id': OWNER}, self.env))
        with self.assertRaises(BackupRejected):
            create_backup(self.root, self.env, self.journal, {'request_id': str(uuid4()), 'actor_id': OWNER})
        with self.assertRaises(BackupRejected):
            start_restore(self.root, self.env, self.journal, ARCHIVE,
                          {'request_id': str(uuid4()), 'actor_id': OWNER, 'confirm': 'RIPRISTINA'})
        schedule = self.root / SCHEDULE_FILE
        schedule.parent.mkdir(parents=True, exist_ok=True)
        schedule.write_text(json.dumps({'enabled': True, 'frequency': 'daily', 'time': '03:00', 'weekday': 6,
                                        'anchor': '2026-01-01T00:00:00+00:00'}))
        self.assertIsNone(schedule_tick(self.root, self.env, self.journal))
        status, payload = self.submit(self.body('go', 1, actor=OTHER_OWNER))
        self.assertEqual((status, payload['code']), (409, 'operation_in_progress'))

    def test_queued_switch_serializes_install_upgrade_restore_backup_restart_and_switches(self):
        operation = self.queued()
        before = self.world()
        self.attempt_other_operations()
        self.assertEqual([item['id'] for item in self.journal.records()], [operation['id']])
        self.assertEqual(self.world(), before)
        self.assertNoCommands()

    def test_update_racing_a_running_switch_cannot_interleave(self):
        operation = self.queued()
        reached, release = threading.Event(), threading.Event()
        outcome = {}

        def barrier(point):
            if point == 'stage:settle':
                reached.set()
                release.wait(30)

        def controller():
            outcome['final'] = self.run_op(operation, barrier=barrier)
        thread = threading.Thread(target=controller)
        thread.start()
        try:
            self.assertTrue(reached.wait(30), 'controller never reached the settle stage')
            self.attempt_other_operations()
            # A host CLI upgrade/restore/backup/install waits on the same lifecycle locks.
            for name in ('.lifecycle.flock', '.operations/lifecycle.lock'):
                with (self.root / name).open('a') as handle, self.assertRaises(BlockingIOError):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.row(), ('django', 1, None))
        finally:
            release.set()
            thread.join(30)
        self.assertEqual(outcome['final']['outcome'], 'committed')
        self.assertEqual(self.row(), ('go', 2, operation['id']))
        self.assertEqual([item['kind'] for item in self.journal.records()], ['runtime_switch'])
        self.assertNoCommands()

    def test_switch_racing_an_active_update_or_restore_alters_nothing(self):
        for kind in ('update', 'restore', 'backup', 'restart'):
            with self.subTest(kind=kind):
                self.database = PG.fresh_database()
                directory = TemporaryDirectory()
                self.addCleanup(directory.cleanup)
                self.journal = Journal(Path(directory.name) / 'operations.sqlite3')
                request = {'request_id': str(uuid4()), 'actor_id': OWNER, 'kind': kind, 'release_id': 3 if kind == 'update' else None,
                           'tag': 'v1.4.1' if kind == 'update' else '1.4.0', 'source_version': '1.4.0'}
                if kind == 'restore':
                    request['archive'] = ARCHIVE
                active = self.journal.create(request)
                self.journal.update(active['id'], status='running', stage='migrating' if kind == 'update' else 'checking')
                before = self.world()
                status, payload = self.submit(self.body('go', 1))
                self.assertEqual((status, payload['code']), (409, 'operation_in_progress'))
                self.assertEqual((payload['active']['id'], payload['active']['kind']), (active['id'], kind))
                self.assertEqual(self.world(), before)
                self.assertEqual(self.runtime_records(), [])
        self.assertNoCommands()

    def test_blocked_restore_lock_cannot_change_routing_scheduling_or_containers(self):
        operation = self.queued()
        before = self.world()
        # An in-app data restore holds the shared operations lease (DATA_RESTORE_LOCK_PATH).
        with (self.root / '.operations/lifecycle.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            final = self.run_op(operation)
        self.assertEqual((final['status'], final['error_code']), ('failed', 'lifecycle_busy'))
        self.assertEqual(self.world(), before)
        self.assertNoCommands()


class RecoveryTests(HandoffCase):
    def snapshot(self, operation_id):
        record = self.record(operation_id)
        runtime, generation, owner = self.row()
        return {'authority': (runtime, generation, owner == operation_id),
                'record': {key: record.get(key) for key in ('status', 'stage', 'outcome', 'committed_generation',
                                                            'previous_runtime', 'target', 'error_code')}}

    def test_kill_at_every_write_boundary_converges_to_the_uninterrupted_state(self):
        module = rs('BARRIER_POINTS')
        control = self.queued()
        self.run_op(control)
        expected = self.snapshot(control['id'])
        self.assertEqual(expected['authority'], ('go', 2, True))
        for point in module.BARRIER_POINTS:
            with self.subTest(point=point):
                self.database = PG.fresh_database()
                directory = TemporaryDirectory()
                self.addCleanup(directory.cleanup)
                self.journal = Journal(Path(directory.name) / 'operations.sqlite3')
                operation = self.queued()

                def kill(reached):
                    if reached == point:
                        raise SimulatedKill(point)
                with self.assertRaises(SimulatedKill):
                    self.run_op(operation, barrier=kill)
                # The runner restarts: its startup interruption pass must not
                # misreport the switch; recover() owns it.
                restarted = Journal(self.journal.path)
                before_interrupt = self.record(operation['id'])
                restarted.interrupt_running()
                self.assertEqual(self.record(operation['id']), before_interrupt)
                self.recover()
                self.assertEqual(self.snapshot(operation['id']), expected)
                # Recovery is idempotent.
                self.assertEqual(self.recover(), [])
                self.assertEqual(self.snapshot(operation['id']), expected)

    def test_reverse_switch_from_go_commits_once_and_recovers_on_both_sides_of_the_commit(self):
        for point in (None, 'stage:commit', 'authority:committed'):
            with self.subTest(kill=point):
                self.database = PG.fresh_database()
                directory = TemporaryDirectory()
                self.addCleanup(directory.cleanup)
                self.journal = Journal(Path(directory.name) / 'operations.sqlite3')
                forward = self.fixture_commit('django', 1, 'go')
                self.assertEqual(self.row(), ('go', 2, forward))
                status, payload = self.submit(self.body('django', 2))
                self.assertEqual(status, 202, payload)
                operation = self.record(payload['operation']['id'])
                self.assertEqual((operation['previous_runtime'], operation['source_generation']), ('go', 2))

                def kill(reached):
                    if reached == point:
                        raise SimulatedKill(point)
                if point is None:
                    self.run_op(operation)
                else:
                    with self.assertRaises(SimulatedKill):
                        self.run_op(operation, barrier=kill)
                    self.recover()
                record = self.record(operation['id'])
                self.assertEqual((record['status'], record['outcome'], record['committed_generation']), ('succeeded', 'committed', 3))
                self.assertEqual(self.row(), ('django', 3, operation['id']))

    def test_database_committed_while_journal_says_an_earlier_stage(self):
        operation = self.queued()
        self.journal.update(operation['id'], status='running', stage='preflight')
        self.fixture_commit('django', 1, 'go', operation['id'])
        changed = self.recover()
        self.assertEqual([item['id'] for item in changed], [operation['id']])
        record = self.record(operation['id'])
        self.assertEqual((record['status'], record['outcome'], record['committed_generation']), ('succeeded', 'committed', 2))
        self.assertEqual(self.row(), ('go', 2, operation['id']), 'no second commit')

    def test_journal_ahead_of_the_database_is_repaired_from_the_durable_source(self):
        operation = self.queued()
        self.journal.update(operation['id'], status='running', stage='activate', committed_generation=2)
        self.recover()
        record = self.record(operation['id'])
        self.assertEqual((record['status'], record['outcome'], record['committed_generation']), ('succeeded', 'committed', 2))
        self.assertEqual(self.row(), ('go', 2, operation['id']))

    def test_diverged_authority_is_reported_never_overwritten(self):
        operation = self.queued()
        self.journal.update(operation['id'], status='running', stage='commit')
        self.fixture_commit('django', 1, 'go')
        other = self.fixture_commit('go', 2, 'django')
        self.recover()
        record = self.record(operation['id'])
        self.assertEqual((record['status'], record['error_code']), ('failed', 'authority_diverged'))
        self.assertEqual(self.row(), ('django', 3, other))

    def test_runner_startup_interruption_pass_leaves_switches_to_recover(self):
        operation = self.journal.create({'request_id': str(uuid4()), 'actor_id': OWNER, 'kind': 'runtime_switch',
                                         'target': 'go', 'expected_generation': 1, 'previous_runtime': 'django',
                                         'source_generation': 1, 'release_id': None, 'tag': '1.4.0',
                                         'source_version': '1.4.0'})
        self.journal.update(operation['id'], status='running', stage='settle')
        self.journal.interrupt_running()
        record = self.record(operation['id'])
        self.assertEqual((record['status'], record['stage']), ('running', 'settle'),
                         'interrupt_running must not mark a runtime switch as an interrupted update')

    def test_repeated_recovery_while_the_database_is_down_does_not_churn_journal_or_log(self):
        # The runner calls recover() on every work-loop iteration (about every 2 s).
        module = rs('PsqlAuthority')
        operation = self.queued()
        self.journal.update(operation['id'], status='running', stage='commit')
        down = module.PsqlAuthority(PG.psql_command('handoff_database_that_does_not_exist'))
        self.recover(authority=down)
        first = self.record(operation['id'])
        self.assertEqual((first['status'], first['error_code']), ('recovery_required', 'authority_unknown'))
        log = self.root / '.updater/logs' / f"{operation['id']}.log"
        lines = log.read_text().count('\n') if log.exists() else 0
        for _ in range(3):
            self.recover(authority=down)
        self.assertEqual(self.record(operation['id']), first, 'an unchanged outcome must not be rewritten each loop')
        self.assertEqual(log.read_text().count('\n') if log.exists() else 0, lines, 'no log line per retry')
        self.recover()
        final = self.record(operation['id'])
        self.assertEqual((final['status'], final['outcome']), ('succeeded', 'committed'))
        self.assertEqual(self.row(), ('go', 2, operation['id']))

    def test_a_new_error_during_an_outage_is_recorded_once_with_the_original_failed_stage(self):
        # A post-commit failure followed by a database outage is a real change of
        # state (a different error): it must be recorded, keeping the stage that failed.
        module = rs('PsqlAuthority')
        operation = self.queued()
        steps = self.noop_steps()

        def failing(context):
            raise RuntimeError('target not ready')
        steps['activate'] = failing
        failed = self.run_op(operation, steps=steps)
        self.assertEqual((failed['error_code'], failed['failed_stage']), ('post_commit_failed', 'activate'))
        down = module.PsqlAuthority(PG.psql_command('handoff_database_that_does_not_exist'))
        self.recover(authority=down)
        outage = self.record(operation['id'])
        self.assertEqual((outage['status'], outage['error_code'], outage['failed_stage']),
                         ('recovery_required', 'authority_unknown', 'activate'))
        self.recover(authority=down)
        self.assertEqual(self.record(operation['id']), outage)
        self.recover()
        final = self.record(operation['id'])
        self.assertEqual((final['status'], final['outcome']), ('succeeded', 'committed'))
        self.assertEqual(self.row(), ('go', 2, operation['id']))

    def test_recover_leaves_an_operation_owned_by_a_live_controller(self):
        operation = self.queued()
        self.journal.update(operation['id'], status='running', stage='settle')
        with (self.root / '.lifecycle.flock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.recover(), [])
        self.assertEqual(self.record(operation['id'])['stage'], 'settle')
        self.assertEqual(self.row(), ('django', 1, None))

    def test_real_process_kill_immediately_before_and_after_the_authority_commit(self):
        for point, expected_row in (('stage:commit', ('django', 1, None)), ('authority:committed', 'committed')):
            with self.subTest(point=point):
                self.database = PG.fresh_database()
                directory = TemporaryDirectory()
                self.addCleanup(directory.cleanup)
                self.journal = Journal(Path(directory.name) / 'operations.sqlite3')
                operation = self.queued()
                process = REAL_POPEN([sys.executable, str(Path(__file__).with_name('handoff_controller_driver.py')),
                                      str(self.root), str(self.env), operation['id'], point,
                                      json.dumps(PG.psql_command(self.database)), str(self.journal.path), str(UPDATER)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    line = process.stdout.readline().strip()
                    if line != 'AT ' + point:
                        process.kill()
                        _, error = process.communicate(timeout=30)
                        self.fail(f'controller did not reach {point}: {line!r} {error[-2000:]}')
                    os.kill(process.pid, signal.SIGKILL)
                    process.wait(30)
                finally:
                    if process.poll() is None:
                        process.kill()
                    for stream in (process.stdin, process.stdout, process.stderr):
                        stream.close()
                record = self.record(operation['id'])
                self.assertEqual(record['stage'], 'commit', 'the commit stage is durable before the authority changes')
                self.assertEqual(self.row(), expected_row if expected_row != 'committed' else ('go', 2, operation['id']))
                self.recover()
                final = self.record(operation['id'])
                self.assertEqual((final['status'], final['outcome'], final['committed_generation']), ('succeeded', 'committed', 2))
                self.assertEqual(self.row(), ('go', 2, operation['id']))


class RedactionAndStatusTests(HandoffCase):
    def test_status_log_and_journal_identify_the_operation_without_secrets_or_payloads(self):
        module = rs('status')
        PG.query(self.database, "INSERT INTO assozeta_go_jobs(id, association_id, kind, version, idempotency_key, payload, "
                                "max_attempts, available_at) VALUES (gen_random_uuid(), gen_random_uuid(), 'fixture.pending', 1, "
                                f"'p1-key', '{{\"secret\": \"{SECRETS[5]}\"}}', 3, clock_timestamp());")
        operation = self.queued()
        self.run_op(operation)
        status = module.status(self.root, self.env, self.journal, self.authority())
        view = status['operation']
        self.assertEqual({k: view[k] for k in ('id', 'actor_id', 'previous_runtime', 'requested_runtime', 'stage', 'counts', 'outcome')},
                         {'id': operation['id'], 'actor_id': OWNER, 'previous_runtime': 'django', 'requested_runtime': 'go',
                          'stage': 'completed', 'counts': COUNTS, 'outcome': 'committed'})
        log = self.root / '.updater/logs' / f"{operation['id']}.log"
        self.assertTrue(log.is_file(), 'the operation keeps a private log')
        self.assertEqual(log.stat().st_mode & 0o077, 0, 'the log is private to the updater')
        lines = [json.loads(line) for line in log.read_text().splitlines()]
        for line in lines:
            self.assertTrue({'at', 'operation_id', 'actor_id', 'previous_runtime', 'requested_runtime', 'stage',
                             'counts', 'outcome'} <= set(line), line)
        self.assertEqual({line['stage'] for line in lines} >= {'preflight', 'commit', 'completed'}, True)
        self.assertEqual(lines[-1]['outcome'], 'committed')
        published = (json.dumps(status) + log.read_text() + (self.root / '.updater/operations.sqlite3').read_bytes().decode('latin-1'))
        for secret in SECRETS:
            self.assertNotIn(secret, published)
        self.assertNotIn('payload', json.dumps(status))

    def test_status_reports_effective_requested_and_active_operation(self):
        module = rs('status')
        idle = module.status(self.root, self.env, self.journal, self.authority())
        self.assertEqual(set(idle), STATUS_KEYS)
        self.assertEqual({k: idle[k] for k in STATUS_KEYS - {'operation', 'active'}},
                         {'protocol': 1, 'effective_runtime': 'django', 'generation': 1, 'last_operation_id': None,
                          'requested_runtime': None, 'authority_error': None,
                          'recovery_command': './bin/assozeta runtime-recover'})
        self.assertIsNone(idle['operation'])
        self.assertIsNone(idle['active'])
        operation = self.queued()
        pending = module.status(self.root, self.env, self.journal, self.authority())
        self.assertEqual((pending['requested_runtime'], pending['active']['id'], pending['operation']['id']),
                         ('go', operation['id'], operation['id']))
        self.run_op(operation)
        done = module.status(self.root, self.env, self.journal, self.authority())
        self.assertEqual((done['effective_runtime'], done['generation'], done['last_operation_id'], done['requested_runtime'], done['active']),
                         ('go', 2, operation['id'], None, None))

    def test_status_without_a_database_still_reports_the_journal(self):
        module = rs('status', 'PsqlAuthority')
        operation = self.queued()
        status = module.status(self.root, self.env, self.journal,
                               module.PsqlAuthority(PG.psql_command('handoff_database_that_does_not_exist')))
        self.assertEqual((status['effective_runtime'], status['authority_error']), (None, 'authority_unavailable'))
        self.assertEqual(status['operation']['id'], operation['id'])


class OperatorPathTests(HandoffCase):
    """Status and recovery through the updater tool, with neither app API running."""

    def docker_shim(self):
        directory = self.root / 'shim'
        directory.mkdir()
        record = directory / 'argv.jsonl'
        shim = directory / 'docker'
        shim.write_text(f'''#!{sys.executable}
import json, os, sys
argv = sys.argv[1:]
with open({str(record)!r}, 'a') as stream:
    stream.write(json.dumps(argv) + '\\n')
if 'psql' not in argv:
    sys.exit('shim: only docker compose exec postgres psql is allowed')
rest, skip = [], False
for index, value in enumerate(argv[argv.index('psql') + 1:]):
    if skip:
        skip = False
        continue
    if value in ('-U', '-d', '-h', '-p'):
        skip = True
        continue
    rest.append(value)
command = {json.dumps(PG.psql_command(self.database))} + rest
os.execv(command[0], command)
''')
        shim.chmod(0o755)
        return directory, record

    def tool(self, *arguments, shim):
        directory, _ = shim
        return subprocess.run([sys.executable, str(UPDATER / 'runtime_switch.py'), *arguments],
                              capture_output=True, text=True, timeout=120,
                              # The updater image ships release_catalog.py beside the tools.
                              env={**os.environ, 'PATH': f'{directory}{os.pathsep}{os.environ["PATH"]}',
                                   'PYTHONPATH': str(ROOT / 'BE/instance')})

    def test_compose_authority_reads_through_the_postgres_service(self):
        require_authority_schema()
        module = rs('compose_authority', 'read_authority')
        shim = self.docker_shim()
        with patch.dict(os.environ, {'PATH': f'{shim[0]}{os.pathsep}{os.environ["PATH"]}'}), \
                patch('subprocess.Popen', side_effect=lambda *a, **k: REAL_POPEN(*a, **k)):
            authority = module.read_authority(self.root, self.env)
        self.assertEqual(tuple(authority), ('django', 1, None))
        argv = json.loads(shim[1].read_text().splitlines()[0])
        self.assertEqual(argv[:3], ['compose', '--env-file', str(self.env)])
        tail = argv[argv.index('exec'):]
        self.assertEqual(tail[:4], ['exec', '-T', 'postgres', 'psql'])
        self.assertIn('ON_ERROR_STOP=1', tail)
        self.assertFalse(any(secret in ' '.join(argv) for secret in SECRETS))

    def test_operator_status_and_recover_work_without_either_app_api(self):
        rs('main')
        operation = self.queued()
        self.journal.update(operation['id'], status='running', stage='verify', committed_generation=2)
        self.fixture_commit('django', 1, 'go', operation['id'])
        shim = self.docker_shim()
        status = self.tool('status', str(self.root), str(self.env), shim=shim)
        self.assertEqual(status.returncode, 0, status.stderr)
        payload = json.loads(status.stdout)
        self.assertEqual((payload['effective_runtime'], payload['generation'], payload['operation']['stage']), ('go', 2, 'verify'))
        recovered = self.tool('recover', str(self.root), str(self.env), shim=shim)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        record = self.record(operation['id'])
        self.assertEqual((record['status'], record['outcome']), ('succeeded', 'committed'))
        self.assertEqual(self.row(), ('go', 2, operation['id']))
        for secret in SECRETS:
            self.assertNotIn(secret, status.stdout + status.stderr + recovered.stdout + recovered.stderr)

    def test_operator_switch_command_refuses_on_a_release_without_switch_support(self):
        rs('main')
        shim = self.docker_shim()
        result = self.tool('switch', str(self.root), str(self.env), 'go', shim=shim)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual((payload['operation']['error_code'], payload['effective_runtime']), ('switch_unavailable', 'django'))
        [record] = [item for item in Journal(self.root / '.updater/operations.sqlite3').records()]
        self.assertEqual((record['actor_id'], record['status']), (rs('OPERATOR_ACTOR').OPERATOR_ACTOR, 'failed'))
        self.assertEqual(self.row(), ('django', 1, None))


class ReconcileUpdatesTests(HandoffCase):
    """reconcile-updates (recovery.reconcile) is for versioned updates and restores only.

    A runtime switch needing recovery carries no release tags; reconcile must
    leave it untouched and point the operator to ./bin/assozeta runtime-recover,
    while still reconciling update/restore records next to it.
    """

    def runtime_record(self):
        operation = self.queued()
        return self.journal.update(operation['id'], status='recovery_required', stage='recovery_required',
                                   failed_stage='commit', error_code='authority_unknown')

    def test_a_runtime_switch_alone_is_refused_with_the_runtime_recovery_command(self):
        from recovery import reconcile
        from release_catalog import ReleaseError
        record = self.runtime_record()
        with self.assertRaises(ReleaseError) as refused:
            reconcile(self.root, self.env)
        self.assertIn('runtime-recover', str(refused.exception))
        self.assertEqual(self.record(record['id']), record)
        self.assertNoCommands()

    def test_updates_are_still_reconciled_beside_an_untouched_runtime_switch(self):
        from recovery import reconcile
        runtime = self.runtime_record()
        # reconcile() opens the installation journal, which is this one.
        self.assertEqual(Path(self.journal.path), self.root / '.updater/operations.sqlite3')
        with self.journal.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            update = {'request_id': str(uuid4()), 'actor_id': OWNER, 'release_id': 3, 'tag': 'v1.4.1',
                      'source_version': 'v1.4.0', 'kind': 'update', 'id': str(uuid4()), 'status': 'recovery_required',
                      'stage': 'recovery_required', 'target_version': 'v1.4.1', 'created_at': 'x', 'updated_at': 'x',
                      'error': None, 'recovery': None}
            db.execute('INSERT INTO operations VALUES (?, ?, ?, ?)',
                       (update['id'], update['request_id'], 'recovery_required', json.dumps(update)))
        with patch('recovery.Engine.verify_running') as verify:
            reconcile(self.root, self.env)
        verify.assert_called_once()
        self.assertEqual(self.record(update['id'])['status'], 'recovered')
        self.assertEqual(self.record(runtime['id']), runtime)


CLI_HARNESS = r'''
run_updater_tool() { printf 'tool %s\n' "$*"; }
preflight() { :; }
acquire_prod_lock() { printf 'locked %s\n' "$1"; prod_lock_held=true; }
release_prod_lock() { printf 'released\n'; }
require_no_surviving_lifecycle_helpers() { :; }
env_value() { printf 'updater-ref-placeholder\n'; }
flock() { :; }
'''


class CliTests(unittest.TestCase):
    def shell(self, body):
        with TemporaryDirectory() as directory:
            env = Path(directory) / '.env'
            env.write_text('ASSOZETA_UPDATER_REF=updater-ref-placeholder\n')
            return subprocess.run(['sh', '-c', DEFINITIONS + CLI_HARNESS + body], capture_output=True, text=True,
                                  env={**os.environ, 'ASSOZETA_INSTALL_ROOT': directory, 'ASSOZETA_ENV_FILE': str(env)}), directory, env

    def test_usage_lists_the_runtime_commands(self):
        result, _, _ = self.shell('\nusage\n')
        for command in ('runtime-status', 'runtime-recover', 'runtime-switch'):
            self.assertTrue(command in result.stdout, 'CONTRACT MISSING in usage: ./bin/assozeta ' + command)

    def test_status_is_read_only_and_recover_and_switch_run_under_the_lifecycle_lock(self):
        result, directory, env = self.shell('\nruntime_status_prod\n')
        self.assertEqual(result.returncode, 0, 'CONTRACT MISSING: runtime_status_prod in bin/assozeta: ' + result.stderr)
        self.assertEqual(result.stdout.splitlines(), [f'tool /runner/runtime_switch.py status {directory} {env}'])
        result, directory, env = self.shell('\nrun_locked_prod_command runtime-recover\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], 'locked runtime-recover')
        self.assertIn(f'tool /runner/runtime_switch.py recover {directory} {env}', lines)
        result, directory, env = self.shell('\nrun_locked_prod_command runtime-switch go\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'tool /runner/runtime_switch.py switch {directory} {env} go', result.stdout.splitlines())
        result, _, _ = self.shell('\nrun_locked_prod_command runtime-switch node\n')
        self.assertNotEqual(result.returncode, 0, 'only django|go are accepted')
        self.assertNotIn('tool ', result.stdout)


class RunnerWiringTests(unittest.TestCase):
    def test_runner_exposes_and_runs_the_switch_through_runtime_switch(self):
        source = (UPDATER / 'server.py').read_text()
        for needle in ("'/runtime-switches'", "'/runtime'", 'runtime_switch.submit(', 'runtime_switch.run(',
                       'runtime_switch.recover(', 'runtime_switch.status(', "'runtime_switch'"):
            self.assertTrue(needle in source, 'CONTRACT MISSING in selfhost/updater/server.py: ' + needle)

    def test_updater_image_ships_the_controller(self):
        self.assertTrue((UPDATER / 'runtime_switch.py').is_file(),
                        'CONTRACT MISSING: selfhost/updater/runtime_switch.py')
        dockerfile = (UPDATER / 'Dockerfile').read_text()
        self.assertTrue('COPY selfhost/updater/*.py ' in dockerfile or 'runtime_switch.py' in dockerfile,
                        'the updater image must ship runtime_switch.py')


if __name__ == '__main__':
    unittest.main()
