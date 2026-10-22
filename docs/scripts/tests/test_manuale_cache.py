"""Portable cache proof uses real Git bundles, the real verifier, and unit PNGs."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import unittest

import test_manuale_reuse as fixtures

spec = importlib.util.spec_from_file_location('manual_portable_cache', fixtures.ROOT / 'docs/scripts/manuale-cache.py')
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class PortableCacheTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReuseFixtureTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_relocated_cache_keeps_original_evidence_without_credentials_or_source_worktree(self):
        f = self.fixture
        (f.old_run / 'browser-input.json').write_text('{"token":"private-fixture-token"}')
        (f.old_run / 'execution.log').write_text('private-fixture-token')
        (f.old_code / '.env').write_text('SECRET=private-fixture-token')
        archive = f.root / 'portable.tar.gz'
        cache.export_cache(f.old_run, archive)
        with tarfile.open(archive) as contents:
            names = contents.getnames()
            self.assertIn('application.bundle', names)
            self.assertFalse(any('.git/' in name or '.env' in name or 'execution.log' in name or 'browser-input' in name for name in names))
            for member in contents:
                self.assertNotIn(b'private-fixture-token', contents.extractfile(member).read(), member.name)
        restored = f.root / 'restored'
        cache.import_cache(archive, restored, f.root / 'manual-source', register=False)
        f.old_run.rename(f.root / 'original-unavailable')
        plan = fixtures.reuse.build_reuse_plan(f.state, restored)
        self.assertEqual(plan['reused_recipes'], ['unit-members'], plan['rejected'])
        self.assertEqual(plan['recipes']['unit-members']['origin']['capture_id'], f.report['capture_id'])
        fixtures.reuse.validate_reuse_plan(f.state)

    def test_incomplete_capture_cannot_replace_existing_cache(self):
        f = self.fixture
        f.manifest['scenarios'] = []
        fixtures.write(f.old_run, 'manifest.json', f.manifest)
        archive = f.root / 'previous-cache.tar.gz'
        archive.write_bytes(b'previous-cache')
        with self.assertRaisesRegex(ValueError, 'complete capture'):
            cache.export_cache(f.old_run, archive)
        self.assertEqual(archive.read_bytes(), b'previous-cache')

    def test_import_rejects_traversal_git_metadata_links_and_private_environment(self):
        f = self.fixture
        for name, kind in [('../escape', tarfile.REGTYPE), ('application/.git/config', tarfile.REGTYPE),
                           ('proof/../../escape', tarfile.REGTYPE), ('application/.env', tarfile.REGTYPE),
                           ('application/symlink', tarfile.SYMTYPE)]:
            with self.subTest(name=name):
                archive = f.root / 'bad.tar.gz'
                with tarfile.open(archive, 'w:gz') as contents:
                    item = tarfile.TarInfo(name)
                    item.type, item.size, item.linkname = kind, 4, '/tmp/escape'
                    contents.addfile(item, io.BytesIO(b'unit'))
                output = f.root / 'rejected'
                with self.assertRaises(ValueError):
                    cache.import_cache(archive, output, f.root / 'manual-source', register=False)
                self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
