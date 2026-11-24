"""Phase 5 part C: adoption of old mixed installations (CONTRACT P5-C v1).

Boundary: the Phase 5 simulated installation (TESTS/handoff/p5_harness.py) started from
the plan's baseline distribution (2284ed46, installed with its own CLI) on a database
with only the historical operational migrations (<= 000004), loaded with the Phase 0
fixtures (TESTS/handoff/fixtures: fresh, django-default, legacy-mixed): legacy env
knobs, the instance row, a pending Go durable job, a queued WhatsApp delivery and
envelopes on both historical queues. The upgrade then replaces the distribution files
(and, for the adoption itself, applies the current operational migrations), as the
updater's distribution step and `initialise_prod` do.

Tool (selfhost/updater/runtime_switch.py, run by the target release's updater image)
  adopt-preflight ROOT ENV   read-only; works on the historical schema (no operation_id
      column, no ledger tables) and with either distribution's files in place. Prints
      one JSON object and exits 0 (adoptable), 3 (blocked) or 1 (error):
        requested_runtime 'django'|'go'  from the persisted instance setting
            (first instance_instanceconfiguration row by id): ui_api_version 'v2' -> 'go',
            'v1' -> 'django'; no table/row/value -> 'django' ("fresh or unset")
        decided_by 'instance_setting'|'default'; instance_setting 'v1'|'v2'|null
        adoption_required bool  (false once an adoption succeeded or the authority was
            committed by an operation)
        legacy {services: sorted running application services (any runtime),
                queues: {'celery': n, 'assozeta-go': n} (lists incl. priority subqueues and
                unacked copies), sql_jobs: pending/running assozeta_go_jobs, whatsapp_outbox:
                queued communications_whatsappdelivery rows, go_task_routing: bool
                (GO_TASK_QUEUE_TASKS non-empty)}
        blockers [{ref, reason}]  what the Phase 3 handoff would block for requested_runtime
            (e.g. unknown_task, no_target_handler); never task arguments or credentials.
      A browser's cached preference (localStorage uiApiVersion, sessionStorage override) is
      client state: it is not an input.
  adopt ROOT ENV   runs the adoption through the same handoff as a switch: a journal
      record kind 'runtime_switch' with adoption true, previous_runtime = the authority's
      runtime; its own preflight first (blocked -> exit 3, nothing stopped, moved or
      written, the legacy release keeps running); then freeze, settle, stage the mixed
      backlog of BOTH historical queues and the Go durable rows, stop every legacy
      application service of either runtime, private target preflight, commit the
      authority only when requested_runtime differs from it (otherwise generation
      unchanged), start exactly the requested set, deliver, verify. Success is recorded
      only when the requested topology runs and the mixed backlog is reconciled; then the
      legacy knobs are retired from .env (ASSOZETA_GO_TASKS, ASSOZETA_REALTIME_RELAY_BACKEND,
      ASSOZETA_DJANGO_RELAY_REPLICAS, ASSOZETA_GO_RELAY_REPLICAS,
      ASSOZETA_BAKNEY_PROTOCOL_BACKEND, ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM absent;
      GO_TASK_QUEUE_TASKS absent or empty). Idempotent: a later adopt is a no-op (exit 0,
      no new record) whatever the instance setting says by then.
CLI: `bin/assozeta upgrade VERSION` runs `runtime_switch.py adopt-preflight` at the
  checking stage, before the backup stops anything, prints
  '@@ASSOZETA_RUNTIME_ADOPTION <json>' and refuses (non-zero, legacy release untouched)
  on exit 3; after initialise_prod it runs `runtime_switch.py adopt` before starting the
  application services. `bin/assozeta runtime-adopt` runs the same adopt under the
  lifecycle lock.

Run: python3 -B -m unittest -v TESTS.handoff.test_p5_adoption
"""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from TESTS.handoff import fixtures, p5_harness as h
from TESTS.handoff.topology import BASELINE_REVISION, BUNDLE, ROOT

