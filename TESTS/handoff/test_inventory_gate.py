"""Phase 0 handoff inventory: contract and gate tests.

CONTRACT (t-p0; implementation by i-p0)

Files
  docs/architecture/go-migration/handoff-inventory.json   checked-in inventory (data)
  tools/go-migration/handoff_inventory.py                discovery + gate (stdlib only:
                                                          no Docker, Go toolchain, Django or network)
  TESTS/handoff/fixtures/manifest.json                   fixture ids (see test_fixture_contract.py)

Command line
  python3 -B tools/go-migration/handoff_inventory.py discover [--root DIR]
      stdout: JSON {kind: {key: {"sources": ["path:line", ...]}}}, with at least the
      gated kinds route, task, schedule, service, publisher.
  python3 -B tools/go-migration/handoff_inventory.py check [--root DIR] [--inventory FILE] [--fixtures FILE]
      exit 0 (JSON summary on stdout) when every discovered item of a gated kind is
      classified exactly once and every entry is valid; otherwise exit 1 with one
      problem per stderr line: "<code> <kind> <key or entry id>[: detail]",
      code in {unclassified, stale, duplicate, invalid}. Defaults are relative to
      --root (repo root): the inventory and fixture manifest paths above.
  CI gate: python3 -B -m TESTS.handoff.run_expected_red (runs this module).

Document
  {"format": 1,
   "baseline": {"revision": <40-hex commit>, "images": {"backend", "backend-go", "web", "renderer",
                "updater": <image ref>}, "upgrade_from": [<version>...], "rollback_to": [<version>...],
                "controllers": {<upgrade step>: <controller that drives it>}},
   "entries": [Entry, ...],
   "ledger_reconciliation": [{"ledger_id", "status", "finding": str, "executable_gap": bool,
                              "evidence_gap": bool}],   one per open ledger row (status not verified/deprecated)
   "django_handoff": [{"shape": str, "route": str, "evidence": str}]}   supported HTTP shapes that
                                                          still rely on the Go API's Django handoff
  Entry = {"id": "<kind>:<slug>" (unique), "kind", "items": [discovered key, ...] (nonempty),
           "fixture": fixture id or list of ids (in the manifest), "effect": str,
           "owner": {"django": Owner, "go": Owner}, "refs": [str] (optional), "notes": [str] (optional),
           "task": TaskFamily (required for kind task)}
    gated kinds: route, task, schedule, service, publisher (items must be discovered keys);
    other kinds: internal_caller, side_effect, schema_step, release_artifact, rollback_source, client
    (free keys; each kind needs at least one entry in the real inventory).
  Owner = {"runtime": "django"|"go"|"shared"|"none", "component": str (unless none),
           "status": "present"|"planned", "phase": 1..7 (when planned),
           "disposition": str (when none: stopped, retired, refused, not-applicable or test-only, and why)}
    The Django-mode owner is never "go"; the Go-mode owner is never "django".
  TaskFamily = every field of TASK_FAMILY_FIELDS, non-empty (task-coverage.md).
  Strings must not be placeholders (TODO, TBD, unknown, ?, -, n/a).

Discovered keys (exact formats for the real inventory)
  route     django:<TESTS.inventory.catalog id> for catalog kinds route, mount, websocket;
            go:<METHOD> <path> for every echo registration in non-test Go under BE_GO/cmd/server
            and BE_GO/internal/handler, wherever it is made (main.go, helper functions such as
            registerCommunicationDelivery, handler Register(e *echo.Echo, ...) methods, literal
            loops, router-stock expansion; groups resolved; METHOD ANY for Any);
            go:WS <path> for each WebSocket path dispatched in BE_GO/internal/handler/websocket.go
            (case "/ws/..."); caddy:<path> for each handle/handle_path path in selfhost/caddy/Caddyfile
  task      the Celery name, as go_tasks_inventory.django_tasks() finds it, plus celery.backend_cleanup
  schedule  beat:<entry> (BE/core/celery.py app.conf.beat_schedule, BE/core/settings.py
            CELERY_BEAT_SCHEDULE), beat:celery.backend_cleanup, go-periodic:<task name>
            (periodicJobKinds in BE_GO/internal/domain/schedule.go, constants resolved),
            go-loop:<file>:<func> (non-test Go calling time.NewTicker/Tick/AfterFunc),
            django-mutation:<catalog id> (catalog schedule_mutation)
  service   compose:<service> (selfhost/compose.yml, all profiles), compose-updater:<service>,
            go-cmd:<dir> (BE_GO/cmd/<dir>/main.go), cli-run:<service> (run one-shots in
            selfhost/bin/assozeta)
  publisher py:<file>:<qualname>:<callee> for .delay/.apply_async/.send_task/.retry in non-test BE;
            go:<file>:<func>:<callee> for .Publish(/.Enqueue( in non-test BE_GO; repeated
            identical keys get #2, #3
"""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from TESTS.handoff import synthetic

