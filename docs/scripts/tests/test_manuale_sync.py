"""Validate actual release refresh and public results without Django/network."""
import copy
import base64
import importlib.util
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]


def load_modules():
    package = types.ModuleType('manual_sync_offline')
    package.__path__ = [str(ROOT / 'BE/application/manuale')]
    django = types.ModuleType('django')
    conf = types.ModuleType('django.conf')
    conf.settings = types.SimpleNamespace()
    modules = {'manual_sync_offline': package, 'django': django, 'django.conf': conf}
    with patch.dict(sys.modules, modules):
        loaded = {}
        for name in ('index', 'answers', 'sync', 'tools'):
            qualified = 'manual_sync_offline.' + name
            spec = importlib.util.spec_from_file_location(qualified, ROOT / ('BE/application/manuale/' + name + '.py'))
            module = importlib.util.module_from_spec(spec)
            sys.modules[qualified] = module
            spec.loader.exec_module(module)
            loaded[name] = module
    return loaded


MODULES = load_modules()
index, sync, tools = (MODULES[name] for name in ('index', 'sync', 'tools'))


class ManualSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'BE/source.py'
        self.source.parent.mkdir()
        self.source.write_text('def verified_action(): return True\n')
        self.settings = types.SimpleNamespace(MANUAL_CORPUS_BASE_URL='https://manual.invalid/corpus',
            MANUAL_APPLICATION_REVISION='a' * 40, RUNNING_VERSION='v1', MANUAL_RUN_ID='',
            MANUAL_INDEX_PATH=str(self.root / 'index.json'), MANUAL_SOURCE_ROOT=str(self.root))
        self.chunk = {'id': 'docs/action#azione', 'page': 'docs/action', 'section': 'azione',
            'title': 'Azione', 'page_title': 'Azioni', 'text': 'Apri il modulo.', 'intent': '',
            'status': 'verified', 'url': 'https://manual.invalid/docs/action#azione', 'language': 'it',
            'screenshots': [], 'scenario_ids': [], 'features': ['self_hosted'], 'audience': 'public',
            'content_sha256': index.digest('Apri il modulo.'),
            'evidence': [{'path': 'BE/source.py', 'sha256': index.file_digest(self.source)}]}
        self.value = index.seal_index({'format': index.FORMAT, 'embedding': index.EMBEDDING,
            'metadata': {'application_revision': 'a' * 40, 'manual_revision': 'b' * 40, 'release': 'v1',
                'publication_status': 'published', 'code_state': 'committed', 'manual_state': 'committed'},
            'chunks': [self.chunk], 'dependencies': {}, 'gaps': [], 'semantics': {}})
        index.promote(self.value, self.settings.MANUAL_INDEX_PATH)
        self.previous = Path(self.settings.MANUAL_INDEX_PATH).read_bytes()

    def download(self, value, images=None):
        def response(url, **kwargs):
            if url == 'https://manual.invalid/corpus/' + 'a' * 40 + '/v1.json':
                return io.BytesIO(index.canonical(value).encode())
            if images is None or url not in images:
                raise OSError('Screenshot download unavailable')
            return io.BytesIO(images[url])
        with patch.object(sync, 'settings', self.settings), patch.object(sync, 'urlopen',
                side_effect=response):
            return sync.synchronize()

    def image_candidate(self):
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        image = {'path': 'images/action/1.png', 'url': 'https://manual.invalid/images/action/1.png',
                 'sha256': index.digest(png)}
        value = copy.deepcopy(self.value)
        value['chunks'][0]['screenshots'] = [image]
        return index.seal_index(value), image, png

    def test_clean_install_then_process_restart_serves_retained_verified_images(self):
        value, image, png = self.image_candidate()
        Path(self.settings.MANUAL_INDEX_PATH).unlink()
        result = self.download(value, {image['url']: png})
        self.assertEqual(result['screenshots'], 1)
        with patch.object(sync, 'settings', self.settings):
            self.assertEqual(sync.cached_asset_path(image).read_bytes(), png)
        # A new index instance and refresh need no image network access.
        loaded = index.ManualIndex.load(self.settings.MANUAL_INDEX_PATH)
        self.assertEqual(loaded.value['identity'], value['identity'])
        self.assertEqual(self.download(value)['status'], 'updated')

    def test_missing_or_corrupt_image_does_not_replace_previous_index(self):
        value, image, png = self.image_candidate()
        for payload in (None, {image['url']: b'corrupted screenshot'}):
            with self.subTest(payload=payload), self.assertRaises((OSError, index.EvidenceError)):
                self.download(value, payload)
            self.assertEqual(Path(self.settings.MANUAL_INDEX_PATH).read_bytes(), self.previous)
        self.download(value, {image['url']: png})
        good = Path(self.settings.MANUAL_INDEX_PATH).read_bytes()
        updated = copy.deepcopy(value)
        updated['chunks'][0]['screenshots'][0]['sha256'] = 'd' * 64
        with self.assertRaises(index.EvidenceError):
            self.download(index.seal_index(updated), {image['url']: b'changed bytes'})
        self.assertEqual(Path(self.settings.MANUAL_INDEX_PATH).read_bytes(), good)
        with patch.object(sync, 'settings', self.settings):
            self.assertEqual(sync.cached_asset_path(image).read_bytes(), png)

    def test_corrupt_cached_image_is_repaired_from_verified_bytes(self):
        value, image, png = self.image_candidate()
        self.download(value, {image['url']: png})
        with patch.object(sync, 'settings', self.settings):
            target = sync.cached_asset_path(image)
        target.write_bytes(b'disk corruption')
        self.download(value, {image['url']: png})
        self.assertEqual(target.read_bytes(), png)

    def test_unpublished_dirty_or_wrong_manual_revision_cannot_install(self):
        for key, invalid in [('publication_status', 'prepared'), ('code_state', 'working_tree'),
                             ('manual_state', 'working_tree'), ('manual_revision', 'not-a-commit')]:
            value = copy.deepcopy(self.value)
            value['metadata'][key] = invalid
            with self.subTest(key=key), self.assertRaises(index.EvidenceError):
                self.download(index.seal_index(value))
            self.assertEqual(Path(self.settings.MANUAL_INDEX_PATH).read_bytes(), self.previous)

    def test_unsafe_asset_hash_url_and_non_png_never_promote(self):
        original, image, png = self.image_candidate()
        for key, invalid in [('sha256', '../escape'), ('url', 'http://manual.invalid/image.png'),
                             ('url', 'https://user:password@manual.invalid/image.png'), ('path', 'image.svg'),
                             ('url', None), ('path', 42)]:
            value = copy.deepcopy(original)
            value['chunks'][0]['screenshots'][0][key] = invalid
            with self.subTest(key=key, invalid=invalid), self.assertRaises(index.EvidenceError):
                self.download(index.seal_index(value), {image['url']: png})
            self.assertEqual(Path(self.settings.MANUAL_INDEX_PATH).read_bytes(), self.previous)

    def test_oversized_asset_and_https_downgrade_are_rejected(self):
        value, image, png = self.image_candidate()
        with patch.object(sync, 'MAX_IMAGE_BYTES', 8), self.assertRaises(index.EvidenceError):
            self.download(value, {image['url']: png})
        with patch.object(sync, 'settings', self.settings), self.assertRaises(index.EvidenceError):
            sync.ManualRedirectHandler().redirect_request(None, None, 302, '', {}, 'http://manual.invalid/image.png')
        self.assertEqual(Path(self.settings.MANUAL_INDEX_PATH).read_bytes(), self.previous)

    def test_publisher_outage_stops_after_one_bounded_image_batch(self):
        value, image, _ = self.image_candidate()
        value['chunks'][0]['screenshots'] = [
            {**image, 'path': f'images/{number}.png', 'sha256': index.digest(str(number))}
            for number in range(30)]
        with patch.object(sync, 'install_image', side_effect=OSError('publisher unavailable')) as install:
            with self.assertRaises(OSError):
                self.download(index.seal_index(value))
        self.assertLessEqual(install.call_count, 8)
        self.assertGreater(install.call_count, 0)
        self.assertEqual(Path(self.settings.MANUAL_INDEX_PATH).read_bytes(), self.previous)

    def test_stale_candidate_is_rejected_and_previous_usable_corpus_is_retained(self):
        candidate = copy.deepcopy(self.value)
        candidate['chunks'][0]['evidence'][0]['sha256'] = 'c' * 64
        with self.assertRaisesRegex(index.EvidenceError, 'stale implementation evidence'):
            self.download(index.seal_index(candidate))
        self.assertEqual(Path(self.settings.MANUAL_INDEX_PATH).read_bytes(), self.previous)
        previous = index.ManualIndex.load(self.settings.MANUAL_INDEX_PATH)
        self.assertEqual(len(previous.applicable(revision='a' * 40, release='v1',
            features=['self_hosted'], code_root=self.root)), 1)

    def test_current_candidate_promotes_without_weakening_exact_release_or_revision(self):
        candidate = copy.deepcopy(self.value)
        candidate['metadata']['manual_revision'] = 'c' * 40
        self.assertEqual(self.download(index.seal_index(candidate))['status'], 'updated')
        promoted = Path(self.settings.MANUAL_INDEX_PATH).read_bytes()
        for field, value in [('release', 'v2'), ('application_revision', 'd' * 40)]:
            incompatible = copy.deepcopy(candidate)
            incompatible['metadata'][field] = value
            with self.assertRaises(index.EvidenceError):
                self.download(index.seal_index(incompatible))
            self.assertEqual(Path(self.settings.MANUAL_INDEX_PATH).read_bytes(), promoted)

    def test_public_result_exposes_configuration_requirements_without_code_evidence(self):
        corpus = index.ManualIndex(self.value)
        result = tools._public(self.chunk, corpus)
        self.assertEqual(result['features'], ['self_hosted'])
        self.assertEqual(result['audience'], 'public')
        self.assertEqual(result['application_revision'], 'a' * 40)
        self.assertNotIn('evidence', result)
        result['features'].append('client-forged')
        self.assertEqual(self.chunk['features'], ['self_hosted'])


if __name__ == '__main__':
    unittest.main()
