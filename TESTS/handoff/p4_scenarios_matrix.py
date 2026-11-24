"""Phase 4 part C: the task matrix suite. Contract: TESTS/handoff/test_p4_task_matrix.py.

Scenarios are '<family id>:<scenario>' collected from every family module
TESTS/handoff/p4_family_<id>.py (SCENARIOS dict). ASSOZETA_HANDOFF_P4_FAMILIES selects families
(p4_driver honours it through select()).
"""
import importlib

from TESTS.handoff.p4_matrix import FAMILIES
from TESTS.handoff.p4_matrix_kit import SELECTED, P4MatrixStack

STACK = P4MatrixStack


def family_scenarios(family_id):
    module = importlib.import_module(f'TESTS.handoff.p4_family_{family_id}')
    return {f'{family_id}:{name}': scenario for name, scenario in module.SCENARIOS.items()}


SCENARIOS = {}
for _family in FAMILIES:
    if not _family.covered_by and _family.id in SELECTED:
        SCENARIOS.update(family_scenarios(_family.id))


def select(families):
    return {name: scenario for name, scenario in SCENARIOS.items() if name.split(':', 1)[0] in families}
