"""Explicit offline unit fixtures, not live browser reports or production proof.

Use actual temporary Git repositories and complete generated PNG fixture bytes
so dependency, provenance and staging outcomes are exercised without a backend.
"""
import copy
import importlib.util
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
runner_spec = importlib.util.spec_from_file_location('manual_cache_runner', ROOT / 'docs/scripts/manuale.py')
runner = importlib.util.module_from_spec(runner_spec)
runner_spec.loader.exec_module(runner)
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
        # Exercise the actual pure editorial projector, with an explicit unit
        # procedure. These files do not execute browsers or fabricate captures.
        for relative in ('docs/manuale/manual-content.mjs', 'docs/manuale/rendered-content.mjs', 'docs/scripts/manuale-project.mjs'):
            write(self.old_code, relative, (ROOT / relative).read_text())
        write(self.old_code, 'docs/manuale/authored-workflows.mjs', 'export const authoredWorkflows = ' + json.dumps({
            'unit-members': {'checkpoints': [{'id': 'unit-list', 'caption': 'Unit fixture'}], 'sections': [
                {'path': 'docs/members.mdx', 'id': 'elenco', 'title': 'Elenco', 'checkpoints': ['unit-list']}]}}) + ';\n')
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

    def test_report_accepts_empty_descriptive_check_labels(self):
        report = {**self.report, 'checks': []}
        origin = {'application_revision': self.revision, 'capture_id': 'unit-capture'}
        reuse.check_report(self.old, self.recipe, report, origin)
        for changed in ({'checks': None}, {'status': 'failed'}, {'screenshots': []}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                reuse.check_report(self.old, self.recipe, {**report, **changed}, origin)

    def test_report_requires_a_nonempty_capture_identifier(self):
        for capture_id in (None, '', '   ', 17):
            with self.subTest(capture_id=capture_id):
                report = {**self.report, 'capture_id': capture_id}
                origin = {'application_revision': self.revision, 'capture_id': capture_id}
                with self.assertRaisesRegex(ValueError, 'original-report-provenance-invalid'):
                    reuse.check_report(self.old, self.recipe, report, origin)
        report = {key: value for key, value in self.report.items() if key != 'capture_id'}
        with self.assertRaisesRegex(ValueError, 'original-report-provenance-invalid'):
            reuse.check_report(self.old, self.recipe, report, {'application_revision': self.revision})

    def test_captured_tooling_must_be_sealed_and_byte_identical(self):
        relative = 'selfhost/tests/browser/manuale/unit-helper.mjs'
        report = {**self.report, 'source_hashes': {
            **self.report['source_hashes'], relative: reuse.file_hash(self.old_code, relative)}}
        origin = {'application_revision': self.revision, 'capture_id': 'unit-capture'}
        reuse.check_report(self.old, self.recipe, report, origin)
        unsealed = {**self.old, 'tooling_hashes': {key: value for key, value in self.old['tooling_hashes'].items()
                                                if key != relative}}
        with self.assertRaisesRegex(ValueError, 'captured-source-changed'):
            reuse.check_report(unsealed, self.recipe, report, origin)
        write(self.old_code, relative, 'export const fixture = false;\n')
        with self.assertRaisesRegex(ValueError, 'captured-source-changed'):
            reuse.check_report(self.old, self.recipe, report, origin)

    def test_automatic_cache_finds_a_complete_baseline_and_preserves_capture_bytes(self):
        repository = str(self.root / 'manual-source')
        self.old['manual_repository'] = self.state['manual_repository'] = repository
        self.state['selected_recipes'] = ['unit-members']
        args = SimpleNamespace(command='build', changed_since=None, manual_changed_since=None)
        with patch.object(runner, 'ROOT', self.root), patch.object(runner, 'retained_evidence_helper', return_value=reuse), \
                patch.object(runner, 'execute', side_effect=AssertionError('offline cache started runtime')):
            runner.remember_run(self.old)
            self.assertEqual(len(runner.cached_runs(repository)), 1)
            self.assertEqual(runner.cached_runs(self.root / 'another-manual'), [])
            runner.reuse_inputs(self.state, args)
        self.assertEqual(self.state['selected_recipes'], [])
        self.assertEqual(self.state['evidence_reuse']['reused_recipes'], ['unit-members'])
        self.assertEqual(self.state['cache']['mode'], 'automatic')
        self.assertEqual((self.run / 'unit-members.json').read_bytes(), (self.old_run / 'unit-members.json').read_bytes())

    def test_ci_optional_baseline_failure_explains_fresh_fallback(self):
        self.state['manual_repository'] = str(self.root / 'manual-source')
        self.old['status'] = 'failed'
        write(self.old_run, 'run.json', self.old)
        args = SimpleNamespace(command='run', changed_since=None, manual_changed_since=None,
                               reuse_evidence=str(self.old_run), baseline_optional=True)
        with patch.object(runner, 'retained_evidence_helper', return_value=reuse):
            runner.reuse_inputs(self.state, args)
        self.assertEqual(self.state['selected_recipes'], ['unit-members'])
        self.assertNotIn('evidence_reuse', self.state)
        decision, = self.state['cache']['decisions']
        self.assertEqual(decision['result'], 'unavailable')
        self.assertIn('baseline-run-not-verified', decision['reason'])

    def test_partial_capture_manifest_is_discoverable_without_being_marked_complete(self):
        repository = str(self.root / 'manual-source')
        self.old['manual_repository'] = self.state['manual_repository'] = repository
        with patch.object(runner, 'ROOT', self.root), patch.object(runner, 'recipe_registry', return_value={
                'recipes': [{'id': 'unit-members'}, {'id': 'still-needs-capture'}]}):
            runner.remember_run(self.old)
            entry, = runner.cached_runs(repository)
        self.assertTrue(entry['evidence'])
        self.assertFalse(entry['complete'])
        self.assertFalse((self.old_run / 'index.json').exists())

    def test_staging_does_not_reintroduce_evidence_excluded_by_dependency_selection(self):
        self.assertEqual(reuse.build_reuse_plan(self.state, self.old_run, stage=False)['reused_recipes'], ['unit-members'])
        plan = reuse.build_reuse_plan(self.state, self.old_run, eligible_recipes=[])
        self.assertEqual(plan['reused_recipes'], [])
        self.assertEqual(plan['fresh_recipes'], [])
        self.assertFalse((self.run / 'unit-members.json').exists())
        reuse.validate_reuse_plan(self.state, plan)

    def test_automatic_cache_recaptures_unknown_production_and_shared_orchestration_changes(self):
        repository = str(self.root / 'manual-source')
        self.old['manual_repository'] = self.state['manual_repository'] = repository
        args = SimpleNamespace(command='build', changed_since=None, manual_changed_since=None)
        registry = runner.recipe_registry(self.code)
        registry['shared_dependencies'].append('docs/scripts/manuale')
        for code in (self.code, self.old_code):
            write(code, 'docs/manuale/recipes.json', registry)
        with patch.object(runner, 'ROOT', self.root), patch.object(runner, 'retained_evidence_helper', return_value=reuse):
            runner.remember_run(self.old)
            for relative in ('BE/unregistered_behavior.py', 'docs/scripts/manuale.py'):
                with self.subTest(relative=relative):
                    write(self.code, relative, '# Changed execution input\n')
                    state = {**self.state, 'selected_recipes': []}
                    runner.reuse_inputs(state, args)
                    self.assertEqual(state['selected_recipes'], ['unit-members'])
                    decision = state['cache']['decisions'][0]
                    self.assertIn(relative, decision['changed_inputs'])
                    self.assertEqual(decision['affected_recipes'], ['unit-members'])
                    self.assertEqual(state['evidence_reuse']['reused_recipes'], [])
                    (self.code / relative).unlink()

    def test_missing_cache_makes_incremental_verification_complete_but_keeps_explicit_capture_scope(self):
        self.state['manual_repository'] = str(self.root / 'manual-source')
        args = SimpleNamespace(command='build', changed_since='unit-parent', manual_changed_since=None, no_cache=True)
        with patch.object(runner, 'ROOT', self.root), patch.object(runner, 'retained_evidence_helper', side_effect=AssertionError('cold run read cache')):
            runner.reuse_inputs(self.state, args)
            self.assertEqual(self.state['selected_recipes'], ['unit-members'])
            args.command = 'capture'
            self.state['selected_recipes'] = []
            runner.reuse_inputs(self.state, args)
            self.assertEqual(self.state['selected_recipes'], [])

    def test_frontend_cache_uses_build_inputs_across_commits_and_rejects_modified_artifacts(self):
        for code, state in ((self.old_code, self.old), (self.code, self.state)):
            write(code, '.gitignore', 'UI/dist/\n')
            write(code, 'selfhost/compose.dev.yml', 'services: {}\n')
            override = Path(state['run']) / 'compose.override.json'
            override.write_text(json.dumps({'services': {'ui': {'command': ['vite', 'preview'], 'environment': {'OEM_ENV': 'assozeta'}}}}))
            state['override'] = str(override)
        write(self.old_code, 'UI/dist/public/index.html', '<p>Unit compiled artifact</p>')
        write(self.old_run, 'run.json', self.old)
        write(self.old_run, 'frontend-build.json', {'sources': runner.ui_sources(self.old_code),
            'artifacts': {'index.html': reuse.file_hash(self.old_code, 'UI/dist/public/index.html')}})
        self.state['reuse_frontend'] = str(self.old_run)
        self.assertNotEqual(self.old['application_input']['revision'], self.state['application_input']['revision'])
        result = runner.reuse_frontend(self.state)
        self.assertEqual(result['source_run'], self.old['run_id'])
        target = self.code / 'UI/dist/public/index.html'
        original = target.read_bytes()
        write(self.old_code, 'UI/dist/public/index.html', '<p>Modified after verification</p>')
        with self.assertRaisesRegex(RuntimeError, 'changed after its recorded build'):
            runner.reuse_frontend(self.state)
        self.assertEqual(target.read_bytes(), original)

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

    def wording_review(self, text, *, previous=None, manual=None):
        manual = manual or self.manual
        review = {'format': 1, 'kind': 'wording-only', 'from_sha256': reuse.digest(previous or self.published),
                  'to_sha256': reuse.digest(text), 'recipe_sha256': self.binding['recipe_sha256'],
                  'note': 'Correzione editoriale; comportamento e passaggi invariati.'}
        binding = {**self.binding, 'content_sha256': reuse.digest(text), 'editorial_review': review}
        write(manual, 'docs/members.mdx', text)
        write(manual, '.manuale-evidence.json', {'format': 1, 'purpose': 'reviewed-content', 'verified': False,
                                               'sections': {'docs/members.mdx#elenco': binding}})
        return binding

    def test_wording_review_reuses_original_capture_and_binds_projected_text(self):
        changed = self.published.replace('Consulta il riepilogo.', 'Consulta anche il riepilogo.')
        binding = self.wording_review(changed)
        original = (self.old_run / 'unit-members.json').read_bytes()
        plan = reuse.build_reuse_plan(self.state, self.old_run)
        self.assertEqual(plan['reused_recipes'], ['unit-members'], plan['rejected'])
        item = plan['recipes']['unit-members']['sections'][0]
        self.assertEqual(item['published_content_sha256'], reuse.digest(self.published))
        self.assertEqual(reuse.editorial_target(item), (reuse.digest(changed), reuse.digest(changed)))
        self.assertEqual((self.run / 'unit-members.json').read_bytes(), original)
        self.assertEqual(item['editorial_updates'][0]['review'], binding['editorial_review'])
        # Consume the same retained plan at the actual index boundary after the
        # generator's normal publication binding, without new browser evidence.
        from application.manuale.index import build_index
        self.state['manual_input'] = {'revision': 'unit-manual'}
        self.state['evidence_reuse'] = {'path': 'evidence-reuse.json', 'sha256': reuse.file_hash(self.run, 'evidence-reuse.json'),
                                      'reused_recipes': ['unit-members']}
        write(self.run, 'run.json', self.state)
        binding.update(editorial_content_sha256=reuse.digest(changed), materialization=item['materialization'])
        write(self.manual, '.manuale-evidence.json', {'format': 1, 'purpose': 'reviewed-content', 'verified': False,
                                                    'sections': {'docs/members.mdx#elenco': binding}})
        frame = self.report['screenshots'][0]
        image = self.manual / frame['path']
        image.parent.mkdir(parents=True)
        image.write_bytes((self.old_run / 'captures' / frame['path']).read_bytes())
        manifest = copy.deepcopy(self.manifest)
        manifest['metadata'].update(application_revision=self.state['application_input']['revision'],
            manual_revision='unit-manual', manual_url='https://manual.example.test', release=self.state['release'],
            reference_date=self.state['reference_date'], tooling_hashes=self.state['tooling_hashes'],
            evidence_reuse=self.state['evidence_reuse'])
        manifest['scenarios'][0].update(reuse={'path': 'evidence-reuse.json', 'sha256': self.state['evidence_reuse']['sha256'],
                                              'recipe_id': 'unit-members'}, capture_id=self.report['capture_id'])
        manifest['pages'][0]['sections'][0].update(content_sha256=reuse.digest(changed), kind='workflow', screenshots=[frame],
            evidence=[{'path': 'BE/members/views.py', 'start': 1, 'end': 1, 'symbol': 'members',
                       'sha256': self.report['source_hashes']['BE/members/views.py']}])
        index = build_index(self.manual, self.code, manifest, artifact_root=self.run)
        self.assertIn('Consulta anche', index['chunks'][0]['text'])
        self.assertEqual(index['chunks'][0]['capture_provenance'][0]['capture_id'], self.report['capture_id'])
        self.wording_review(changed + ' Aggiunta non revisionata.', previous=changed)
        with self.assertRaisesRegex(ValueError, 'editorial-content-changed'):
            reuse.validate_reuse_plan(self.state)

    def test_wording_marker_cannot_reuse_changed_steps_links_numbers_or_code(self):
        for extra in ('\n\n<Step title="Nuova azione">Salva.</Step>', '\n\n[Guida](https://example.invalid/other)',
                      '\n\nRipeti 2 volte.', '\n\n`nuovo_comando()`'):
            with self.subTest(extra=extra):
                self.wording_review(self.published + extra)
                self.rejection('editorial-structure-changed')
        self.wording_review(self.published.replace('![Unit fixture]', '[Unit fixture]'))
        self.rejection('editorial-structure-changed')

    def test_supplemental_workflow_reuse_requires_an_explicit_empty_publication_contract(self):
        self.manifest['pages'][0]['sections'] = []
        write(self.old_run, 'manifest.json', self.manifest)
        self.rejection('no-retained-verified-editorial-sections')
        for code, state in ((self.old_code, self.old), (self.code, self.state)):
            registry = reuse.load(code, 'docs/manuale/recipes.json')
            registry['recipes'][0]['publication_sections'] = []
            write(code, 'docs/manuale/recipes.json', registry)
            state['application_input']['uncommitted_files']['docs/manuale/recipes.json'] = reuse.file_hash(code, 'docs/manuale/recipes.json')
        write(self.old_run, 'run.json', self.old)
        plan = reuse.build_reuse_plan(self.state, self.old_run)
        self.assertEqual(plan['reused_recipes'], ['unit-members'], plan['rejected'])
        self.assertEqual(plan['recipes']['unit-members']['sections'], [])
        reuse.validate_reuse_plan(self.state)

    def test_wording_review_preserves_immutable_capture_origin_across_successive_builds(self):
        first_text = self.published.replace('Consulta', 'Consulta anche')
        binding = self.wording_review(first_text)
        first_plan = reuse.build_reuse_plan(self.state, self.old_run)
        entry = first_plan['recipes']['unit-members']
        self.state['evidence_reuse'] = {'path': 'evidence-reuse.json', 'sha256': reuse.file_hash(self.run, 'evidence-reuse.json'),
                                      'reused_recipes': ['unit-members']}
        self.state['status'] = 'generated'
        write(self.run, 'run.json', self.state)
        binding.update(editorial_content_sha256=reuse.digest(first_text), materialization=entry['sections'][0]['materialization'])
        write(self.manual, '.manuale-evidence.json', {'format': 1, 'purpose': 'reviewed-content', 'verified': False,
                                                   'sections': {'docs/members.mdx#elenco': binding}})
        manifest = copy.deepcopy(self.manifest)
        manifest['metadata'].update(application_revision=self.state['application_input']['revision'], release=self.state['release'])
        manifest['scenarios'][0]['reuse'] = {'path': 'evidence-reuse.json', 'sha256': self.state['evidence_reuse']['sha256'], 'recipe_id': 'unit-members'}
        manifest['pages'][0]['sections'][0]['content_sha256'] = reuse.digest(first_text)
        write(self.run, 'manifest.json', manifest)
        third_run, third_manual = self.root / 'third', self.root / 'third/manual'
        third = {**self.state, 'run': str(third_run), 'manual': str(third_manual), 'release': 'unit-third-release'}
        third.pop('evidence_reuse')
        second_text = first_text.replace('anche', 'sempre')
        self.wording_review(second_text, previous=first_text, manual=third_manual)
        plan = reuse.build_reuse_plan(third, self.run)
        self.assertEqual(plan['reused_recipes'], ['unit-members'], plan['rejected'])
        updates = plan['recipes']['unit-members']['sections'][0]['editorial_updates']
        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[-1]['published_content_sha256'], reuse.digest(second_text))
        self.assertEqual(plan['recipes']['unit-members']['origin']['capture_id'], self.report['capture_id'])

    def test_selected_recipe_never_reused_and_original_reports_do_not_get_rewritten(self):
        self.state['selected_recipes'] = ['unit-members']
        plan = reuse.build_reuse_plan(self.state, self.old_run)
        self.assertEqual(plan['fresh_recipes'], ['unit-members'])
        self.assertEqual(plan['recipes'], {})
        self.assertFalse((self.run / 'unit-members.json').exists())

    def test_failed_run_or_failed_verification_cannot_supply_reusable_evidence(self):
        for filename in ('render.json', 'evaluation.json', 'embedded-manual.json', 'release-verification.json'):
            with self.subTest(report=filename):
                self.old['status'] = 'evaluated'
                write(self.old_run, 'run.json', self.old)
                write(self.old_run, filename, {'status': 'failed'})
                with self.assertRaises(ValueError):
                    reuse.reuse_compatible_captures(self.state, self.old_run, stage=False)
                (self.old_run / filename).unlink()
        for status in ('failed', 'capturing'):
            self.old['status'] = status
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
