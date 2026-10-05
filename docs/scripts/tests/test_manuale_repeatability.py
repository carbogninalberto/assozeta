"""Synthetic unit bundles check the comparator, never attest browser evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('repeatability_test', ROOT / 'docs/scripts/manuale-repeatability.py')
repeat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repeat)
index = repeat.index


class RepeatabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.before, self.after = self.root / 'before', self.root / 'after'
        self.make(self.before, 'before', 'first-password')
        self.make(self.after, 'after', 'rotated-password')

    def write(self, root, path, value):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(index.canonical(value))

    def make(self, root, nonce, password):
        source = 'BE/application/management/commands/seed_manuale.py'
        state = {'run_id': nonce, 'status': 'evaluated', 'release': 'test', 'reference_date': '2026-09-30',
                 'manual_url': 'https://manual.invalid', 'tooling_hashes': {source: 'a' * 64},
                 'selected_recipes': ['unit'], 'application_input': {'revision': 'a' * 40, 'state': 'committed'},
                 'manual_input': {'revision': 'b' * 40, 'state': 'committed'}}
        self.write(root, 'run.json', state)
        self.write(root, 'browser-input.json', {'fixture_version': 8, 'reference_date': state['reference_date'],
            'origin': 'http://localhost/' + nonce, 'login_password': password, 'token': nonce,
            'refresh_token': nonce, 'association_id': 'stable-fixture',
            'identities': {'reader': {'user_id': 'stable-reader', 'token': nonce, 'refresh_token': nonce}}})
        for relative in ('captures/images/unit/1.png', 'masters/images/unit/1.png'):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (1920, 1080), 'white').save(target)
        sha = index.file_digest(root / 'captures/images/unit/1.png')
        capture = {'path': 'images/unit/1.png', 'sha256': sha, 'width': 1920, 'height': 1080,
                   'checkpoint': 'saved', 'master': {'path': 'masters/images/unit/1.png', 'sha256': sha,
                                                   'width': 1920, 'height': 1080}}
        report = {'id': 'unit', 'status': 'passed', 'backend': 'real', 'evidence_kind': 'offline-unit-fixture',
                  'capture_id': nonce, 'source_hashes': {'BE/source.py': 'a' * 64},
                  'saved': {'persisted': True, 'amount': 25}, 'screenshots': [capture]}
        self.write(root, 'unit.json', report)
        metadata = {'application_revision': 'a' * 40, 'manual_revision': 'b' * 40,
                    'release': 'test', 'manual_url': 'http://localhost/' + nonce}
        value = index.seal_index({'format': index.FORMAT, 'embedding': index.EMBEDDING,
            'metadata': metadata, 'dependencies': {}, 'gaps': [], 'semantics': {},
            'chunks': [{'id': 'docs/unit#saved', 'content_sha256': index.digest('Salva.'),
                       'text': 'Salva.', 'evidence': [], 'features': [], 'audience': 'public', 'screenshots': [capture],
                       'url': metadata['manual_url'] + '/docs/unit#saved',
                       'capture_provenance': [{'capture_id': nonce,
                           'report_sha256': index.file_digest(root / 'unit.json'), 'scenario_id': 'unit'}]}]})
        self.write(root, 'index.json', value)
        self.write(root, 'manifest.json', {'metadata': metadata, 'scenarios': [{'id': 'unit',
            'report_path': 'unit.json', 'report_sha256': index.file_digest(root / 'unit.json')}]})
        self.write(root, 'render.json', {'status': 'passed'})
        self.write(root, 'evaluation.json', {'status': 'passed', 'corpus_identity': value['identity']})
        self.write(root, 'embedded-manual.json', {'status': 'passed', 'backend': 'real'})

    def rewrite_report(self, root, mutate):
        report = repeat.read(root, 'unit.json')
        mutate(report)
        self.write(root, 'unit.json', report)
        manifest = repeat.read(root, 'manifest.json')
        manifest['scenarios'][0]['report_sha256'] = index.file_digest(root / 'unit.json')
        self.write(root, 'manifest.json', manifest)
        value = repeat.read(root, 'index.json')
        value['chunks'][0]['capture_provenance'][0].update(
            capture_id=report['capture_id'], report_sha256=manifest['scenarios'][0]['report_sha256'])
        value['chunks'][0]['screenshots'] = copy.deepcopy(report['screenshots'])
        value = index.seal_index(value)
        self.write(root, 'index.json', value)
        self.write(root, 'evaluation.json', {'status': 'passed', 'corpus_identity': value['identity']})

    def test_rotated_credentials_and_nonces_preserve_semantics_and_original_provenance(self):
        report = repeat.compare(self.before, self.after)
        self.assertEqual(report['status'], 'passed')
        self.assertTrue(all(report['checks'].values()))
        self.assertNotEqual(report['original_provenance']['before']['corpus_identity'],
                            report['original_provenance']['after']['corpus_identity'])
        self.assertNotIn('first-password', index.canonical(report))
        self.assertNotIn('rotated-password', index.canonical(report))

    def test_changed_saved_outcome_and_removed_chunk_do_not_normalize_away(self):
        self.rewrite_report(self.after, lambda report: report['saved'].update(amount=30))
        result = repeat.compare(self.before, self.after)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['checks']['semantic_evidence'])
        value = repeat.read(self.after, 'index.json')
        value['chunks'][0]['text'] = 'Non salvare.'
        value = index.seal_index(value)
        self.write(self.after, 'index.json', value)
        self.write(self.after, 'evaluation.json', {'status': 'passed', 'corpus_identity': value['identity']})
        self.assertFalse(repeat.compare(self.before, self.after)['checks']['semantic_corpus'])

    def test_changed_fixture_identity_or_input_invalidates_identical_inputs(self):
        value = repeat.read(self.after, 'browser-input.json')
        value['association_id'] = 'another-association'
        self.write(self.after, 'browser-input.json', value)
        self.assertFalse(repeat.compare(self.before, self.after)['checks']['fixture'])
        state = repeat.read(self.after, 'run.json')
        state['application_input']['uncommitted_files'] = {'BE/source.py': 'c' * 64}
        self.write(self.after, 'run.json', state)
        self.assertFalse(repeat.compare(self.before, self.after)['checks']['inputs'])

    def test_recorded_fixture_retains_identity_after_cleanup_without_secrets(self):
        for root in (self.before, self.after):
            identity = repeat.fixture_identity(root, repeat.read(root, 'run.json'))
            self.write(root, 'fixture-identity.json', identity)
            (root / 'browser-input.json').unlink()
        self.assertEqual(repeat.compare(self.before, self.after)['status'], 'passed')
        self.assertNotIn('token', (self.after / 'fixture-identity.json').read_text())

    def test_real_pixel_variance_passes_bounded_drift_and_fails_large_drift(self):
        def change_pixels(count):
            for relative in ('captures/images/unit/1.png', 'masters/images/unit/1.png'):
                with Image.open(self.after / relative) as image:
                    for pixel in range(count):
                        image.putpixel((pixel % 1920, pixel // 1920), (0, 0, 0))
                    image.save(self.after / relative)
            sha = index.file_digest(self.after / 'captures/images/unit/1.png')
            def mutate(report):
                report['screenshots'][0]['sha256'] = sha
                report['screenshots'][0]['master']['sha256'] = sha
            self.rewrite_report(self.after, mutate)
        change_pixels(1)
        result = repeat.compare(self.before, self.after)
        self.assertEqual(result['status'], 'passed')
        self.assertFalse(result['screenshots'][0]['identical_bytes'])
        change_pixels(3000)
        self.assertEqual(repeat.compare(self.before, self.after)['status'], 'failed')

    def test_incomplete_tampered_and_simulated_bundles_are_rejected(self):
        self.rewrite_report(self.after, lambda report: report.update(backend='simulated'))
        with self.assertRaisesRegex(ValueError, 'real scenarios'):
            repeat.compare(self.before, self.after)
        self.make(self.after, 'after', 'password')
        (self.after / 'unit.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'bytes differ'):
            repeat.compare(self.before, self.after)
        self.make(self.after, 'after', 'password')
        self.write(self.after, 'render.json', {'status': 'failed'})
        with self.assertRaisesRegex(ValueError, 'passed render'):
            repeat.compare(self.before, self.after)


if __name__ == '__main__':
    unittest.main()
