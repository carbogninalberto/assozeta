"""Release graph invariants; the rehearsal separately exercises real artifacts."""
import ast
import json
import os
import subprocess
import tempfile
import itertools
import re
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[3]


class ReleaseWorkflowTests(unittest.TestCase):
    def test_one_shared_verifier_pins_inputs_and_preserves_scheduled_full_runs(self):
        main = yaml.safe_load((ROOT / '.github/workflows/manuale.yml').read_text())
        release = yaml.safe_load((ROOT / '.github/workflows/manuale-release.yml').read_text())
        shared = yaml.safe_load((ROOT / '.github/workflows/manuale-verify.yml').read_text())
        self.assertNotIn('pull_request', release[True])
        for job in (main['jobs']['verify'], release['jobs']['bundle']):
            self.assertEqual(job['uses'], './.github/workflows/manuale-verify.yml')
        self.assertIn("github.event_name == 'schedule'", main['jobs']['verify']['with']['force_full'])
        steps = shared['jobs']['verify']['steps']
        commands = '\n'.join(step.get('run', '') for step in steps)
        self.assertIn('manuale-ci.py pin', commands)
        self.assertNotIn('MANUAL_REF', commands)
        self.assertNotIn('--changed-since', commands)
        for phase in ('capture', 'compile', 'render', 'runtime', 'package', 'baseline'):
            self.assertIn('--phase ' + phase, commands)
        candidate = next(step for step in steps if step.get('with', {}).get('name') == 'manuale-verified-baseline')
        self.assertNotIn('always()', candidate.get('if', ''))
        self.assertEqual(candidate['with']['if-no-files-found'], 'error')
        self.assertEqual(candidate['with']['retention-days'], 90)
        self.assertEqual(shared['jobs']['appliance']['needs'], 'verify')

    def test_installable_images_require_manual_verification(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/publish-images.yml').read_text())['jobs']
        self.assertIn('manual-release', jobs['publish']['needs'])
        self.assertIn("needs.manual-release.result == 'success'", jobs['publish']['if'])
        gate = jobs['manual-release']
        self.assertEqual(gate['uses'], './.github/workflows/manuale-release.yml')
        steps = jobs['publish']['steps']
        download = next(step for step in steps if step.get('uses', '').startswith('actions/download-artifact@'))
        self.assertEqual(download['with']['name'], 'manuale-release-runtime')
        self.assertEqual(download['with']['path'], 'BE/release-manual')
        build = next(step for step in steps if step.get('uses', '').startswith('docker/build-push-action@'))
        self.assertIn('REQUIRE_MANUAL=', build['with']['build-args'])
        self.assertIn('manual-release', jobs['release-bundle']['needs'])

    def test_exact_package_and_historical_rehearsal_are_separate_required_phases(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/manuale-verify.yml').read_text())['jobs']
        steps = jobs['verify']['steps']
        runs = '\n'.join(step.get('run', '') for step in steps)
        self.assertIn('--release "$RELEASE_VERSION"', runs)
        self.assertNotIn('--allow-dirty', runs)
        self.assertNotIn('--edit-content', runs)
        upload = next(step for step in steps if step.get('with', {}).get('name') == 'manuale-release-runtime')
        self.assertNotIn('always()', upload.get('if', ''))
        self.assertEqual(upload['with']['if-no-files-found'], 'error')
        rehearsal = next(step for step in jobs['appliance']['steps'] if 'CHECK_SCOPE' in step.get('env', {}))
        self.assertIn("'full' || 'compatibility'", rehearsal['env']['CHECK_SCOPE'])
        self.assertIn('--previous-release 1.0.17', rehearsal['run'])
        self.assertIn('--scope "$CHECK_SCOPE"', rehearsal['run'])

    def test_failed_manual_gate_cannot_allow_release_publication(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/publish-images.yml').read_text())['jobs']
        expression = jobs['publish']['if'].strip().removeprefix('${{').removesuffix('}}').strip()
        for event, manual, quality, cancelled in itertools.product(
                ('release', 'workflow_dispatch', 'push'),
                ('success', 'failure', 'cancelled', 'skipped'),
                ('success', 'failure', 'cancelled', 'skipped'), (True, False)):
            with self.subTest(event=event, manual=manual, quality=quality, cancelled=cancelled):
                values = {'validate': 'success', 'production-smoke': 'success',
                          'manual-release': manual, 'selfhost-quality': quality}
                source = re.sub(r'needs\.([a-z-]+)\.result', lambda match: repr(values[match[1]]), expression)
                source = source.replace('github.event_name', repr(event))
                source = source.replace('!cancelled()', repr(not cancelled)).replace('&&', ' and ').replace('||', ' or ')
                tree = ast.parse('(' + source + ')', mode='eval')
                allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq, ast.Constant)
                self.assertTrue(all(isinstance(node, allowed) for node in ast.walk(tree)))
                permitted = eval(compile(tree, '<publication guard>', 'eval'), {'__builtins__': {}})
                expected = not cancelled and (
                    manual == 'success' or event == 'push' and manual == 'skipped') and (
                    quality == 'success' or event == 'push' and quality == 'skipped')
                self.assertEqual(permitted, expected)


FAKE_CACHE = '''import json, sys
from pathlib import Path
args = sys.argv[1:]
def value(name):
    return args[args.index(name) + 1] if name in args else None
if value('--output'):
    Path(value('--output')).write_text('{}')
if args[0] == 'store-merge' and Path(value('--candidate'), 'entries.json').is_file():
    store = Path(value('--store'))
    store.mkdir(parents=True, exist_ok=True)
    (store / 'index.json').write_text(Path(value('--candidate'), 'entries.json').read_text())
    (store / 'blobs' / 'sha256').mkdir(parents=True, exist_ok=True)
    (store / 'blobs' / 'sha256' / ('0' * 64)).write_bytes(b'png')
'''
FAKE_TIMING = '''import subprocess, sys
raise SystemExit(subprocess.run(sys.argv[sys.argv.index('--') + 1:]).returncode)
'''


class CaptureConcurrencyDefaultTests(unittest.TestCase):
    def test_ci_captures_sequentially_unless_explicitly_configured(self):
        verify = yaml.safe_load((ROOT / '.github/workflows/manuale-verify.yml').read_text())['jobs']['verify']
        self.assertEqual(verify['env']['MANUALE_CAPTURE_CONCURRENCY'], "${{ vars.MANUALE_CAPTURE_CONCURRENCY || '1' }}")


class EvidenceStoreAvailabilityTests(unittest.TestCase):
    """Store publication never blocks image publication and tolerates an empty store."""

    def setUp(self):
        self.images = yaml.safe_load((ROOT / '.github/workflows/publish-images.yml').read_text())['jobs']
        self.release = yaml.safe_load((ROOT / '.github/workflows/manuale-release.yml').read_text())
        self.publisher = yaml.safe_load((ROOT / '.github/workflows/manuale-evidence-publish.yml').read_text())

    def test_store_publication_runs_after_authorization_outside_the_image_chain(self):
        self.assertIs(self.images['manual-release']['with']['evidence_store'], False)
        store = self.images['manual-evidence-store']
        self.assertEqual(store['uses'], './.github/workflows/manuale-evidence-publish.yml')
        self.assertEqual(store['needs'], 'manual-release')
        self.assertIn("needs.manual-release.result == 'success'", store['if'])
        self.assertIn("!startsWith(github.event_name, 'pull_request')", store['if'])
        for name, job in self.images.items():
            needs = job.get('needs', [])
            self.assertNotIn('manual-evidence-store', [needs] if isinstance(needs, str) else needs, name)
            self.assertNotIn('manual-evidence-store', json.dumps(job.get('if', '')), name)
        # Direct release rehearsals still publish the store, after their own authorization only.
        for trigger in ('workflow_call', 'workflow_dispatch'):
            self.assertIs(self.release[True][trigger]['inputs']['evidence_store']['default'], True)
        job = self.release['jobs']['evidence-store']
        self.assertEqual(job['needs'], 'authorize')
        self.assertIn("needs.authorize.result == 'success'", job['if'])
        self.assertIn('inputs.evidence_store', job['if'])
        self.assertIn("!startsWith(github.event_name, 'pull_request')", job['if'])

    def test_store_uploads_are_guarded_and_never_fail_on_missing_files(self):
        job = self.publisher['jobs']['publish']
        self.assertIn("!startsWith(github.event_name, 'pull_request')", job['if'])
        self.assertIn('refs/tags/', job['if'])
        uploads = [step for step in job['steps'] if step.get('uses', '').startswith('actions/upload-artifact')]
        self.assertEqual(len(uploads), 2)
        for step in uploads:
            self.assertIn("steps.merge.outputs.publish == 'true'", step['if'])
            self.assertNotEqual(step['with']['if-no-files-found'], 'error')
        blobs = next(step for step in uploads if step['with']['name'] == 'manuale-evidence-store-blobs')
        self.assertIn("steps.merge.outputs.blobs == 'true'", blobs['if'])

    def run_merge(self, entries=None, *, candidate=True):
        step = next(step for step in self.publisher['jobs']['publish']['steps'] if step.get('id') == 'merge')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'docs/scripts').mkdir(parents=True)
            (root / 'docs/scripts/manuale-cache.py').write_text(FAKE_CACHE)
            (root / 'docs/scripts/manuale_timing.py').write_text(FAKE_TIMING)
            candidate_dir = root / 'candidate'
            candidate_dir.mkdir()
            if candidate:
                (candidate_dir / 'index.json').write_text('{}')
            if entries is not None:
                (candidate_dir / 'entries.json').write_text(json.dumps({'format': 1, 'entries': entries}))
            output = root / 'github-output'
            environment = {'PATH': os.environ['PATH'], 'RUNNER_TEMP': str(root), 'STORE': str(root / 'store'),
                           'CANDIDATE': str(candidate_dir), 'GITHUB_OUTPUT': str(output), 'GITHUB_RUN_ID': '7',
                           'GH_TOKEN': ''}
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']], cwd=root, env=environment,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return dict(line.split('=', 1) for line in output.read_text().split())

    def test_empty_or_absent_store_publishes_nothing_without_failing(self):
        self.assertEqual(self.run_merge(candidate=False), {'publish': 'false'})
        self.assertEqual(self.run_merge(), {'publish': 'false', 'blobs': 'false'})
        self.assertEqual(self.run_merge({}), {'publish': 'false', 'blobs': 'false'})
        self.assertEqual(self.run_merge({'unit/' + 'a' * 64: {'producer_run_id': '7'}}),
                         {'publish': 'true', 'blobs': 'true'})