RED = 'P5 contract red'
CATALOG = {row['name']: row for row in json.loads((ROOT / 'docs/architecture/go-migration/go-tasks-inventory.json').read_text())}
RETIRED = ('ASSOZETA_GO_TASKS', 'ASSOZETA_REALTIME_RELAY_BACKEND', 'ASSOZETA_DJANGO_RELAY_REPLICAS',
           'ASSOZETA_GO_RELAY_REPLICAS', 'ASSOZETA_BAKNEY_PROTOCOL_BACKEND', 'ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM')


def setUpModule():
    h.services()


def tearDownModule():
    h.stop_services()


def go_handled(name):
    """The Phase 3 target-go rule for a broker task (job_handoff blocks the rest with no_target_handler)."""
    entry = CATALOG.get(name)
    return bool(entry and (entry['consumer'] or entry['job']))


class LegacyCase(h.ContractCase, unittest.TestCase):
    maxDiff = None

    def legacy(self, fixture_id, *, only_go_handled=False, extra=()):
        """A running baseline installation holding the fixture's state."""
        directory = tempfile.TemporaryDirectory(prefix='assozeta-p5c-')
        self.addCleanup(directory.cleanup)
        inst = h.P5Installation(Path(directory.name), revision=BASELINE_REVISION, legacy_database=True)
        inst.install()
        state = fixtures.load(fixture_id)
        knobs = {key: value for key, value in (state.env or {}).items() if key in h.LEGACY_KNOBS}
        if knobs:
            inst.set_env(**knobs)
            inst.cli('restart')
        items = {}
        for row in (state.database or {}).get('instance_instanceconfiguration', []):
            inst.seed_instance(row['ui_api_version'], row['go_tasks_enabled'])
        for row in (state.database or {}).get('assozeta_go_jobs', []):
            inst.go_row(row['kind'], row['id'], None)
            items[row['id']] = ('sql', row['kind'])
        for row in (state.database or {}).get('communications_whatsappdelivery', []):
            inst.sql("INSERT INTO public.communications_whatsappdelivery (id, status, attempts, body, recipient, "
                     f"event_type, next_attempt_at, created_at, updated_at) VALUES ('{row['id']}', '{row['status']}', "
                     f"{row['attempts']}, 'Fixture.', '{row['recipient']}', '{row['event_type']}', clock_timestamp(), "
                     "clock_timestamp(), clock_timestamp());")
        for queue, envelopes in list(state.broker.items()) + list(extra):
            for message in envelopes:
                name = message['headers']['task']
                if only_go_handled and not go_handled(name):
                    continue
                priority = message['properties'].get('priority') or 0
                inst.broker().call('LPUSH', h.priority_key(queue, priority), json.dumps(message).encode())
                items[message['headers']['id']] = (queue, name)
        return inst, state, items

    def replace_distribution(self, inst):
        """The upgrade's distribution step: the current bundle replaces the files, .env is kept."""
        for name in BUNDLE:
            source, target = ROOT / 'selfhost' / name, inst.root / name
            if source.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__'))
            elif source.exists():
                shutil.copy2(source, target)

    def migrate(self, inst):
        """initialise_prod's operational migrations on the historical database."""
        inst.services.apply(inst.database, after=4)

    def world(self, inst):
        return {'running': sorted(inst.running()), 'env': inst.env_text(),
                'queues': {q: inst.queue_ids(q) for q in ('celery', 'assozeta-go')},
                'jobs': inst.rows('SELECT id::text, state FROM public.assozeta_go_jobs ORDER BY id'),
                'whatsapp': inst.rows('SELECT id::text, status, attempts FROM public.communications_whatsappdelivery'),
                'maintenance': inst.maintenance()}


