"""Scenario driver for the Phase 3 handoff tests (run by the unittest modules in a subprocess).

    python -m TESTS.handoff.p3_driver --suite db --report PATH [--only NAME ...]

Runs on the production Python interpreter (BE/Dockerfile major.minor) with the BE
requirements, because the scenarios are a real Django producer and real Celery workers.
One private stack (TESTS/handoff/p3_stack.py) serves every scenario of the suite; it is
reset between scenarios. The report is JSON: {"setup": {"ok", "error"}, "scenarios":
{name: {"ok", "failure", "error", "seconds"}}}. A ContractFailure is a red; any other
exception is recorded as an error (never a valid red). Logs go to
quality-reports/handoff/p3/<suite>/.
"""
import argparse
import importlib
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
SUITES = {'db': 'TESTS.handoff.p3_scenarios_db', 'email': 'TESTS.handoff.p3_scenarios_email',
          'broker': 'TESTS.handoff.p3_scenarios_broker', 'durable': 'TESTS.handoff.p3_scenarios_durable',
          'producers': 'TESTS.handoff.p3_scenarios_producers'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', required=True, choices=sorted(SUITES))
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--only', action='append', default=[])
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from TESTS.handoff.p3_stack import ContractFailure, Stack
    scenarios = importlib.import_module(SUITES[args.suite]).SCENARIOS
    report = {'setup': {'ok': False}, 'scenarios': {}}
    output = ROOT / 'quality-reports/handoff/p3' / args.suite
    stack = Stack(output)

    def write():
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
