"""Use real Git commits and changed/deleted inputs to test evidence provenance."""
import copy
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'manuale-catalog.py'
spec = importlib.util.spec_from_file_location('manual_catalog', SCRIPT)
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.code, self.manual = root / 'code', root / 'manual'
        for repo in (self.code, self.manual):
            repo.mkdir()
            for args in [('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.test'),
                         ('remote', 'add', 'origin', 'git@github.com:example/manual-fixture.git')]:
                subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
        files = {
            'UI/src/routes.js': "export default {\n    '/members': wrap({\n        asyncComponent: () => import('routes/Members.svelte'),\n        conditions: [() => canPerformAction('members.read')],\n    }),\n};\n",
            'UI/src/routes/Members.svelte': '<script>let members = [];</script>\n<h1>Tesserati</h1>\n',
            'UI/vite.config.js': "alias: { 'routes': path.resolve(projectRootDir, 'src/routes') }\n",
            'BE/application/views.py': 'def members_list(request):\n    return []\n',
            'BE/application/models.py': 'class Member:\n    pass\n',
            'BE/application/urls.py': "from .views import members_list\nurlpatterns = [path('members/list', members_list)]\n",
            'BE/application/permissions_registry.py': "PERMISSIONS_REGISTRY = {('GET', 'members/list'): 'members.read'}\n",
            'BE/templates/document/receipt.html': "{% if enumerate_invoices %}\nRicevuta n. {{ invoice.number }}\n{% endif %}\n",
        }
        for relative, contents in files.items():
            target = self.code / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents)
        target = self.code / 'BE/application/manuale/index.py'
        target.parent.mkdir(parents=True)
        shutil.copy2(SCRIPT.parents[2] / 'BE/application/manuale/index.py', target)
        (self.manual / 'docs').mkdir()
        (self.manual / 'docs/members.mdx').write_text('---\ntitle: Tesserati\n---\n\n## Elenco\nConsulta i tesserati.\n\n## Aggiungere\nPremi Aggiungi.\n')
        (self.manual / 'mint.json').write_text(json.dumps({'navigation': [{'pages': ['docs/members']}]}))
        (self.manual / 'SCREENSHOTS-NEEDED.md').write_text('## Documentation Pages\n| `members.mdx` | Needs screenshots | 1 | Elenco |\n')
        self.revisions = []
        for repo in (self.code, self.manual):
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'Actual fixture'], check=True)
            self.revisions.append(catalog.git(repo, 'rev-parse', 'HEAD').decode().strip())
        self.mapping = {'format': 1, 'mapping_status': 'review_targets',
            'domains': {'members': {'routes': ['/members'],
                'handlers': [{'path': 'BE/application/views.py', 'symbols': ['members_list']}],
                'models': [{'path': 'BE/application/models.py', 'symbols': ['Member']}],
                'templates': ['BE/templates/document/receipt.html']}},
            'pages': [{'path': 'docs/members.mdx', 'domains': ['members']}]}

    def compile(self, manifest=None):
        return catalog.compile_catalog(self.code, self.manual, *self.revisions, self.mapping, manifest)

    def test_catalog_resolves_ui_api_models_permissions_and_keeps_prose_pending(self):
        result = self.compile()
        self.assertEqual(result['summary']['sections'], 2)
        self.assertEqual(result['summary']['verified_sections'], 0)
        domain = result['domains']['members']
        self.assertEqual(domain['routes'][0]['components'][0]['path'], 'UI/src/routes/Members.svelte')
        self.assertEqual(domain['routes'][0]['permission_literals'], ['members.read'])
        handler = domain['handlers'][0]
        self.assertEqual(handler['endpoints'][0]['pattern'], 'members/list')
        self.assertEqual(handler['permission_rules'][0]['permission'], 'members.read')
        ref = handler['source']
        self.assertEqual(ref['source_state'], 'committed')
        self.assertIn('/blob/' + self.revisions[0] + '/', ref['commit_url'])
        blob = catalog.git(self.code, 'show', self.revisions[0] + ':' + ref['path'])
        self.assertEqual(ref['sha256'], catalog.sha(blob))
        self.assertEqual(domain['templates'][0]['path'], 'BE/templates/document/receipt.html')

    def test_local_contents_never_get_false_commit_pinned_citations(self):
        source = self.code / 'BE/application/views.py'
        source.write_text('def members_list(request):\n    return ["new"]\n')
        ref = self.compile()['domains']['members']['handlers'][0]['source']
        self.assertEqual(ref['source_state'], 'working_tree')
        self.assertNotEqual(ref['sha256'], ref['base_blob_sha256'])
        self.assertNotIn('commit_url', ref)
        self.assertNotIn('commit_revision', ref)
        self.assertIn('snapshot_reference', ref)
        new_file = self.code / 'BE/new.py'
        new_file.write_text('def fresh():\n    pass\n')
        new_ref = catalog.Sources(self.code, self.revisions[0]).symbol('BE/new.py', 'fresh')
        self.assertEqual(new_ref['source_state'], 'working_tree')
        self.assertNotIn('base_blob_oid', new_ref)
        self.assertNotIn('commit_url', new_ref)

    def test_navigation_backlog_missing_routes_and_symbols_abort(self):
        for change, error in [
            (lambda: self.mapping['pages'].clear(), 'complete navigation'),
            (lambda: self.mapping['domains']['members']['routes'].append('/removed'), 'Missing mapped UI route'),
            (lambda: self.mapping['domains']['members']['models'][0]['symbols'].append('Deleted'), 'Python symbol'),
            (lambda: (self.manual / 'SCREENSHOTS-NEEDED.md').write_text(''), 'Screenshot backlog')]:
            with self.subTest(error=error):
                original = copy.deepcopy(self.mapping)
                backlog = (self.manual / 'SCREENSHOTS-NEEDED.md').read_text()
                change()
                with self.assertRaisesRegex(catalog.CatalogError, error):
                    self.compile()
                self.mapping = original
                (self.manual / 'SCREENSHOTS-NEEDED.md').write_text(backlog)

    def test_enrichment_preserves_review_status_and_rejects_changed_evidence(self):
        mdx = '## Elenco\nConsulta i tesserati.'
        ref = catalog.Sources(self.code, self.revisions[0]).symbol('BE/application/views.py', 'members_list')
        manifest = {'metadata': {'application_revision': self.revisions[0], 'manual_revision': self.revisions[1]},
                    'pages': [{'path': 'docs/members.mdx', 'sections': [{'id': 'elenco', 'status': 'verified',
                        'content_sha256': catalog.sha(mdx.encode()), 'evidence': [ref]}]}]}
        result = self.compile(manifest)
        self.assertEqual(result['summary']['verified_sections'], 1)
        self.assertEqual(result['pages'][0]['sections'][1]['status'], 'pending')
        self.assertEqual(manifest['pages'][0]['status'], 'partial')
        manifest['pages'][0]['sections'].append({'id': 'aggiungere', 'status': 'verified',
            'content_sha256': catalog.sha('## Aggiungere\nPremi Aggiungi.'.encode()), 'evidence': [ref]})
        self.compile(manifest)
        self.assertEqual(manifest['pages'][0]['status'], 'verified')
        manifest['pages'][0]['sections'].pop()
        self.compile(manifest)
        self.assertEqual(manifest['pages'][0]['status'], 'partial')
        self.assertIn('commit_url', manifest['pages'][0]['sections'][0]['evidence'][0])
        (self.code / 'BE/application/views.py').write_text('def members_list(request):\n    return ["changed"]\n')
        with self.assertRaisesRegex(catalog.CatalogError, 'implementation changed'):
            self.compile(manifest)
        manifest['pages'][0]['sections'][0]['content_sha256'] = '0' * 64
        with self.assertRaisesRegex(catalog.CatalogError, 'text changed'):
            self.compile(manifest)

    def test_wrong_revision_and_source_traversal_are_rejected(self):
        with self.assertRaises(catalog.CatalogError):
            catalog.Sources(self.code, 'f' * 40)
        with self.assertRaises(catalog.CatalogError):
            catalog.Sources(self.code, self.revisions[0]).file('BE/../../outside.py')

    def test_html_templates_keep_real_git_provenance_and_reject_other_html_or_traversal(self):
        relative = 'BE/templates/document/receipt.html'
        source = catalog.Sources(self.code, self.revisions[0])
        ref = source.reference(relative, 1, 3, '{{ invoice.number }}')
        self.assertEqual(ref['source_state'], 'committed')
        self.assertIn('/blob/' + self.revisions[0] + '/', ref['commit_url'])
        spec = importlib.util.spec_from_file_location('template_manual_index', SCRIPT.parents[2] / 'BE/application/manuale/index.py')
        index = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(index)
        self.assertEqual(index.verify_source(self.code, ref)['snippet_sha256'], ref['snippet_sha256'])
        (self.code / relative).write_text('Ricevuta n. {{ invoice.number }} aggiornato\n')
        with self.assertRaisesRegex(index.EvidenceError, 'Stale code'):
            index.verify_source(self.code, ref)
        changed = catalog.Sources(self.code, self.revisions[0]).reference(relative, 1, 1, '{{ invoice.number }}')
        self.assertEqual(changed['source_state'], 'working_tree')
        self.assertNotIn('commit_url', changed)
        self.assertIn('snapshot_reference', changed)
        for forbidden in ('BE/application/receipt.html', 'UI/receipt.html',
                          'BE/templates/../application/receipt.html'):
            with self.subTest(path=forbidden):
                with self.assertRaises(catalog.CatalogError):
                    catalog.Sources(self.code, self.revisions[0]).file(forbidden)
                with self.assertRaises(index.EvidenceError):
                    index.verify_source(self.code, {**ref, 'path': forbidden})

    def evidence_bundle(self):
        spec = importlib.util.spec_from_file_location('offline_manual_index', SCRIPT.parents[2] / 'BE/application/manuale/index.py')
        index = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(index)
        ref = catalog.Sources(self.code, self.revisions[0]).symbol('BE/application/views.py', 'members_list')
        manifest = {'metadata': {'application_revision': self.revisions[0], 'manual_revision': self.revisions[1],
                                 'release': 'fixture-v1', 'manual_url': 'https://manual.example'},
                    'pages': [{'path': 'docs/members.mdx', 'sections': [{'id': 'elenco', 'status': 'verified',
                        'kind': 'code-reference', 'content_sha256': catalog.sha(b'## Elenco\nConsulta i tesserati.'), 'evidence': [ref]}]}]}
        result = self.compile(manifest)
        artifact = self.code.parent / 'source-catalog.json'
        artifact.write_text(catalog.canonical(result))
        manifest['metadata']['source_catalog'] = {'format': 1, 'path': artifact.name, 'sha256': catalog.sha(artifact.read_bytes())}
        return index, manifest, result, artifact

    @unittest.skipUnless(importlib.util.find_spec('numpy'), 'Offline index tests require numpy; run with uv --with numpy.')
    def test_real_git_catalog_is_consumed_by_index_and_preserves_section_gaps(self):
        index, manifest, result, artifact = self.evidence_bundle()
        value = index.build_index(self.manual, self.code, manifest, artifact_root=artifact.parent)
        self.assertEqual(value['chunks'][0]['evidence'][0]['commit_revision'], self.revisions[0])
        self.assertEqual(value['gaps'][0]['section'], 'aggiungere')
        self.assertEqual(value['catalog']['summary']['verified_sections'], 1)
        self.assertEqual(len(value['chunks']), 1)
        self.assertEqual(value['catalog']['pages'][0]['sections'][1]['status'], 'pending')
        # Renderer IDs can retain Italian punctuation while stable corpus IDs
        # remain unchanged. Use the measured heading in the external citation.
        section = next(section for page in manifest['pages'] for section in page['sections']
                       if section['status'] == 'verified')
        section['citation_anchor'] = 'consultare-l’archivio'
        rendered = index.build_index(self.manual, self.code, manifest, artifact_root=artifact.parent)
        self.assertEqual(rendered['chunks'][0]['id'], value['chunks'][0]['id'])
        self.assertTrue(rendered['chunks'][0]['url'].endswith('#consultare-l%E2%80%99archivio'))

    @unittest.skipUnless(importlib.util.find_spec('numpy'), 'Offline index tests require numpy; run with uv --with numpy.')
    def test_forged_pins_omitted_sections_and_corrupt_catalog_cannot_replace_valid_index(self):
        index, manifest, result, artifact = self.evidence_bundle()
        output = artifact.with_name('index.json')
        index.promote(index.build_index(self.manual, self.code, manifest, artifact_root=artifact.parent), output)
        original = output.read_bytes()
        ref = manifest['pages'][0]['sections'][0]['evidence'][0]
        original_url = ref['commit_url']
        ref['commit_url'] = original_url.replace(self.revisions[0], 'f' * 40)
        with self.assertRaisesRegex(index.EvidenceError, 'citation differs'):
            index.promote(index.build_index(self.manual, self.code, manifest, artifact_root=artifact.parent), output)
        self.assertEqual(output.read_bytes(), original)
        ref['commit_url'] = original_url
        result['pages'][0]['sections'].pop()
        artifact.write_text(catalog.canonical(result))
        manifest['metadata']['source_catalog']['sha256'] = catalog.sha(artifact.read_bytes())
        with self.assertRaisesRegex(index.EvidenceError, 'omits a manual section'):
            index.build_index(self.manual, self.code, manifest, artifact_root=artifact.parent)
        artifact.write_text('{truncated')
        with self.assertRaisesRegex(index.EvidenceError, 'catalog changed'):
            index.build_index(self.manual, self.code, manifest, artifact_root=artifact.parent)
        self.assertEqual(output.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