class AdoptionPreflight(LegacyCase):
    def test_preflight_reports_the_requested_target_and_the_mixed_inventory_read_only(self):
        inst, state, items = self.legacy('legacy-mixed')
        running_app = sorted(name for name in inst.running() if name not in h.SHARED)
        before = self.world(inst)
        result, report = inst.tool_json('adopt-preflight', str(inst.root), str(inst.env_file))
        self.assertIsNotNone(report, f'{RED}: adopt-preflight printed no JSON (exit {result.returncode}): '
                                     + (result.stderr or result.stdout)[-600:])
        expected_blockers = {task for task, (queue, name) in items.items() if queue != 'sql' and not go_handled(name)}
        self.assertEqual(result.returncode, 3 if expected_blockers else 0, f'{RED}: adopt-preflight exit code')
        self.assertEqual({key: report.get(key) for key in ('requested_runtime', 'decided_by', 'instance_setting',
                                                           'adoption_required')},
                         {'requested_runtime': 'go', 'decided_by': 'instance_setting', 'instance_setting': 'v2',
                          'adoption_required': True}, f'{RED}: legacy-mixed (uiApiVersion v2) must request Go')
        legacy = report.get('legacy') or {}
        self.assertEqual(legacy, {'services': running_app, 'queues': {'celery': len(state.broker['celery']),
                                                                      'assozeta-go': len(state.broker['assozeta-go'])},
                                  'sql_jobs': 1, 'whatsapp_outbox': 1, 'go_task_routing': True},
                         f'{RED}: inventory of the mixed installation')
        self.assertEqual({b.get('ref') for b in report.get('blockers', [])}, expected_blockers,
                         f'{RED}: adopt-preflight blockers must be what the handoff would block for Go')
        self.assertEqual(self.world(inst), before, f'{RED}: adopt-preflight changed the installation')
        published = json.dumps(report)
        for forbidden in ('Fixture reminder', 'recipient@example.invalid', 'fixture-redis', inst.services.redis_password):
            self.assertNotIn(forbidden, published, f'{RED}: adopt-preflight leaks job data or credentials')

    def test_unset_or_v1_selects_django_whatever_a_cached_browser_prefers(self):
        cases = (('fresh', None, 'default', None), ('django-default', None, 'instance_setting', 'v1'),
                 ('legacy-mixed', 'v1', 'instance_setting', 'v1'))
        for fixture_id, override, decided_by, setting in cases:
            with self.subTest(fixture=fixture_id):
                inst, state, _ = self.legacy(fixture_id, only_go_handled=True)
                if override:
                    inst.sql(f"UPDATE public.instance_instanceconfiguration SET ui_api_version='{override}';")
                browsers = {b['name']: b['localStorage'].get('assozeta_instance_config', '') for b in state.browser}
                result, report = inst.tool_json('adopt-preflight', str(inst.root), str(inst.env_file))
                self.assertIsNotNone(report, f'{RED}: adopt-preflight printed no JSON for {fixture_id}: '
                                             + (result.stderr or result.stdout)[-400:])
                self.assertEqual((report.get('requested_runtime'), report.get('decided_by'),
                                  report.get('instance_setting')), ('django', decided_by, setting),
                                 f'{RED}: {fixture_id} must request Django (browsers {sorted(browsers)} are client state)')


