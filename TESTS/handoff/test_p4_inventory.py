"""Phase 4, part D: machine-checked gates (static; no services, no toolchain).

CONTRACT P4-D.
1. Phase0Inventory: at Phase 4 exit no entry of docs/architecture/go-migration/handoff-inventory.json
   keeps an owner planned for phase 4 (flip each to present, in the change that lands it).
2. TaskMatrixCoverage: every task name of docs/architecture/go-migration/go-tasks-inventory.json
   belongs to exactly one family of TESTS/handoff/p4_matrix.py, and every family that is not
   covered elsewhere has its test class in TESTS/handoff/test_p4_task_matrix.py.
3. Dispositions: docs/architecture/go-migration/task-handoff-dispositions.json (written by the
   implementers, family by family) records what Phase 0's TaskFamily fields do not carry:
   {"format": 1,
    "handback_sites": {"<BE_GO path>:<func>#<n>": {"disposition": "unsupported"|"unreachable",
        "outcome": "terminal:<celery state>"|"failure:<exception class>"|"blocked:<reason>",
        "evidence": "<TESTS.handoff test id>", "sites": [<handoff-inventory django_handoff_evidence site ids>]}},
    "handback_reasons": {"undecodable"|"unregistered"|"options"|"input": {"outcome": ..., "evidence": ...}},
    "legacy_names": {"<task>": {"disposition": "adapter"|"retired"|"refuse_transition", "outcome": "<text>",
        "evidence": "<test id>"}},
    "tasks": {"<task>": {"family": "<p4_matrix id>", "rate_limit": "<Django annotation>"|null,
        "go_rate_limit": "<mechanism>"|null, "time_limits": [soft|null, hard|null],
        "go_time_limits": "<mechanism>"|null}}}
   Keys "<path>:<func>#<n>" are discovered from source: the n-th line referencing ErrHandoff inside
   func, in non-test Go files of BE_GO/internal/service, BE_GO/cmd/celery-consumer and
   BE_GO/internal/celeryworker (declaration and comment lines excluded). A ported site disappears
   from source and from the record; every remaining site is classified, and every consumer
   hand-back site of the Phase 0 evidence (categories go_consumer_plan_handback,
   go_consumer_handback_to_python_queue, go_api_inprocess_planner_handback) is traced by a record.
   rate_limit must equal BE/core/celery.py task_annotations; a non-null Django limit needs a Go mechanism.
Command: python3 -B -m unittest -v TESTS.handoff.test_p4_inventory
"""
import ast
import json
from pathlib import Path
import re
import unittest

from TESTS.handoff.p4_matrix import FAMILIES, family_of

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / 'docs/architecture/go-migration/handoff-inventory.json'
TASKS_INVENTORY = ROOT / 'docs/architecture/go-migration/go-tasks-inventory.json'
DISPOSITIONS = ROOT / 'docs/architecture/go-migration/task-handoff-dispositions.json'
SCAN = ('BE_GO/internal/service', 'BE_GO/cmd/celery-consumer', 'BE_GO/internal/celeryworker')
CONSUMER_CATEGORIES = ('go_consumer_plan_handback', 'go_consumer_handback_to_python_queue', 'go_api_inprocess_planner_handback')
OUTCOME = re.compile(r'^(terminal:[A-Z]+|failure:[A-Za-z_.]+|blocked:[a-z_]+)$')
FUNC = re.compile(r'^func (?:\([^)]*\) )?([A-Za-z0-9_]+)')


def handback_sites():
    """{'<path>:<func>#<n>': 'path:line'} for every ErrHandoff reference in the scanned Go sources."""
    found = {}
    for directory in SCAN:
        for path in sorted((ROOT / directory).glob('*.go')):
            if path.name.endswith('_test.go'):
                continue
            function, counts = '<file>', {}
            for number, line in enumerate(path.read_text().splitlines(), 1):
                match = FUNC.match(line)
                if match:
                    function = match[1]
                stripped = line.strip()
                if 'ErrHandoff' not in line or stripped.startswith('//') or stripped.startswith('var ErrHandoff'):
                    continue
                counts[function] = counts.get(function, 0) + 1
                relative = path.relative_to(ROOT).as_posix()
                found[f'{relative}:{function}#{counts[function]}'] = f'{relative}:{number}'
    return found


def django_rate_limits():
    tree = ast.parse((ROOT / 'BE/core/celery.py').read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == 'task_annotations'
                                                for t in node.targets):
            return {key: value['rate_limit'] for key, value in ast.literal_eval(node.value).items()}
    raise AssertionError('BE/core/celery.py: app.conf.task_annotations not found')


def test_exists(identifier):
    parts = identifier.split('.')
    if len(parts) < 5 or parts[:2] != ['TESTS', 'handoff']:
        return False
    module = ROOT / 'TESTS/handoff' / f'{parts[2]}.py'
    return module.exists() and re.search(rf'def {re.escape(parts[-1])}\(', module.read_text()) is not None


class Phase0Inventory(unittest.TestCase):
    def test_no_entry_keeps_an_owner_planned_for_phase_4(self):
        document = json.loads(INVENTORY.read_text())
        planned = sorted(f'{entry["id"]} ({mode})' for entry in document['entries']
                         for mode, owner in entry['owner'].items()
                         if owner.get('status') == 'planned' and owner.get('phase') == 4)
        self.assertEqual(planned, [], f'{len(planned)} owners still planned for phase 4 (land the owner, then mark it '
                                      'present in the same change): ' + ', '.join(planned))


