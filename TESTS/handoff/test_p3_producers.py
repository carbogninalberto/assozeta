"""Phase 3, part B: every producer path is routed by the effective authority. Contract: test_p3_email_job.py docstring.

The producer inventory is not re-listed here: Phase0Inventory reads the publisher entries of
docs/architecture/go-migration/handoff-inventory.json (contract B of Phase 0) and requires that
none keeps an owner planned for phase 3, and that every planned item publishes through an API the
admission covers. The dynamic tests exercise those APIs on real Celery and the Go worker wiring.
"""
import json
from pathlib import Path
import unittest

from TESTS.handoff.p3_suite import ScenarioAssertions

ROOT = Path(__file__).resolve().parents[2]
# Publisher keys end with ':<receiver>.<method>' (optionally '#n'); these methods reach the admission.
ADMITTED_METHODS = {'py': ('delay', 'apply_async', 'send_task', 'retry'), 'go': ('Publish', 'Enqueue')}


class ProducerPaths(ScenarioAssertions, unittest.TestCase):
    SUITE = 'producers'

    def test_every_django_publication_api_is_refused_after_commit(self):
        self.scenario('django_paths_stale_after_commit')

    def test_every_django_publication_api_is_held_during_frozen_intake(self):
        self.scenario('django_paths_held_while_frozen')

    def test_go_task_child_published_during_drain_is_admitted_and_runs_once_on_target(self):
        self.scenario('go_task_child_is_admitted')


class Phase0Inventory(unittest.TestCase):
    def entries(self):
        document = json.loads((ROOT / 'docs/architecture/go-migration/handoff-inventory.json').read_text())
        return [entry for entry in document['entries'] if entry['kind'] == 'publisher']

    def test_planned_phase3_publishers_use_an_admitted_api(self):
        for entry in self.entries():
            for item in entry['items']:
                language, method = item.split(':', 1)[0], item.rsplit(':', 1)[-1].split('#')[0].rsplit('.', 1)[-1]
                self.assertIn(method, ADMITTED_METHODS.get(language, ()),
                                f'{entry["id"]}: {item} publishes through an API the admission does not cover')

    def test_no_publisher_keeps_an_owner_planned_for_phase_3(self):
        planned = sorted(f'{entry["id"]} ({mode})' for entry in self.entries()
                         for mode, owner in entry['owner'].items()
                         if owner.get('status') == 'planned' and owner.get('phase') == 3)
        self.assertEqual(planned, [], 'publishers still owner-planned for phase 3 (route them by the effective '
                                      'authority, then mark the owner present): ' + ', '.join(planned))


if __name__ == '__main__':
    unittest.main()
