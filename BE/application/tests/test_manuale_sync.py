"""Release cache lifecycle, using sealed corpus fixtures and actual files."""
import base64
import copy
import io
import os
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from application.tests import test_manuale as fixtures
from application.manuale.index import ManualIndex, canonical, digest, promote, seal_index
from application.manuale.sync import synchronize


class ManualSyncTests(SimpleTestCase):
    def setUp(self):
        fixtures.ManualIndexTests.setUp(self)
        self.manifest['metadata'].update(application_revision='a' * 40, manual_revision='b' * 40,
            publication_status='published', code_state='committed', manual_state='committed')
        self.value = fixtures.ManualIndexTests.index(self).value
        self.output = self.root / 'runtime/index.json'
        self.assets = self.output.parent / 'assets'
        self.assets.mkdir(parents=True)
        config = override_settings(MANUAL_CORPUS_BASE_URL='https://manual.example/corpus',
            MANUAL_APPLICATION_REVISION='a' * 40, RUNNING_VERSION='v1',
            MANUAL_INDEX_PATH=str(self.output), MANUAL_SOURCE_ROOT=str(self.code))
        config.enable()
        self.addCleanup(config.disable)

    def sync(self, value=None, now=1000000):
        payload = canonical(self.value if value is None else value).encode()
        with patch('application.manuale.sync.urlopen', return_value=io.BytesIO(payload)), \
             patch('application.manuale.sync.time.time', return_value=now):
            return synchronize()

    def cache(self, name, age=0):
        path = self.assets / name
        path.write_bytes(b'old screenshot')
        os.utime(path, (age, age))
        return path

    def test_successful_sync_prunes_old_orphans_but_keeps_recent_and_unowned_files(self):
        old = self.cache('c' * 64 + '.png')
        recent = self.cache('d' * 64 + '.png', 1000000)
        unrelated = self.cache('notes.txt')
        outside = self.root / 'outside.png'
        outside.write_bytes(b'outside')
        link = self.assets / ('e' * 64 + '.png')
        link.symlink_to(outside)
        self.sync()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_bytes(), b'outside')

    def test_retired_active_images_get_a_full_grace_period(self):
        previous = copy.deepcopy(self.value)
        old = self.cache('c' * 64 + '.png')
        previous['chunks'][0]['screenshots'] = [{'sha256': 'c' * 64,
            'path': 'images/old.png', 'url': 'https://manual.example/old.png'}]
        promote(seal_index(previous), self.output)
        self.sync(now=1000000)
        self.assertTrue(old.exists())
        self.sync(now=1000000 + 86400 - 1)
        self.assertTrue(old.exists())
        self.sync(now=1000000 + 86400 + 1)
        self.assertFalse(old.exists())

    def test_active_images_are_never_pruned_and_failed_download_preserves_previous_corpus(self):
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        image = {'sha256': digest(png), 'path': 'images/current.png', 'url': 'https://manual.example/current.png'}
        active = self.assets / (image['sha256'] + '.png')
        active.write_bytes(png)
        os.utime(active, (0, 0))
        value = copy.deepcopy(self.value)
        value['chunks'][0]['screenshots'] = [image]
        value = seal_index(value)
        self.sync(value)
        self.assertEqual(active.read_bytes(), png)
        previous = self.output.read_bytes()
        orphan = self.cache('d' * 64 + '.png')
        with patch('application.manuale.sync.urlopen', return_value=io.BytesIO(b'{broken')):
            with self.assertRaises(ValueError):
                synchronize()
        self.assertEqual(self.output.read_bytes(), previous)
        self.assertEqual(active.read_bytes(), png)
        self.assertTrue(orphan.exists())

    def test_cleanup_error_does_not_roll_back_a_successful_promotion(self):
        orphan = self.cache('c' * 64 + '.png')
        from pathlib import Path
        unlink = Path.unlink
        def fail_orphan(path, *args, **kwargs):
            if path == orphan:
                raise PermissionError('fixture')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_orphan), self.assertLogs('application.manuale.sync', level='WARNING'):
            result = self.sync()
        self.assertEqual(result['status'], 'updated')
        self.assertEqual(ManualIndex.load(self.output).value, self.value)
        self.assertTrue(orphan.exists())

    def test_concurrent_synchronizations_share_the_volume_lock(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        entered = threading.Event()
        release = threading.Event()
        second_entered = threading.Event()
        def first():
            entered.set()
            if not release.wait(5):
                raise AssertionError('fixture timed out')
            return 'first'
        def second():
            second_entered.set()
            return 'second'
        with patch('application.manuale.sync._synchronize_locked',
                   side_effect=lambda: first() if not entered.is_set() else second()):
            with ThreadPoolExecutor(max_workers=2) as executor:
                one = executor.submit(synchronize)
                self.assertTrue(entered.wait(5))
                two = executor.submit(synchronize)
                try:
                    self.assertFalse(second_entered.wait(.2), 'concurrent synchronization bypassed the volume lock')
                finally:
                    release.set()
                self.assertEqual(one.result(timeout=5), 'first')
                self.assertEqual(two.result(timeout=5), 'second')

    def test_retirement_does_not_touch_paths_outside_the_cache(self):
        outside = self.output.parent / 'outside.png'
        outside.write_bytes(b'unrelated')
        os.utime(outside, (1, 1))
        previous = copy.deepcopy(self.value)
        previous['chunks'][0]['screenshots'] = [{'sha256': '../outside',
            'path': 'images/old.png', 'url': 'https://manual.example/old.png'}]
        promote(seal_index(previous), self.output)
        self.sync()
        self.assertEqual(outside.stat().st_mtime, 1)
        self.assertEqual(outside.read_bytes(), b'unrelated')