class Adoption(LegacyCase):
    def adopt(self, inst):
        result, report = inst.tool_json('adopt', str(inst.root), str(inst.env_file))
        return result, report

    def adopted(self, inst, label):
        records = [r for r in inst.runtime_records() if r.get('adoption')]
        self.assertEqual(len(records), 1, f'{RED}: {label}: expected one adoption record, got {len(records)}')
        return records[0]

    def test_legacy_mixed_install_is_adopted_into_go_through_the_handoff(self):
        inst, _, items = self.legacy('legacy-mixed', only_go_handled=True)
        self.replace_distribution(inst)
        self.migrate(inst)
        result, _ = self.adopt(inst)
        self.assertEqual(result.returncode, 0, f'{RED}: adopt of legacy-mixed exited {result.returncode}: '
                                               + (result.stderr or result.stdout)[-800:])
        record = self.adopted(inst, 'legacy-mixed')
        self.assertEqual((record['status'], record['outcome'], record['target'], record['previous_runtime']),
                         ('succeeded', 'committed', 'go', 'django'), f'{RED}: the adoption record')
        self.assertEqual(inst.authority(), ('go', 2, record['id']), f'{RED}: adoption into Go commits the authority once')
        inst.assert_mode(self, 'go', 'the adoption into Go')
        self.assertFalse(inst.maintenance(), f'{RED}: maintenance left after the adoption')
        for task_id, (queue, name) in items.items():
            row = inst.ledger(task_id)
            self.assertEqual((row or {}).get('owner_runtime'), 'go', f'{RED}: {name} {task_id} not owned by Go')
            copies = inst.copies(task_id)
            self.assertFalse(any(key.startswith('celery') for key in copies),
                             f'{RED}: {name} {task_id} left on the Django queue after adoption into Go')
            job = inst.go_job(task_id)
            self.assertEqual(sum(copies.values()) + (1 if (job or {}).get('state') == 'pending' else 0), 1,
                             f'{RED}: {name} {task_id} must have exactly one runnable Go representation')
        self.assertEqual(inst.rows("SELECT status, attempts FROM public.communications_whatsappdelivery"),
                         [{'status': 'queued', 'attempts': 0}], f'{RED}: the queued WhatsApp row must be kept as is')
        env = dict(line.split('=', 1) for line in inst.env_text().splitlines() if '=' in line and not line.startswith('#'))
        self.assertEqual(sorted(key for key in RETIRED if key in env), [], f'{RED}: legacy knobs not retired')
        self.assertEqual(env.get('GO_TASK_QUEUE_TASKS', ''), '', f'{RED}: GO_TASK_QUEUE_TASKS not retired')
        # Idempotent, and the instance setting is not consulted again.
        inst.sql("UPDATE public.instance_instanceconfiguration SET ui_api_version='v1';")
        again, _ = self.adopt(inst)
        self.assertEqual((again.returncode, len(inst.runtime_records()), inst.authority()[:2]), (0, 1, ('go', 2)),
                         f'{RED}: a second adopt must be a no-op whatever the instance setting says')
        inst.assert_mode(self, 'go', 'a second adopt')

    def test_default_dual_install_is_adopted_into_django_without_an_authority_change(self):
        stray = h.envelope(h.MAIL_TASK, 'c0c0c0c0-0000-4000-8000-00000000000c', args=h.mail_args(9), queue='assozeta-go')
        inst, _, items = self.legacy('django-default', extra=[('assozeta-go', [json.loads(stray)])])
        self.assertIn('api-go', inst.running(), 'the baseline dual distribution runs api-go (positive control)')
        self.replace_distribution(inst)
        self.migrate(inst)
        result, _ = self.adopt(inst)
        self.assertEqual(result.returncode, 0, f'{RED}: adopt of django-default exited {result.returncode}: '
                                               + (result.stderr or result.stdout)[-800:])
        record = self.adopted(inst, 'django-default')
        self.assertEqual((record['status'], record['target']), ('succeeded', 'django'), f'{RED}: the adoption record')
        self.assertEqual(inst.authority(), ('django', 1, None), f'{RED}: adoption into Django changes no authority')
        inst.assert_mode(self, 'django', 'the adoption into Django')
        for task_id, (queue, name) in items.items():
            if queue == 'sql':
                continue
            copies = inst.copies(task_id)
            self.assertEqual(sum(copies.values()), 1, f'{RED}: {name} {task_id} copies after adoption {copies}')
            self.assertFalse(any(key.startswith('assozeta-go') for key in copies),
                             f'{RED}: {name} {task_id} left on the stopped Go queue')

    def test_blocked_adoption_keeps_the_legacy_release_running(self):
        unknown = h.envelope('fixture.p5.unknown_task', 'b10cb10c-0000-4000-8000-00000000000b', args=('P5-ARG',))
        inst, _, _ = self.legacy('legacy-mixed', extra=[('celery', [json.loads(unknown)])])
        before = self.world(inst)
        authority = inst.authority()
        result, report = self.adopt(inst)
        self.assertEqual(result.returncode, 3, f'{RED}: a blocked adoption must exit 3 before changing anything: '
                                               + (result.stderr or result.stdout)[-600:])
        self.assertEqual((self.world(inst), inst.authority()), (before, authority),
                         f'{RED}: a blocked adoption changed the legacy installation')
        self.assertIn('b10cb10c-0000-4000-8000-00000000000b', json.dumps(report), f'{RED}: the blocker is not reported')


