"""Runs a Phase 3 scenario suite once per test process and hands each test its scenario result.

The unittest modules (test_p3_*.py) are thin: each test method asserts one scenario of the
driver report. The driver needs the production Python interpreter (BE/Dockerfile major.minor)
with the BE requirements: ``ASSOZETA_HANDOFF_PYTHON`` names it when the interpreter running
the tests is not that one. A missing interpreter, a crashed driver or a failed stack setup
is an error (never a valid red).
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
_RESULTS = {}


def production_version():
    match = re.search(r'^FROM python:(\d+\.\d+)\.\d+-', (ROOT / 'BE/Dockerfile').read_text(), re.MULTILINE)
    return match[1]


def production_python():
    wanted = production_version()
    tried = []
    for candidate in (os.environ.get('ASSOZETA_HANDOFF_PYTHON'), sys.executable):
        if not candidate:
            continue
        probe = subprocess.run([candidate, '-c', 'import sys, django, celery, kombu, redis, psycopg2; '
                                'print("%d.%d" % sys.version_info[:2])'], capture_output=True, text=True, timeout=60)
        if probe.returncode == 0 and probe.stdout.strip() == wanted:
            return candidate
        tried.append(f'{candidate}: {probe.stdout.strip() or probe.stderr.strip()[-200:]}')
    raise RuntimeError(f'Phase 3 handoff tests need Python {wanted} with the BE requirements; set '
                       f'ASSOZETA_HANDOFF_PYTHON (tried {tried})')


def suite(name, timeout=3600):
    if name in _RESULTS:
        return _RESULTS[name]
    with tempfile.TemporaryDirectory(prefix='az-p3-report-') as directory:
        report = Path(directory) / 'report.json'
        output = ROOT / 'quality-reports/handoff/p3' / name
        output.mkdir(parents=True, exist_ok=True)
        with (output / 'driver.log').open('w') as log:
            completed = subprocess.run([production_python(), '-B', '-m', 'TESTS.handoff.p3_driver', '--suite', name,
                                        '--report', str(report)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       timeout=timeout)
        if not report.exists():
            raise RuntimeError(f'p3 driver ({name}) wrote no report, exit {completed.returncode}; see {output / "driver.log"}')
        result = json.loads(report.read_text())
    if not result['setup'].get('ok'):
        raise RuntimeError(f'p3 stack setup failed ({name}): {result["setup"].get("error")}')
    _RESULTS[name] = result
    return result


class ScenarioAssertions:
    """Mixin: SUITE names the driver suite; scenario(name) asserts that scenario passed."""
    SUITE = None

    def scenario(self, name):
        result = suite(self.SUITE)['scenarios'].get(name)
        if result is None:
            raise RuntimeError(f'scenario {name} did not run')
        if 'error' in result:
            raise RuntimeError(f'scenario {name} crashed (not a contract red):\n{result["error"]}')
        self.assertTrue(result['ok'], result.get('failure'))
