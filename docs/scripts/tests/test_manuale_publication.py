"""Temporary Git unit fixtures only; no live/browser/production publication proof."""
import importlib.util
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'BE'))
from application.manuale import index, publication


def write(root, relative, value):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(value if isinstance(value, bytes) else (index.canonical(value) + '\n').encode())


def command(root, *arguments):
    return subprocess.check_output(['git', '-C', str(root), *arguments], stderr=subprocess.DEVNULL).decode().strip()


def commit(root):
    command(root, 'add', '.')
    command(root, '-c', 'user.name=Offline Fixture', '-c', 'user.email=fixture@invalid.example',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Offline unit fixture')
    return command(root, 'rev-parse', 'HEAD')


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\0\x80\x80\x80')) + chunk(b'IEND', b'')


class PublicationFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run, self.application, self.manual = (self.root / name for name in ('fixture-run', 'application', 'manual'))
        self.output = self.root / 'prepared'
        for repository in (self.application, self.manual):
            repository.mkdir()
            command(repository, 'init')
        source_bytes = b'def verified_action(): return True\n'
        write(self.application, 'BE/action.py', source_bytes)
        self.revision = commit(self.application)
        write(self.manual, 'docs/action.mdx', b'## Azione\nBozza.\n')
        write(self.manual, 'mint.json', {'navigation': [{'group': 'Guide', 'pages': ['docs/action']}]})
        write(self.manual, 'SCREENSHOTS-NEEDED.md', b'Offline screenshot fixture.\n')
        self.authoring_revision = commit(self.manual)
        body = '## Azione\nApri il modulo.\n\n![Modulo](/images/action/1.png)'
        write(self.manual, 'docs/action.mdx', (body + '\n').encode())
        write(self.manual, 'images/action/1.png', png())
        write(self.manual, '.manuale-evidence.json', {'evidence_kind': 'offline-unit-fixture'})
        self.manual_revision = commit(self.manual)
        shutil.copytree(self.manual, self.run / 'manual', ignore=shutil.ignore_patterns('.git'))
        source_hash = index.digest(source_bytes)
        source = {'path': 'BE/action.py', 'start': 1, 'end': 1, 'symbol': 'verified_action',
            'sha256': source_hash, 'snippet_sha256': index.digest(source_bytes.decode().strip()),
            'source_state': 'committed', 'base_revision': self.revision, 'commit_revision': self.revision}
        capture = {'path': 'images/action/1.png', 'sha256': index.digest(png())}
        report = {'id': 'fixture-action', 'status': 'passed', 'backend': 'real', 'application_revision': self.revision,
            'evidence_kind': 'offline-unit-fixture', 'source_hashes': {'BE/action.py': source_hash}, 'screenshots': [capture]}
        write(self.run, 'fixture-action.json', report)
        section = {'id': 'azione', 'status': 'verified', 'kind': 'workflow', 'content_sha256': index.digest(body),
            'evidence': [source], 'scenario_ids': ['fixture-action'], 'screenshots': [capture]}
        self.manifest = {'metadata': {'application_revision': self.revision, 'manual_revision': self.authoring_revision,
            'release': 'v-fixture', 'manual_url': 'https://preview.invalid', 'canonical_manual_url': 'https://manual.invalid',
            'code_state': 'committed', 'manual_state': 'working_tree', 'publication_status': 'local-preview-only'},
            'pages': [{'path': 'docs/action.mdx', 'sections': [section]}],
            'scenarios': [{**report, 'report_path': 'fixture-action.json',
                'report_sha256': index.file_digest(self.run / 'fixture-action.json')}]}
        catalog = {'format': 1, 'status': 'source_targets_validated', 'application_revision': self.revision,
            'manual_revision': self.authoring_revision,
            'files': {'BE/action.py': {'path': 'BE/action.py', 'sha256': source_hash, 'base_blob_sha256': source_hash,
                'base_revision': self.revision, 'source_state': 'committed'}},
            'manual_files': {relative: index.file_digest(self.manual / relative)
                for relative in ('docs/action.mdx', 'mint.json', 'SCREENSHOTS-NEEDED.md')},
            'pages': [{'path': 'docs/action.mdx', 'sections': [{'id': 'azione', 'status': 'verified',
                'content_sha256': index.digest(body)}]}]}
        write(self.run, 'source-catalog.json', catalog)
        self.manifest['metadata']['source_catalog'] = {'format': 1, 'path': 'source-catalog.json',
            'sha256': index.file_digest(self.run / 'source-catalog.json')}
        write(self.run, 'manifest.json', self.manifest)
        self.corpus = index.build_index(self.run / 'manual', self.application, self.manifest, artifact_root=self.run)
        write(self.run, 'index.json', self.corpus)
        self.state = {'status': 'evaluated', 'run_id': self.run.name, 'evidence_kind': 'offline-unit-fixture',
            'application_input': {'state': 'committed', 'revision': self.revision, 'uncommitted_files': {}},
            'manual_input': {'revision': self.authoring_revision}, 'release': 'v-fixture'}
        write(self.run, 'run.json', self.state)
        write(self.run, 'evaluation.json', {'status': 'passed', 'corpus_identity': self.corpus['identity'],
            'application_revision': self.revision, 'version': 'v-fixture', 'evidence_kind': 'offline-unit-fixture'})
        write(self.run, 'embedded-manual.json', {'status': 'passed', 'backend': 'real',
            'application_revision': self.revision, 'evidence_kind': 'offline-unit-fixture'})
        write(self.run, 'render.json', {'status': 'passed', 'evidence_kind': 'offline-unit-fixture',
            'pages': [{'page': 'docs/action', 'source_sha256': index.file_digest(self.manual / 'docs/action.mdx'),
                'anchors': ['azione'], 'images': [capture['path']]}]})

    def prepare(self):
        return publication.prepare_release(self.run, self.application, self.manual, self.output, release='v-fixture')

    def test_release_keeps_the_renderer_citation_anchor(self):
        self.manifest['pages'][0]['sections'][0]['citation_anchor'] = 'l’azione'
        write(self.run, 'manifest.json', self.manifest)
        self.corpus = index.build_index(self.run / 'manual', self.application, self.manifest, artifact_root=self.run)
        write(self.run, 'index.json', self.corpus)
        evaluation = publication.read_json(self.run, 'evaluation.json')
        evaluation['corpus_identity'] = self.corpus['identity']
        write(self.run, 'evaluation.json', evaluation)
        self.prepare()
        candidate = index.ManualIndex.load(self.output / 'index.json')
        self.assertTrue(candidate.value['chunks'][0]['url'].endswith('#l%E2%80%99azione'))
        page = '<h2 id="l’azione">Azione</h2><p>Apri il modulo.</p><img alt="Modulo">'.encode()
        catalog_page = {'sections': [{'id': 'azione'}]}
        publication.verify_page_prose(page, candidate.value['chunks'], catalog_page)
        with self.assertRaisesRegex(index.EvidenceError, 'missing verified section'):
            publication.verify_page_prose(page.replace('l’azione'.encode(), b'wrong'), candidate.value['chunks'], catalog_page)

    def test_prepared_candidate_binds_final_git_bytes_and_retains_original_report_and_index(self):
        original_report = (self.run / 'fixture-action.json').read_bytes()
        original_index = (self.run / 'index.json').read_bytes()
        plan = self.prepare()
        candidate = index.ManualIndex.load(self.output / 'index.json')
        self.assertEqual(plan['status'], 'prepared')
        self.assertFalse(plan['published'])
        self.assertFalse(plan['external_publication_verified'])
        self.assertEqual(plan['evidence_kind'], 'offline-unit-fixture')
        self.assertEqual(candidate.value['metadata']['publication_status'], 'prepared')
        self.assertEqual(candidate.value['metadata']['manual_revision'], self.manual_revision)
        self.assertEqual(candidate.value['metadata']['authoring_manual_revision'], self.authoring_revision)
        self.assertEqual(candidate.value['catalog']['manual_revision'], self.manual_revision)
        self.assertEqual(candidate.value['metadata']['verification']['metadata'], self.corpus['metadata'])
        self.assertEqual(candidate.value['chunks'][0]['capture_provenance'], self.corpus['chunks'][0]['capture_provenance'])
        self.assertEqual((self.output / 'evidence/fixture-action.json').read_bytes(), original_report)
        self.assertEqual((self.run / 'fixture-action.json').read_bytes(), original_report)
        self.assertEqual((self.run / 'index.json').read_bytes(), original_index)
        for relative, expected in plan['manual_file_hashes'].items():
            self.assertEqual(index.digest(publication.git(self.manual, 'show', self.manual_revision + ':' + relative)), expected)
        # Exercise the actual production synchronization gate with fixture
        # settings and downloaded bytes; neither Django nor network is needed.
        django, conf = types.ModuleType('django'), types.ModuleType('django.conf')
        conf.settings = types.SimpleNamespace(MANUAL_CORPUS_BASE_URL='https://manual.invalid/corpus',
            MANUAL_APPLICATION_REVISION=self.revision, RUNNING_VERSION='v-fixture', MANUAL_RUN_ID='',
            MANUAL_INDEX_PATH=str(self.root / 'production-index.json'), MANUAL_SOURCE_ROOT=str(self.application))
        spec = importlib.util.spec_from_file_location('application.manuale.publication_fixture_sync',
            ROOT / 'BE/application/manuale/sync.py')
        sync = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'django': django, 'django.conf': conf}):
            spec.loader.exec_module(sync)
        with patch.object(sync, 'urlopen', return_value=io.BytesIO((self.output / 'index.json').read_bytes())):
            with self.assertRaisesRegex(index.EvidenceError, 'Only committed, published'):
                sync.synchronize()
        self.assertFalse(Path(conf.settings.MANUAL_INDEX_PATH).exists())

    def test_changed_committed_manual_or_dirty_inputs_cannot_produce_output(self):
        write(self.manual, '.manuale-evidence.json', {'changed': True})
        commit(self.manual)
        with self.assertRaisesRegex(index.EvidenceError, 'Committed release bytes differ'):
            self.prepare()
        self.assertFalse(self.output.exists())
        write(self.application, 'BE/action.py', b'def verified_action(): return False\n')
        with self.assertRaisesRegex(index.EvidenceError, 'clean and committed'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_working_tree_application_or_mismatched_evaluation_is_rejected(self):
        with self.assertRaisesRegex(index.EvidenceError, 'this committed application'):
            publication.prepare_release(self.run, self.application, self.manual, self.output, release='wrong-release')
        self.state['application_input']['state'] = 'working_tree'
        write(self.run, 'run.json', self.state)
        with self.assertRaisesRegex(index.EvidenceError, 'this committed application'):
            self.prepare()
        self.state['application_input']['state'] = 'committed'
        write(self.run, 'run.json', self.state)
        write(self.run, 'evaluation.json', {'status': 'passed', 'corpus_identity': 'different'})
        with self.assertRaisesRegex(index.EvidenceError, 'successful MCP/agent'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def availability_fixtures(self):
        """Deterministic HTTPS response bytes; explicitly not real availability."""
        plan = self.prepare()
        source = 'https://raw.githubusercontent.com/Bakney/manuale/' + self.manual_revision
        evidence = 'https://evidence.invalid/fixture'
        responses = {source + '/' + relative: (self.output / 'manual' / relative).read_bytes()
            for relative in plan['manual_file_hashes']}
        responses.update({evidence + '/' + relative: (self.output / 'evidence' / relative).read_bytes()
            for relative in plan['retained_evidence']})
        responses['https://manual.invalid/docs/action'] = b'<main><h2 id="azione">Azione</h2><p>Apri il modulo.</p><img alt="Modulo"></main>'
        responses['https://manual.invalid/images/action/1.png'] = png()
        responses['https://api.github.com/repos/Bakney/manuale/deployments?per_page=100'] = index.canonical([
            {'id': 9, 'environment': 'fixture-production', 'sha': self.manual_revision, 'created_at': '2026-01-01T00:00:00Z'}]).encode()
        responses['https://api.github.com/repos/Bakney/manuale/deployments/9/statuses?per_page=100'] = index.canonical([
            {'id': 10, 'created_at': '2026-01-01T00:00:00Z', 'state': 'success', 'environment_url': 'https://manual.invalid'}]).encode()
        return evidence, responses

    def verify_availability_fixture(self, evidence, responses, *, environment='fixture-production', name='availability'):
        def response(url, **requirements):
            self.assertTrue(url.startswith('https://'))
            if url == 'https://manual.invalid/docs/action':
                self.assertTrue(requirements.get('require_html'))
            if url not in responses:
                raise OSError('Missing deterministic HTTPS fixture')
            return responses[url]
        with patch.object(publication, 'read_https', side_effect=response):
            return publication.verify_release_availability(self.output, self.application, self.manual,
                self.root / name, release='v-fixture', evidence_base=evidence, deployment_environment=environment)

    def test_https_fixture_success_derives_local_index_and_digest_without_changing_prepared_proof(self):
        evidence, responses = self.availability_fixtures()
        original = (self.output / 'index.json').read_bytes()
        report = self.verify_availability_fixture(evidence, responses)
        self.assertEqual(report['status'], 'availability-verified')
        self.assertEqual(report['content_availability'], 'verified')
        self.assertEqual(report['deployment_revision'], 'verified')
        self.assertEqual(report['evidence_kind'], 'offline-unit-fixture')
        self.assertEqual(report['external_actions'], [])
        published = index.ManualIndex.load(self.root / 'availability/index.json')
        self.assertEqual(published.value['metadata']['publication_status'], 'published')
        self.assertEqual(published.value['metadata']['manual_revision'], self.manual_revision)
        self.assertEqual(published.value['metadata']['publication_availability']['sha256'],
            index.file_digest(self.root / 'availability/availability-report.json'))
        self.assertEqual((self.output / 'index.json').read_bytes(), original)
        self.assertEqual(publication.read_json(self.root / 'availability', 'manifest.json')['metadata'], published.value['metadata'])
        self.assertEqual(published.value['metadata']['verification']['metadata'], self.corpus['metadata'])

    def test_content_200_without_authoritative_current_revision_keeps_publication_blocked(self):
        evidence, responses = self.availability_fixtures()
        report = self.verify_availability_fixture(evidence, responses, environment=None)
        self.assertEqual(report['status'], 'publication-blocked')
        self.assertEqual(report['content_availability'], 'verified')
        self.assertEqual(report['deployment_revision'], 'unattested')
        self.assertTrue(report['missing_proof'])
        self.assertFalse((self.root / 'availability/index.json').exists())
        responses['https://api.github.com/repos/Bakney/manuale/deployments/9/statuses?per_page=100'] = index.canonical([
            {'id': 11, 'created_at': '2026-01-02T00:00:00Z', 'state': 'failure', 'environment_url': 'https://manual.invalid'},
            {'id': 10, 'created_at': '2026-01-01T00:00:00Z', 'state': 'success', 'environment_url': 'https://manual.invalid'}]).encode()
        report = self.verify_availability_fixture(evidence, responses, name='failed-deployment')
        self.assertEqual(report['status'], 'publication-blocked')
        self.assertFalse((self.root / 'failed-deployment/index.json').exists())
        responses['https://api.github.com/repos/Bakney/manuale/deployments/9/statuses?per_page=100'] = index.canonical([
            {'id': 12, 'created_at': '2026-01-03T00:00:00Z', 'state': 'success', 'environment_url': 'https://manual.invalid'}]).encode()
        deployments = json.loads(responses['https://api.github.com/repos/Bakney/manuale/deployments?per_page=100'])
        deployments[0]['sha'] = 'd' * 40
        responses['https://api.github.com/repos/Bakney/manuale/deployments?per_page=100'] = index.canonical(deployments).encode()
        report = self.verify_availability_fixture(evidence, responses, name='wrong-deployed-revision')
        self.assertEqual(report['status'], 'publication-blocked')
        self.assertFalse((self.root / 'wrong-deployed-revision/index.json').exists())

    def test_latest_status_is_selected_by_creation_time_instead_of_response_order(self):
        evidence, responses = self.availability_fixtures()
        responses['https://api.github.com/repos/Bakney/manuale/deployments/9/statuses?per_page=100'] = index.canonical([
            {'id': 10, 'created_at': '2026-01-01T00:00:00Z', 'state': 'success', 'environment_url': 'https://manual.invalid'},
            {'id': 11, 'created_at': '2026-01-02T00:00:00Z', 'state': 'failure', 'environment_url': 'https://manual.invalid'}]).encode()
        report = self.verify_availability_fixture(evidence, responses)
        self.assertEqual(report['status'], 'publication-blocked')
        self.assertFalse((self.root / 'availability/index.json').exists())

    def test_later_page_failure_prevents_publication_despite_older_successes(self):
        evidence, responses = self.availability_fixtures()
        endpoint = 'https://api.github.com/repos/Bakney/manuale/deployments/9/statuses?per_page=100'
        responses[endpoint] = index.canonical([{'id': identifier, 'created_at': '2026-01-01T00:00:00Z',
            'state': 'success', 'environment_url': 'https://manual.invalid'} for identifier in range(1, 101)]).encode()
        responses[endpoint + '&page=2'] = index.canonical([{'id': 101, 'created_at': '2026-01-02T00:00:00Z',
            'state': 'failure', 'environment_url': 'https://manual.invalid'}]).encode()
        report = self.verify_availability_fixture(evidence, responses)
        self.assertEqual(report['status'], 'publication-blocked')
        self.assertFalse((self.root / 'availability/index.json').exists())

    def test_changed_png_or_script_only_prose_fails_even_with_successful_revision_labels(self):
        evidence, responses = self.availability_fixtures()
        image_url = 'https://manual.invalid/images/action/1.png'
        responses[image_url] = b'changed PNG fixture bytes'
        report = self.verify_availability_fixture(evidence, responses, name='changed-image')
        self.assertEqual(report['status'], 'availability-failed')
        self.assertFalse((self.root / 'changed-image/index.json').exists())
        responses[image_url] = png()
        responses['https://manual.invalid/docs/action'] = b'<script id="azione">Azione Apri il modulo. Modulo</script>'
        report = self.verify_availability_fixture(evidence, responses, name='hidden-prose')
        self.assertEqual(report['status'], 'availability-failed')
        self.assertFalse((self.root / 'hidden-prose/index.json').exists())

    def test_tampered_prepared_proof_is_rejected_before_reading_https(self):
        self.prepare()
        write(self.output, 'evidence/fixture-action.json', {'tampered': True})
        with patch.object(publication, 'read_https') as reader:
            with self.assertRaisesRegex(index.EvidenceError, 'Prepared package bytes changed'):
                publication.verify_release_availability(self.output, self.application, self.manual,
                    self.root / 'invalid', release='v-fixture', evidence_base='https://evidence.invalid/fixture')
            reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
