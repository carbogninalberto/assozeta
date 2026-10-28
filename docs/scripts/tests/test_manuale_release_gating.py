"""Mode C: only complete comprehensive verification can authorize publication."""
import copy
import importlib.util
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / '.github/workflows'
RELEASE, REVISION = '1.2.3', 'a' * 40


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_') + '_gating', ROOT / 'docs/scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


ci = module('manuale-ci')


def passing_results(scope='full'):
    return {'status': 'passed', 'scope': scope, 'release': RELEASE, 'application_revision': REVISION,
            'checks': ['production images built and historical image digests resolved',
                       *[check + ' (recorded)' for check in ci.REQUIRED_RELEASE_CHECKS]]}


def needs(contracts='success', verify='success', appliance='success', *, comprehensive='true', scope='full', required='true',
          binding=('verified', RELEASE, REVISION)):
    outputs = {'comprehensive': comprehensive, 'appliance_scope': scope, 'appliance_required': required, 'mode': 'reuse'}
    if binding:
        outputs.update(release_binding=binding[0], release_binding_release=binding[1],
                       release_binding_revision=binding[2])
    return {'contracts': {'result': contracts, 'outputs': {}},
            'verify': {'result': verify, 'outputs': outputs},
            'appliance': {'result': appliance, 'outputs': {}}}


