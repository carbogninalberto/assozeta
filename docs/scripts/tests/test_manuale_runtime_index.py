"""Runtime reuse must still reject corruption, replacement and stale sources."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'BE'))
from application.manuale.index import ManualIndex, EvidenceError, FORMAT, EMBEDDING, digest, promote, seal_index

class RuntimeIndexTests(unittest.TestCase):
    def test_cache_detects_same_size_corruption_even_when_mtime_is_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'index.json'
            value = seal_index({'format': FORMAT, 'embedding': EMBEDDING,
                'metadata': {'application_revision': 'a' * 40, 'release': 'v1'},
                'dependencies': {}, 'chunks': [], 'gaps': []})
            promote(value, path)
            first = ManualIndex.load_runtime(path)
            self.assertIs(ManualIndex.load_runtime(path), first)
            stamp = path.stat()
            path.write_text(path.read_text().replace('"v1"', '"v2"'))
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            with self.assertRaises(EvidenceError):
                ManualIndex.load_runtime(path)
            replacement = seal_index({**value, 'metadata': {**value['metadata'], 'release': 'v2'}})
            promote(replacement, path)
            self.assertEqual(ManualIndex.load_runtime(path).value['metadata']['release'], 'v2')
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                ManualIndex.load_runtime(path)

    def test_cached_index_still_checks_current_application_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'BE/example.py'
            source.parent.mkdir()
            source.write_text('original')
            chunk = {'id': 'docs/example#step', 'content_sha256': 'b' * 64,
                'audience': 'public', 'features': [], 'status': 'verified',
                'evidence': [{'path': 'BE/example.py', 'sha256': digest(b'original')}]}
            path = root / 'index.json'
            promote(seal_index({'format': FORMAT, 'embedding': EMBEDDING,
                'metadata': {'application_revision': 'a' * 40, 'release': 'v1'},
                'dependencies': {}, 'chunks': [chunk], 'gaps': []}), path)
            context = {'revision': 'a' * 40, 'release': 'v1', 'code_root': root}
            self.assertEqual(len(ManualIndex.load_runtime(path).applicable(**context)), 1)
            source.write_text('changed')
            self.assertEqual(ManualIndex.load_runtime(path).applicable(**context), [])

if __name__ == '__main__':
    unittest.main()
