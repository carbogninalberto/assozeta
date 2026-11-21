"""Phase 5 part B: one effective mode, and the status the browser is given (CONTRACT P5-B v1).

Boundary: the Phase 5 simulated installation (TESTS/handoff/p5_harness.py; real private
PostgreSQL and Redis, the checkout's updater tools, simulated containers).

STATUS (P5-STATUS v1, posted on topics p5 and p5ui). One object, served identically by
runner GET /runtime (runtime_switch.status), API GET /instance/admin/runtime (verbatim
relay, Phase 1), ./bin/assozeta runtime-status and, with neither app API running, the
runner status socket POST /instance-update-status under the key 'runtime'.
  Phase 1 keys unchanged (protocol 1, effective_runtime, generation, last_operation_id,
  requested_runtime, operation, active, authority_error, recovery_command) plus
  intended_runtime  target of the active (queued|running|recovery_required) runtime
                    operation, else effective_runtime
  maintenance       bool, .operations/maintenance.flag exists
  switch_available  bool, the installed Compose declares the django and go profiles
  unavailable_reason str|null (Italian) when switch_available is false
  operation view (operation, active) = Phase 1 keys plus source_generation, failed_stage,
  adoption (bool), progress {stage, index, total 9, maintenance_since,
  maintenance_deadline}, dispositions {staged, transferred, blocked, ambiguous},
  blockers null|{count, by_reason, oldest_age_seconds, items[{ref, reason, age_seconds}]
  (oldest first, <= 20), action}, recovery_action null|'retry'|'recover'|'reconcile'.
  Phase 1 counts = {pending: dispositions.staged, transferred, blocked}.
  Never: env values, credentials, task names, arguments, envelopes, provider ids.

ONE EFFECTIVE MODE (exclusive releases). Every worker, relay, protocol and backend toggle
follows the effective runtime; independent preferences cannot split the stack:
  - ASSOZETA_GO_TASKS, ASSOZETA_REALTIME_RELAY_BACKEND, ASSOZETA_DJANGO_RELAY_REPLICAS,
    ASSOZETA_GO_RELAY_REPLICAS, ASSOZETA_BAKNEY_PROTOCOL_BACKEND/_UPSTREAM: ignored by the
    switch in both directions (the process set is exactly the target's, Phase 2 sets).
  - GO_TASK_QUEUE_TASKS non-empty (routes Django producers to a Go queue): a switch to
    django refuses at preflight (error_code 'stale_preference', error naming the key,
    nothing changed); same refusal as Phase 2's Django-mode start.
  - ASSOZETA_GO_WHATSAPP_OUTBOX is a controller-only gate (the private preflight passes
    0 with -e): an installation .env value is retired by the lifecycle (removed by
    `validate`, hence by every lifecycle command), so a steady Go api-go always renders 1.
  - The owner Go-tasks switch (selfhost/updater/go_tasks.py, runner POST /go-tasks,
    PUT /instance/admin goTasksEnabled) is retired on exclusive distributions:
    go_tasks.retired(root, env_file) -> True (decided from the installed compose.yml,
    without a command); the runner answers POST /go-tasks with 410 {'code': 'retired',
    'error': ...} and journals nothing; a go_tasks record queued before the upgrade fails
    with error_code 'retired' without editing .env or restarting anything.
  - The instance setting behind uiApiVersion (instance_instanceconfiguration.ui_api_version)
    and go_tasks_enabled are read once, by the legacy adoption (part C); otherwise they
    never change the deployment, nor does a browser's cached preference, ?api= or a
    feature flag (none of them reaches the controller).
  - Phase 5 exit: no handoff-inventory entry keeps an owner planned for phase 5.

Run: python3 -B -m unittest -v TESTS.handoff.test_p5_one_mode
"""
import json
import tempfile
import unittest
from pathlib import Path

from TESTS.handoff import p5_harness as h
from TESTS.handoff.p2_harness import EMAIL_TASKS, render
from TESTS.handoff.topology import BASELINE_REVISION, ROOT

RED = 'P5 contract red'
P1_STATUS = {'protocol', 'effective_runtime', 'generation', 'last_operation_id', 'requested_runtime', 'operation',
             'active', 'authority_error', 'recovery_command'}
P5_STATUS = {'intended_runtime', 'maintenance', 'switch_available', 'unavailable_reason'}
P1_VIEW = {'id', 'kind', 'actor_id', 'previous_runtime', 'requested_runtime', 'expected_generation', 'status',
           'stage', 'outcome', 'counts', 'committed_generation', 'error', 'error_code', 'recovery', 'created_at',
           'updated_at'}
