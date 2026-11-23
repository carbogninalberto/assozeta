"""Phase 6 milestone A: installation, maintenance and recovery select the effective runtime (CONTRACT P6-A v1).

Boundary: the distribution's own bin/assozeta (and the updater's in-app restore job) on a
simulated installation (TESTS/handoff/p6_harness.py): real Compose rendering, simulated
containers, a private PostgreSQL with every operational migration where `backup` really
dumps and `restore` really swaps databases, a private Redis that `restore` really flushes.
Every test runs in both directions (a Django-mode and a Go-mode installation). Failures read
'P6A contract red: ...' (the expected-red signature); harness failures are errors.

Milestone A forbids, in each steady mode, starting any resident service of the other runtime
and running any opposite-runtime container except the documented maintenance-only schema
migrator (`migrate` in Go mode, `migrate-go` in Django mode). Milestone B
(test_p6_independence.py) removes that exception.

Contract pinned here (names are the implementer's interface):
  create-superuser   execs into the running active API only: Go mode
                     `exec -T -e DJANGO_SUPERUSER_PASSWORD api-go /app/create-instance-superuser ARGS`,
                     Django mode `exec ... api python manage.py create_instance_superuser ARGS`
                     (the password travels by variable name, never in argv).
  sync-manual        one-shot of the active runtime's image with --no-deps: Go mode the packaged
                     `/app/manuale-cli sync-corpus` (/app/manuale stays the shared manual-data
                     volume), Django mode `python manage.py sync_manuale_corpus`.
  health             new read-only command `bin/assozeta health`: last stdout line is one JSON object
                     {runtime, generation, status: passed|failed, checks: {routing, schema, worker,
                     scheduler, relay}}, each check {status: passed|failed, reason: code|null}; exit 0
                     iff every check passed. It starts, runs and execs no container (SQL through the
                     postgres service and the public readiness URL only). Checks of the effective runtime R
                     (thresholds ASSOZETA_HEALTH_HEARTBEAT_SECONDS, default 90, and
                     ASSOZETA_HEALTH_STALL_SECONDS, default 600, from the process or .env):
                       routing   $APP_URL/api/readyz answers                     -> readiness_failed
                       schema    assozeta_go_schema_migrations row present, not dirty
                                                                                 -> schema_missing|schema_dirty
                       worker    a fresh public.assozeta_handoff_consumers heartbeat of R with role broker
                                 (Go also role durable); none fresh for the other runtime; no outstanding
                                 ledger row of R due longer than the stall threshold
                                                                -> no_heartbeat|stale_heartbeat|inactive_runtime_active|stalled
                       scheduler assozeta_scheduler_lease held by R with a fresh heartbeat and an unexpired
                                 lease; no unexpired lease of the other runtime
                                                                -> no_lease|stale_lease|inactive_runtime_active
                       relay     a fresh heartbeat of R with role relay, none fresh for the other runtime,
                                 no undelivered assozeta_go_realtime_events row older than the stall
                                 threshold                      -> no_heartbeat|stale_heartbeat|inactive_runtime_active|stalled
                     These are the Go equivalents of the Celery control ping and the Beat heartbeat, and
                     the same checks for Django. `status` prints the same document after
                     '@@ASSOZETA_HEALTH '.
  backup             manifest.txt keeps its five historical keys and adds format=2, runtime=<R>,
                     runtime_generation=<generation>, work_checkpoint=ledger and
                     work_outstanding=<outstanding ledger rows in the dump>. Outstanding work is
                     reconciled before the dump: a broker message on either historical queue that the
                     ledger does not account for is recorded in the ledger (owner R) so the dump holds it.
  restore            keeps the effective runtime that ran before the restore (the archive's mode is
                     data, not a switch): after the swap the authority row reads that runtime with a
                     generation greater than both the pre-restore and the archived generation (no
                     rewind). Every outstanding ledger row of the restored database is owned by that
                     runtime and generation, keeps its ETA, and has exactly one delivery on that
                     runtime's queue (celery or assozeta-go, priority subqueues included) and none on
                     the other. `--no-backup` refuses before stopping anything while the live
                     installation has outstanding work (ledger or broker), naming 'outstanding work'.
                     A historical archive (no format key) restores and prints
                     '@@ASSOZETA_RESTORE_LIMITATION no_work_checkpoint'. An interrupted database swap
                     (the CLI killed between the two renames) is completed back to the previous live
                     database by the next locked lifecycle command (`start` here), which then succeeds.
                     The in-app restore (updater restore_jobs) has the same outcome.

Run: python3 -B -m unittest -v TESTS.handoff.test_p6_resident   (needs docker compose CLI, PostgreSQL
server binaries, redis-server; ~10 min)
"""
import json
import unittest
import uuid