class UpgradeWiring(LegacyCase):
    """bin/assozeta upgrade_prod with the heavy lifecycle steps stubbed; each stub appends to a step log."""
    STUBS = r"""
step() { printf '%s\n' "$*" >> "$P5_STEPS"; }
backup_prod() { step backup; archive=; }
check_prod_manual() { :; }
initialise_prod() { step initialise; }
start_prod_app_services() { step start; }
status_prod() { :; }
backup_mark_upgrade_completed() { :; }
backup_prune() { :; }
run_updater_tool() {
    step "tool $*"
    case "$*" in
        *adopt-preflight*)
            if [ -f "$P5_BLOCKED" ]; then
                printf '%s\n' '{"requested_runtime": "go", "blockers": [{"ref": "b10cb10c", "reason": "unknown_task"}]}'
                return 3
            fi
            printf '%s\n' '{"requested_runtime": "go", "blockers": []}' ;;
    esac
}
"""

    def upgrade(self, inst, blocked):
        marker, log = inst.root / '.p5-blocked', inst.root / '.p5-steps'
        if blocked:
            marker.write_text('')
        result = inst.shell(f'P5_BLOCKED={marker}\nP5_STEPS={log}\n' + self.STUBS + '\nupgrade_prod 9.9.9\n')
        return result, (log.read_text().splitlines() if log.exists() else [])

    def test_upgrade_runs_the_adoption_preflight_before_the_backup_and_refuses_when_blocked(self):
        inst, _, _ = self.legacy('legacy-mixed')
        self.replace_distribution(inst)
        result, steps = self.upgrade(inst, blocked=True)
        self.assertNotEqual(result.returncode, 0, f'{RED}: an upgrade with a blocked adoption preflight must refuse')
        self.assertTrue(any('adopt-preflight' in step for step in steps), f'{RED}: no adoption preflight: {steps}')
        self.assertFalse(any(step in ('backup', 'initialise', 'start') for step in steps),
                         f'{RED}: the upgrade stopped or changed the legacy release after a blocked preflight: {steps}')

    def test_upgrade_adopts_after_the_migrations_and_before_the_application_starts(self):
        inst, _, _ = self.legacy('legacy-mixed')
        self.replace_distribution(inst)
        result, steps = self.upgrade(inst, blocked=False)
        self.assertEqual(result.returncode, 0, f'{RED}: upgrade exited {result.returncode}: '
                                               + (result.stderr or result.stdout)[-600:])

        def position(predicate, label):
            found = [index for index, step in enumerate(steps) if predicate(step)]
            self.assertTrue(found, f'{RED}: upgrade never ran {label}: {steps}')
            return found[0]
        preflight = position(lambda s: s.startswith('tool ') and 'adopt-preflight' in s, 'the adoption preflight')
        backup = position(lambda s: s == 'backup', 'the backup')
        initialise = position(lambda s: s == 'initialise', 'the migrations')
        adopt = position(lambda s: s.startswith('tool ') and ' adopt ' in s, 'the adoption')
        start = position(lambda s: s == 'start', 'the application start')
        self.assertTrue(preflight < backup < initialise < adopt < start,
                        f'{RED}: upgrade order must be adoption preflight, backup, migrations, adopt, start: {steps}')
        reports = [line for line in result.stdout.splitlines() if line.startswith('@@ASSOZETA_RUNTIME_ADOPTION ')]
        self.assertTrue(reports and json.loads(reports[0].split(' ', 1)[1]).get('requested_runtime') == 'go',
                        f'{RED}: the upgrade must show the requested runtime (@@ASSOZETA_RUNTIME_ADOPTION <json>)')

if __name__ == '__main__':
    unittest.main()