ROOT = Path(__file__).resolve().parents[2]
# Overridable only so the test author can run these tests against a deliberately broken tool.
TOOL = Path(os.environ.get('HANDOFF_INVENTORY_TOOL') or ROOT / 'tools/go-migration/handoff_inventory.py')
INVENTORY = ROOT / 'docs/architecture/go-migration/handoff-inventory.json'
GATED = ('route', 'task', 'schedule', 'service', 'publisher')
OTHER = ('internal_caller', 'side_effect', 'schema_step', 'release_artifact', 'rollback_source', 'client')
TASK_FAMILY_FIELDS = ('producers', 'children', 'handler', 'payload_versions', 'tenant_scope', 'priority',
                      'eta_expiry', 'retry_ack', 'result', 'effect_identity', 'mounts_network',
                      'schedule_owner', 'reverse_mapping', 'legacy_fixture')
FIXTURE_IDS = ('fresh', 'django-default', 'legacy-mixed')


def control_owner(kind):
    return {'django': {'runtime': 'django', 'component': 'api', 'status': 'present'},
            'go': {'runtime': 'go', 'component': 'api-go', 'status': 'planned', 'phase': 2}}


def control_inventory(discovered):
    entries = []
    for kind in GATED:
        entry = {'id': f'{kind}:synthetic', 'kind': kind, 'items': sorted(discovered[kind]),
                 'fixture': 'fresh', 'effect': 'Synthetic control: the item is served or executed by its owner.',
                 'owner': control_owner(kind)}
        if kind == 'task':
            entry['task'] = {field: f'synthetic {field}' for field in TASK_FAMILY_FIELDS}
            entry['task']['handler'] = {'django': 'probe.tasks', 'go': 'planned (phase 4)'}
            entry['task']['producers'] = ['publisher:synthetic']
        entries.append(entry)
    return {'format': 1,
            'baseline': {'revision': '0' * 40, 'images': {}, 'upgrade_from': [], 'rollback_to': [],
                         'controllers': {}},
            'entries': entries, 'ledger_reconciliation': [], 'django_handoff': []}