class TaskMatrixCoverage(unittest.TestCase):
    def test_every_task_name_belongs_to_exactly_one_matrix_family(self):
        names = [task['name'] for task in json.loads(TASKS_INVENTORY.read_text())]
        orphans = sorted(name for name in names if family_of(name) is None)
        self.assertEqual(orphans, [], 'task names in no (or several) p4_matrix families')
        listed = sorted(task for family in FAMILIES for task in family.tasks)
        self.assertEqual(sorted(set(listed) - set(names)), [], 'p4_matrix lists names that are not in the task inventory')

    def test_every_family_has_a_test_class_or_a_covering_test(self):
        import importlib
        matrix = importlib.import_module('TESTS.handoff.test_p4_task_matrix')
        for family in FAMILIES:
            if family.covered_by:
                for identifier in family.covered_by:
                    module, cls = identifier.split('.')[2], identifier.split('.')[3]
                    self.assertIn(f'class {cls}(', (ROOT / 'TESTS/handoff' / f'{module}.py').read_text(), identifier)
            else:
                name = matrix.class_name(family.id)
                cls = getattr(matrix, name, None)
                self.assertTrue(cls and any(a.startswith('test_') for a in dir(cls)),
                                f'test_p4_task_matrix.{name} with scenarios for family {family.id}')
                for task in family.tasks:
                    slug = task.replace('.', '_')
                    self.assertTrue(any(a.startswith(f'test_{slug}__') for a in dir(cls)),
                                    f'family {family.id}: no scenario for task {task} (test_{slug}__<boundary>)')


class Dispositions(unittest.TestCase):
    def document(self):
        self.assertTrue(DISPOSITIONS.exists(), 'contract: docs/architecture/go-migration/task-handoff-dispositions.json '
                                               'is missing (Phase 4 hand-back, legacy-name and per-task option record)')
        document = json.loads(DISPOSITIONS.read_text())
        self.assertEqual(document.get('format'), 1)
        return document

    def test_every_current_consumer_handback_site_is_classified(self):
        records, current = self.document()['handback_sites'], handback_sites()
        self.assertEqual(sorted(set(current) - set(records)), [], 'hand-back sites without a disposition')
        self.assertEqual(sorted(set(records) - set(current)), [], 'stale records (the site was ported: remove the record)')
        for key, record in records.items():
            self.assertIn(record.get('disposition'), ('unsupported', 'unreachable'), key)
            self.assertRegex(record.get('outcome', ''), OUTCOME, key)
            self.assertTrue(test_exists(record.get('evidence', '')), f'{key}: evidence must be an existing TESTS.handoff test')

    def test_every_phase0_consumer_handback_site_is_traced(self):
        document = self.document()
        evidence = json.loads(INVENTORY.read_text())['django_handoff_evidence']['sites']
        wanted = {site['site'] for site in evidence if site['category'] in CONSUMER_CATEGORIES}
        traced = {site for record in document['handback_sites'].values() for site in record.get('sites', [])}
        traced |= set(document.get('ported_sites', []))
        self.assertEqual(sorted(wanted - traced), [], 'Phase 0 hand-back sites neither ported (ported_sites) nor classified')

    def test_the_four_worker_handback_reasons_have_go_mode_outcomes(self):
        reasons = self.document()['handback_reasons']
        self.assertEqual(sorted(reasons), ['input', 'options', 'undecodable', 'unregistered'])
        for reason, record in reasons.items():
            self.assertRegex(record.get('outcome', ''), OUTCOME, reason)
            self.assertTrue(test_exists(record.get('evidence', '')), reason)

    def test_legacy_names_have_a_proven_disposition(self):
        legacy = self.document()['legacy_names']
        self.assertEqual(sorted(legacy), ['auto_move_users_to_base_plan', 'nurturing_warm_leads', 'save_binary_to_storage',
                                          'send_updated_certificate_email'])
        for name, record in legacy.items():
            self.assertIn(record.get('disposition'), ('adapter', 'retired', 'refuse_transition'), name)
            self.assertTrue(record.get('outcome'), name)
            self.assertTrue(test_exists(record.get('evidence', '')), name)

    def test_rate_and_time_limits_are_recorded_for_both_runtimes(self):
        tasks, limits = self.document()['tasks'], django_rate_limits()
        names = [task['name'] for task in json.loads(TASKS_INVENTORY.read_text()) if task['name'] != 'fixture.early_ack']
        self.assertEqual(sorted(set(names) - set(tasks)), [], 'tasks without a record')
        for name in names:
            record = tasks[name]
            self.assertEqual(record.get('family'), family_of(name), name)
            self.assertEqual(record.get('rate_limit'), limits.get(name), f'{name}: rate_limit must equal the Django annotation')
            if record.get('rate_limit'):
                self.assertTrue(record.get('go_rate_limit'), f'{name}: a Django rate limit needs the Go mechanism')
            self.assertEqual(len(record.get('time_limits', [])), 2, name)
            if any(record['time_limits']):
                self.assertTrue(record.get('go_time_limits'), f'{name}: Django time limits need the Go mechanism')


if __name__ == '__main__':
    unittest.main()
