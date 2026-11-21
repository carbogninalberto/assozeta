"""Run each Phase 5 UI assertion as a separately tracked handoff test.

    python3 -B -m TESTS.handoff.run_expected_red TESTS.handoff.test_p5_ui

Install the UI and selfhost/tests/browser lockfiles (Node 22), then Playwright's
matching Chromium headless shell. One build, one browser worker, no deployment.
Run the Node files with --test or Playwright with p5-runtime.config.js directly
when debugging. During promotion move the Node files to *.test.js, include the
Playwright project in the operational UI job and remove their manifest entries.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
NODE = ('bootstrap_preferences', 'bootstrap_no_fallback', 'socket_and_request_urls',
        'store_transitions', 'store_poll_fallback', 'store_denied')
BROWSER = ('owner_access', 'switch_to_go', 'switch_to_django', 'lost_reply', 'cold_maintenance',
           'stale_generation', 'idempotency_conflict', 'operation_in_progress', 'blocked_dispositions',
           'pre_commit_failure', 'post_commit_failure', 'stale_browser', 'other_route_refresh')


def check_run(process, outcomes, expected):
    if set(outcomes) != set(expected) or process.returncode != int(any(outcomes.values())):
        raise RuntimeError(f'Incomplete UI run (exit {process.returncode}): {sorted(outcomes)}; '
                           f'expected {sorted(expected)}\n{process.stderr[-1500:]}')
    return outcomes


def run_node():
    cmd = ['node', '--test', '--test-reporter=junit', 'src/store/runtimeSwitchStore.handoff.mjs',
           'src/utils/runtimeUrls.handoff.mjs']
    result = subprocess.run(cmd, cwd=ROOT / 'UI', capture_output=True, text=True, timeout=60)
    cases = ET.fromstring(result.stdout).findall('.//testcase')
    outcomes = {}
    for case in cases:
        name = case.attrib['name']
        if name in outcomes or case.find('skipped') is not None or case.find('error') is not None:
            raise RuntimeError(f'Duplicate, skipped or errored Node test: {name}')
        failure = case.find('failure')
        if failure is not None and 'cause: AssertionError [ERR_ASSERTION]' not in (failure.text or ''):
            raise RuntimeError(f'Node test {name} failed outside its assertion: {failure.attrib}')
        outcomes[name] = failure.get('message') if failure is not None else None
    return check_run(result, outcomes, NODE)


def run_browser():
    directory = ROOT / 'selfhost/tests/browser'
    with tempfile.TemporaryDirectory(prefix='p5-ui-') as scratch:
        env = {**os.environ, 'TMPDIR': scratch, 'P5UI_OUTPUT': scratch, 'FORCE_COLOR': '0'}
        result = subprocess.run(['node', 'node_modules/@playwright/test/cli.js', 'test', '-c',
                                 'p5-runtime.config.js', '--reporter=json'], cwd=directory,
                                env=env, capture_output=True, text=True, timeout=1000)
        if not result.stdout.lstrip().startswith('{'):
            raise RuntimeError(f'Missing Playwright report: {result.stderr[-1500:]}')
        report = json.loads(result.stdout)
        if report.get('errors'):
            raise RuntimeError('Playwright setup failed: ' + str(report['errors'])[:1500])
        outcomes = {}
        for suite in report['suites']:
            for spec in suite['specs']:
                name = spec['title']
                (case,) = spec['tests']
                (attempt,) = case['results']
                errors = attempt['errors']
                if name in outcomes or attempt['status'] not in ('passed', 'failed') or case.get('expectedStatus') != 'passed':
                    raise RuntimeError(f'Duplicate, skipped, retried or incomplete Playwright test: {name}')
                if attempt['status'] == 'passed' and not errors:
                    outcomes[name] = None
                    continue
                if len(errors) != 1:
                    raise RuntimeError(f'Playwright test/fixture errors in {name}: {errors!s}'[:2500])
                message = re.sub(r'\x1b\[[0-9;]*m', '', errors[0]['message'])
                if 'expect(' not in message:
                    raise RuntimeError(f'{name}: {message[:1500]}')
                outcomes[name] = message.split('Call log:')[0].strip()
    return check_run(result, outcomes, BROWSER)


def register(test_case, names):

    def make_test(name):
        def check(self):
            if self.outcomes[name] is not None:
                self.fail(self.outcomes[name])
        return check

    for name in names:
        setattr(test_case, 'test_' + name, make_test(name))


class Unit(unittest.TestCase):
    """One Node assertion result per expected-red entry."""
    @classmethod
    def setUpClass(cls):
        cls.outcomes = run_node()


class Browser(unittest.TestCase):
    """One Playwright scenario per expected-red entry."""
    @classmethod
    def setUpClass(cls):
        cls.outcomes = run_browser()


register(Unit, NODE)
register(Browser, BROWSER)