class ToolCase(unittest.TestCase):
    def tool(self, *arguments, root=None):
        if not TOOL.is_file():
            self.fail(f'handoff inventory gate not implemented: {TOOL} is missing')
        result = subprocess.run([sys.executable, '-B', str(TOOL), *arguments,
                                 *(['--root', str(root)] if root else [])],
                                capture_output=True, text=True, timeout=300, cwd=ROOT,
                                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        return result

    def problems(self, result):
        return [line.strip() for line in result.stderr.splitlines() if line.strip()]

    def assertProblem(self, result, code, kind, names):
        problems = self.problems(result)
        self.assertEqual(result.returncode, 1, f'gate did not fail (exit {result.returncode}); stderr: {problems}')
        matching = [line for line in problems if line.split()[:2] == [code, kind]
                    and any(name in line for name in names)]
        self.assertTrue(matching, f'no "{code} {kind}" problem naming {names}; stderr: {problems[:20]}')


class SyntheticGate(ToolCase):
    """The gate on a small synthetic source tree (synthetic.py)."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='assozeta-handoff-gate-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def tree(self, name='tree'):
        return synthetic.build(self.directory / name)

    def discover(self, root):
        result = self.tool('discover', root=root)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        discovered = json.loads(result.stdout)
        for kind in GATED:
            self.assertIsInstance(discovered.get(kind), dict, f'discover output lacks kind {kind}')
        return discovered

    def write(self, document, name='inventory.json'):
        path = self.directory / name
        path.write_text(json.dumps(document, indent=2))
        return path

    def fixtures(self):
        return self.write({'format': 1, 'fixtures': {name: {} for name in FIXTURE_IDS}}, 'fixtures.json')

    def check(self, root, document):
        return self.tool('check', '--inventory', str(self.write(document)), '--fixtures', str(self.fixtures()),
                         root=root)

    def classified(self):
        root = self.tree()
        document = control_inventory(self.discover(root))
        return root, document

    def test_discovery_reports_every_gated_kind_from_each_source(self):
        discovered = self.discover(self.tree())
        for kind, names in synthetic.BASELINE.items():
            keys = list(discovered[kind])
            for name in names:
                with self.subTest(kind=kind, name=name):
                    self.assertTrue(any(name in key for key in keys), f'{kind} {name!r} not discovered in {keys}')
        for kind, names in synthetic.EXCLUDED.items():
            for name in names:
                self.assertFalse(any(name in key for key in discovered[kind]), f'test source discovered: {name}')

    def test_fully_classified_tree_passes(self):
        root, document = self.classified()
        result = self.check(root, document)
        self.assertEqual(result.returncode, 0, self.problems(result)[:20])

    def unclassified(self, kind):
        root, document = self.classified()
        sources = [(source, change) for (gated, source), change in synthetic.ADDITIONS.items() if gated == kind]
        self.assertTrue(sources)
        for source, (mutate, names) in sources:
            with self.subTest(kind=kind, source=source):
                changed = self.directory / f'changed-{source}'
                shutil.copytree(root, changed)
                mutate(changed)
                self.assertProblem(self.check(changed, document), 'unclassified', kind, names)

    def test_new_unclassified_route_fails(self):
        self.unclassified('route')

    def test_new_unclassified_task_fails(self):
        self.unclassified('task')

    def test_new_unclassified_schedule_fails(self):
        self.unclassified('schedule')

    def test_new_unclassified_lifecycle_service_fails(self):
        self.unclassified('service')

    def test_new_unclassified_publisher_fails(self):
        self.unclassified('publisher')

    def test_removed_source_item_is_stale(self):
        root, document = self.classified()
        document['entries'][0]['items'].append('django:route:BE/core/urls.py:handoff-removed/ -> views.base')
        self.assertProblem(self.check(root, document), 'stale', 'route', ('handoff-removed',))

    def test_item_classified_twice_is_rejected(self):
        root, document = self.classified()
        twice = dict(document['entries'][0], id='route:second', items=document['entries'][0]['items'][:1])
        document['entries'].append(twice)
        self.assertProblem(self.check(root, document), 'duplicate', 'route', (twice['items'][0],))

    def test_entry_without_fixture_effect_or_owner_per_mode_is_rejected(self):
        def drop(*path):
            def apply(entry):
                target = entry
                for key in path[:-1]:
                    target = target[key]
                del target[path[-1]]
            return apply

        def put(value, *path):
            def apply(entry):
                target = entry
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
            return apply

        mutations = {
            'missing fixture': drop('fixture'),
            'unknown fixture': put('no-such-fixture', 'fixture'),
            'missing effect': drop('effect'),
            'empty effect': put('', 'effect'),
            'placeholder effect': put('TODO', 'effect'),
            'missing owner': drop('owner'),
            'missing Django-mode owner': drop('owner', 'django'),
            'missing Go-mode owner': drop('owner', 'go'),
            'owner without runtime': drop('owner', 'go', 'runtime'),
            'owner without component': drop('owner', 'django', 'component'),
            'Go owns a Django-mode item': put({'runtime': 'go', 'component': 'api-go', 'status': 'present'},
                                              'owner', 'django'),
            'Django owns a Go-mode item': put({'runtime': 'django', 'component': 'worker', 'status': 'present'},
                                              'owner', 'go'),
            'no owner without disposition': put({'runtime': 'none', 'status': 'present'}, 'owner', 'go'),
            'planned without phase': put({'runtime': 'go', 'component': 'api-go', 'status': 'planned'},
                                         'owner', 'go'),
            'unknown owner status': put('maybe', 'owner', 'django', 'status'),
            'empty items': put([], 'items'),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                root, document = self.classified()
                entry = next(entry for entry in document['entries'] if entry['kind'] == 'schedule')
                mutate(entry)
                self.assertProblem(self.check(root, document), 'invalid', 'schedule', (entry['id'],))

    def test_task_family_fields_are_required(self):
        for field in TASK_FAMILY_FIELDS:
            with self.subTest(field=field):
                root, document = self.classified()
                entry = next(entry for entry in document['entries'] if entry['kind'] == 'task')
                del entry['task'][field]
                self.assertProblem(self.check(root, document), 'invalid', 'task', (entry['id'],))
        root, document = self.classified()
        entry = next(entry for entry in document['entries'] if entry['kind'] == 'task')
        del entry['task']
        self.assertProblem(self.check(root, document), 'invalid', 'task', (entry['id'],))

    def test_unknown_kind_and_duplicate_entry_id_are_rejected(self):
        root, document = self.classified()
        document['entries'].append(dict(document['entries'][0], kind='mystery', id='mystery:one', items=['x']))
        self.assertProblem(self.check(root, document), 'invalid', 'mystery', ('mystery:one',))
        root, document = self.classified()
        document['entries'].append({**document['entries'][-1], 'items': ['client:duplicate-id-probe'],
                                    'kind': 'client'})
        result = self.check(root, document)
        self.assertEqual(result.returncode, 1, self.problems(result))
        self.assertTrue(any(line.startswith('invalid') and document['entries'][-1]['id'] in line
                            for line in self.problems(result)), self.problems(result))


class CheckedInInventory(ToolCase):
    """The real checkout: red until i-p0 builds discovery and fills the inventory."""

    def load(self):
        if not INVENTORY.is_file():
            self.fail(f'handoff inventory missing: {INVENTORY.relative_to(ROOT)}')
        return json.loads(INVENTORY.read_text())

    def classified_keys(self, document, kind):
        return {item for entry in document['entries'] if entry['kind'] == kind for item in entry['items']}

    def test_checked_in_inventory_matches_this_checkout(self):
        result = self.tool('check')
        self.assertEqual(result.returncode, 0, 'handoff inventory gate failed:\n' + '\n'.join(self.problems(result)[:40]))

    def test_baseline_pins_revision_images_versions_and_controllers(self):
        baseline = self.load()['baseline']
        revision = baseline['revision']
        self.assertRegex(revision, r'^[0-9a-f]{40}$')
        exists = subprocess.run(['git', '-C', str(ROOT), 'cat-file', '-e', revision + '^{commit}'],
                                capture_output=True)
        self.assertEqual(exists.returncode, 0, f'baseline revision {revision} is not a commit in this clone')
        self.assertTrue({'backend', 'backend-go', 'web', 'renderer', 'updater'} <= set(baseline['images']))
        self.assertTrue(all(isinstance(ref, str) and ref for ref in baseline['images'].values()))
        self.assertTrue(baseline['upgrade_from'] and baseline['rollback_to'])
        self.assertTrue(baseline['controllers'] and all(baseline['controllers'].values()))

    def test_every_inventory_category_is_present(self):
        kinds = {entry['kind'] for entry in self.load()['entries']}
        self.assertEqual(sorted(set(GATED + OTHER) - kinds), [])

    def test_open_ledger_rows_are_reconciled_with_separate_gap_counts(self):
        ledger = json.loads((ROOT / 'docs/architecture/go-migration/ledger.json').read_text())
        open_rows = {entry['id']: entry['status'] for entry in ledger['entries']
                     if entry['status'] not in ('verified', 'deprecated')}
        rows = {row['ledger_id']: row for row in self.load()['ledger_reconciliation']}
        self.assertEqual(sorted(set(open_rows) - set(rows)), [], 'open ledger rows without reconciliation')
        self.assertEqual(sorted(set(rows) - set(open_rows)), [], 'reconciliation rows that are not open')
        for identifier, row in rows.items():
            with self.subTest(identifier):
                self.assertEqual(row['status'], open_rows[identifier])
                self.assertTrue(isinstance(row['finding'], str) and len(row['finding']) > 10)
                self.assertIsInstance(row['executable_gap'], bool)
                self.assertIsInstance(row['evidence_gap'], bool)

    def test_known_sources_are_discovered_and_classified(self):
        """Discovery completeness anchors from the plan (task-coverage.md, README.md)."""
        document = self.load()
        expected = {
            'schedule': ['beat:notify-backup-failures', 'beat:celery.backend_cleanup', 'beat:deliver-whatsapp',
                         'go-loop:BE_GO/internal/whatsapp/delivery.go:'],
            'task': ['instance.backups.notify_failures', 'communications.tasks.deliver_whatsapp',
                     'auto_move_users_to_base_plan', 'nurturing_warm_leads', 'save_binary_to_storage',
                     'send_updated_certificate_email', 'fixture.early_ack', 'renew_memberships_payments'],
            'service': ['compose:api', 'compose:worker', 'compose:beat', 'compose:realtime-relay',
                        'compose:api-go', 'compose:worker-go', 'compose:realtime-relay-go', 'compose:migrate',
                        'compose:migrate-go', 'compose:web', 'go-cmd:scheduler', 'go-cmd:worker',
                        'go-cmd:celery-consumer', 'go-cmd:relay-go-realtime', 'go-cmd:server'],
            'route': ['caddy:/api/v2/*', 'caddy:/api/*', 'caddy:/ws/*', 'caddy:/bakney/v1/*', 'go:ANY /*',
                      'go:GET /communications/whatsapp/webhook', 'go:ANY /communications/send/email',
                      'go:ANY /communications/settings/message-templates', 'go:WS /ws/notifications/',
                      'go:WS /ws/agent/'],
            'publisher': ['go:BE_GO/internal/service/tasks_db_d.go:', 'py:BE/application/signals.py:',
                          'py:BE/docmanager/views/printing_views.py:'],
        }
        for kind, keys in expected.items():
            classified = self.classified_keys(document, kind)
            for key in keys:
                with self.subTest(kind=kind, key=key):
                    self.assertTrue(key in classified or (key.endswith(':') and any(
                        item.startswith(key) for item in classified)), f'{kind} {key} not classified')

    def test_target_owners_make_each_mode_exclusive(self):
        entries = {item: entry for entry in self.load()['entries'] if entry['kind'] == 'service'
                   for item in entry['items']}
        for service in ('compose:api', 'compose:worker', 'compose:beat', 'compose:realtime-relay'):
            with self.subTest(service):
                self.assertEqual(entries[service]['owner']['go']['runtime'], 'none')
                self.assertEqual(entries[service]['owner']['django']['runtime'], 'django')
        for service in ('compose:api-go', 'compose:worker-go', 'compose:realtime-relay-go'):
            with self.subTest(service):
                self.assertEqual(entries[service]['owner']['django']['runtime'], 'none')
                self.assertEqual(entries[service]['owner']['go']['runtime'], 'go')
        for service in ('compose:postgres', 'compose:redis', 'compose:minio', 'compose:renderer', 'compose:web'):
            with self.subTest(service):
                self.assertEqual({entries[service]['owner'][mode]['runtime'] for mode in ('django', 'go')},
                                 {'shared'})

    def entries_with(self, document, predicate):
        return [entry for entry in document['entries'] if any(predicate(item) for item in entry['items'])]

    def test_present_owners_are_true_for_the_current_distribution(self):
        """status "present" means the named owner does this in that mode on this revision.

        Each rule is anchored to source that the later phase changes; once that source
        changes, the rule stops applying and the inventory may say "present".
        """
        document = self.load()
        compose = (ROOT / 'selfhost/compose.yml').read_text()
        cli = (ROOT / 'selfhost/bin/assozeta').read_text()
        problems = []

        def require_planned(entries, mode, why):
            for entry in entries:
                if entry['owner'][mode]['status'] != 'planned':
                    problems.append(f"{entry['id']}: owner.{mode} is 'present' but {why}")

        # The stock Compose file has no native Go scheduler or durable worker service.
        if not any(f'/app/{binary}' in compose for binary in ('scheduler', 'worker"')):
            native = {row['name'] for row in json.loads(
                (ROOT / 'docs/architecture/go-migration/go-tasks-inventory.json').read_text())
                if row['job'] and not row['consumer']}
            require_planned(self.entries_with(document, lambda item: item.startswith('go-periodic:')),
                            'go', 'no Go scheduler service runs in the stock distribution (Phase 2 adds it)')
            require_planned([entry for entry in document['entries'] if entry['kind'] == 'task'
                             and set(entry['items']) & native], 'go',
                            'no Go durable worker service runs in the stock distribution')
            require_planned(self.entries_with(document, lambda item: item.startswith(
                ('go:BE_GO/internal/service/occurrence.go:', 'go:BE_GO/internal/repository/occurrence.go:',
                 'go-loop:BE_GO/internal/service/job_executor.go:'))), 'go',
                'its Go process (scheduler or durable worker) is not deployed')
        # The Go consumer hands unported inputs back to the Python celery queue.
        consumer = (ROOT / 'BE_GO/cmd/celery-consumer/main.go').read_text()
        if 'HandoffQueue: celery.DefaultQueue' in consumer:
            registered = {row['name'] for row in json.loads(
                (ROOT / 'docs/architecture/go-migration/go-tasks-inventory.json').read_text()) if row['consumer']}
            require_planned([entry for entry in document['entries'] if entry['kind'] == 'task'
                             and set(entry['items']) & registered], 'go',
                            'worker-go hands unported inputs back to the Python celery queue (Phase 4)')
        # Go producers fall back to the Python celery queue unless GO_TASK_QUEUE_TASKS routes the name.
        if 'return DefaultQueue' in (ROOT / 'BE_GO/internal/celery/routes.go').read_text():
            def go_broker_publish(item):
                return (item.startswith('go:BE_GO/') and re.search(r'\.Publish(#\d+)?$', item) is not None
                        and 'internal/celery/pidbox.go' not in item and 'internal/service/occurrence.go' not in item)
            require_planned(self.entries_with(document, go_broker_publish), 'go',
                            'Go producers publish to the celery queue only a Django worker consumes (Phase 3 routing)')
        # The stock CLI and Compose start api-go, the WhatsApp loop inside it, worker and Beat in every mode.
        if 'api-go:\n        condition: service_healthy' in compose or 'set -- "$@" "$go_api_service"' in cli:
            require_planned(self.entries_with(document, lambda item: item in ('compose:api-go', 'go-cmd:server')),
                            'django', 'the stock distribution starts api-go in Django mode (RED1)')
            require_planned(self.entries_with(document, lambda item: item ==
                                              'go-loop:BE_GO/internal/whatsapp/delivery.go:DeliveryQueue.Run'),
                            'django', 'api-go runs this WhatsApp loop next to Beat deliver_whatsapp in Django mode')
        if 'up -d worker beat' in cli or 'up -d --force-recreate worker beat' in cli:
            require_planned(self.entries_with(document, lambda item: item in ('compose:worker', 'compose:beat')),
                            'go', 'the stock CLI starts worker and beat whatever the mode')
        if 'api:\n        condition: service_healthy' in compose:
            require_planned(self.entries_with(document, lambda item: item == 'compose:api'),
                            'go', 'web depends on api, so api cannot be stopped')
        self.assertEqual(problems, [])


if __name__ == '__main__':
    unittest.main()
