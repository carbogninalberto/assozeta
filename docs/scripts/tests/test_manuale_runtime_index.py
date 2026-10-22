"""Runtime reuse must still reject corruption, replacement and stale sources."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'BE'))
from application.manuale.index import ManualIndex, EvidenceError, FORMAT, EMBEDDING, digest, promote, seal_index, build_index, sections
from application.manuale import index as index_module

class RuntimeIndexTests(unittest.TestCase):
    def test_build_shares_section_proofs_but_revalidates_reports_and_sources_on_next_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'BE').mkdir()
            source = root / 'BE/example.py'
            source.write_text('def example():\n    return True\n')
            mdx = '## Primo\n\nPrima spiegazione.\n\n## Secondo\n\nSeconda spiegazione.\n'
            (root / 'guide.mdx').write_text(mdx)
            proof = {'path': 'BE/example.py', 'start': 1, 'end': 2, 'symbol': 'example',
                     'sha256': index_module.file_digest(source)}
            # This is an explicit offline validation fixture, never published evidence.
            report = {'id': 'unit', 'status': 'passed', 'backend': 'real', 'application_revision': 'unit',
                      'source_hashes': {proof['path']: proof['sha256']}, 'screenshots': []}
            report_path = root / 'report.json'
            report_path.write_text(json.dumps(report))
            manifest = {'metadata': {'application_revision': 'unit', 'manual_revision': 'unit',
                                    'release': 'unit', 'manual_url': 'https://example.test'},
                        'scenarios': [{**report, 'report_path': 'report.json',
                                       'report_sha256': index_module.file_digest(report_path)}],
                        'pages': [{'path': 'guide.mdx', 'sections': [
                            {'id': identifier, 'kind': 'workflow', 'status': 'verified',
                             'content_sha256': digest(section['mdx']), 'scenario_ids': ['unit'], 'evidence': [proof]}
                            for identifier, section in sections(mdx).items()]}]}
            with patch.object(index_module, 'verify_source', wraps=index_module.verify_source) as validate, \
                    patch.object(index_module, 'file_digest', wraps=index_module.file_digest) as hash_file:
                self.assertEqual(len(build_index(root, root, manifest, artifact_root=root)['chunks']), 2)
                self.assertEqual(validate.call_count, 1)
                self.assertEqual(sum(Path(call.args[0]).resolve() == report_path.resolve() for call in hash_file.call_args_list), 1)
            report_path.write_text(json.dumps({**report, 'status': 'failed'}))
            with self.assertRaisesRegex(EvidenceError, 'Browser report changed'):
                build_index(root, root, manifest, artifact_root=root)
            report_path.write_text(json.dumps(report))
            source.write_text('def example():\n    return False\n')
            with self.assertRaisesRegex(EvidenceError, 'Stale code evidence'):
                build_index(root, root, manifest, artifact_root=root)

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