from TESTS.handoff import p5_harness as h5
from TESTS.handoff import p6_harness as h

RED = 'P6A contract red'
IN_APP_RESTORE = """
import json, sys, uuid
from pathlib import Path
import restore_jobs
from journal import Journal
root, env_file, name, actor = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
journal = Journal(root / '.updater/operations.sqlite3')
record = restore_jobs.start_restore(root, env_file, journal, name, {'request_id': str(uuid.uuid4()), 'actor_id': actor,
                                                                    'confirm': restore_jobs.CONFIRM_WORD})
restore_jobs.run_restore(root, env_file, journal, record)
final = next(item for item in journal.records() if item['id'] == record['id'])
print(json.dumps({'status': final['status'], 'error': final.get('error')}))
"""


def tearDownModule():
    h.stop_services()


class Base(h.ContractA, unittest.TestCase):
    maxDiff = None

    def assert_mode_set(self, inst, runtime, label):
        self.assertEqual(sorted(inst.app_running()), sorted(h.APP[runtime]),
                         f'{RED}: application services after {label} are not exactly the {runtime} set')

    def assert_no_opposite(self, inst, runtime, since, label):
        self.assertEqual(inst.opposite(runtime, since, milestone='A'), [],
                         f'{RED}: {label} in {runtime} mode used an opposite-runtime container')


class ActiveRuntimeOperations(Base):
    """Superuser creation and manual sync run on the active runtime."""

    def superuser(self, runtime):
        with h.installation(self, runtime) as inst:
            mark = inst.mark()
            result = inst.cli('create-superuser', '--username', 'owner6', '--email', 'owner6@example.invalid',
                              '--noinput', check=False, env={'DJANGO_SUPERUSER_PASSWORD': 'fixture-password-6'})
            execs = [item for item in inst.executables(mark) if item['kind'] == 'exec']
            self.assertEqual(result.returncode, 0, f'{RED}: create-superuser failed in {runtime} mode: '
                                                   f'{result.stderr.strip()[-300:]} {execs}')
            self.assert_no_opposite(inst, runtime, mark, 'create-superuser')
            self.assertEqual([item['service'] for item in execs], [h.ACTIVE_API[runtime]],
                             f'{RED}: create-superuser must exec into the running {h.ACTIVE_API[runtime]} only')
            argv = execs[0]['argv']
            expected = ['/app/create-instance-superuser'] if runtime == 'go' else [
                'python', 'manage.py', 'create_instance_superuser']
            self.assertEqual(argv[:len(expected)], expected, f'{RED}: native superuser command for {runtime}')
            self.assertIn('--noinput', argv)
            calls = [e['call'] for e in inst.events()[mark:] if e.get('call', [])[:2] == ['compose', 'exec']]
            self.assertFalse(any('fixture-password-6' in ' '.join(call) for call in calls),
                             f'{RED}: the superuser password reached argv')
            self.assertTrue(any('DJANGO_SUPERUSER_PASSWORD' in call for call in calls),
                            f'{RED}: the password must be passed by variable name')
            self.assert_mode_set(inst, runtime, 'create-superuser')

    def test_django_superuser_creation_uses_the_django_api(self):
        self.superuser('django')

    def test_go_superuser_creation_uses_the_go_api(self):
        self.superuser('go')

    def manual(self, runtime):
        with h.installation(self, runtime) as inst:
            inst.set_env(MANUAL_CORPUS_BASE_URL='https://manual.example.invalid/corpus')
            mark = inst.mark()
            result = inst.cli('sync-manual', check=False)
            self.assertEqual(result.returncode, 0, f'{RED}: sync-manual failed in {runtime} mode: '
                                                   f'{result.stderr.strip()[-300:]}')
            self.assert_no_opposite(inst, runtime, mark, 'sync-manual')
            oneshots = [item for item in inst.executables(mark) if item['kind'] == 'oneshot']
            self.assertEqual([item['runtime'] for item in oneshots], [runtime],
                             f'{RED}: sync-manual must be exactly one {runtime} one-shot: {oneshots}')
            argv = ' '.join(oneshots[0]['argv'])
            needle = '/app/manuale-cli sync-corpus' if runtime == 'go' else 'manage.py sync_manuale_corpus'
            self.assertIn(needle, argv, f'{RED}: native manual sync for {runtime}')
            self.assertFalse([item for item in inst.executables(mark) if item['kind'] == 'resident'
                              and item['runtime'] != 'shared'], f'{RED}: sync-manual started an application service')

    def test_django_manual_sync_uses_the_django_command(self):
        self.manual('django')

    def test_go_manual_sync_uses_the_packaged_go_command(self):
        self.manual('go')


