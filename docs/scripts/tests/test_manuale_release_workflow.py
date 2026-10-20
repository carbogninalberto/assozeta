"""Release graph invariants; the rehearsal separately exercises real artifacts."""
import ast
import itertools
import re
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[3]


class ReleaseWorkflowTests(unittest.TestCase):
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
        self.assertIn('docs/scripts/manuale.py run', runs)
        self.assertIn('--release "$RELEASE_VERSION"', runs)
        self.assertIn('docs/scripts/manuale-release.py --bundle', runs)
        self.assertIn('selfhost/tests/manual-release-smoke.sh', runs)
        self.assertIn('--previous-package', runs)
        self.assertIn('--previous-release', runs)
        self.assertIn('--reuse-evidence', runs)
        self.assertNotIn('--allow-dirty', runs)
        self.assertNotIn('--edit-content', runs)
        upload = next(step for step in steps if step.get('with', {}).get('name') == 'manuale-release-runtime')
        self.assertNotIn('always()', upload.get('if', ''))
        self.assertEqual(upload['with']['if-no-files-found'], 'error')

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
