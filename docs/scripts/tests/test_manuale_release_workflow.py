"""Release graph invariants; the rehearsal separately exercises real artifacts."""
import ast
import itertools
import re
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[3]


class ReleaseWorkflowTests(unittest.TestCase):
    def test_pr_verification_retains_complete_evidence_and_only_default_branch_saves_cache(self):
        steps = yaml.safe_load((ROOT / '.github/workflows/manuale.yml').read_text())['jobs']['verify']['steps']
        restore = next(step for step in steps if step.get('uses', '').startswith('actions/cache/restore@'))
        save = next(step for step in steps if step.get('uses', '').startswith('actions/cache/save@'))
        export = next(step for step in steps if step.get('id') == 'manual-cache-export')
        self.assertEqual(restore['with']['path'], save['with']['path'])
        self.assertEqual(restore['with']['key'], save['with']['key'])
        for step in (save, export):
            self.assertIn("github.ref == format('refs/heads/{0}', github.event.repository.default_branch)", step['if'])
            self.assertNotIn('always()', step['if'])
        self.assertIn("steps.manual-cache-export.outcome == 'success'", save['if'])
        self.assertTrue(restore['continue-on-error'])
        verification = next(step for step in steps if 'BASE_REVISION' in step.get('env', {}))
        self.assertEqual(verification['env']['BASE_REVISION'], '${{ github.event.pull_request.base.sha }}')
        self.assertIn('--changed-since "$BASE_REVISION"', verification['run'])
        self.assertNotIn('--allow-dirty', verification['run'])
    def test_installable_images_require_complete_current_go_inventory(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/publish-images.yml').read_text())['jobs']
        self.assertEqual(jobs['go-quality']['uses'], './.github/workflows/go-quality.yml')
        self.assertIn('go-quality', jobs['publish']['needs'])
        quality = yaml.safe_load((ROOT / '.github/workflows/go-quality.yml').read_text())['jobs']['golang']
        inventory_gate = next(step for step in quality['steps']
                              if step.get('run') == 'python3 -m TESTS.inventory.release_gate')
        self.assertEqual(inventory_gate['if'], "github.event_name == 'release'")
        self.assertFalse(inventory_gate.get('continue-on-error', False))

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

    def test_release_package_uses_exact_release_and_real_rehearsal(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/manuale-release.yml').read_text())['jobs']
        steps = jobs['bundle']['steps']
        runs = '\n'.join(step.get('run', '') for step in steps)
        self.assertEqual(runs.count('docs/scripts/manuale.py verify-release'), 1)
        self.assertIn('--release "$RELEASE_VERSION"', runs)
        self.assertIn('--previous-release 1.0.17', runs)
        self.assertNotIn('manuale-previous-run', runs)
        self.assertNotIn('docs/scripts/manuale.py run', runs)
        self.assertNotIn('--allow-dirty', runs)
        self.assertNotIn('--edit-content', runs)
        upload = next(step for step in steps if step.get('with', {}).get('name') == 'manuale-release-runtime')
        self.assertNotIn('always()', upload.get('if', ''))
        self.assertEqual(upload['with']['if-no-files-found'], 'error')

    def test_failed_manual_gate_cannot_allow_release_publication(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/publish-images.yml').read_text())['jobs']
        expression = jobs['publish']['if'].strip().removeprefix('${{').removesuffix('}}').strip()
        for event, manual, quality, go_quality, cancelled in itertools.product(
                ('release', 'workflow_dispatch', 'push'),
                ('success', 'failure', 'cancelled', 'skipped'),
                ('success', 'failure', 'cancelled', 'skipped'),
                ('success', 'failure', 'cancelled', 'skipped'), (True, False)):
            with self.subTest(event=event, manual=manual, quality=quality, go_quality=go_quality, cancelled=cancelled):
                values = {'validate': 'success', 'production-smoke': 'success',
                          'manual-release': manual, 'selfhost-quality': quality, 'go-quality': go_quality}
                source = re.sub(r'needs\.([a-z-]+)\.result', lambda match: repr(values[match[1]]), expression)
                source = source.replace('github.event_name', repr(event))
                source = source.replace('!cancelled()', repr(not cancelled)).replace('&&', ' and ').replace('||', ' or ')
                tree = ast.parse('(' + source + ')', mode='eval')
                allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq, ast.Constant)
                self.assertTrue(all(isinstance(node, allowed) for node in ast.walk(tree)))
                permitted = eval(compile(tree, '<publication guard>', 'eval'), {'__builtins__': {}})
                expected = not cancelled and go_quality == 'success' and (
                    manual == 'success' or event == 'push' and manual == 'skipped') and (
                    quality == 'success' or event == 'push' and quality == 'skipped')
                self.assertEqual(permitted, expected)