class ResidentCharacterization(Base):
    """Green guard (Phase 2 behaviour Phase 6 must keep): lifecycle commands start only the mode's set."""

    def commands(self, runtime):
        with h.installation(self, runtime) as inst:
            for command in (('start',), ('restart',), ('migrate',), ('backup',), ('stop',), ('start',)):
                with self.subTest(command=command):
                    mark = inst.mark()
                    inst.cli(*command)
                    self.assert_no_opposite(inst, runtime, mark, ' '.join(command))
                    if command != ('stop',):
                        self.assert_mode_set(inst, runtime, ' '.join(command))

    def test_django_lifecycle_commands_start_only_django(self):
        self.commands('django')

    def test_go_lifecycle_commands_start_only_go(self):
        self.commands('go')


class Health(Base):
    """`bin/assozeta health`: routing, schema, worker progress, scheduler freshness and relay delivery."""

    def report(self, inst, runtime):
        mark = inst.mark()
        code, document, result = h.health(inst)
        self.assertIsNotNone(document, f'{RED}: bin/assozeta health printed no JSON document '
                                       f'(exit {result.returncode}: {result.stderr.strip()[-200:]})')
        self.assertEqual([item for item in inst.executables(mark) if item['kind'] != 'controller'], [],
                         f'{RED}: health must not start, run or exec an application container')
        return code, document

    def healthy(self, runtime):
        with h.installation(self, runtime) as inst:
            inst.healthy_runtime(runtime)
            code, document = self.report(inst, runtime)
            checks = document.get('checks') or {}
            self.assertEqual(sorted(checks), ['relay', 'routing', 'scheduler', 'schema', 'worker'],
                             f'{RED}: health checks')
            self.assertEqual({name: check.get('status') for name, check in checks.items()},
                             dict.fromkeys(checks, 'passed'), f'{RED}: a healthy {runtime} installation')
            self.assertEqual((code, document.get('status'), document.get('runtime'), document.get('generation')),
                             (0, 'passed', runtime, inst.authority()[1]), f'{RED}: health summary')
            status = inst.cli('status', check=False)
            line = next((item for item in status.stdout.splitlines() if item.startswith('@@ASSOZETA_HEALTH ')), None)
            self.assertIsNotNone(line, f'{RED}: status does not print the health document')

    def test_django_health_passes_on_django_signals(self):
        self.healthy('django')

    def test_go_health_passes_on_go_signals(self):
        self.healthy('go')

    def faults(self, runtime):
        other = h.OPPOSITE[runtime]
        cases = [
            ('worker', 'stale_heartbeat', lambda i: i.heartbeat(runtime, 'broker', age_seconds=600), ('broker',)),
            ('worker', 'inactive_runtime_active', lambda i: i.heartbeat(other, 'broker'), ()),
            ('worker', 'stalled', lambda i: i.sql(
                "UPDATE public.assozeta_handoff_work SET accepted_at = clock_timestamp() - interval '2 hours' "
                f"WHERE task_id = '{i.push(h.QUEUE[runtime], h.MAIL_TASK, args=h5.mail_args(9))}';"), ()),
            ('scheduler', 'stale_lease', lambda i: i.scheduler_lease(runtime, age_seconds=600), ('scheduler',)),
            ('relay', 'stale_heartbeat', lambda i: i.heartbeat(runtime, 'relay', age_seconds=600), ('relay',)),
            ('relay', 'inactive_runtime_active', lambda i: i.heartbeat(other, 'relay'), ()),
            ('relay', 'stalled', lambda i: i.sql(
                "INSERT INTO public.assozeta_go_realtime_events (envelope_version, kind, target_id, created_at) "
                f"VALUES (1, 'notification', '{uuid.uuid4()}', clock_timestamp() - interval '2 hours');"), ()),
            ('schema', 'schema_dirty', lambda i: i.sql('UPDATE public.assozeta_go_schema_migrations SET dirty = true;'),
             ()),
            ('routing', 'readiness_failed', lambda i: i.fail_url(i.env()['APP_URL'] + '/api/readyz'), ()),
        ]
        if runtime == 'go':
            cases.insert(1, ('worker', 'no_heartbeat', None, ('durable',)))
        with h.installation(self, runtime) as inst:
            for check, reason, fault, skip_roles in cases:
                with self.subTest(check=check, reason=reason):
                    inst.reset_health()
                    for role in h.ROLES[runtime]:
                        if role not in skip_roles:
                            inst.heartbeat(runtime, role)
                    if 'scheduler' not in skip_roles:
                        inst.scheduler_lease(runtime)
                    if fault:
                        fault(inst)
                    code, document = self.report(inst, runtime)
                    found = (document.get('checks') or {}).get(check) or {}
                    self.assertEqual((code, document.get('status'), found.get('status'), found.get('reason')),
                                     (1, 'failed', 'failed', reason),
                                     f'{RED}: {runtime} health must fail {check} ({reason})')

    def test_django_health_fails_for_each_missing_signal(self):
        self.faults('django')

    def test_go_health_fails_for_each_missing_signal(self):
        self.faults('go')


