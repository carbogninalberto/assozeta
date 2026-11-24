"""Scenario driver for the Phase 4 handoff tests (run by the unittest modules in a subprocess).

    python -m TESTS.handoff.p4_driver --suite sched|outbox|matrix --report PATH [--only NAME ...]

Same report format and rules as TESTS/handoff/p3_driver.py (a ContractFailure is a red, any
other exception an error), on the Phase 4 stack (TESTS/handoff/p4_stack.py). The matrix suite
honours ASSOZETA_HANDOFF_P4_FAMILIES (comma-separated family ids, see p4_matrix.py) so each task
family runs on its own stack. Logs go to quality-reports/handoff/p4/<suite>/.
"""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
SUITES = {'sched': 'TESTS.handoff.p4_scenarios_sched', 'outbox': 'TESTS.handoff.p4_scenarios_outbox',
          'matrix': 'TESTS.handoff.p4_scenarios_matrix'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', required=True, choices=sorted(SUITES))
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--only', action='append', default=[])
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from TESTS.handoff.p3_stack import ContractFailure
    module = importlib.import_module(SUITES[args.suite])
    scenarios = module.SCENARIOS
    families = [f for f in os.environ.get('ASSOZETA_HANDOFF_P4_FAMILIES', '').split(',') if f]
    if families and hasattr(module, 'select'):
        scenarios = module.select(families)
    report = {'setup': {'ok': False}, 'scenarios': {}}
    output = ROOT / 'quality-reports/handoff/p4' / args.suite
    stack = module.STACK(output)

    def write():
        # A component receipt (tools/go-migration/ledger_components.json): success only for a complete, unfiltered
        # run whose setup and every scenario passed.
        results = report['scenarios'].values()
        report['success'] = bool(report['setup'].get('ok') and results and all(r['ok'] for r in results)
                                 and not args.only and not families and len(report['scenarios']) == len(scenarios))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=1, default=str) + '\n')

    try:
        started = time.monotonic()
        stack.start()
        report['setup'] = {'ok': True, 'seconds': round(time.monotonic() - started, 1),
                           'migrations': stack.migration_versions}
        write()
        for name, scenario in scenarios.items():
            if args.only and name not in args.only:
                continue
            started = time.monotonic()
            result = {'ok': False}
            try:
                stack.reset()
                scenario(stack)
                result['ok'] = True
            except ContractFailure as failure:
                result['failure'] = str(failure)
            except Exception:
                result['error'] = traceback.format_exc()[-4000:]
            result['seconds'] = round(time.monotonic() - started, 1)
            report['scenarios'][name] = result
            print(f"{name}: {'ok' if result['ok'] else ('RED ' + result.get('failure', '')[:300]) if 'failure' in result else 'ERROR'}",
                  flush=True)
            write()
    except Exception:
        report['setup'] = {'ok': False, 'error': traceback.format_exc()[-4000:]}
    finally:
        try:
            stack.close()
        finally:
            write()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
