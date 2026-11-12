"""Behavioral tests for the opt-in content-addressed binary cache."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from TESTS.adapters.go import binary_cache


def writer(output, payload=b'#!/bin/sh\nexit 0\n', calls=None):
    def build():
        if calls is not None:
            calls.append(payload)
        output.write_bytes(payload)
        output.chmod(0o755)
    return build


class BinaryCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        # The code under test resolves the work directory, and macOS /var is a
        # symlink to /private/var, so compare against the resolved path.
        self.tmp_path = Path(directory.name).resolve()

    def patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher

    def source(self):
        root = self.tmp_path / 'repo'
        tree = root / 'BE_GO'
        tree.mkdir(parents=True)
        (tree / 'main.go').write_text('package main\n')
        self.patch(binary_cache, 'ROOT', root)
        self.patch(binary_cache, '_toolchain_identity', lambda kind, env: 'fake-toolchain')
        return tree

    def test_cache_off_builds_every_time_without_cache_or_subprocess(self):
        self.source()
        cache = self.tmp_path / 'cache'
        output = self.tmp_path / 'server'
        calls = []
        self.patch(binary_cache, 'cache_directory', lambda: None)
        self.patch(binary_cache.subprocess, 'run',
                   lambda *args, **kwargs: self.fail('unexpected subprocess'))

        binary_cache.cached_build('go', output, ['go', 'build', './cmd/server'], {}, writer(output, calls=calls))
        binary_cache.cached_build('go', output, ['go', 'build', './cmd/server'], {}, writer(output, calls=calls))

        self.assertEqual(len(calls), 2)
        self.assertEqual(output.read_bytes(), calls[0])
        self.assertFalse(cache.exists())

    def test_miss_then_hit_reuses_bytes_and_executable_bit(self):
        self.source()
        cache = self.tmp_path / 'cache'
        output = self.tmp_path / 'server'
        calls = []
        self.patch(binary_cache, 'cache_directory', lambda: cache)

        binary_cache.cached_build('go', output, ['go', 'build', './cmd/server'], {}, writer(output, b'first', calls))
        self.assertEqual(len(calls), 1)
        first = output.read_bytes()
        self.assertTrue(output.stat().st_mode & 0o111)

        output.unlink()
        binary_cache.cached_build('go', output, ['go', 'build', './cmd/server'], {}, writer(output, b'second', calls))
        self.assertEqual(len(calls), 1)
        self.assertEqual(output.read_bytes(), first)
        self.assertTrue(output.stat().st_mode & 0o111)

    def test_command_and_tree_changes_produce_different_keys(self):
        source = self.source()
        first = binary_cache.build_key('go', ['go', 'build', './cmd/server'], {})
        self.assertNotEqual(first, binary_cache.build_key('go', ['go', 'build', './cmd/migrate'], {}))
        self.assertNotEqual(first, binary_cache.build_key('go', ['go', 'build', './cmd/server'], {'CGO_ENABLED': '1'}))

        (source / 'main.go').write_text('package main\n// changed\n')
        self.assertNotEqual(first, binary_cache.build_key('go', ['go', 'build', './cmd/server'], {}))

    def test_failing_build_raises_and_leaves_no_entry(self):
        self.source()
        cache = self.tmp_path / 'cache'
        output = self.tmp_path / 'server'
        self.patch(binary_cache, 'cache_directory', lambda: cache)
        key = binary_cache.build_key('go', ['go', 'build', './cmd/server'], {})

        def build():
            raise RuntimeError('build failed')

        with self.assertRaisesRegex(RuntimeError, 'build failed'):
            binary_cache.cached_build('go', output, ['go', 'build', './cmd/server'], {}, build)
        self.assertFalse((cache / key).exists())

    def test_cache_write_error_falls_back_silently(self):
        self.source()
        cache = self.tmp_path / 'cache'
        cache.write_text('not a directory')
        output = self.tmp_path / 'server'
        calls = []
        self.patch(binary_cache, 'cache_directory', lambda: cache)

        binary_cache.cached_build('cc', output, ['cc', 'image-codec'], {}, writer(output, calls=calls))

        self.assertEqual(len(calls), 1)
        self.assertEqual(output.read_bytes(), b'#!/bin/sh\nexit 0\n')

    def test_prune_keeps_newest_key_directories(self):
        cache = self.tmp_path / 'cache'
        cache.mkdir()
        created = []
        for index in range(8):
            directory = cache / f'key-{index}'
            directory.mkdir()
            stamp = 1_600_000_000 + index
            os.utime(directory, ns=(stamp * 10**9, stamp * 10**9))
            created.append(directory)

        binary_cache.prune(cache, keep=3)

        remaining = sorted(path.name for path in cache.iterdir())
        self.assertEqual(remaining, ['key-5', 'key-6', 'key-7'])

    def test_tree_digest_ignores_skipped_directories(self):
        root = self.tmp_path / 'tree'
        root.mkdir()
        (root / 'kept.txt').write_text('kept')
        digest = binary_cache.tree_digest(root)
        for name in ('.git', '__pycache__'):
            nested = root / name
            nested.mkdir()
            (nested / 'ignored.txt').write_text('ignored content')
        self.assertEqual(binary_cache.tree_digest(root), digest)