class BackupCheckpoint(Base):
    """Backups record the runtime and hold every outstanding job."""

    def checkpoint(self, runtime):
        with h.installation(self, runtime) as inst:
            work = inst.seed_work()
            unledgered = inst.push(h.QUEUE[runtime], h.MAIL_TASK, args=h5.mail_args(3), admit=False)
            generation = inst.authority()[1]
            result = inst.cli('backup', check=False)
            self.assertEqual(result.returncode, 0, f'{RED}: backup failed in {runtime} mode: {result.stderr[-300:]}')
            archive = inst.backups()[-1]
            manifest = inst.manifest(archive)
            for key in ('created_at', 'version', 'database', 'bucket', 'aws_location'):
                self.assertIn(key, manifest, f'{RED}: historical manifest key {key} dropped')
            self.assertEqual({key: manifest.get(key) for key in ('format', 'runtime', 'runtime_generation',
                                                                 'work_checkpoint', 'work_outstanding')},
                             {'format': '2', 'runtime': runtime, 'runtime_generation': str(generation),
                              'work_checkpoint': 'ledger', 'work_outstanding': '3'},
                             f'{RED}: backup manifest must record the runtime and the work checkpoint')
            dumped = {row['task_id']: row for row in inst.dumped_rows(
                archive, 'SELECT task_id, state, owner_runtime FROM public.assozeta_handoff_work')}
            for label, task_id in {**work, 'unledgered broker message': unledgered}.items():
                row = dumped.get(task_id) or {}
                self.assertIn(row.get('state'), ('pending', 'held', 'staged'),
                              f'{RED}: {label} is not outstanding in the backup: {row}')
                self.assertEqual(row.get('owner_runtime'), runtime, f'{RED}: {label} owner in the backup')

    def test_django_backup_checkpoints_outstanding_work(self):
        self.checkpoint('django')

    def test_go_backup_checkpoints_outstanding_work(self):
        self.checkpoint('go')