P5_VIEW = {'source_generation', 'failed_stage', 'adoption', 'progress', 'dispositions', 'blockers', 'recovery_action'}
DISPOSITIONS = {'staged', 'transferred', 'blocked', 'ambiguous'}


def setUpModule():
    h.services()


def tearDownModule():
    h.stop_services()


class ModeCase(h.ContractCase, unittest.TestCase):
    maxDiff = None
    revision = None

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='assozeta-p5b-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.inst = h.P5Installation(self.directory, revision=self.revision)
        self.inst.install()

    def committed(self, record, generation, label):
        self.assertEqual((record['status'], record['outcome'], record['committed_generation']),
                         ('succeeded', 'committed', generation),
                         f'{RED}: {label}: status {record["status"]}, error_code {record.get("error_code")}, '
                         f'failed_stage {record.get("failed_stage")}')

    def assert_view(self, view, label):
        self.assertTrue(P1_VIEW | P5_VIEW <= set(view), f'{RED}: {label}: operation view lacks '
                                                       f'{sorted((P1_VIEW | P5_VIEW) - set(view))}')
        self.assertEqual(set(view['dispositions']), DISPOSITIONS, f'{RED}: {label}: disposition keys')
        self.assertTrue(all(type(view['dispositions'][k]) is int for k in DISPOSITIONS), f'{RED}: {label}: counts')
        self.assertEqual(view['counts'], {'pending': view['dispositions']['staged'],
                                          'transferred': view['dispositions']['transferred'],
                                          'blocked': view['dispositions']['blocked']},
                         f'{RED}: {label}: Phase 1 counts must equal the dispositions')
        progress = view['progress']
        self.assertEqual(set(progress), {'stage', 'index', 'total', 'maintenance_since', 'maintenance_deadline'},
                         f'{RED}: {label}: progress keys')
        self.assertEqual(progress['total'], 9, f'{RED}: {label}: progress total')
        self.assertIn(view['recovery_action'], (None, 'retry', 'recover', 'reconcile'), f'{RED}: {label}')


class StatusContract(ModeCase):
    def test_idle_status_reports_intended_effective_and_capability(self):
        status = self.inst.status()
        self.assertTrue(P1_STATUS | P5_STATUS <= set(status),
                        f'{RED}: status lacks {sorted((P1_STATUS | P5_STATUS) - set(status))}')
        self.assertEqual({key: status[key] for key in ('protocol', 'effective_runtime', 'generation', 'intended_runtime',
                                                       'maintenance', 'switch_available', 'unavailable_reason')},
                         {'protocol': 1, 'effective_runtime': 'django', 'generation': 1, 'intended_runtime': 'django',
                          'maintenance': False, 'switch_available': True, 'unavailable_reason': None},
                         f'{RED}: idle status of a fresh exclusive installation')
        result, operator = self.inst.tool_json('status', str(self.inst.root), str(self.inst.env_file))
        self.assertEqual((result.returncode, {k: operator.get(k) for k in P5_STATUS}),
                         (0, {k: status[k] for k in P5_STATUS}), f'{RED}: operator status differs from the API status')

    def test_operation_view_while_running_and_after_success(self):
        inst = self.inst
        work = [inst.push('celery', h.MAIL_TASK, args=h.mail_args(n)) for n in range(3)]
        seen = {}

        def barrier(point):
            if point == 'stage:stop_source':
                seen['status'] = inst.status()
        final = inst.switch('go', barrier=barrier)
        self.committed(final, 2, 'django->go')
        running = seen.get('status') or {}
        view = running.get('active') or {}
        self.assert_view(view, 'while running')
        self.assertEqual((running['intended_runtime'], running['requested_runtime'], running['effective_runtime'],
                          running['maintenance']), ('go', 'go', 'django', True),
                         f'{RED}: status in the maintenance window')
        self.assertEqual({k: view[k] for k in ('source_generation', 'failed_stage', 'adoption', 'blockers',
                                               'recovery_action')},
                         {'source_generation': 1, 'failed_stage': None, 'adoption': False, 'blockers': None,
                          'recovery_action': None}, f'{RED}: operation view while running')
        self.assertEqual((view['progress']['stage'], view['progress']['index']), ('stop_source', 5),
                         f'{RED}: progress while running')
        self.assertEqual(view['dispositions'], {'staged': 3, 'transferred': 0, 'blocked': 0, 'ambiguous': 0},
                         f'{RED}: dispositions after stage_backlog')
        done = inst.status()
        self.assert_view(done['operation'], 'after success')
        self.assertEqual((done['operation']['progress']['stage'], done['operation']['progress']['index'],
                          done['operation']['dispositions'], done['intended_runtime'], done['maintenance'],
                          done['active']),
                         ('completed', 10, {'staged': 0, 'transferred': 3, 'blocked': 0, 'ambiguous': 0}, 'go', False,
                          None), f'{RED}: status after success')
        self.assertEqual(len(work), 3)

    def test_status_never_carries_secrets_or_job_data(self):
        inst = self.inst
        sentinel = 'P5-STATUS-ARGUMENT-SENTINEL'
        inst.push('celery', h.MAIL_TASK, args=(sentinel, 'body', 'sender@example.invalid', ['x@example.invalid']))
        values = {key: value for key, value in (line.split('=', 1) for line in inst.env_text().splitlines()
                                                if '=' in line and not line.startswith('#'))}
        final = inst.switch('go')
        self.committed(final, 2, 'django->go')
        published = json.dumps(inst.status())
        log = inst.root / '.updater/logs' / f"{final['id']}.log"
        published += log.read_text() if log.exists() else ''
        for forbidden in [sentinel, h.MAIL_TASK, 'sender@example.invalid'] + [
                values[key] for key in ('DBPASSWORD', 'SECRET_KEY', 'REDIS_PASSWORD', 'MINIO_ROOT_PASSWORD')
                if len(values.get(key, '')) >= 8]:
            self.assertNotIn(forbidden, published, f'{RED}: status or operation log leaks job data or a credential')

    def test_runner_status_socket_serves_the_runtime_status(self):
        source = (h.UPDATER / 'server.py').read_text()
        handler = source[source.index('class StatusHandler'):source.index('def work():')]
        self.assertIn("'runtime': runtime_switch.status(", handler,
                      f'{RED}: POST /instance-update-status must carry the runtime status under the key runtime')


