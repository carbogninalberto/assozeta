"""Explicit offline unit fixtures, not live browser reports or production proof.

Use actual temporary Git repositories and complete generated PNG fixture bytes
so dependency, provenance and staging outcomes are exercised without a backend.
"""
import copy
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'BE'))
from application.manuale import reuse
from application.manuale import index as manual_index


def write(root, relative, value):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value)


def command(root, *arguments):
    return subprocess.check_output(['git', '-C', str(root), *arguments], stderr=subprocess.DEVNULL).decode().strip()


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    pixels = (b'\0' + b'\xd8\xe3\xed' * 1920) * 1080
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1920, 1080, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


class ReuseFixtureTests(unittest.TestCase):
    def test_cached_commit_does_not_hide_live_worktree_changes(self):
        relative = 'BE/members/service.py'
        reuse.verify_snapshot(self.code, self.state['application_input'], [relative])
        reuse.verify_snapshot(self.code, self.state['application_input'], [relative])
        write(self.code, relative, 'def service(): return [999]\n')
        with self.assertRaisesRegex(ValueError, 'committed-snapshot-input-changed'):
            reuse.verify_snapshot(self.code, self.state['application_input'], [relative])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.old_run, self.run = self.root / 'baseline', self.root / 'target'
        self.old_code, self.code = self.old_run / 'application', self.run / 'application'
        self.old_manual, self.manual = self.old_run / 'manual', self.run / 'manual'
        self.old_code.mkdir(parents=True)
        self.code.mkdir(parents=True)
        self.recipe = {'id': 'unit-members', 'version': 1,
                       'script': 'selfhost/tests/browser/manuale/unit-members.mjs',
                       'pages': ['docs/members.mdx'], 'fixture': 'baseline',
                       'dependencies': ['BE/members/', 'UI/src/members/', 'docs/manuale/unit-recipes.mjs'],
                       'capture_prefix': 'images/unit-members/'}
        registry = {'format': 1, 'shared_dependencies': ['BE/core/', 'UI/src/utils/'], 'recipes': [self.recipe]}
        for relative in reuse.CORE_TOOLING:
            write(self.old_code, relative, '// Offline dependency fixture\n' if relative.endswith('.mjs') else '# Offline dependency fixture\n')
        write(self.old_code, 'BE/application/management/commands/seed_manuale.py', 'FIXTURE_VERSION = 8\n')
        write(self.old_code, self.recipe['script'], "import './unit-helper.mjs';\n")
        write(self.old_code, 'selfhost/tests/browser/manuale/unit-helper.mjs', 'export const fixture = true;\n')
        write(self.old_code, 'docs/manuale/unit-recipes.mjs', 'export const unitFixture = true;\n')
        write(self.old_code, 'docs/manuale/recipes.json', registry)
        write(self.old_code, 'BE/members/views.py', 'def members(): return []\n')
        write(self.old_code, 'BE/members/service.py', 'def service(): return []\n')
        write(self.old_code, 'BE/core/access.py', 'def allowed(): return True\n')
        write(self.old_code, 'UI/src/members/List.svelte', '<p>Unit fixture list</p>\n')
        write(self.old_code, 'UI/src/utils/access.js', 'export const allowed = true;\n')
        write(self.old_code, 'selfhost/tests/browser/package-lock.json', {'name': 'unit-fixture-lock'})
        command(self.old_code, 'init', '-q')
        command(self.old_code, 'config', 'user.email', 'fixture@example.invalid')
        command(self.old_code, 'config', 'user.name', 'Offline Unit Fixture')
        command(self.old_code, 'add', '.')
        command(self.old_code, 'commit', '-qm', 'Explicit unit dependency fixture')
        self.revision = command(self.old_code, 'rev-parse', 'HEAD')
        subprocess.run(['git', 'clone', '-q', str(self.old_code), str(self.code)], check=True)
        command(self.code, 'config', 'user.email', 'fixture@example.invalid')
        command(self.code, 'config', 'user.name', 'Offline Unit Fixture')
        write(self.code, 'unrelated.md', 'A separate revision with identical recipe dependencies.\n')
        command(self.code, 'add', '.')
        command(self.code, 'commit', '-qm', 'Unrelated target revision')
        target_revision = command(self.code, 'rev-parse', 'HEAD')
        tooling_paths = reuse.closure(self.old_code, self.recipe)
        tooling = {relative: reuse.file_hash(self.old_code, relative) for relative in sorted(tooling_paths)}
        self.old = {'run': str(self.old_run), 'application': str(self.old_code), 'manual': str(self.old_manual),
                    'run_id': 'unit-origin', 'capture_id': 'unit-capture', 'status': 'indexed',
                    'application_input': {'revision': self.revision, 'uncommitted_files': {}},
                    'release': 'unit-origin-release', 'reference_date': '2026-09-30', 'tooling_hashes': tooling,
                    'selected_recipes': ['unit-members']}
        self.state = {**self.old, 'run': str(self.run), 'application': str(self.code), 'manual': str(self.manual),
                      'run_id': 'unit-target', 'capture_id': 'unit-fresh-capture', 'release': 'unit-target-release',
                      'application_input': {'revision': target_revision, 'uncommitted_files': {}}, 'selected_recipes': []}
        image = png()
        relative = 'images/unit-members/1.png'
        master = {'path': 'masters/' + relative, 'sha256': reuse.digest(image), 'width': 1920, 'height': 1080}
        frame = {'path': relative, 'sha256': reuse.digest(image), 'width': 1920, 'height': 1080,
                 'checkpoint': 'unit-list', 'master': master}
        for destination in ('captures/' + relative, master['path']):
            path = self.old_run / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image)
        self.report = {'id': 'unit-members', 'status': 'passed', 'backend': 'real',
                       'application_revision': self.revision, 'capture_id': 'unit-capture', 'tooling_hashes': tooling,
                       'source_hashes': {relative: reuse.file_hash(self.old_code, relative) for relative in ('BE/members/views.py', 'UI/src/members/List.svelte')},
                       'fixture_version': 8, 'fixture_profile': 'baseline', 'reference_date': '2026-09-30',
                       'capture_format': 'full-hd-v1', 'viewport': {'width': 1920, 'height': 1080},
                       'device_scale_factor': 1, 'locale': 'it-IT', 'timezone': 'Europe/Rome', 'theme': 'light',
                       'checks': ['Explicit offline unit fixture assertion'], 'screenshots': [frame]}
        write(self.old_run, 'unit-members.json', self.report)
        report_hash = reuse.file_hash(self.old_run, 'unit-members.json')
        self.editorial = '## Elenco\n\nConsulta il riepilogo.'
        self.published = self.editorial + '\n\n<Frame>![Unit fixture](/images/unit-members/1.png)</Frame>'
        module_hash = reuse.file_hash(self.old_code, 'docs/manuale/unit-recipes.mjs')
        self.binding = {'title': 'Elenco', 'content_sha256': reuse.digest(self.editorial),
                        'recipe_module': 'docs/manuale/unit-recipes.mjs', 'recipe_sha256': module_hash}
        published_binding = {**self.binding, 'editorial_content_sha256': reuse.digest(self.editorial),
                             'content_sha256': reuse.digest(self.published),
                             'materialization': {'format': 1, 'capture_id': 'unit-capture', 'scenario_id': 'unit-members', 'report_sha256': report_hash}}
        self.manifest = {'metadata': {'application_revision': self.revision, 'release': self.old['release']},
                         'scenarios': [{'id': 'unit-members', 'status': 'passed', 'application_revision': self.revision,
                                        'source_hashes': self.report['source_hashes'], 'report_path': 'unit-members.json', 'report_sha256': report_hash}],
                         'pages': [{'path': 'docs/members.mdx', 'sections': [{'id': 'elenco', 'status': 'verified',
                                    'content_sha256': reuse.digest(self.published), 'scenario_ids': ['unit-members']}]}]}
        write(self.old_run, 'run.json', self.old)
        write(self.old_run, 'manifest.json', self.manifest)
        for manual, body, binding in ((self.old_manual, self.published, published_binding), (self.manual, self.editorial, self.binding)):
            write(manual, 'docs/members.mdx', body)
            write(manual, '.manuale-evidence.json', {'format': 1, 'purpose': 'reviewed-content', 'verified': False,
                                                    'sections': {'docs/members.mdx#elenco': binding}})

    def rejection(self, expected):
        plan = reuse.build_reuse_plan(self.state, self.old_run, stage=False)
        self.assertEqual(plan['reused_recipes'], [])
        self.assertIn(expected, plan['rejected'][0]['reasons'][0])

    def test_new_revision_reuses_immutable_original_bytes_and_relocates_without_baseline(self):
        original = (self.old_run / 'unit-members.json').read_bytes()
        plan = reuse.build_reuse_plan(self.state, self.old_run)
        self.assertEqual(plan['reused_recipes'], ['unit-members'])
        self.assertEqual((self.run / 'unit-members.json').read_bytes(), original)
        self.assertNotEqual(plan['target']['application_revision'], self.report['application_revision'])
        self.assertEqual(plan['recipes']['unit-members']['origin']['capture_id'], 'unit-capture')
        self.old_run.rename(self.root / 'baseline-unmounted')
        self.assertEqual(reuse.validate_reused_report(self.state, self.report)['report_sha256'], reuse.digest(original))
        self.state['evidence_reuse'] = {'path': 'evidence-reuse.json', 'sha256': reuse.file_hash(self.run, 'evidence-reuse.json')}
        write(self.run, 'run.json', self.state)
        result = subprocess.run([sys.executable, str(ROOT / 'BE/application/manuale/reuse.py'), '--validate', str(self.run)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['reused_recipes'], ['unit-members'])

    def test_materialized_editorial_binding_remains_valid_after_generator(self):
        plan = reuse.build_reuse_plan(self.state, self.old_run)
        entry = plan['recipes']['unit-members']['sections'][0]
        write(self.manual, 'docs/members.mdx', self.published)
        published = {**self.binding, 'editorial_content_sha256': entry['editorial_content_sha256'],
                     'content_sha256': entry['published_content_sha256'], 'materialization': entry['materialization']}
        write(self.manual, '.manuale-evidence.json', {'format': 1, 'purpose': 'reviewed-content', 'verified': False, 'sections': {'docs/members.mdx#elenco': published}})
        reuse.validate_reuse_plan(self.state)
        published['materialization']['capture_id'] = 'forged-new-capture'
        write(self.manual, '.manuale-evidence.json', {'format': 1, 'purpose': 'reviewed-content', 'verified': False, 'sections': {'docs/members.mdx#elenco': published}})
        with self.assertRaisesRegex(ValueError, 'capture-provenance'):
            reuse.validate_reuse_plan(self.state)

    def test_full_production_closure_rejects_changed_unquoted_service_and_added_deleted_files(self):
        service = self.code / 'BE/members/service.py'
        original = service.read_text()
        service.write_text('def service(): return [1]\n')
        self.state['application_input']['uncommitted_files']['BE/members/service.py'] = reuse.file_hash(self.code, 'BE/members/service.py')
        self.rejection('dependency-bytes-changed')
        service.write_text(original)
        self.state['application_input']['uncommitted_files'] = {}
        service.unlink()
        self.rejection('added-or-deleted')
        service.write_text(original)
        write(self.code, 'BE/members/new.py', 'def added(): pass\n')
        self.rejection('added-or-deleted')

    def test_retained_snapshot_drift_is_rejected_even_when_report_omits_file(self):
        write(self.old_code, 'BE/members/service.py', 'def service(): return [99]\n')
        self.rejection('committed-snapshot-input-changed')

    def test_scenario_helper_configuration_and_recipe_contract_changes_reject(self):
        for relative in ('selfhost/tests/browser/manuale/unit-helper.mjs', 'selfhost/tests/browser/playwright.manual.config.mjs', 'selfhost/tests/browser/package-lock.json'):
            with self.subTest(relative=relative):
                path = self.code / relative
                original = path.read_bytes()
                path.write_bytes(original + b'\n// modified unit dependency\n')
                self.state['application_input']['uncommitted_files'][relative] = reuse.file_hash(self.code, relative)
                self.rejection('dependency-bytes-changed')
                path.write_bytes(original)
                self.state['application_input']['uncommitted_files'] = {}
        value = reuse.load(self.code, 'docs/manuale/recipes.json')
        value['recipes'][0]['version'] += 1
        write(self.code, 'docs/manuale/recipes.json', value)
        self.rejection('recipe-version-script-or-dependency-contract-changed')

    def test_exact_reviewed_editorial_text_rejects_new_hash_even_after_editorial_review(self):
        write(self.manual, 'docs/members.mdx', self.editorial + '\nA changed instruction.')
        binding = {**self.binding, 'content_sha256': reuse.digest(self.editorial + '\nA changed instruction.')}
        write(self.manual, '.manuale-evidence.json', {'format': 1, 'purpose': 'reviewed-content', 'verified': False,
                                                   'sections': {'docs/members.mdx#elenco': binding}})
        self.rejection('editorial-content-changed')

    def test_selected_recipe_never_reused_and_original_reports_do_not_get_rewritten(self):
        self.state['selected_recipes'] = ['unit-members']
        plan = reuse.build_reuse_plan(self.state, self.old_run)
        self.assertEqual(plan['fresh_recipes'], ['unit-members'])
        self.assertEqual(plan['recipes'], {})
        self.assertFalse((self.run / 'unit-members.json').exists())

    def test_generated_capture_can_be_reused_without_promoting_failed_render(self):
        self.old['status'] = 'generated'
        write(self.old_run, 'run.json', self.old)
        write(self.old_run, 'render.json', {'status': 'failed'})
        self.state['selected_recipes'] = ['unit-members']
        plan = reuse.reuse_compatible_captures(self.state, self.old_run)
        self.assertEqual(plan['reused_recipes'], ['unit-members'])
        self.assertEqual(self.state['selected_recipes'], [])
        self.assertFalse((self.run / 'render.json').exists())
        self.assertFalse((self.run / 'index.json').exists())
        self.old['status'] = 'failed'
        write(self.old_run, 'run.json', self.old)
        write(self.old_run, 'evaluation.json', {'status': 'failed', 'error': 'timeout'})
        plan = reuse.reuse_compatible_captures(self.state, self.old_run)
        self.assertEqual(plan['reused_recipes'], ['unit-members'])
        self.assertFalse((self.run / 'evaluation.json').exists())
        self.old['status'] = 'capturing'
        write(self.old_run, 'run.json', self.old)
        with self.assertRaisesRegex(ValueError, 'baseline-run-not-verified'):
            reuse.reuse_compatible_captures(self.state, self.old_run, stage=False)

    def test_full_reuse_recaptures_changed_dependencies(self):
        path = 'BE/members/service.py'
        write(self.code, path, 'def service(): return [1]\n')
        self.state['application_input']['uncommitted_files'][path] = reuse.file_hash(self.code, path)
        plan = reuse.reuse_compatible_captures(self.state, self.old_run)
        self.assertEqual(plan['reused_recipes'], [])
        self.assertEqual(plan['fresh_recipes'], ['unit-members'])
        self.assertIn('dependency-bytes-changed', self.state['selection']['recapture_reasons'][0]['reasons'][0])

    def test_target_plan_report_and_frame_tampering_fail_closed(self):
        plan = reuse.build_reuse_plan(self.state, self.old_run)
        wrong = copy.deepcopy(plan)
        wrong['target']['release'] = 'wrong-release'
        with self.assertRaisesRegex(ValueError, 'target-mismatch'):
            reuse.validate_reuse_plan(self.state, wrong)
        path = self.run / 'unit-members.json'
        original = path.read_bytes()
        path.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'report-bytes'):
            reuse.validate_reuse_plan(self.state)
        path.write_bytes(original)
        image = self.run / 'captures/images/unit-members/1.png'
        image.write_bytes(image.read_bytes() + b'tampered')
        with self.assertRaisesRegex(ValueError, 'bytes or dimensions'):
            reuse.validate_reuse_plan(self.state)

    def test_report_settings_and_unsafe_paths_are_rejected(self):
        origin = {'application_revision': self.revision, 'capture_id': 'unit-capture'}
        for key, changed in (('fixture_version', 7), ('reference_date', '2025-01-01'), ('fixture_profile', 'other'), ('device_scale_factor', 2), ('backend', 'mock')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                report = {**self.report, key: changed}
                reuse.check_report(self.old, self.recipe, report, origin)
        for relative in ('../outside', '/absolute', 'images/../outside'):
            with self.subTest(path=relative), self.assertRaises(ValueError):
                reuse.owned(self.run, relative)
        (self.run / 'link').symlink_to(self.old_run)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            reuse.owned(self.run, 'link/unit-members.json')

    def test_index_retains_origin_and_current_version_gate_and_filters_uncited_service_drift(self):
        plan = reuse.build_reuse_plan(self.state, self.old_run)
        entry = plan['recipes']['unit-members']
        section = entry['sections'][0]
        self.state['manual_input'] = {'revision': 'b' * 40}
        pointer = {'path': 'evidence-reuse.json', 'sha256': reuse.file_hash(self.run, 'evidence-reuse.json'),
                   'reused_recipes': plan['reused_recipes']}
        self.state['evidence_reuse'] = pointer
        write(self.run, 'run.json', self.state)
        write(self.manual, 'docs/members.mdx', self.published)
        binding = {**self.binding, 'content_sha256': section['published_content_sha256'],
                   'editorial_content_sha256': section['editorial_content_sha256'], 'materialization': section['materialization']}
        write(self.manual, '.manuale-evidence.json', {'format': 1, 'purpose': 'reviewed-content', 'verified': False,
                                                   'sections': {'docs/members.mdx#elenco': binding}})
        image = self.manual / 'images/unit-members/1.png'
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes((self.run / 'captures/images/unit-members/1.png').read_bytes())
        evidence = {'path': 'BE/members/views.py', 'symbol': 'members', 'start': 1, 'end': 1,
                    'sha256': self.report['source_hashes']['BE/members/views.py']}
        scenario = {**self.manifest['scenarios'][0], 'capture_id': self.report['capture_id'],
                    'reuse': {'path': pointer['path'], 'sha256': pointer['sha256'], 'recipe_id': 'unit-members'}}
        metadata = {'application_revision': self.state['application_input']['revision'],
                    'manual_revision': self.state['manual_input']['revision'], 'release': self.state['release'],
                    'manual_url': 'https://unit-manual.example.invalid', 'reference_date': self.state['reference_date'],
                    'tooling_hashes': self.state['tooling_hashes'], 'evidence_reuse': pointer}
        frame = {**self.report['screenshots'][0], 'caption': 'Editorial caption without rewriting the report'}
        manifest = {'metadata': metadata, 'scenarios': [scenario], 'pages': [{'path': 'docs/members.mdx',
                    'sections': [{'id': 'elenco', 'status': 'verified', 'kind': 'workflow',
                                  'content_sha256': reuse.digest(self.published), 'evidence': [evidence],
                                  'scenario_ids': ['unit-members'], 'screenshots': [frame]}]}]}
        corpus = manual_index.build_index(self.manual, self.code, manifest, artifact_root=self.run)
        index = manual_index.ManualIndex(corpus)
        context = {'revision': metadata['application_revision'], 'release': metadata['release'], 'code_root': self.code}
        chunk = index.applicable(**context)[0]
        self.assertEqual(chunk['capture_provenance'][0]['application_revision'], self.revision)
        self.assertEqual(chunk['capture_provenance'][0]['capture_id'], 'unit-capture')
        self.assertTrue(chunk['capture_provenance'][0]['reused'])
        self.assertIn('BE/members/service.py', chunk['runtime_dependencies'])
        with self.assertRaisesRegex(ValueError, 'revision/release'):
            index.applicable(**{**context, 'revision': self.revision})
        write(self.code, 'BE/members/service.py', 'def service(): return [99]\n')
        self.assertEqual(index.applicable(**context), [])
        with self.assertRaisesRegex(ValueError, 'Retained evidence rejected'):
            manual_index.build_index(self.manual, self.code, manifest, artifact_root=self.run)


if __name__ == '__main__':
    unittest.main()
