"""Runs a Phase 4 scenario suite once per test process (TESTS/handoff/p4_driver.py) and hands each
test its scenario result. Same rules as p3_suite: the driver needs the production Python with the
BE requirements (ASSOZETA_HANDOFF_PYTHON); a missing interpreter, crashed driver or failed stack
setup is an error, never a valid red. The matrix suite passes ASSOZETA_HANDOFF_P4_FAMILIES through.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from TESTS.handoff.p3_suite import production_python

ROOT = Path(__file__).resolve().parents[2]
_RESULTS = {}


def suite(name, timeout=5400):
    if name in _RESULTS:
        return _RESULTS[name]
    with tempfile.TemporaryDirectory(prefix='az-p4-report-') as directory:
        report = Path(directory) / 'report.json'
        output = ROOT / 'quality-reports/handoff/p4' / name
        output.mkdir(parents=True, exist_ok=True)
        with (output / 'driver.log').open('w') as log:
            completed = subprocess.run([production_python(), '-B', '-m', 'TESTS.handoff.p4_driver', '--suite', name,
                                        '--report', str(report)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       timeout=timeout, env=dict(os.environ))
        if not report.exists():
            raise RuntimeError(f'p4 driver ({name}) wrote no report, exit {completed.returncode}; see {output / "driver.log"}')
        result = json.loads(report.read_text())
    if not result['setup'].get('ok'):
        raise RuntimeError(f'p4 stack setup failed ({name}): {result["setup"].get("error")}')
    _RESULTS[name] = result
    return result


class ScenarioAssertions:
    """Mixin: SUITE names the driver suite; scenario(name) asserts that scenario passed."""
    SUITE = None

    def scenario(self, name):
        result = suite(self.SUITE)['scenarios'].get(name)
        if result is None:
            raise RuntimeError(f'scenario {name} did not run (ASSOZETA_HANDOFF_P4_FAMILIES excludes it?)')
        if 'error' in result:
            raise RuntimeError(f'scenario {name} crashed (not a contract red):\n{result["error"]}')
        self.assertTrue(result['ok'], result.get('failure'))