class HistoricalDistributionStatus(ModeCase):
    revision = BASELINE_REVISION

    def test_a_distribution_without_runtime_profiles_reports_the_switch_unavailable(self):
        status = self.inst.status()
        self.assertEqual((status.get('switch_available'), bool(status.get('unavailable_reason'))), (False, True),
                         f'{RED}: a historical dual-API distribution must report the switch unavailable')


class OneEffectiveMode(ModeCase):
    DJANGO_KNOBS = {'ASSOZETA_GO_TASKS': '0', 'GO_TASK_QUEUE_TASKS': '', 'ASSOZETA_REALTIME_RELAY_BACKEND': 'django',
                    'ASSOZETA_DJANGO_RELAY_REPLICAS': '1', 'ASSOZETA_GO_RELAY_REPLICAS': '0',
                    'ASSOZETA_BAKNEY_PROTOCOL_BACKEND': 'django', 'ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM': 'api:8000'}
    GO_KNOBS = {'ASSOZETA_GO_TASKS': '1', 'GO_TASK_QUEUE_TASKS': '', 'ASSOZETA_REALTIME_RELAY_BACKEND': 'go',
                'ASSOZETA_DJANGO_RELAY_REPLICAS': '0', 'ASSOZETA_GO_RELAY_REPLICAS': '1',
                'ASSOZETA_BAKNEY_PROTOCOL_BACKEND': 'go', 'ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM': 'api-go:8000'}

    def test_stale_legacy_toggles_cannot_split_the_stack_in_either_direction(self):
        inst = self.inst
        inst.set_env(**self.DJANGO_KNOBS)
        self.committed(inst.switch('go'), 2, 'switch to go with Django-pointing legacy toggles')
        inst.assert_mode(self, 'go', 'a switch to go with Django-pointing legacy toggles')
        inst.cli('restart')
        inst.assert_mode(self, 'go', 'restart in go mode with Django-pointing legacy toggles')
        inst.set_env(**self.GO_KNOBS)
        self.committed(inst.switch('django'), 3, 'switch to django with Go-pointing legacy toggles')
        inst.assert_mode(self, 'django', 'a switch to django with Go-pointing legacy toggles')
        inst.cli('restart')
        inst.assert_mode(self, 'django', 'restart in django mode with Go-pointing legacy toggles')

    def test_routed_go_task_names_block_a_switch_to_django_before_anything_changes(self):
        inst = self.inst
        self.committed(inst.switch('go'), 2, 'switch to go')
        inst.set_env(ASSOZETA_GO_TASKS='1', GO_TASK_QUEUE_TASKS=EMAIL_TASKS)
        before, env = inst.mark(), inst.env_text()
        final = inst.switch('django')
        self.assertEqual((final['status'], final.get('error_code'), final.get('failed_stage')),
                         ('failed', 'stale_preference', 'preflight'),
                         f'{RED}: a routed GO_TASK_QUEUE_TASKS must refuse a switch to django at preflight')
        self.assertIn('GO_TASK_QUEUE_TASKS', final.get('error') or '', f'{RED}: the refusal must name the key')
        self.assertEqual((inst.changes(before), inst.maintenance(), inst.authority()[:2], inst.env_text()),
                         ([], False, ('go', 2), env), f'{RED}: the refusal changed something')
        inst.assert_mode(self, 'go', 'the refused switch')

    def test_the_whatsapp_gate_in_the_env_file_cannot_disable_go_delivery(self):
        inst = self.inst
        inst.set_env(ASSOZETA_GO_WHATSAPP_OUTBOX='0')
        self.committed(inst.switch('go'), 2, 'switch to go')
        inst.cli('restart')
        gate = (render(inst, 'go')['services']['api-go'].get('environment') or {}).get('ASSOZETA_GO_WHATSAPP_OUTBOX')
        self.assertEqual(gate, '1', f'{RED}: a steady Go api-go must own WhatsApp delivery whatever .env says')
        self.assertNotIn('ASSOZETA_GO_WHATSAPP_OUTBOX=', inst.env_text(),
                         f'{RED}: the controller-only WhatsApp gate must be retired from .env')

    def test_instance_settings_and_cached_preferences_alone_never_change_the_deployment(self):
        inst = self.inst
        inst.seed_instance(ui_api_version='v2', go_tasks_enabled=True)
        inst.cli('restart')
        self.assertEqual(inst.recover(), [], 'a runner recovery pass found an operation to run')
        result, status = inst.tool_json('status', str(inst.root), str(inst.env_file))
        self.assertEqual((inst.authority(), inst.runtime_records(), status and status.get('effective_runtime')),
                         (('django', 1, None), [], 'django'),
                         'the instance setting changed the authority or created an operation')
        inst.assert_mode(self, 'django', 'restart with uiApiVersion v2 and go_tasks_enabled in the database')

    def test_the_owner_go_tasks_switch_is_retired(self):
        inst = self.inst
        inst.rs()
        import importlib
        go_tasks = importlib.import_module('go_tasks')
        self.assertTrue(hasattr(go_tasks, 'retired'), f'{RED}: CONTRACT MISSING go_tasks.retired(root, env_file)')
        self.assertIs(go_tasks.retired(inst.root, inst.env_file), True,
                      f'{RED}: an exclusive distribution must report the Go-tasks switch retired')
        journal = inst.journal()
        record = journal.create({'request_id': 'f0f0f0f0-0000-4000-8000-000000000001', 'actor_id': h.OWNER,
                                 'enabled': True, 'kind': 'go_tasks', 'release_id': None, 'tag': 'test',
                                 'source_version': 'test'})
        before, env = inst.mark(), inst.env_text()
        with inst.active():
            go_tasks.apply_go_tasks(inst.root, inst.env_file, journal, record)
        final = inst.record(record['id'])
        self.assertEqual((final['status'], final.get('error_code')), ('failed', 'retired'),
                         f'{RED}: a queued Go-tasks request must fail as retired')
        self.assertEqual((inst.env_text(), inst.changes(before)), (env, []),
                         f'{RED}: a retired Go-tasks request edited .env or restarted services')
        server = (h.UPDATER / 'server.py').read_text()
        branch = server[server.index("elif self.path == '/go-tasks':"):][:900]
        self.assertTrue('410' in branch and 'retired' in branch,
                        f'{RED}: runner POST /go-tasks must answer 410 retired on an exclusive distribution')


class Phase5Inventory(h.ContractCase, unittest.TestCase):
    def test_no_inventory_entry_keeps_an_owner_planned_for_phase_5(self):
        document = json.loads((ROOT / 'docs/architecture/go-migration/handoff-inventory.json').read_text())
        planned = sorted(entry['id'] for entry in document['entries']
                         if any(owner.get('status') == 'planned' and owner.get('phase') == 5
                                for owner in entry['owner'].values()))
        self.assertEqual(planned, [], f'{RED}: inventory entries still planned for phase 5: {planned}')


if __name__ == '__main__':
    unittest.main()
