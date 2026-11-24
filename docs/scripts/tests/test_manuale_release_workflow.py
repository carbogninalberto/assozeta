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
    def test_installable_images_require_complete_current_go_inventory(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/publish-images.yml').read_text())['jobs']
        self.assertEqual(jobs['go-quality']['uses'], './.github/workflows/go-quality.yml')
        self.assertIn('go-quality', jobs['publish']['needs'])
        quality = yaml.safe_load((ROOT / '.github/workflows/go-quality.yml').read_text())['jobs']
        inventory_gate = next(step for step in quality['go-inventory']['steps']
                              if step.get('run') == 'python3 -m TESTS.inventory.release_gate')
        self.assertEqual(inventory_gate['if'], "github.event_name == 'release'")
        self.assertFalse(inventory_gate.get('continue-on-error', False))
        # The foundation job keeps its id and name as a gate over the split jobs.
        gate = quality['golang']
        self.assertEqual(gate['name'], 'Go foundation and inventory')
        self.assertEqual(gate['if'], 'always()')
        self.assertEqual(sorted(gate['needs']), sorted([
            'go-inventory', 'go-race', 'go-django-contracts', 'go-manual-contracts',
            'go-logo-storage', 'go-runtime-adapters', 'go-background-tasks',
            'go-model-hook-coverage', 'go-image']))
        self.assertFalse([step for step in gate['steps'] if 'uses' in step])

    def test_release_images_bundle_a_fresh_or_carried_manual(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/publish-images.yml').read_text())['jobs']
        # The slow manual build runs only on an explicit manual dispatch.
        gate = jobs['manual-release']
        self.assertEqual(gate['uses'], './.github/workflows/manuale-release.yml')
        self.assertEqual(gate['if'], "github.event_name == 'workflow_dispatch' && inputs.include_manual")
        # Release images are built once as the candidate (Phase 7) and promoted by digest after
        # qualification; commit images use the same build steps. Both carry the manual.
        builder = jobs['candidate-images']
        self.assertEqual(jobs['branch-images']['steps'], builder['steps'])
        for name in ('candidate-images', 'publish', 'branch-images'):
            self.assertIn('manual-release', jobs[name]['needs'])
            self.assertIn("needs.manual-release.result == 'success'", jobs[name]['if'])
        steps = builder['steps']
        download = next(step for step in steps if step.get('uses', '').startswith('actions/download-artifact@'))
        self.assertEqual(download['with']['name'], 'manuale-release-runtime')
        self.assertEqual(download['with']['path'], '${{ matrix.context }}/release-manual')
        self.assertIn("needs.manual-release.result == 'success'", download['if'])
        for backend, context in (('backend', './BE'), ('backend-go', './BE_GO')):
            self.assertIn("matrix.name == '" + backend + "'", download['if'])
            entry = next(item for item in builder['strategy']['matrix']['include']
                         if item['name'] == backend)
            self.assertEqual(entry['context'], context)
        carried = next(step for step in steps if step.get('id') == 'carried')
        self.assertIn("needs.manual-release.result != 'success'", carried['if'])
        self.assertIn("env.RELEASE_TAG != ''", carried['if'])
        # Each backend image carries the manual of its own previous image (Phase 6 B4).
        self.assertEqual(carried['env']['SOURCE_IMAGE'], 'ghcr.io/${{ github.repository_owner }}/${{ matrix.image }}:latest')
        for backend, image in (('backend', 'assozeta-backend'), ('backend-go', 'assozeta-backend-go')):
            entry = next(item for item in builder['strategy']['matrix']['include'] if item['name'] == backend)
            self.assertEqual(entry['image'], image)
        for backend in ('backend', 'backend-go'):
            self.assertIn("matrix.name == '" + backend + "'", carried['if'])
        self.assertEqual(carried['env']['DESTINATION'], '${{ matrix.context }}/release-manual')
        self.assertIn('"$DESTINATION"', carried['run'])
        build = next(step for step in steps if step.get('uses', '').startswith('docker/build-push-action@'))
        args = build['with']['build-args']
        self.assertIn("REQUIRE_MANUAL=${{ needs.manual-release.result == 'success' || steps.carried.outputs.origin != '' }}", args)
        self.assertIn('MANUAL_CARRIED_FROM=${{ steps.carried.outputs.origin }}', args)
        self.assertIn('VERSION=${{ env.RELEASE_TAG || github.sha }}', args)
        # Assets attach after the public images, whether or not a manual was rebuilt.
        self.assertEqual(jobs['release-bundle']['needs'], ['publish', 'verify-release-images'])
        self.assertIn("needs.verify-release-images.result == 'success'", jobs['release-bundle']['if'])

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
        self.assertEqual(rehearsal['env']['PART'], '${{ matrix.part }}')
        self.assertIn('--part "$PART"', rehearsal['run'])

    def test_the_full_appliance_splits_into_parallel_parts_merged_for_the_gate(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/manuale-verify.yml').read_text())['jobs']
        appliance = jobs['appliance']
        self.assertEqual(appliance['strategy']['fail-fast'], False)
        self.assertEqual(appliance['strategy']['matrix']['part'],
                         "${{ fromJSON(needs.verify.outputs.appliance_scope == 'full' && '[\"current\",\"historical\"]' "
                         "|| '[\"all\"]') }}")
        # Each part uploads under its own name; the compatibility run keeps the
        # final name so nothing downstream changes for existing callers.
        uploads = [step for step in appliance['steps'] if step.get('uses', '').startswith('actions/upload-artifact')]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0]['with']['name'],
                         "${{ needs.verify.outputs.appliance_scope == 'full' && "
                         "format('manuale-release-evidence-{0}', matrix.part) || 'manuale-release-evidence' }}")
        self.assertIn('quality-reports/manual-release/', uploads[0]['with']['path'])
        self.assertIn('!quality-reports/manual-release/private-execution.log', uploads[0]['with']['path'])
        # The merge job runs only for the split full run and republishes exactly
        # the artifact name the gate already downloads.
        merge = jobs['appliance-merge']
        # Its `if` reads verify outputs, so verify must be a direct dependency.
        self.assertEqual(sorted(merge['needs']), ['appliance', 'verify'])
        self.assertIn("needs.verify.outputs.appliance_required == 'true'", merge['if'])
        self.assertIn("needs.verify.outputs.appliance_scope == 'full'", merge['if'])
        downloads = [step for step in merge['steps']
                     if step.get('uses', '').startswith('actions/download-artifact')]
        self.assertEqual([step['with']['name'] for step in downloads],
                         ['manuale-release-evidence-current', 'manuale-release-evidence-historical'])
        command = next(step for step in merge['steps'] if 'manual-release-merge.py' in step.get('run', ''))['run']
        for argument in ('--current "$RUNNER_TEMP/manuale-release-current"',
                         '--historical "$RUNNER_TEMP/manuale-release-historical"',
                         '--output quality-reports/manual-release'):
            self.assertIn(argument, command)
        published = next(step for step in merge['steps']
                         if step.get('uses', '').startswith('actions/upload-artifact'))
        self.assertEqual(published['with']['name'], 'manuale-release-evidence')
        self.assertIn('!quality-reports/manual-release/private-execution.log', published['with']['path'])
        self.assertEqual(published['with']['if-no-files-found'], 'error')
        self.assertIn('appliance-merge', jobs['gate']['needs'])

    def test_failed_manual_gate_cannot_allow_release_publication(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/publish-images.yml').read_text())['jobs']
        # Releases publish through `publish` (qualified digests), commit images through `branch-images`.
        guards = {name: jobs[name]['if'].strip().removeprefix('${{').removesuffix('}}').strip()
                  for name in ('publish', 'branch-images')}
        results = ('success', 'failure', 'cancelled', 'skipped')
        for event, manual, quality, target, go_quality, qualification, include, tag, cancelled in itertools.product(
                ('release', 'workflow_dispatch', 'push'), results, results, results, results, results,
                (True, False), ('', 'v1.0.0'), (True, False)):
            if event != 'workflow_dispatch' and (include or tag):
                continue  # inputs exist only on manual dispatch
            values = {'validate': 'success', 'production-smoke': 'success', 'release-target': target,
                      'manual-release': manual, 'selfhost-quality': quality, 'go-quality': go_quality,
                      'release-qualification': qualification}
            release_mode = event == 'release' or bool(tag)
            requested = event == 'workflow_dispatch' and include
            manual_ok = manual == 'success' or not requested and manual == 'skipped'
            expected = {
                'publish': not cancelled and qualification == 'success' and go_quality == 'success'
                           and target == 'success' and manual_ok and quality == 'success',
                'branch-images': not cancelled and not release_mode and qualification == 'skipped'
                                 and target == 'skipped' and go_quality == 'success' and manual_ok
                                 and (quality == 'success' or event == 'push' and quality == 'skipped')}
            for job, expression in guards.items():
                with self.subTest(job=job, event=event, manual=manual, quality=quality, target=target,
                                  go_quality=go_quality, qualification=qualification, include=include, tag=tag,
                                  cancelled=cancelled):
                    source = re.sub(r'needs\.([a-z-]+)\.result', lambda match: repr(values[match[1]]), expression)
                    source = source.replace('github.event_name', repr(event)).replace('github.ref_type', repr('branch'))
                    source = source.replace('inputs.include_manual', repr(include)).replace('inputs.release_tag', repr(tag))
                    source = source.replace('!cancelled()', repr(not cancelled)).replace('!=', ' NE ')
                    source = source.replace('!', ' not ').replace(' NE ', '!=')
                    source = source.replace('&&', ' and ').replace('||', ' or ')
                    tree = ast.parse('(' + source + ')', mode='eval')
                    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq, ast.NotEq,
                               ast.UnaryOp, ast.Not, ast.Constant)
                    self.assertTrue(all(isinstance(node, allowed) for node in ast.walk(tree)))
                    permitted = eval(compile(tree, '<publication guard>', 'eval'), {'__builtins__': {}})
                    self.assertEqual(bool(permitted), expected[job])
                    if requested and manual != 'success':
                        self.assertFalse(permitted, 'a requested manual must succeed')
                    if release_mode and job == 'publish' and qualification != 'success':
                        self.assertFalse(permitted, 'a release publishes only a qualified candidate')
                    if release_mode and target != 'success':
                        self.assertFalse(permitted, 'a release must run from its tag')

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


class DistributedCaptureWorkflowTests(unittest.TestCase):
    """Opt-in distributed capture; the default run keeps the single-runner path."""

    def setUp(self):
        self.shared = yaml.safe_load((ROOT / '.github/workflows/manuale-verify.yml').read_text())
        self.jobs = self.shared['jobs']

    def steps(self, job):
        return self.jobs[job]['steps']

    def commands(self, job):
        return '\n'.join(step.get('run', '') for step in self.steps(job))

    def test_distribution_requires_a_full_run_and_an_explicit_runner_count(self):
        plan = self.jobs['capture-plan']
        self.assertEqual(plan['if'], "${{ inputs.force_full && (vars.MANUALE_CAPTURE_RUNNERS || '1') != '1' }}")
        self.assertEqual(plan['runs-on'], 'ubuntu-24.04')
        self.assertEqual(plan['timeout-minutes'], 30)
        self.assertEqual(set(plan['outputs']), {'shard_count', 'matrix', 'capture_id', 'manual_revision'})
        self.assertEqual(plan['outputs']['manual_revision'], '${{ steps.pin.outputs.manual_revision }}')
        for output in ('shard_count', 'matrix', 'capture_id'):
            self.assertEqual(plan['outputs'][output], '${{ steps.transport.outputs.%s }}' % output)
        commands = self.commands('capture-plan')
        self.assertIn('manuale-ci.py pin', commands)
        self.assertIn('--force-full', commands)
        self.assertIn('manuale-ci.py transport', commands)
        self.assertIn('--runners "$RUNNERS"', commands)
        # The plan never starts services and never merges, captures or verifies.
        for phase in ('capture', 'compile', 'render', 'runtime', 'package', 'baseline'):
            self.assertNotIn('--phase ' + phase, commands)
        # An invalid runner count fails with an explicit message.
        self.assertIn('MANUALE_CAPTURE_RUNNERS must be an integer from 2 to 6', commands)
        upload = next(step for step in self.steps('capture-plan')
                      if step.get('with', {}).get('name') == 'manuale-capture-transport')
        self.assertEqual(upload['with']['path'], '${{ runner.temp }}/manuale-transport/transport.json')
        self.assertEqual(upload['with']['retention-days'], 3)
        self.assertIs(upload['with']['overwrite'], True)
        cleanup = [step for step in self.steps('capture-plan') if step.get('if') == 'always()']
        self.assertEqual(len(cleanup), 1)
        self.assertIn('--phase cleanup', cleanup[0]['run'])

    def test_one_job_per_shard_captures_exports_and_cleans_up_itself(self):
        capture = self.jobs['capture']
        self.assertEqual(capture['needs'], 'capture-plan')
        self.assertEqual(capture['if'],
                         "${{ needs.capture-plan.result == 'success' && needs.capture-plan.outputs.shard_count != '0' }}")
        self.assertEqual(capture['timeout-minutes'], 90)
        self.assertEqual(capture['strategy']['fail-fast'], True)
        self.assertEqual(capture['strategy']['matrix'], '${{ fromJSON(needs.capture-plan.outputs.matrix) }}')
        commands = self.commands('capture')
        self.assertIn('manuale-ci.py pin', commands)
        self.assertIn('manuale.py doctor', commands)
        download = next(step for step in self.steps('capture')
                        if step.get('with', {}).get('name') == 'manuale-capture-transport')
        self.assertEqual(download['with']['path'], '${{ runner.temp }}/manuale-transport')
        self.assertIn('capture-shard', commands)
        self.assertIn('--shard ${{ matrix.index }}', commands)
        self.assertIn('--output "$RUNNER_TEMP/manuale-shard"', commands)
        self.assertIn('capture-export', commands)
        self.assertIn('--output "$RUNNER_TEMP/manuale-shard-export"', commands)
        uploads = [step for step in self.steps('capture') if step.get('uses', '').startswith('actions/upload-artifact')]
        shard = next(step for step in uploads if step['with']['name'] == '${{ matrix.artifact }}')
        self.assertEqual(shard['with']['path'], '${{ runner.temp }}/manuale-shard-export/')
        self.assertEqual(shard['with']['if-no-files-found'], 'error')
        self.assertEqual(shard['with']['retention-days'], 3)
        self.assertNotIn('if', shard, 'a failed shard must never upload capture results')
        for step in uploads:
            paths = json.dumps(step['with']['path'])
            for private in ('run.json', 'browser-input', '.env', 'execution.log'):
                self.assertNotIn(private, paths, step['with']['name'])
        diagnostics = next(step for step in uploads if step['with']['name'].startswith('manuale-capture-diag-'))
        self.assertIn('${{ needs.capture-plan.outputs.capture_id }}', diagnostics['with']['name'])
        self.assertIn('${{ matrix.index }}', diagnostics['with']['name'])
        self.assertIn('${{ github.run_attempt }}', diagnostics['with']['name'])
        self.assertEqual(diagnostics['if'], 'always()')
        self.assertEqual(diagnostics['with']['retention-days'], 14)
        self.assertIn('manuale-diagnostics.py', self.commands('capture'))
        cleanup = [step for step in self.steps('capture') if step.get('name') == "Remove only this run's fixture services"]
        self.assertEqual(cleanup[0]['if'], 'always()')
        self.assertIn('--phase cleanup', cleanup[0]['run'])

    def test_the_verify_job_merges_shards_and_keeps_every_later_phase(self):
        verify = self.jobs['verify']
        self.assertEqual(verify['needs'], ['capture-plan', 'capture'])
        self.assertIn('!cancelled()', verify['if'])
        self.assertIn("fromJSON('[\"success\",\"skipped\"]'), needs.capture-plan.result", verify['if'])
        self.assertIn("fromJSON('[\"success\",\"skipped\"]'), needs.capture.result", verify['if'])
        detect = next(step for step in self.steps('verify') if step.get('name') == 'Detect a distributed capture plan')
        self.assertEqual(detect['env']['SHARD_COUNT'], '${{ needs.capture-plan.outputs.shard_count }}')
        self.assertIn('DISTRIBUTED=true', detect['run'])
        transport = next(step for step in self.steps('verify')
                         if step.get('with', {}).get('name') == 'manuale-capture-transport')
        self.assertEqual(transport['if'], "${{ env.DISTRIBUTED == 'true' }}")
        shards = next(step for step in self.steps('verify')
                      if 'pattern' in step.get('with', {}))
        self.assertEqual(shards['with']['pattern'], 'manuale-capture-${{ needs.capture-plan.outputs.capture_id }}-s*')
        self.assertEqual(shards['with']['path'], '${{ runner.temp }}/manuale-shards')
        self.assertIs(shards['with']['merge-multiple'], False)
        legacy = next(step for step in self.steps('verify')
                      if step.get('name') == 'Capture only workflows requiring fresh evidence')
        self.assertEqual(legacy['run'], 'python3 docs/scripts/manuale-ci.py phase --run "$RUNNER_TEMP/manuale-run" --phase capture')
        self.assertEqual(legacy['if'], "${{ env.DISTRIBUTED != 'true' }}")
        merge = next(step for step in self.steps('verify')
                     if step.get('name') == 'Merge the distributed capture shards')
        self.assertIn('--phase capture', merge['run'])
        self.assertIn('--transport "$RUNNER_TEMP/manuale-transport/transport.json"', merge['run'])
        self.assertIn('--shards "$RUNNER_TEMP/manuale-shards"', merge['run'])
        commands = self.commands('verify')
        for phase in ('capture', 'compile', 'render', 'runtime', 'package', 'baseline'):
            self.assertIn('--phase ' + phase, commands)
        # Planning-only evidence is skipped: a distributed run is always fresh.
        names = [step.get('name', '') for step in self.steps('verify')]
        baseline = self.steps('verify')[names.index('Admit a successful trusted baseline')]
        self.assertEqual(baseline['if'], '${{ !inputs.force_full }}')
        store = self.steps('verify')[names.index(
            'Restore trusted per-workflow evidence (manifests first, screenshots only when reusable)')]
        self.assertEqual(store['if'], '${{ !inputs.force_full }}')

    def test_the_gate_waits_for_the_distributed_jobs(self):
        self.assertIn('capture-plan', self.jobs['gate']['needs'])
        self.assertIn('capture', self.jobs['gate']['needs'])
        self.assertIn('verify', self.jobs['gate']['needs'])

    def test_existing_callers_need_no_new_input(self):
        inputs = self.shared[True]['workflow_call']['inputs']
        main = yaml.safe_load((ROOT / '.github/workflows/manuale.yml').read_text())
        release = yaml.safe_load((ROOT / '.github/workflows/manuale-release.yml').read_text())
        for call in (main['jobs']['verify']['with'], release['jobs']['bundle']['with']):
            self.assertLessEqual(set(call), set(inputs))
        outputs = self.shared[True]['workflow_call']['outputs']
        self.assertEqual(set(outputs), {'mode', 'appliance_scope', 'authorized'})
        self.assertEqual(outputs['authorized']['value'], '${{ jobs.gate.outputs.authorized }}')


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
