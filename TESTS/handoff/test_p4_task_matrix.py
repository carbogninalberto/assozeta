"""Phase 4, part C: the task matrix -- every row of task-coverage.md through its actual producer,
routing and destination worker in both modes and across a switch.

CONTRACT P4-C. Data: TESTS/handoff/p4_matrix.py (one Family per task-coverage row, cheapest
first). Engine: p4_matrix_kit.py. Per-family contract and scenarios: the module docstring and
SCENARIOS of TESTS/handoff/p4_family_<id>.py. Builds on P3-A/B (ledger, admission, claim, effects,
transfer) and P4-A/B (schedulers, outbox); a Go-native job keeps the P3 mapping (id = task id).

1. BOUNDARIES (p4_matrix_kit.standard, per task): steady Django; steady exclusive Go; accepted on
   Django and still queued at the switch, run once on Go; the reverse. Family modules add the
   boundaries task-coverage.md names for their row (after a child publishes, at a batch/page
   boundary, rerun after completion, rollback mid-batch, after object creation, partial upload,
   renewal lock retry, ...).
2. EXCLUSIVE GO MODE: an admitted supported input finishes on Go. No envelope reaches the 'celery'
   queue, no Django attempt exists, no Django process runs. An input outside the product contract
   gets its documented outcome (P4-D handback_reasons / handback_sites) -- never the dormant queue.
3. IDENTITY: every scenario ends in the Phase 3 identity oracle (one ledger row per accepted job,
   one completed attempt, source copy gone, target representation keeps the id) and in the
   family's business verification against the uninterrupted Django control (row identities and
   counts, files and stored objects, SMTP counts, provider keys and payment state, retention cutoffs).
4. RECORD: a scenario passes only when the task's record exists in
   docs/architecture/go-migration/task-handoff-dispositions.json with its family (CONTRACT P4-D).
5. RUNNING ONE FAMILY: ASSOZETA_HANDOFF_P4_FAMILIES=<id> python3 -B -m unittest -v
   TESTS.handoff.test_p4_task_matrix.<FamilyClass> (one private stack for that family only), or the
   driver: ASSOZETA_HANDOFF_P4_FAMILIES=<id> $ASSOZETA_HANDOFF_PYTHON -B -m TESTS.handoff.p4_driver
   --suite matrix --report r.json [--only '<id>:<scenario>']. Without the variable every family runs
   on one stack (CI).

6. RED TRACKING: a scenario that is not green fails with 'P4 matrix contract red: <reason>' (always a
   ContractFailure; a crash is an error and never a valid red). The reason moves as the Phase 3
   transfer seams, the Go executors and the record land; the expected-red entry follows the prefix.
   CI runs this module in its own job (.github/workflows/handoff-contracts.yml task-matrix, ~16 min red).

Test ids are stable: TESTS.handoff.test_p4_task_matrix.<FamilyClass>.test_<scenario> where
FamilyClass is the CamelCase family id and <scenario> the family module's SCENARIOS key.
"""
import importlib
import unittest

from TESTS.handoff.p4_matrix import FAMILIES
from TESTS.handoff.p4_suite import ScenarioAssertions


def _test(name):
    def test(self):
        self.scenario(name)
    test.__name__ = 'test_' + name.split(':', 1)[1]
    return test


def class_name(family_id):
    return ''.join(part.title() for part in family_id.split('_'))


for _family in FAMILIES:
    if _family.covered_by:
        continue
    _module = importlib.import_module(f'TESTS.handoff.p4_family_{_family.id}')
    _attributes = {'SUITE': 'matrix', 'FAILURE_PREFIX': 'P4 matrix contract red: ', '__doc__': (_module.__doc__ or '').strip().splitlines()[0],
                   '__module__': __name__}
    for _name in _module.SCENARIOS:
        _attributes['test_' + _name] = _test(f'{_family.id}:{_name}')
    globals()[class_name(_family.id)] = type(class_name(_family.id), (ScenarioAssertions, unittest.TestCase), _attributes)

del _family, _module, _attributes


if __name__ == '__main__':
    unittest.main()