class GateFunctionTests(unittest.TestCase):
    def failures(self, value, results=None, comprehensive=True):
        return ci.gate_failures(value, comprehensive=comprehensive, release=RELEASE, revision=REVISION,
                                results=passing_results() if results is None else results)

    def test_complete_comprehensive_verification_is_authorized(self):
        self.assertEqual(self.failures(needs()), [])

    def test_every_non_success_mandatory_job_blocks_release(self):
        for job, result in itertools.product(('contracts', 'verify', 'appliance'),
                                             ('failure', 'cancelled', 'skipped', 'missing')):
            with self.subTest(job=job, result=result):
                value = needs(**{job: result})
                if result == 'missing':
                    value.pop(job)
                self.assertTrue(self.failures(value))

    def test_fast_or_reduced_results_never_authorize_release(self):
        reduced = {
            'non-comprehensive verifier': needs(comprehensive='false'),
            'compatibility appliance scope': needs(scope='compatibility'),
            'no verify outputs': {**needs(), 'verify': {'result': 'success'}},
            'empty needs': {},
        }
        for name, value in reduced.items():
            with self.subTest(name):
                self.assertTrue(self.failures(value))

    def test_release_requires_a_verified_binding_for_this_exact_identity(self):
        cases = {'missing': None, 'unverified': ('', RELEASE, REVISION),
                 'another release': ('verified', '9.9.9', REVISION),
                 'another revision': ('verified', RELEASE, 'b' * 40)}
        for name, binding in cases.items():
            with self.subTest(name):
                failures = self.failures(needs(binding=binding))
                self.assertTrue(any('release binding' in failure for failure in failures), failures)
        # Incremental runs still verify the binding in the job, but it does not gate them here.
        self.assertEqual(self.failures(needs(contracts='skipped', comprehensive='false', scope='compatibility',
                                             binding=None), results=passing_results('compatibility'),
                                       comprehensive=False), [])

    def test_appliance_results_must_be_complete_and_bound_to_this_release(self):
        results = passing_results()
        cases = {'missing': False, 'failed': {**results, 'status': 'failed'},
                 'running': {**results, 'status': 'running'},
                 'other release': {**results, 'release': '1.2.2'},
                 'other revision': {**results, 'application_revision': 'b' * 40},
                 'compatibility scope': {**results, 'scope': 'compatibility'}}
        for check in ci.REQUIRED_RELEASE_CHECKS:
            reduced = copy.deepcopy(results)
            reduced['checks'] = [item for item in reduced['checks'] if check not in item]
            cases['without ' + check] = reduced
        for name, value in cases.items():
            with self.subTest(name):
                failures = ci.gate_failures(needs(), comprehensive=True, release=RELEASE, revision=REVISION,
                                            results=None if value is False else value)
                self.assertTrue(failures)

    def test_required_checks_are_the_ones_the_appliance_rehearsal_records(self):
        source = (ROOT / 'selfhost/tests/manual-release-smoke.py').read_text()
        for check in ci.REQUIRED_RELEASE_CHECKS:
            self.assertIn(check, source)

    def test_incremental_runs_pass_only_what_they_required(self):
        ok = ci.gate_failures(needs(contracts='skipped', appliance='skipped', comprehensive='false',
                                    scope='compatibility', required='false'),
                              comprehensive=False, release=RELEASE, revision=REVISION, results=None)
        self.assertEqual(ok, [])
        required = needs(contracts='skipped', appliance='skipped', comprehensive='false', scope='compatibility')
        self.assertTrue(ci.gate_failures(required, comprehensive=False, release=RELEASE, revision=REVISION, results=None))
        for job in ('contracts', 'verify', 'appliance'):
            for result in ('failure', 'cancelled'):
                value = needs(comprehensive='false', scope='compatibility', **{job: result})
                self.assertTrue(ci.gate_failures(value, comprehensive=False, release=RELEASE, revision=REVISION,
                                                 results=passing_results('compatibility')))

    def test_gate_command_exits_nonzero_and_reports_unauthorized(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            results = directory / 'results.json'
            results.write_text(json.dumps(passing_results()))
            for value, expected in ((needs(), 0), (needs(appliance='cancelled'), 1), (needs(scope='compatibility'), 1)):
                github_output = directory / 'output'
                github_output.write_text('')
                environment = {**os.environ, 'GITHUB_OUTPUT': str(github_output), 'GITHUB_STEP_SUMMARY': '',
                               'MANUALE_TIMING_FILE': '', 'MANUALE_NEEDS_JSON': json.dumps(value)}
                completed = subprocess.run([sys.executable, str(ROOT / 'docs/scripts/manuale-ci.py'), 'gate',
                                            '--comprehensive', 'true', '--release', RELEASE, '--revision', REVISION,
                                            '--results', str(results), '--output', str(directory / 'gate.json')],
                                           env=environment, capture_output=True, text=True)
                self.assertEqual(completed.returncode, expected, completed.stdout + completed.stderr)
                self.assertIn('authorized=' + ('true' if expected == 0 else 'false'), github_output.read_text())
                self.assertEqual(json.loads((directory / 'gate.json').read_text())['authorized'], expected == 0)
            # A missing results file is a block, never a pass.
            completed = subprocess.run([sys.executable, str(ROOT / 'docs/scripts/manuale-ci.py'), 'gate',
                                        '--comprehensive', 'true', '--release', RELEASE, '--revision', REVISION,
                                        '--results', str(directory / 'absent.json')],
                                       env={**environment, 'MANUALE_NEEDS_JSON': json.dumps(needs())},
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 1)


class ReleaseWorkflowGateTests(unittest.TestCase):
    def setUp(self):
        self.release = yaml.safe_load((WORKFLOWS / 'manuale-release.yml').read_text())
        self.shared = yaml.safe_load((WORKFLOWS / 'manuale-verify.yml').read_text())
        self.main = yaml.safe_load((WORKFLOWS / 'manuale.yml').read_text())

    def test_release_always_runs_the_comprehensive_mode(self):
        bundle = self.release['jobs']['bundle']
        self.assertEqual(bundle['uses'], './.github/workflows/manuale-verify.yml')
        self.assertIs(bundle['with']['comprehensive'], True)
        self.assertIs(bundle['with']['run_contracts'], True)
        self.assertNotIn('workflows', bundle['with'])
        self.assertNotIn('impact', bundle['with'])
        inputs = self.shared[True]['workflow_call']['inputs']
        # Callers must opt out of comprehensive verification explicitly.
        self.assertIs(inputs['comprehensive']['default'], True)
        self.assertNotIn('comprehensive', self.release[True]['workflow_call']['inputs'])
        self.assertNotIn('comprehensive', self.release[True]['workflow_dispatch']['inputs'])

    def test_comprehensive_mode_forces_full_appliance_and_contracts(self):
        jobs = self.shared['jobs']
        outputs = jobs['verify']['outputs']
        self.assertIn('inputs.comprehensive', outputs['appliance_scope'])
        self.assertIn('inputs.comprehensive', outputs['appliance_required'])
        self.assertIn('inputs.comprehensive', jobs['contracts']['if'])
        plan = next(step for step in jobs['verify']['steps'] if step.get('id') == 'plan')
        self.assertIn('"$COMPREHENSIVE" != true', plan['run'])
        download = next(step for step in jobs['verify']['steps'] if step.get('with', {}).get('name') == 'manuale-impact')
        self.assertIn('!inputs.comprehensive', download['if'])
        contracts = '\n'.join(step.get('run', '') for step in jobs['contracts']['steps'])
        for suite in ('--suite python', '--suite node --with-browser', '--suite bot-mcp'):
            self.assertIn(suite, contracts)

    def test_verify_job_generates_and_verifies_the_release_binding(self):
        verify = self.shared['jobs']['verify']
        steps = verify['steps']
        names = [step.get('id') or step.get('name') for step in steps]
        bind = next(index for index, step in enumerate(steps) if 'store-bind ' in step.get('run', ''))
        check = names.index('binding')
        self.assertLess(bind, check)
        step = steps[check]
        self.assertIn('store-verify-binding', step['run'])
        self.assertIn('release-binding.json', step['run'])
        self.assertNotIn('continue-on-error', step)
        self.assertNotIn('if', step)
        self.assertEqual(step['env']['APPLICATION_REVISION'], '${{ github.sha }}')
        self.assertEqual(step['env']['MANUAL_REVISION'], '${{ steps.pin.outputs.manual_revision }}')
        for key in ('release_binding', 'release_binding_release', 'release_binding_revision'):
            self.assertEqual(verify['outputs'][key], '${{ steps.binding.outputs.%s }}' % key)
        gate = next(item for item in self.shared['jobs']['gate']['steps'] if item.get('id') == 'gate')
        self.assertEqual(gate['env']['APPLICATION_REVISION'], '${{ github.sha }}')

    def test_gate_jobs_cannot_be_skipped_into_success(self):
        gate = self.shared['jobs']['gate']
        self.assertEqual(set(gate['needs']), {'contracts', 'verify', 'appliance'})
        self.assertEqual(gate['if'], '${{ always() }}')
        step = next(step for step in gate['steps'] if step.get('id') == 'gate')
        self.assertEqual(step['env']['MANUALE_NEEDS_JSON'], '${{ toJSON(needs) }}')
        self.assertEqual(step['env']['COMPREHENSIVE'], '${{ inputs.comprehensive }}')
        self.assertIn('manuale-ci.py gate', step['run'])
        self.assertNotIn('continue-on-error', step)
        self.assertEqual(self.shared[True]['workflow_call']['outputs']['authorized']['value'],
                         '${{ jobs.gate.outputs.authorized }}')
        authorize = self.release['jobs']['authorize']
        self.assertEqual(authorize['needs'], 'bundle')
        self.assertEqual(authorize['if'], '${{ always() }}')

    def run_authorize(self, result, authorized, scope):
        step = self.release['jobs']['authorize']['steps'][0]
        environment = {'PATH': os.environ['PATH'], 'BUNDLE_RESULT': result, 'AUTHORIZED': authorized,
                       'APPLIANCE_SCOPE': scope}
        return subprocess.run(['bash', '-e', '-c', step['run']], env=environment, capture_output=True).returncode

    def test_release_authorization_requires_explicit_success_of_everything(self):
        self.assertEqual(self.run_authorize('success', 'true', 'full'), 0)
        for result, authorized, scope in itertools.product(('success', 'failure', 'cancelled', 'skipped', ''),
                                                          ('true', 'false', ''), ('full', 'compatibility', '')):
            if (result, authorized, scope) == ('success', 'true', 'full'):
                continue
            with self.subTest(result=result, authorized=authorized, scope=scope):
                self.assertNotEqual(self.run_authorize(result, authorized, scope), 0)

    def test_publication_still_depends_on_the_release_workflow(self):
        jobs = yaml.safe_load((WORKFLOWS / 'publish-images.yml').read_text())['jobs']
        self.assertEqual(jobs['manual-release']['uses'], './.github/workflows/manuale-release.yml')
        self.assertIn("needs.manual-release.result == 'success'", jobs['publish']['if'])

    def test_incremental_pull_request_runs_never_produce_a_release_authorization(self):
        verify = self.main['jobs']['verify']
        self.assertIs(verify['with']['comprehensive'], False)
        self.assertTrue(verify['with']['release'].startswith("${{ format('manual-verification-"))
        self.assertNotIn('manual-release', json.dumps(self.main['jobs']))

    def test_appliance_runs_with_full_scope_and_legacy_storage_for_release(self):
        appliance = self.shared['jobs']['appliance']
        self.assertIn("needs.verify.outputs.appliance_required == 'true'", appliance['if'])
        storage = next(step for step in appliance['steps'] if step.get('uses') == './.github/actions/prepare-storage')
        self.assertIn("appliance_scope == 'full'", storage['with']['legacy'])


class GateEndToEndTests(unittest.TestCase):
    """The gate reads exactly the artifact layout the appliance job uploads."""

    def test_results_path_matches_upload_root(self):
        shared = yaml.safe_load((WORKFLOWS / 'manuale-verify.yml').read_text())
        upload = next(step for step in shared['jobs']['appliance']['steps']
                      if step.get('with', {}).get('name') == 'manuale-release-evidence')
        self.assertIn('quality-reports/manual-release/', upload['with']['path'])
        download = next(step for step in shared['jobs']['gate']['steps']
                        if step.get('with', {}).get('name') == 'manuale-release-evidence')
        gate = next(step for step in shared['jobs']['gate']['steps'] if step.get('id') == 'gate')
        self.assertIn(download['with']['path'].replace('${{ runner.temp }}', '$RUNNER_TEMP') + '/results.json', gate['run'])
        self.assertIn("(output / 'results.json')", (ROOT / 'selfhost/tests/manual-release-smoke.py').read_text())

    def test_gate_without_results_blocks_even_with_successful_jobs(self):
        with patch.dict(os.environ, {'MANUALE_TIMING_FILE': ''}):
            self.assertIn('appliance results are missing',
                          ci.gate_failures(needs(), comprehensive=True, release=RELEASE, revision=REVISION, results=None))


if __name__ == '__main__':
    unittest.main()