class Restore(Base):
    """Restores keep the effective runtime, never rewind it, and re-deliver the archived work."""

    def fixture_switch(self, inst, target):
        runtime, generation, _ = inst.authority()
        if runtime == target:
            return
        outcome = inst.sql(f"SELECT outcome FROM public.assozeta_commit_runtime_authority('{uuid.uuid4()}', "
                           f"'{runtime}', {generation}, '{target}');")
        if outcome != 'committed':
            raise h5.HarnessError(f'fixture authority commit answered {outcome}')
        inst.cli('restart')

    def finish_live_work(self, inst, work):
        """Fixture: the work queued before the backup completed on the live installation."""
        listed = ','.join(f"'{task}'" for task in work.values())
        inst.sql(f"UPDATE public.assozeta_handoff_work SET state = 'completed' WHERE task_id IN ({listed});")
        inst.broker().call('FLUSHALL')

    def assert_restored(self, inst, runtime, before_generation, archived_generation, work, since, eta):
        authority = inst.authority()
        self.assertEqual(authority[0], runtime, f'{RED}: the restore must keep the {runtime} runtime that was running')
        self.assertGreater(authority[1], max(before_generation, archived_generation),
                           f'{RED}: the restore rewound the generation (before {before_generation}, archived '
                           f'{archived_generation}, now {authority[1]})')
        self.assert_mode_set(inst, runtime, 'restore')
        self.assert_no_opposite(inst, runtime, since, 'restore')
        rows = inst.ledger_rows(list(work.values()))
        for label, task_id in work.items():
            row = rows.get(task_id) or {}
            self.assertEqual((row.get('owner_runtime'), row.get('owner_generation')), (runtime, authority[1]),
                             f'{RED}: restored {label} is not owned by the effective runtime: {row}')
            copies = inst.copies(task_id)
            self.assertEqual(sum(n for key, n in copies.items() if key.startswith(h.QUEUE[runtime])), 1,
                             f'{RED}: restored {label} has no single delivery on {h.QUEUE[runtime]}: {copies}')
            self.assertFalse([key for key in copies if key.startswith(h.QUEUE[h.OPPOSITE[runtime]])],
                             f'{RED}: restored {label} left on the inactive queue: {copies}')
        due = inst.rows(f"SELECT eta FROM public.assozeta_handoff_work WHERE task_id = '{work['next month']}'")
        self.assertEqual(due and due[0]['eta'][:16], eta[:16], f'{RED}: the restored future job lost its ETA')

    def cross(self, backup_mode, restore_mode, in_app=False):
        with h.installation(self, backup_mode) as inst:
            work = inst.seed_work()
            eta = inst.rows(f"SELECT eta FROM public.assozeta_handoff_work WHERE task_id = '{work['next month']}'")[0]['eta']
            archived_generation = inst.authority()[1]
            inst.cli('backup')
            archive = inst.backups()[-1]
            self.finish_live_work(inst, work)
            self.fixture_switch(inst, restore_mode)
            before = inst.authority()[1]
            mark = inst.mark()
            if in_app:
                self.in_app_restore(inst, archive)
            else:
                result = inst.cli('restore', str(archive), '--yes', check=False)
                self.assertEqual(result.returncode, 0, f'{RED}: restore failed: {result.stdout[-400:]} {result.stderr[-400:]}')
            self.assert_restored(inst, restore_mode, before, archived_generation, work, mark, eta)

    def in_app_restore(self, inst, archive):
        """The runner's restore job (selfhost/updater/restore_jobs.py), as the updater image lays out /runner."""
        result = inst.run_python(IN_APP_RESTORE, str(inst.root), str(inst.env_file), archive.name, h5.OWNER,
                                 extra_path=(h5.UPDATER, h.ROOT / 'BE/instance'))
        try:
            final = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise h5.HarnessError(f'in-app restore driver failed: {result.stderr[-800:]}') from None
        self.assertEqual(final['status'], 'succeeded', f'{RED}: in-app restore: {final.get("error")}')

    def test_django_backup_restored_into_go_mode_with_work_queued(self):
        self.cross('django', 'go')

    def test_go_backup_restored_into_django_mode_with_work_queued(self):
        self.cross('go', 'django')

    def test_django_backup_restored_in_django_mode_redelivers_work(self):
        self.cross('django', 'django')

    def test_go_backup_restored_in_go_mode_redelivers_work(self):
        self.cross('go', 'go')

    def test_in_app_restore_of_a_django_backup_in_go_mode(self):
        self.cross('django', 'go', in_app=True)

    def test_in_app_restore_of_a_go_backup_in_django_mode(self):
        self.cross('go', 'django', in_app=True)

    def refuse(self, runtime):
        with h.installation(self, runtime) as inst:
            inst.cli('backup')
            archive = inst.backups()[-1]
            work = inst.seed_work()
            generation = inst.authority()[1]
            mark = inst.mark()
            result = inst.cli('restore', str(archive), '--yes', '--no-backup', check=False)
            output = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, f'{RED}: restore --no-backup proceeded with outstanding work')
            self.assertIn('outstanding work', output, f'{RED}: the refusal must name the outstanding work')
            stopped = [e for e in inst.events()[mark:] if e.get('call', [])[:2] == ['compose', 'stop']]
            self.assertEqual(stopped, [], f'{RED}: the refused restore stopped services')
            self.assertEqual(inst.authority()[1], generation, f'{RED}: the refused restore changed the database')
            for label, task_id in work.items():
                self.assertEqual(sum(inst.copies(task_id).values()), 1, f'{RED}: the refused restore dropped {label}')
            self.assert_mode_set(inst, runtime, 'a refused restore')

    def test_django_restore_without_backup_refuses_outstanding_work(self):
        self.refuse('django')

    def test_go_restore_without_backup_refuses_outstanding_work(self):
        self.refuse('go')

    def historical(self, runtime):
        with h.installation(self, runtime) as inst:
            inst.cli('backup')
            archive = inst.backups()[-1]
            manifest = inst.manifest(archive)
            inst.rewrite_manifest(archive, {key: manifest[key] for key in ('created_at', 'version', 'database',
                                                                           'bucket', 'aws_location') if key in manifest})
            mark = inst.mark()
            result = inst.cli('restore', str(archive), '--yes', check=False)
            self.assertEqual(result.returncode, 0, f'{RED}: a historical archive must still restore: {result.stderr[-300:]}')
            self.assertIn('@@ASSOZETA_RESTORE_LIMITATION no_work_checkpoint', result.stdout,
                          f'{RED}: the restore must report that the archive has no work checkpoint')
            self.assertEqual(inst.authority()[0], runtime, f'{RED}: historical restore changed the runtime')
            self.assert_mode_set(inst, runtime, 'historical restore')
            self.assert_no_opposite(inst, runtime, mark, 'historical restore')

    def test_django_historical_archive_restores_with_its_limitation(self):
        self.historical('django')

    def test_go_historical_archive_restores_with_its_limitation(self):
        self.historical('go')

    def interrupted(self, runtime):
        with h.installation(self, runtime) as inst:
            inst.cli('backup')
            archive = inst.backups()[-1]
            before = inst.authority()
            inst.sql("CREATE TABLE public.p6_sentinel (value text); INSERT INTO public.p6_sentinel VALUES ('live');")
            # The first rename moves the live database aside; the CLI dies before the staged one takes its name.
            inst.set_state(kill_after='new=assozeta_restore_rollback_')
            result = inst.cli('restore', str(archive), '--yes', '--no-backup', check=False)
            killed = [e for e in inst.events() if e.get('call', [])[:1] == ['killed']]
            if not killed:
                raise h5.HarnessError(f'kill injection never fired (restore exit {result.returncode}): '
                                      f'{result.stdout[-400:]} {result.stderr[-400:]}')
            if inst.database_exists(inst.database):
                raise h5.HarnessError('the interrupted swap left the live database in place (fixture did not interrupt)')
            mark = inst.mark()
            recovered = inst.cli('start', check=False)
            self.assertEqual(recovered.returncode, 0, f'{RED}: the next lifecycle command after an interrupted '
                                                      f'database swap must recover: {recovered.stderr.strip()[-300:]}')
            self.assertTrue(inst.database_exists(inst.database), f'{RED}: the live database was not put back')
            self.assertEqual(inst.sql('SELECT value FROM public.p6_sentinel;'), 'live',
                             f'{RED}: the recovered live database is not the pre-restore one')
            self.assertEqual(inst.authority()[:2], before[:2], f'{RED}: recovery changed the authority')
            self.assertFalse([name for name in inst.restore_databases() if 'rollback' in name],
                             f'{RED}: recovery left the rollback database behind')
            self.assert_mode_set(inst, runtime, 'recovery from an interrupted swap')
            self.assert_no_opposite(inst, runtime, mark, 'recovery from an interrupted swap')

    def test_django_interrupted_database_swap_recovers_on_next_command(self):
        self.interrupted('django')

    def test_go_interrupted_database_swap_recovers_on_next_command(self):
        self.interrupted('go')


