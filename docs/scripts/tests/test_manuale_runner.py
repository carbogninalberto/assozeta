"""Exercise incremental selection and snapshot integrity using real files/Git."""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'manuale.py'
spec = importlib.util.spec_from_file_location('manuale_runner', SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
scaffold_spec = importlib.util.spec_from_file_location('manuale_runner_scaffold_fixture', SCRIPT.with_name('manuale-scaffold.py'))
scaffold_fixture = importlib.util.module_from_spec(scaffold_spec)
scaffold_spec.loader.exec_module(scaffold_fixture)


class ManualRunnerTests(unittest.TestCase):
    def test_retained_only_capture_keeps_original_bytes_without_new_capture_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            original = b'{"unit_fixture":"not-browser-evidence"}\n'
            (run / 'guide.json').write_bytes(original)
            state = {'run': str(run), 'selected_recipes': [], 'tooling_hashes': {}, 'status': 'prepared'}
            with patch.object(runner, 'validate_retained_evidence', return_value={'reused_recipes': ['guide']}), \
                    patch.object(runner, 'execute') as execute:
                runner.capture(state)
            execute.assert_not_called()
            self.assertEqual(state['capture_mode'], 'retained-only')
            self.assertEqual(state['status'], 'captured')
            self.assertNotIn('capture_id', state)
            self.assertEqual((run / 'guide.json').read_bytes(), original)

    def test_empty_capture_selection_requires_compatible_retained_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = {'run': temporary, 'selected_recipes': [], 'tooling_hashes': {}, 'status': 'prepared'}
            with self.assertRaisesRegex(RuntimeError, 'No fresh captures'):
                runner.capture(state)
            self.assertEqual(state['status'], 'prepared')

    def test_capture_restores_fixture_after_success_and_failure_without_hiding_primary_error(self):
        for scenario_fails, reset_fails in ((False, False), (True, False), (True, True), (False, True)):
            with self.subTest(scenario_fails=scenario_fails, reset_fails=reset_fails), tempfile.TemporaryDirectory() as temporary:
                state = {'run': temporary, 'application': temporary, 'selected_recipes': ['guide'],
                         'tooling_hashes': {}, 'origin': 'http://127.0.0.1:5010', 'reference_date': '2026-09-30'}
                calls = []
                def execute(command, run, label, **kwargs):
                    calls.append(label)
                    if label == 'Verify real application scenario guide' and scenario_fails:
                        raise RuntimeError('primary scenario failure')
                    if label == 'Restore baseline after the capture batch' and reset_fails:
                        raise RuntimeError('fixture reset failure')
                with patch.object(runner, 'validate_retained_evidence', return_value=None), \
                        patch.object(runner, 'recipe_registry', return_value={'recipes': [
                            {'id': 'guide', 'script': 'guide.mjs'}]}), \
                        patch.object(runner, 'management', return_value=['seed-fixture']), \
                        patch.object(runner, 'execute', side_effect=execute):
                    if scenario_fails or reset_fails:
                        with self.assertRaisesRegex(RuntimeError, 'primary scenario failure' if scenario_fails else 'fixture reset failure'):
                            runner.capture(state)
                    else:
                        runner.capture(state)
                        self.assertEqual(state['status'], 'captured')
                self.assertEqual(calls[-1], 'Restore baseline after the capture batch')
                saved = json.loads((Path(temporary) / 'run.json').read_text())
                self.assertEqual(saved['fixture_reset'], 'failed' if reset_fails else 'passed')
                if scenario_fails or reset_fails:
                    self.assertNotEqual(saved['status'], 'captured')

    def test_editor_failure_removes_model_key_before_any_later_subprocess(self):
        with tempfile.TemporaryDirectory() as temporary:
            application = Path(temporary) / 'application'
            script = application / 'docs/scripts/manuale-edit.py'
            script.parent.mkdir(parents=True)
            script.write_text('def edit_run(*args, **kwargs):\n    raise RuntimeError("fixture editor failed")\n')
            state = {'application': str(application), 'tooling_hashes': {
                'docs/scripts/manuale-edit.py': runner.sha(script)}}
            with patch.dict(runner.os.environ, {'CODEX_API_KEY': 'unit-fixture-key'}):
                with self.assertRaisesRegex(RuntimeError, 'fixture editor failed'):
                    runner.edit_content(state, SimpleNamespace())
                self.assertNotIn('CODEX_API_KEY', runner.os.environ)

    def test_independent_browser_failures_are_collected_but_seed_failure_stops_the_batch(self):
        for seed_fails in (False, True):
            with self.subTest(seed_fails=seed_fails), tempfile.TemporaryDirectory() as temporary:
                state = {'run': temporary, 'application': temporary, 'selected_recipes': ['first', 'second'],
                         'tooling_hashes': {}, 'origin': 'http://127.0.0.1:5010', 'reference_date': '2026-09-30'}
                calls = []
                def execute(command, run, label, **kwargs):
                    calls.append(label)
                    if seed_fails and label.endswith('for first'):
                        raise RuntimeError('seed isolation failure')
                    if label == 'Verify real application scenario first':
                        raise RuntimeError('browser failure')
                with patch.object(runner, 'validate_retained_evidence', return_value=None), \
                        patch.object(runner, 'recipe_registry', return_value={'recipes': [
                            {'id': name, 'script': name + '.mjs'} for name in ('first', 'second')]}), \
                        patch.object(runner, 'management', return_value=['seed-fixture']), \
                        patch.object(runner, 'execute', side_effect=execute):
                    with self.assertRaisesRegex(RuntimeError, 'seed isolation failure' if seed_fails else 'Capture batch failed for first'):
                        runner.capture(state)
                self.assertEqual('Verify real application scenario second' in calls, not seed_fails)
                self.assertEqual(calls[-1], 'Restore baseline after the capture batch')
                batch = json.loads((Path(temporary) / 'capture-execution.json').read_text())
                self.assertEqual(batch['status'], 'failed')
                self.assertNotEqual(state['status'], 'captured')
                if not seed_fails:
                    self.assertEqual(batch['scenarios'], [{'id': 'first', 'status': 'failed'}, {'id': 'second', 'status': 'passed'}])

    def bootstrap_fixture(self, root):
        """Real Git/Node preparation fixture, with no application services."""
        application, manual = root / 'application-input', root / 'manual-input'
        for repo in (application, manual):
            repo.mkdir()
            for args in [('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.test')]:
                subprocess.run(['git', '-C', str(repo), *args], check=True)
        for relative in ('docs/scripts/manuale.py', 'docs/scripts/manuale-scaffold.py', 'BE/application/manuale/index.py'):
            destination = application / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(runner.ROOT / relative, destination)
        template = {'path': 'docs/guide.mdx', 'title': 'Preparazione', 'id': 'preparazione',
            'body': '## Preparazione\n\n<Note>Bozza in attesa di prova.</Note>\n\n<Steps><Step title="Apri">Apri la pagina.\n<Frame>![Vista](/images/guide/1.png)</Frame></Step></Steps>'}
        for relative, exports in scaffold_fixture.DRAFT_MODULES:
            destination = application / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text('\n'.join('export function ' + name + '() {return ' +
                (json.dumps([template]) if name == 'tagDraftPages' else '[]') + ';}' for name in exports) + '\n')
        (application / 'docs/manuale/recipes.json').write_text(json.dumps({'format': 1,
            'shared_dependencies': ['docs/manuale/', 'docs/scripts/manuale'], 'recipes': [
                {'id': 'guide', 'script': 'selfhost/tests/browser/manuale/guide.mjs', 'pages': ['docs/guide.mdx'],
                 'dependencies': ['docs/manuale/tag-recipes.mjs'], 'capture_prefix': 'images/guide/'},
                {'id': 'other', 'script': 'selfhost/tests/browser/manuale/other.mjs', 'pages': ['docs/other.mdx'],
                 'dependencies': [], 'capture_prefix': 'images/other/'}]}))
        for relative in ('docs/manuale/page-map.json', 'docs/scripts/manuale-catalog.py', 'docs/scripts/manuale-generate.mjs',
            'BE/application/management/commands/seed_manuale.py', 'BE/application/management/commands/run_manuale_instance.py',
            'selfhost/compose.dev.yml', 'selfhost/tests/browser/package-lock.json', 'selfhost/tests/browser/playwright.manual.config.mjs',
            'selfhost/tests/browser/manuale/guide.mjs', 'selfhost/tests/browser/manuale/other.mjs'):
            destination = application / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text('{}\n')
        # Preparation-only stub; it records no capture or implementation proof.
        (application / 'docs/scripts/manuale-generate.mjs').write_text(
            "import fs from 'node:fs'; import path from 'node:path';\n"
            "if (!process.argv.includes('--authoring-preflight')) throw new Error('Preparation fixture only');\n"
            "fs.writeFileSync(path.join(process.argv[2], 'authoring-preflight.json'), "
            "JSON.stringify({format:1,status:'passed',missing_sections:[],selected_missing_sections:[]}));\n")
        # The fixture exercises orchestration of planning, never live coverage.
        (application / 'docs/scripts/manuale-coverage.py').write_text(
            "import json,sys\nfrom pathlib import Path\n"
            "target = Path(sys.argv[sys.argv.index('--output') + 1])\n"
            "target.write_text(json.dumps({'purpose':'offline-preparation-fixture','verified':False}))\n")
        (application / 'selfhost/tests/browser/node_modules').mkdir()
        (application / '.gitignore').write_text('selfhost/tests/browser/node_modules/\nselfhost/.env.dev\n')
        executable = application / 'selfhost/bin/assozeta'
        executable.parent.mkdir()
        executable.write_text('#!' + sys.executable + '\nfrom pathlib import Path\n'
            "Path(__file__).resolve().parents[1].joinpath('.env.dev').write_text('BASE=fixture\\n')\n")
        executable.chmod(0o755)
        runner.git(application, 'add', '.')
        runner.git(application, 'commit', '-qm', 'Preparation tooling fixture')
        application_revision = runner.git(application, 'rev-parse', 'HEAD')
        (application / 'README.md').write_text('Later commit must not replace the selected input.\n')
        runner.git(application, 'add', '.')
        runner.git(application, 'commit', '-qm', 'Later application input')
        (manual / 'docs').mkdir()
        (manual / 'docs/guide.mdx').write_text('---\ntitle: Guida\n---\n\n## Preparazione\n\nTesto originale.\n\n## Osservazioni\n\nTesto da conservare.\n')
        (manual / 'docs/other.mdx').write_text('## Altro capitolo\n\nTesto originale separato.\n')
        (manual / 'mint.json').write_text(json.dumps({'navigation': [{'group': 'Guide', 'pages': ['docs/guide', 'docs/other']}]}))
        runner.git(manual, 'add', '.')
        runner.git(manual, 'commit', '-qm', 'Clean public manual baseline')
        manual_baseline = runner.git(manual, 'rev-parse', 'HEAD')
        guide = manual / 'docs/guide.mdx'
        guide.write_text(guide.read_text().replace('Testo originale.', 'Modifica originale selezionata.'))
        runner.git(manual, 'add', '.')
        runner.git(manual, 'commit', '-qm', 'Selected manual guide change')
        args = SimpleNamespace(output=str(root / 'run'), manual_repo=str(manual),
            application_ref=application_revision, manual_ref=runner.git(manual, 'rev-parse', 'HEAD'),
            allow_dirty=False, standalone_checkouts=True, changed_since=None, manual_changed_since=manual_baseline,
            release='fixture-only', manual_url='https://manual.invalid', reference_date='2026-09-30', reuse_frontend=None)
        return application, manual, args

    def test_prepare_bootstraps_clean_selected_snapshot_without_extra_clone_or_incremental_selection_pollution(self):
        with tempfile.TemporaryDirectory() as temporary:
            application, manual, args = self.bootstrap_fixture(Path(temporary))
            original = (manual / 'docs/guide.mdx').read_bytes()
            source_worktrees = runner.git(manual, 'worktree', 'list', '--porcelain')
            with patch.object(runner, 'ROOT', application), patch.object(runner, 'free_port', side_effect=[5010, 5011, 5012, 5013]):
                state = runner.prepare(args)
            owned_manual = Path(state['manual'])
            self.assertEqual(state['application_input']['revision'], args.application_ref)
            self.assertEqual(state['manual_input']['revision'], args.manual_ref)
            self.assertEqual(state['manual_input']['state'], 'committed')
            self.assertEqual(state['manual_input']['uncommitted_files'], {})
            self.assertEqual(runner.git(owned_manual, 'rev-parse', 'HEAD'), args.manual_ref)
            self.assertEqual(state['selected_recipes'], ['guide'])
            self.assertEqual(state['selection']['changed_manual'], ['docs/guide.mdx'])
            self.assertEqual(state['manual_authoring']['mode'], 'initialized-run-owned-snapshot')
            self.assertEqual(state['manual_authoring']['input_revision'], args.manual_ref)
            self.assertEqual(state['manual_authoring']['input_files']['docs/guide.mdx'], scaffold_fixture.digest(original))
            self.assertFalse(state['manual_authoring']['verified'])
            self.assertFalse(state['manual_authoring']['publication_ready'])
            self.assertIn('.manuale-evidence.json', state['manual_authoring']['generated_files'])
            self.assertIn('docs/scripts/manuale-scaffold.py', state['tooling_hashes'])
            draft = (owned_manual / 'docs/guide.mdx').read_text()
            self.assertIn('Bozza in attesa di prova', draft)
            self.assertIn('1.placeholder.svg', draft)
            self.assertIn('## Osservazioni\n\nTesto da conservare.', draft)
            self.assertEqual((manual / 'docs/guide.mdx').read_bytes(), original)
            self.assertEqual(runner.git(manual, 'worktree', 'list', '--porcelain'), source_worktrees)
            self.assertEqual({path.name for path in Path(state['run']).iterdir() if path.is_dir()}, {'application', 'manual'})
            self.assertFalse((Path(state['run']) / 'guide.json').exists())
            # A directly authored section outside bootstrap recipes survives a refresh.
            sidecar_path = owned_manual / '.manuale-evidence.json'
            sidecar = json.loads(sidecar_path.read_text())
            editorial = {'title': 'Osservazioni', 'content_sha256': scaffold_fixture.digest(b'## Osservazioni\n\nTesto da conservare.'),
                'recipe_module': 'docs/manuale/tag-recipes.mjs',
                'recipe_sha256': runner.sha(Path(state['application']) / 'docs/manuale/tag-recipes.mjs'), 'content_source': 'authored-mdx'}
            sidecar['sections']['docs/guide.mdx#osservazioni'] = editorial
            sidecar_path.write_text(json.dumps(sidecar))
            with patch.object(scaffold_fixture, 'ROOT', Path(state['application'])):
                scaffold_fixture.apply_drafts(owned_manual)
            self.assertEqual(json.loads(sidecar_path.read_text())['sections']['docs/guide.mdx#osservazioni'], editorial)

    def test_prepare_preserves_reviewed_prose_and_rejects_invalid_or_partial_sidecars_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            application, manual, args = self.bootstrap_fixture(root)
            authored = '---\ntitle: Guida\n---\n\nIntroduzione revisionata senza intestazione.\n\n## Preparazione\n\nProsa revisionata scritta nel manuale.\n\n## Osservazioni\n\nTesto da conservare.\n'
            (manual / 'docs/guide.mdx').write_text(authored)
            sidecar = {'format': 1, 'purpose': 'reviewed-content', 'verified': False, 'sections': {
                'docs/guide.mdx#preparazione': {'title': 'Preparazione', 'content_source': 'authored-mdx',
                    'content_sha256': scaffold_fixture.digest(b'## Preparazione\n\nProsa revisionata scritta nel manuale.'),
                    'recipe_module': 'docs/manuale/tag-recipes.mjs',
                    'recipe_sha256': runner.sha(application / 'docs/manuale/tag-recipes.mjs')}}}
            sidecar['sections']['docs/guide.mdx#introduzione'] = {
                **sidecar['sections']['docs/guide.mdx#preparazione'], 'title': 'Introduzione',
                'content_sha256': scaffold_fixture.digest(b'Introduzione revisionata senza intestazione.')}
            (manual / '.manuale-evidence.json').write_text(json.dumps(sidecar))
            runner.git(manual, 'add', '.')
            runner.git(manual, 'commit', '-qm', 'Reviewed authored manual content')
            args.manual_ref = runner.git(manual, 'rev-parse', 'HEAD')
            original_files = runner.manual_content_hashes(manual)
            with patch.object(runner, 'ROOT', application), patch.object(runner, 'free_port', side_effect=[5010, 5011, 5012, 5013]):
                state = runner.prepare(args)
            owned_manual = Path(state['manual'])
            self.assertEqual(runner.manual_content_hashes(owned_manual), original_files)
            self.assertEqual(state['manual_authoring']['mode'], 'preserved-reviewed-input')
            self.assertEqual(state['manual_authoring']['generated_files'], {})
            self.assertEqual(state['manual_input']['revision'], args.manual_ref)
            self.assertFalse((owned_manual / '.manuale-authoring.json').exists())
            self.assertEqual((owned_manual / 'docs/guide.mdx').read_text(), authored)
            for malformed in ('invalid', 'partial'):
                sidecar_path = owned_manual / '.manuale-evidence.json'
                if malformed == 'invalid':
                    sidecar_path.write_text('{"format":1,"purpose":"reviewed-content","verified":false,"sections":{}}')
                else:
                    sidecar_path.unlink()
                    (owned_manual / '.manuale-authoring.json').write_text('{"format":1}')
                before = runner.manual_content_hashes(owned_manual)
                with self.subTest(state=malformed), self.assertRaisesRegex(ValueError, 'Invalid|Partial'):
                    runner.prepare_manual_authoring(state)
                self.assertEqual(runner.manual_content_hashes(owned_manual), before)

    def test_recipe_local_modules_follow_registered_imports_without_selecting_every_recipe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            modules = {
                'selfhost/tests/browser/manuale/alpha.mjs': "import {\n value\n} from './alpha-sources.mjs'; import './shared.mjs';\n",
                'selfhost/tests/browser/manuale/alpha-sources.mjs': "export {value} from './alpha-nested.mjs'; import('./lazy.mjs');\n",
                'selfhost/tests/browser/manuale/alpha-nested.mjs': 'export const value = 1;\n',
                'selfhost/tests/browser/manuale/lazy.mjs': 'export const value = 2;\n',
                'selfhost/tests/browser/manuale/beta.mjs': "import './shared.mjs';\n",
                'selfhost/tests/browser/manuale/shared.mjs': 'export const shared = true;\n',
                'selfhost/tests/browser/manuale/gamma.mjs': 'export const independent = true;\n',
                'docs/manuale/alpha-recipes.mjs': "import './nested/alpha-format.mjs';\n",
                'docs/manuale/nested/alpha-format.mjs': 'export const format = 1;\n',
            }
            for relative, body in modules.items():
                file = root / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(body)
            registry = {'shared_dependencies': ['docs/manuale/', 'selfhost/', 'BE/application/permissions', 'UI/package'],
                'recipes': [{'id': name, 'script': f'selfhost/tests/browser/manuale/{name}.mjs',
                    'dependencies': ['docs/manuale/alpha-recipes.mjs'] if name == 'alpha' else [],
                    'pages': [f'docs/{name}.mdx']} for name in ('alpha', 'beta', 'gamma')]}
            for relative in ('selfhost/tests/browser/manuale/alpha.mjs',
                             'selfhost/tests/browser/manuale/alpha-sources.mjs',
                             'selfhost/tests/browser/manuale/alpha-nested.mjs',
                             'selfhost/tests/browser/manuale/lazy.mjs',
                             'docs/manuale/alpha-recipes.mjs', 'docs/manuale/nested/alpha-format.mjs'):
                decisions = []
                self.assertEqual(runner.affected_recipes(registry, [relative], root=root, decisions=decisions), ['alpha'])
                self.assertEqual(decisions[0]['reason'], 'registered-recipe-tooling')
                self.assertEqual(decisions[0]['path'], relative)
            self.assertEqual(runner.affected_recipes(registry, ['selfhost/tests/browser/manuale/shared.mjs'], root=root), ['alpha', 'beta'])
            all_ids = ['alpha', 'beta', 'gamma']
            for relative in ('docs/manuale/unknown-generator.mjs', 'selfhost/tests/browser/manuale/unknown.mjs',
                             'selfhost/tests/browser/manuale/scenario.mjs', 'selfhost/tests/browser/manuale/frame.mjs',
                             'selfhost/compose.dev.yml', 'BE/application/management/commands/seed_manuale.py',
                             'BE/application/permissions_registry.py', 'BE/application/unknown.py',
                             'UI/src/unknown.svelte', 'UI/package.json'):
                self.assertEqual(runner.affected_recipes(registry, [relative], root=root), all_ids, relative)
            for relative in ('docs/scripts/tests/test_manuale_runner.py', 'docs/manuale/README.md', 'selfhost/docs/unrelated.md'):
                self.assertEqual(runner.affected_recipes(registry, [relative], root=root), [], relative)

    def test_manual_dirty_snapshot_copies_faq_assets_sidecars_and_records_incremental_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / 'manual'
            repo.mkdir()
            for args in [('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.test')]:
                subprocess.run(['git', '-C', str(repo), *args], check=True)
            (repo / 'docs').mkdir()
            (repo / 'images').mkdir()
            (repo / 'docs/existing.mdx').write_text('## Introduzione\n\nVersione iniziale.\n')
            (repo / 'images/deleted.png').write_bytes(b'owned old image')
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'Manual baseline'], check=True)
            base = runner.git(repo, 'rev-parse', 'HEAD')
            (repo / 'docs/committed.mdx').write_text('## Novità\n\nModifica committata.\n')
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'Committed manual change'], check=True)
            additions = {'faq/new-question.mdx': b'## Domanda\n\nRisposta.\n',
                         'tutorials/new-guide.mdx': b'## Procedura\n\nPassaggio.\n',
                         'images/new-image.png': b'owned new image',
                         '.manuale-authoring.json': b'{"format":1}\n',
                         '.manuale-evidence.json': b'{"sections":[]}\n'}
            for relative, body in additions.items():
                file = repo / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(body)
            (repo / 'docs/existing.mdx').write_bytes(b'## Introduzione\r\n\r\nModifica locale.\r\n')
            (repo / 'images/deleted.png').unlink()
            (repo / '.env').write_text('PRIVATE_FIXTURE_VALUE=not-for-snapshot\n')
            target = root / 'snapshot'
            with self.assertRaisesRegex(RuntimeError, 'allow-dirty'):
                runner.snapshot(repo, target, root, standalone=True, kind='manual')
            snapshot = runner.snapshot(repo, target, root, allow_dirty=True, standalone=True, kind='manual')
            self.assertEqual(snapshot['snapshot_kind'], 'manual')
            self.assertEqual(snapshot['state'], 'working_tree')
            expected = {relative: runner.sha(repo / relative) for relative in additions}
            expected.update({'docs/existing.mdx': runner.sha(repo / 'docs/existing.mdx'), 'images/deleted.png': None})
            self.assertEqual(snapshot['uncommitted_files'], expected)
            for relative in additions:
                self.assertEqual((target / relative).read_bytes(), (repo / relative).read_bytes())
            self.assertFalse((target / '.env').exists())
            self.assertEqual(runner.changed_snapshot_paths(target, snapshot, base), sorted([*expected, 'docs/committed.mdx']))

    def test_standalone_snapshot_preserves_commit_dirty_bytes_and_origin_without_registering_a_worktree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / 'repo'
            repo.mkdir()
            for args in [('init', '-q'), ('config', 'user.name', 'Fixture'),
                         ('config', 'user.email', 'fixture@example.test'),
                         ('remote', 'add', 'origin', 'https://github.com/example/manual-test.git')]:
                subprocess.run(['git', '-C', str(repo), *args], check=True)
            source = repo / 'BE/example.py'
            source.parent.mkdir()
            source.write_text('def example(): return True\n')
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'Actual source'], check=True)
            registry_before = runner.git(repo, 'worktree', 'list', '--porcelain')
            source.write_text('def example(): return False\n')
            target = root / 'snapshot'
            result = runner.snapshot(repo, target, root, allow_dirty=True, standalone=True, branch='add/manuale-fixture')
            self.assertEqual(result['revision'], runner.git(repo, 'rev-parse', 'HEAD'))
            self.assertEqual(result['checkout_kind'], 'standalone-local-clone')
            self.assertEqual(result['uncommitted_files'], {'BE/example.py': runner.sha(source)})
            self.assertEqual((target / 'BE/example.py').read_bytes(), source.read_bytes())
            self.assertTrue((target / '.git').is_dir())
            self.assertFalse((target / '.git/objects/info/alternates').exists())
            self.assertEqual(runner.git(target, 'remote', 'get-url', 'origin'),
                             'https://github.com/example/manual-test.git')
            self.assertEqual(runner.git(target, 'branch', '--show-current'), 'add/manuale-fixture')
            self.assertEqual(runner.git(repo, 'worktree', 'list', '--porcelain'), registry_before)

    def test_incremental_selection_is_conservative_for_shared_and_unknown_changes(self):
        registry = runner.recipe_registry()
        self.assertEqual(runner.affected_recipes(registry, ['BE/application/services/subscription_service.py']),
                         ['tags-create-assign', 'members-profile-update', 'members-medical-manage', 'members-filter-export', 'members-import', 'medical-followups', 'membership-links-cards', 'member-tags', 'members-fee-plan-selection', 'members-tax-code-correction', 'members-account-access', 'settings-signup-checkout', 'organization-document-templates'])
        self.assertEqual(runner.affected_recipes(registry, ['BE/application/utils/subscriptions_utils.py']),
                         ['members-search', 'members-create', 'members-approve', 'attendance-carnet-manual', 'members-filter-export', 'members-import', 'course-installments-manage', 'instructor-compensation', 'instructor-maintenance', 'medical-followups', 'course-calendar-maintenance', 'members-fee-plan-selection', 'members-tax-code-correction', 'members-account-access', 'course-types-sharing', 'settings-signup-checkout', 'attendance-carnet-maintenance', 'registration-default-category', 'attendance-automatic', 'attendance-carnet-variants', 'camps-enrollment-local', 'course-enrollment-calendar-navigation', 'dashboard-lists'])
        all_ids = [recipe['id'] for recipe in registry['recipes']]
        self.assertEqual(runner.affected_recipes(registry, ['BE/application/permissions_registry.py']), all_ids)
        self.assertEqual(runner.affected_recipes(registry, ['BE/application/new_dependency.py']), all_ids)
        self.assertEqual(runner.affected_recipes(registry, [], ['faq/come-usare-ricerca-filtri-atleti.mdx']), ['members-search', 'members-filter-export'])
        self.assertEqual(runner.affected_recipes(registry, [], ['mint.json']), all_ids)
        self.assertEqual(runner.affected_recipes(registry, ['docs/architecture/unrelated.md']), [])
        self.assertEqual(runner.affected_recipes(registry, ['BE/application/services/invoice_service.py']),
                         ['payments-create-edit-approve', 'receipts-approve-download', 'receipts-edit-delete', 'course-installments-manage', 'settings-receipts-card', 'payments-maintenance', 'organization-exports', 'instructor-compensation', 'settings-print', 'instructor-maintenance', 'course-calendar-maintenance', 'camps-enrollment-local', 'course-enrollment-calendar-navigation', 'payment-classification-local', 'dashboard-lists'])
        receipt_ids = ['receipts-approve-download', 'receipts-edit-delete']
        document_ids = receipt_ids + ['members-medical-manage', 'settings-receipts-card', 'payments-maintenance', 'instructor-compensation', 'settings-print', 'payment-classification-local']
        self.assertEqual(runner.affected_recipes(registry, ['BE/docmanager/tasks.py']), document_ids)
        self.assertEqual(runner.affected_recipes(registry, ['BE/templates/document/application/invoice.html']), receipt_ids + ['settings-receipts-card', 'payments-maintenance', 'settings-print', 'payment-classification-local'])
        self.assertEqual(runner.affected_recipes(registry, [], ['docs/ricevute.mdx']), receipt_ids)

    def test_editorial_bindings_and_test_changes_do_not_expand_a_single_guide_capture(self):
        registry = runner.recipe_registry()
        self.assertEqual(runner.affected_recipes(registry, [
            'BE/application/tests/test_manuale_registration_forms.py',
            'UI/src/routes/manuale/manualPresentation.test.js'], [
            '.manuale-evidence.json', '.manuale-authoring.json',
            'tutorials/come-creare-moduli-iscrizione-personalizzati.mdx',
            'images/tutorials/moduli-iscrizione/1.placeholder.svg']), ['organization-settings', 'registration-forms-manage',
                'members-fee-plan-selection', 'settings-signup-checkout', 'registration-default-category', 'organization-document-templates'])
        self.assertEqual(runner.affected_recipes(registry, [], ['.manuale-evidence.json']), [])

    def test_receipt_mutation_selects_its_isolated_fixture_and_rejects_unknown_profiles(self):
        self.assertEqual(runner.recipe_seed_options({}), ['--scenario', 'baseline'])
        self.assertEqual(runner.recipe_seed_options({'fixture': 'receipts-edit-delete'}),
                         ['--scenario', 'receipts-edit-delete'])
        self.assertEqual(runner.recipe_seed_options({'fixture': 'collaborator-removal'}),
                         ['--scenario', 'collaborator-removal'])
        self.assertEqual(runner.recipe_seed_options({'fixture': 'member-transfer'}),
                         ['--scenario', 'member-transfer'])
        with self.assertRaisesRegex(ValueError, 'fixture'):
            runner.recipe_seed_options({'fixture': 'production'})

    def test_deleted_sources_are_recorded_as_dirty_in_a_real_git_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / 'repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Fixture'], check=True)
            subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'fixture@example.test'], check=True)
            source = repo / 'BE/example.py'
            source.parent.mkdir()
            source.write_text('def example(): return True\n')
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'Fixture source'], check=True)
            source.unlink()
            result = runner.snapshot(repo, root / 'snapshot', root, allow_dirty=True)
            self.assertEqual(result['state'], 'working_tree')
            self.assertEqual(result['uncommitted_files'], {'BE/example.py': None})
            self.assertFalse((root / 'snapshot/BE/example.py').exists())

    def test_mutating_snapshotted_tools_aborts_before_reusing_browser_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'capture.mjs'
            source.write_text('assertRealBackend();\n')
            state = {'application': str(root), 'tooling_hashes': {'capture.mjs': runner.sha(source)}}
            runner.validate_tooling(state)
            source.write_text('simulateBackend();\n')
            with self.assertRaisesRegex(RuntimeError, 'tooling changed'):
                runner.validate_tooling(state)

    def test_git_newline_translation_keeps_exact_capture_bytes_and_local_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / 'repo'
            repo.mkdir()
            for args in [('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.test'),
                         ('config', 'core.autocrlf', 'input')]:
                subprocess.run(['git', '-C', str(repo), *args], check=True)
            source = repo / 'BE/example.py'
            source.parent.mkdir()
            source.write_bytes(b'def example():\n    return True\n')
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'Actual LF source'], check=True)
            source.write_bytes(b'def example():\r\n    return True\r\n')
            self.assertEqual(runner.git(repo, 'diff', 'HEAD', '--binary'), '')
            result = runner.snapshot(repo, root / 'snapshot', root, allow_dirty=True)
            self.assertEqual((root / 'snapshot/BE/example.py').read_bytes(), source.read_bytes())
            self.assertEqual(result['state'], 'working_tree')
            self.assertEqual(result['uncommitted_files'], {'BE/example.py': runner.sha(source)})


if __name__ == '__main__':
    unittest.main()
