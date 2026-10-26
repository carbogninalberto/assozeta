"""Portable verified-baseline cache uses real Git bundles and PNG fixtures."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tarfile
import unittest
from unittest.mock import patch

import test_manuale_reuse as fixtures

spec = importlib.util.spec_from_file_location('manual_portable_cache', fixtures.ROOT / 'docs/scripts/manuale-cache.py')
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


def write_json(root, relative, value):
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def rewrite_archive(source, destination, transform=None, extras=()):
    source, destination = Path(source), Path(destination)
    transform = transform or (lambda name, data: (name, data))
    with tarfile.open(source, 'r:gz') as before, tarfile.open(destination, 'w:gz') as after:
        for member in before:
            contents = before.extractfile(member).read()
            changed = transform(member.name, contents)
            if changed is None:
                continue
            name, contents = changed
            info = tarfile.TarInfo(name)
            info.size = len(contents)
            info.mode = member.mode
            after.addfile(info, io.BytesIO(contents))
        for name, contents in extras:
            info = tarfile.TarInfo(name)
            info.size = len(contents)
            info.mode = 0o644
            after.addfile(info, io.BytesIO(contents))


class VerifiedPortableCacheTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReuseFixtureTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture

        # Keep the shared fixture intact and extend its local setup with the
        # exact public/private inputs exercised by the portable cache boundary.
        for code in (f.old_code, f.code):
            fixtures.write(code, '.gitignore',
                           'UI/dist/\n.env*\nbrowser-input.json\nexecution.log\npreview.log\n')
            fixtures.write(code, 'selfhost/compose.dev.yml', 'services: {}\n')
            fixtures.command(code, 'add', '.gitignore', 'selfhost/compose.dev.yml')
            fixtures.command(code, 'commit', '-qm', 'Cache fixture public build configuration')
        self.application_revision = fixtures.command(f.old_code, 'rev-parse', 'HEAD')
        f.state['application_input']['revision'] = fixtures.command(f.code, 'rev-parse', 'HEAD')

        frame = f.report['screenshots'][0]
        image = fixtures.png()
        manual_image = f.old_manual / frame['path']
        manual_image.parent.mkdir(parents=True, exist_ok=True)
        manual_image.write_bytes(image)
        fixtures.write(f.old_manual, 'mint.json', {
            'navigation': [{'group': 'Guide', 'pages': ['docs/members']}],
        })
        fixtures.write(f.old_manual, 'docs.json', {
            'navigation': [{'group': 'Guide', 'pages': ['docs/members']}],
        })
        fixtures.write(f.old_manual, 'SCREENSHOTS-NEEDED.md', '## Documentation Pages\n')
        fixtures.command(f.old_manual, 'init', '-q')
        fixtures.command(f.old_manual, 'config', 'user.email', 'fixture@example.invalid')
        fixtures.command(f.old_manual, 'config', 'user.name', 'Offline Unit Fixture')
        fixtures.command(f.old_manual, 'add', '.')
        fixtures.command(f.old_manual, 'commit', '-qm', 'Explicit generated manual fixture')
        self.manual_revision = fixtures.command(f.old_manual, 'rev-parse', 'HEAD')

        f.report.update({
            'application_revision': self.application_revision,
            'capture_id': 'unit-capture',
            'tooling_hashes': f.old['tooling_hashes'],
        })
        fixtures.write(f.old_run, 'unit-members.json', f.report)
        report_hash = fixtures.reuse.file_hash(f.old_run, 'unit-members.json')
        sidecar_path = f.old_manual / '.manuale-evidence.json'
        sidecar = json.loads(sidecar_path.read_text())
        sidecar['sections']['docs/members.mdx#elenco']['materialization']['report_sha256'] = report_hash
        write_json(f.old_manual, '.manuale-evidence.json', sidecar)
        fixtures.command(f.old_manual, 'add', '.manuale-evidence.json')
        fixtures.command(f.old_manual, 'commit', '-qm', 'Bind the final generated report bytes')
        self.manual_revision = fixtures.command(f.old_manual, 'rev-parse', 'HEAD')

        source_catalog = {
            'format': 1,
            'status': 'source_targets_validated',
            'application_revision': self.application_revision,
            'manual_revision': self.manual_revision,
            'files': {},
            'manual_files': {},
            'pages': [],
        }
        for relative, record in f.report['source_hashes'].items():
            source_catalog['files'][relative] = {
                'path': relative,
                'sha256': record,
                'base_blob_sha256': record,
                'base_revision': self.application_revision,
                'source_state': 'committed',
            }
        for relative in ('docs/members.mdx', 'mint.json', 'SCREENSHOTS-NEEDED.md'):
            source_catalog['manual_files'][relative] = fixtures.manual_index.file_digest(
                f.old_manual / relative)
        manual_text = (f.old_manual / 'docs/members.mdx').read_text()
        section = fixtures.manual_index.sections(manual_text)['elenco']
        source_catalog['pages'] = [{
            'path': 'docs/members.mdx',
            'sections': [{
                'id': 'elenco',
                'status': 'verified',
                'content_sha256': fixtures.manual_index.digest(section['mdx']),
            }],
        }]
        fixtures.write(f.old_run, 'source-catalog.json', source_catalog)
        catalog_hash = fixtures.manual_index.file_digest(f.old_run / 'source-catalog.json')

        metadata = {
            'application_revision': self.application_revision,
            'manual_revision': self.manual_revision,
            'release': f.old['release'],
            'reference_date': f.old['reference_date'],
            'manual_url': 'https://manual.example.invalid',
            'canonical_manual_url': 'https://manual.example.invalid',
            'publication_status': 'local-preview-only',
            'code_state': 'committed',
            'uncommitted_code': {},
            'manual_state': 'working_tree',
            'manual_input_state': 'committed',
            'uncommitted_manual': {},
            'tooling_hashes': f.old['tooling_hashes'],
            'source_catalog': {
                'format': 1,
                'path': 'source-catalog.json',
                'sha256': catalog_hash,
            },
        }
        f.manifest = {
            'format': 1,
            'metadata': metadata,
            'scenarios': [{
                'id': 'unit-members',
                'status': 'passed',
                'application_revision': self.application_revision,
                'capture_id': 'unit-capture',
                'reference_date': f.old['reference_date'],
                'source_hashes': f.report['source_hashes'],
                'report_path': 'unit-members.json',
                'report_sha256': report_hash,
            }],
            'pages': [{
                'path': 'docs/members.mdx',
                'sections': [{
                    'id': 'elenco',
                    'status': 'verified',
                    'kind': 'workflow',
                    'content_sha256': fixtures.manual_index.digest(section['mdx']),
                    'scenario_ids': ['unit-members'],
                    'screenshots': [frame],
                    'evidence': [{
                        'path': relative,
                        'sha256': expected,
                        'symbol': 'members' if relative.endswith('.py') else 'Unit', 'start': 1, 'end': 1,
                        'base_revision': self.application_revision,
                        'source_state': 'committed', 'commit_revision': self.application_revision,
                        'commit_url': None,
                        'snippet_sha256': fixtures.manual_index.digest((f.old_code / relative).read_text().rstrip()),
                    } for relative, expected in f.report['source_hashes'].items()],
                }],
            }],
        }

        f.corpus = fixtures.manual_index.build_index(f.old_manual, f.old_code, f.manifest, artifact_root=f.old_run)
        write_json(f.old_run, 'index.json', f.corpus)

        render_image = fixtures.png()
        layouts = []
        for name, layout in (
            ('renders/heading.png', {'section': 'elenco', 'kind': 'heading'}),
            ('renders/image.png', {'section': 'elenco', 'kind': 'image',
                                   'checkpoint': 'unit-list', 'image': frame['path']}),
        ):
            target = f.old_run / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(render_image)
            layouts.append({
                **layout,
                'path': name,
                'width': 1920,
                'height': 1080,
                'sha256': fixtures.manual_index.digest(render_image),
            })
        write_json(f.old_run, 'render.json', {
            'status': 'passed',
            'cli': 'mint@4.2.955',
            'pages': [{
                'page': 'docs/members',
                'anchors': ['elenco'],
                'images': [frame['path']],
                'source_sha256': fixtures.manual_index.file_digest(f.old_manual / 'docs/members.mdx'),
                'layouts': layouts,
            }],
        })
        write_json(f.old_run, 'compile.json', {
            'format': 1,
            'status': 'passed',
            'compiler': '@mdx-js/mdx@3.1.1',
            'scope': 'static-mdx-and-local-references',
            'pages': [{
                'page': 'docs/members',
                'source_sha256': fixtures.manual_index.file_digest(f.old_manual / 'docs/members.mdx'),
                'compiled_sha256': '1' * 64,
                'references': 1,
            }],
        })
        write_json(f.old_run, 'evaluation.json', {
            'status': 'passed',
            'corpus_identity': f.corpus['identity'],
            'application_revision': self.application_revision,
            'version': f.old['release'],
            'checks': ['offline unit evaluation binding'],
        })
        write_json(f.old_run, 'embedded-manual.json', {
            'status': 'passed',
            'backend': 'real',
            'corpus_identity': f.corpus['identity'],
            'application_revision': self.application_revision,
            'version': f.old['release'],
            'checks': ['offline unit embedded-reader binding'],
        })
        write_json(f.old_run, 'timings.json', {
            'format': 1,
            'events': [{'kind': 'phase', 'name': 'evaluate', 'status': 'passed',
                        'elapsed_seconds': 0.1}],
        })

        web_artifact = f.old_code / 'UI/dist/public/index.html'
        fixtures.write(f.old_code, 'UI/dist/public/index.html', '<p>Unit compiled frontend</p>')
        write_json(f.old_run, 'frontend-build.json', {
            'sources': fixtures.runner.ui_sources(f.old_code),
            'artifacts': {
                'index.html': fixtures.manual_index.file_digest(web_artifact),
            },
        })
        write_json(f.old_run, 'compose.override.json', {
            'services': {
                'ui': {
                    'command': ['vite', 'preview'],
                    'environment': {
                        'OEM_ENV': 'assozeta',
                        'PRIVATE_FIXTURE_TOKEN': '${PRIVATE_FIXTURE_TOKEN}',
                    },
                },
            },
        })

        f.old.update({
            'format': 1,
            'run_id': f.old_run.name,
            'status': 'evaluated',
            'capture_id': 'unit-capture',
            'manual_repository': str(f.old_manual),
            'override': str(f.old_run / 'compose.override.json'),
            'application_input': {
                'revision': self.application_revision,
                'state': 'committed',
                'snapshot_kind': 'application',
                'checkout_kind': 'standalone-local-clone',
                'uncommitted_files': {},
            },
            'manual_input': {
                'revision': self.manual_revision,
                'state': 'committed',
                'snapshot_kind': 'manual',
                'checkout_kind': 'standalone-local-clone',
                'uncommitted_files': {},
            },
        })
        write_json(f.old_run, 'manifest.json', f.manifest)
        write_json(f.old_run, 'run.json', f.old)

        (f.old_run / 'browser-input.json').write_text('{"token":"private-fixture-token"}')
        (f.old_run / 'execution.log').write_text('private-fixture-token')
        (f.old_code / '.env').write_text('SECRET=private-fixture-token')

        self.archive = f.root / 'verified-format2.tar.gz'

    def export(self):
        return cache.export_cache(self.fixture.old_run, self.archive)

    def test_verified_baseline_relocates_with_original_provenance_and_public_inputs(self):
        f = self.fixture
        result = self.export()
        self.assertEqual(result['format'], 2)
        with tarfile.open(self.archive) as archive:
            names = set(archive.getnames())
            cache_json = json.loads(archive.extractfile('cache.json').read())
            self.assertEqual(cache_json['format'], 2)
            inventory = {item['path']: item for item in cache_json['members']}
            self.assertEqual(set(inventory) | {'cache.json'}, names)
            self.assertIn('application.bundle', inventory)
            self.assertIn('proof/index.json', inventory)
            self.assertIn('proof/source-catalog.json', inventory)
            self.assertIn('proof/evaluation.json', inventory)
            self.assertIn('proof/embedded-manual.json', inventory)
            self.assertIn('proof/render.json', inventory)
            self.assertIn('proof/renders/heading.png', inventory)
            self.assertIn('proof/renders/image.png', inventory)
            self.assertIn('proof/compile.json', inventory)
            self.assertIn('proof/timings.json', inventory)
            self.assertIn('manual/docs/members.mdx', inventory)
            self.assertIn('manual/.manuale-evidence.json', inventory)
            self.assertIn('manual/mint.json', inventory)
            self.assertIn('manual/docs.json', inventory)
            self.assertIn('manual/images/unit-members/1.png', inventory)
            self.assertIn('proof/frontend-build.json', inventory)
            for relative, item in inventory.items():
                contents = archive.extractfile(relative).read()
                self.assertEqual(item['size'], len(contents), relative)
                self.assertEqual(item['sha256'], fixtures.manual_index.digest(contents), relative)

        original_report = (f.old_run / 'unit-members.json').read_bytes()
        original_capture = (f.old_run / 'captures/images/unit-members/1.png').read_bytes()
        restored = f.root / 'restored'
        cache.import_cache(self.archive, restored, f.root / 'manual-source', register=False)
        self.assertEqual((restored / 'unit-members.json').read_bytes(), original_report)
        self.assertEqual((restored / 'captures/images/unit-members/1.png').read_bytes(), original_capture)
        imported = json.loads((restored / 'run.json').read_text())
        self.assertNotIn('env_file', imported)
        self.assertTrue(imported['services_removed'])
        self.assertEqual(Path(imported['override']).resolve(), (restored / 'compose.override.json').resolve())
        override = json.loads((restored / 'compose.override.json').read_text())
        self.assertEqual(override['services']['ui']['environment'], {'OEM_ENV': 'assozeta'})
        write_json(f.run, 'compose.override.json', override)
        f.state['override'] = str(f.run / 'compose.override.json')
        f.state['reuse_frontend'] = str(restored)
        frontend = fixtures.runner.reuse_frontend(f.state)
        self.assertEqual(frontend['source_run'], 'baseline')
        self.assertEqual((f.code / 'UI/dist/public/index.html').read_text(), '<p>Unit compiled frontend</p>')

        f.old_run.rename(f.root / 'original-unavailable')
        plan = fixtures.reuse.build_reuse_plan(f.state, restored)
        self.assertEqual(plan['reused_recipes'], ['unit-members'], plan['rejected'])
        self.assertEqual(plan['recipes']['unit-members']['origin']['capture_id'], 'unit-capture')
        fixtures.reuse.validate_reuse_plan(f.state)

    def test_named_output_directory_preserves_independent_run_identity(self):
        f = self.fixture
        f.old['run_id'] = 'abcdef012345'
        write_json(f.old_run, 'run.json', f.old)
        self.export()
        restored = f.root / 'another-named-output'
        cache.import_cache(self.archive, restored, f.root / 'manual-source', register=False)
        self.assertEqual(json.loads((restored / 'run.json').read_text())['run_id'], 'abcdef012345')

    def test_resealed_index_cannot_change_verified_content(self):
        f = self.fixture
        corpus = copy.deepcopy(f.corpus)
        corpus['chunks'][0]['text'] = 'Unverified altered content'
        corpus = fixtures.manual_index.seal_index(corpus)
        write_json(f.old_run, 'index.json', corpus)
        for report in ('evaluation.json', 'embedded-manual.json'):
            value = json.loads((f.old_run / report).read_text())
            value['corpus_identity'] = corpus['identity']
            write_json(f.old_run, report, value)
        with self.assertRaisesRegex(ValueError, 'cannot be reproduced'):
            self.export()

    def test_failed_incomplete_or_uncommitted_runs_cannot_export(self):
        f = self.fixture
        previous = self.archive
        previous.write_bytes(b'previous-cache')
        original = copy.deepcopy(f.old)
        original_manifest = copy.deepcopy(f.manifest)
        f.old['status'] = 'failed'
        write_json(f.old_run, 'run.json', f.old)
        with self.assertRaisesRegex(ValueError, 'fully evaluated'):
            self.export()
        self.assertEqual(previous.read_bytes(), b'previous-cache')

        f.old = copy.deepcopy(original)
        f.old['application_input']['state'] = 'working_tree'
        f.old['application_input']['uncommitted_files'] = {'BE/members/views.py': '0' * 64}
        write_json(f.old_run, 'run.json', f.old)
        with self.assertRaisesRegex(ValueError, 'clean committed'):
            self.export()

        f.old = copy.deepcopy(original)
        write_json(f.old_run, 'run.json', f.old)
        f.manifest['scenarios'][0]['status'] = 'failed'
        write_json(f.old_run, 'manifest.json', f.manifest)
        with self.assertRaisesRegex(ValueError, 'complete passed capture baseline'):
            self.export()
        self.assertFalse(previous.read_bytes().startswith(b'\x1f\x8b'))

        f.manifest = copy.deepcopy(original_manifest)
        f.manifest['scenarios'] = []
        write_json(f.old_run, 'manifest.json', f.manifest)
        with self.assertRaisesRegex(ValueError, '[Oo]nly a complete passed capture baseline'):
            self.export()
        self.assertEqual(previous.read_bytes(), b'previous-cache')

    def test_evaluation_reader_render_and_compile_must_bind_the_sealed_corpus(self):
        f = self.fixture
        original = (f.old_run / 'render.json').read_bytes()
        render = json.loads(original)
        render['status'] = 'failed'
        write_json(f.old_run, 'render.json', render)
        with self.assertRaisesRegex(ValueError, 'Render evidence'):
            self.export()

        (f.old_run / 'render.json').write_bytes(original)
        write_json(f.old_run, 'embedded-manual.json', {
            'status': 'passed', 'backend': 'mock',
            'corpus_identity': f.corpus['identity'],
            'application_revision': self.application_revision,
            'version': f.old['release'],
        })
        with self.assertRaisesRegex(ValueError, 'Embedded-reader'):
            self.export()

    def test_tampered_missing_extra_duplicate_and_old_format_archives_fail_atomically(self):
        self.export()
        f = self.fixture

        def tampered(name, contents):
            return name, contents + b'\n' if name == 'proof/render.json' else contents

        variants = []
        variants.append(('tampered', f.root / 'tampered.tar.gz',
                         lambda name, contents: None if name == 'proof/render.json' else (name, contents)))
        rewrites = [
            ('digest', f.root / 'digest.tar.gz', tampered, ()),
            ('missing', f.root / 'missing.tar.gz', variants[0][2], ()),
            ('extra', f.root / 'extra.tar.gz', None, (('proof/extra.txt', b'extra'),)),
            ('duplicate', f.root / 'duplicate.tar.gz', None,
             (('proof/render.json', (f.old_run / 'render.json').read_bytes()),)),
        ]
        for label, archive_path, transform, extras in rewrites:
            with self.subTest(label=label):
                rewrite_archive(self.archive, archive_path, transform, extras)
                output = f.root / ('rejected-' + label)
                with self.assertRaises(ValueError):
                    cache.import_cache(archive_path, output, f.root / 'manual-source', register=False)
                self.assertFalse(output.exists())

        private_bytes = b'private-token-value'

        def add_inventoried_private(name, contents):
            if name != 'cache.json':
                return name, contents
            payload = json.loads(contents)
            payload['members'].append({
                'path': 'proof/private-token.json',
                'sha256': fixtures.manual_index.digest(private_bytes),
                'size': len(private_bytes),
                'mode': 0o644,
            })
            return name, json.dumps(payload).encode()

        inventoried_extra = f.root / 'inventoried-extra.tar.gz'
        rewrite_archive(self.archive, inventoried_extra, add_inventoried_private,
                        (('proof/private-token.json', private_bytes),))
        with self.assertRaisesRegex(ValueError, 'undeclared or missing evidence'):
            cache.import_cache(inventoried_extra, f.root / 'rejected-inventoried-extra',
                               f.root / 'manual-source', register=False)
        self.assertFalse((f.root / 'rejected-inventoried-extra').exists())

        def old_format(name, contents):
            if name != 'cache.json':
                return name, contents
            payload = json.loads(contents)
            payload['format'] = 1
            return name, json.dumps(payload).encode()

        old_archive = f.root / 'format1.tar.gz'
        rewrite_archive(self.archive, old_archive, old_format)
        with self.assertRaisesRegex(ValueError, 'cold full verification'):
            cache.import_cache(old_archive, f.root / 'rejected-format', f.root / 'manual-source',
                               register=False)
        self.assertFalse((f.root / 'rejected-format').exists())

        def failed_state(name, contents):
            if name != 'cache.json':
                return name, contents
            payload = json.loads(contents)
            payload['state']['status'] = 'failed'
            return name, json.dumps(payload).encode()

        failed_archive = f.root / 'failed-state.tar.gz'
        rewrite_archive(self.archive, failed_archive, failed_state)
        with self.assertRaisesRegex(ValueError, 'fully evaluated'):
            cache.import_cache(failed_archive, f.root / 'rejected-state',
                               f.root / 'manual-source', register=False)
        self.assertFalse((f.root / 'rejected-state').exists())

    def test_private_runtime_state_is_never_exported(self):
        self.export()
        with tarfile.open(self.archive) as archive:
            names = archive.getnames()
            self.assertFalse(any('.git/' in name or '.env' in name or 'execution.log' in name
                                 or 'browser-input' in name for name in names))
            for member in archive:
                content = archive.extractfile(member).read()
                self.assertNotIn(b'private-fixture-token', content, member.name)
            compose = json.loads(archive.extractfile('proof/compose.override.json').read())
            self.assertEqual(compose['services']['ui']['environment'], {'OEM_ENV': 'assozeta'})
            state = json.loads(archive.extractfile('cache.json').read())['state']
            self.assertEqual(state['run'], '.')
            self.assertEqual(state['application'], 'application')
            self.assertEqual(state['manual'], 'manual')
            self.assertNotIn('override', state)
            self.assertNotIn(str(self.fixture.root), json.dumps(state))

    def test_rejects_traversal_metadata_links_and_private_environment_members(self):
        f = self.fixture
        for name, kind in [('../escape', tarfile.REGTYPE), ('application/.git/config', tarfile.REGTYPE),
                           ('proof/../../escape', tarfile.REGTYPE), ('application/.env', tarfile.REGTYPE),
                           ('application/symlink', tarfile.SYMTYPE)]:
            with self.subTest(name=name):
                archive = f.root / 'bad.tar.gz'
                with tarfile.open(archive, 'w:gz') as contents:
                    item = tarfile.TarInfo(name)
                    item.type, item.size, item.linkname = kind, 4, '/tmp/escape'
                    data = b'unit' if kind == tarfile.REGTYPE else None
                    contents.addfile(item, io.BytesIO(data) if data is not None else None)
                output = f.root / 'rejected'
                with self.assertRaises(ValueError):
                    cache.import_cache(archive, output, f.root / 'manual-source', register=False)
                self.assertFalse(output.exists())

    def test_validation_or_registration_failure_leaves_no_output_or_registry_entry(self):
        self.export()
        f = self.fixture
        with patch.object(cache, '_register_import',
                          side_effect=RuntimeError('registry unavailable')) as register, \
                patch.object(cache, '_remove_import_registration') as remove_registration:
            with self.assertRaisesRegex(RuntimeError, 'registry unavailable'):
                cache.import_cache(self.archive, f.root / 'registration-failed',
                                   f.root / 'manual-source', register=True)
            register.assert_called_once()
            remove_registration.assert_called_once()
        self.assertFalse((f.root / 'registration-failed').exists())

        write_json(f.old_run, 'evaluation.json', {
            'status': 'passed',
            'corpus_identity': '0' * 64,
            'application_revision': self.application_revision,
            'version': f.old['release'],
        })
        with self.assertRaisesRegex(ValueError, 'Evaluation evidence'):
            self.export()
        self.assertFalse(f.root.joinpath('validation-failed').exists())


if __name__ == '__main__':
    unittest.main()