class FailedUpgrade(Base):
    """The installed controller bootstraps the target release; its image of the active runtime cannot start."""

    def failed(self, runtime):
        with h.installation(self, install=False) as inst:
            outcome = h.failed_upgrade(self, inst, runtime)
            upgrade = outcome['upgrade']
            self.assertNotEqual(upgrade.returncode, 0, f'{RED}: an upgrade whose target {runtime} image cannot start '
                                                       'reported success')
            stages = [line.split()[1] for line in upgrade.stdout.splitlines() if line.startswith('@@ASSOZETA_STAGE ')]
            self.assertIn(stages[-1:], (['restarting'], ['health_check']),
                          f'{RED}: the target must fail when it starts, after migrations: {stages}')
            self.assert_no_opposite(inst, runtime, outcome['marks'][0], 'the failed upgrade')
            self.assertIsNotNone(outcome['transaction'], f'{RED}: the failed upgrade left no recoverable transaction')
            recover = outcome['recover']
            self.assertEqual(recover.returncode, 0, f'{RED}: recover-upgrade in {runtime} mode: '
                                                    f'{(recover.stdout + recover.stderr)[-600:]}')
            values = inst.env()
            self.assertEqual((values.get('ASSOZETA_VERSION'), values.get('ASSOZETA_BACKEND_REF'),
                              values.get('ASSOZETA_BACKEND_GO_REF')),
                             ('2.0.1', h.release_ref('backend', 1), h.release_ref('backend-go', 1)),
                             f'{RED}: recovery must restore the source release pins')
            self.assertEqual(sorted(inst.app_running() & (h.DJANGO_APP | h.GO_APP)), sorted(h.APP[runtime]),
                             f'{RED}: application services after recovery are not exactly the {runtime} set')
            source = h.release_ref('backend-go' if runtime == 'go' else 'backend', 1)
            images = {name: (inst.observed()['definitions'].get(name) or {}).get('image') for name in h.APP[runtime]}
            self.assertEqual(images, dict.fromkeys(h.APP[runtime], source), f'{RED}: recovery runs another image')
            authority = inst.authority()
            self.assertEqual(authority[0], runtime, f'{RED}: recovery changed the runtime')
            self.assertGreater(authority[1], outcome['generation'],
                               f'{RED}: recovery restores the pre-update backup and must advance, not rewind, the generation')
            self.assert_no_opposite(inst, runtime, outcome['marks'][1], 'recover-upgrade')

    def test_go_upgrade_with_a_failing_go_image_recovers_to_the_source(self):
        self.failed('go')

    def test_django_upgrade_with_a_failing_django_image_recovers_to_the_source(self):
        self.failed('django')


if __name__ == '__main__':
    unittest.main()
