"""Behavioral tests for the opt-in content-addressed binary cache."""
import os
from pathlib import Path

import pytest

from TESTS.adapters.go import binary_cache


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    tree = root / 'BE_GO'
    tree.mkdir(parents=True)
    (tree / 'main.go').write_text('package main\n')
    monkeypatch.setattr(binary_cache, 'ROOT', root)
    monkeypatch.setattr(binary_cache, '_toolchain_identity', lambda kind, env: 'fake-toolchain')
    return tree


def writer(output, payload=b'#!/bin/sh\nexit 0\n', calls=None):
    def build():
        if calls is not None:
            calls.append(payload)
        output.write_bytes(payload)
        output.chmod(0o755)
    return build


def test_cache_off_builds_every_time_without_cache_or_subprocess(tmp_path, source, monkeypatch):
    cache = tmp_path / 'cache'
    output = tmp_path / 'server'
    calls = []
    monkeypatch.setattr(binary_cache, 'cache_directory', lambda: None)
    monkeypatch.setattr(binary_cache.subprocess, 'run',
                        lambda *args, **kwargs: pytest.fail('unexpected subprocess'))

    binary_cache.cached_build('go', output, ['go', 'build', './cmd/server'], {}, writer(output, calls=calls))
    binary_cache.cached_build('go', output, ['go', 'build', './cmd/server'], {}, writer(output, calls=calls))

    assert len(calls) == 2
    assert output.read_bytes() == calls[0]
    assert not cache.exists()


def test_miss_then_hit_reuses_bytes_and_executable_bit(tmp_path, source, monkeypatch):
    cache = tmp_path / 'cache'
    output = tmp_path / 'server'
    calls = []
    monkeypatch.setattr(binary_cache, 'cache_directory', lambda: cache)

    binary_cache.cached_build('go', output, ['go', 'build', './cmd/server'], {}, writer(output, b'first', calls))
    assert len(calls) == 1
    first = output.read_bytes()
    assert output.stat().st_mode & 0o111

    output.unlink()
    binary_cache.cached_build('go', output, ['go', 'build', './cmd/server'], {}, writer(output, b'second', calls))
    assert len(calls) == 1
    assert output.read_bytes() == first
    assert output.stat().st_mode & 0o111


def test_command_and_tree_changes_produce_different_keys(tmp_path, source):
    first = binary_cache.build_key('go', ['go', 'build', './cmd/server'], {})
    assert first != binary_cache.build_key('go', ['go', 'build', './cmd/migrate'], {})
    assert first != binary_cache.build_key('go', ['go', 'build', './cmd/server'], {'CGO_ENABLED': '1'})

    (source / 'main.go').write_text('package main\n// changed\n')
    assert first != binary_cache.build_key('go', ['go', 'build', './cmd/server'], {})


def test_failing_build_raises_and_leaves_no_entry(tmp_path, source, monkeypatch):
    cache = tmp_path / 'cache'
    output = tmp_path / 'server'
    monkeypatch.setattr(binary_cache, 'cache_directory', lambda: cache)
    key = binary_cache.build_key('go', ['go', 'build', './cmd/server'], {})

    def build():
        raise RuntimeError('build failed')

    with pytest.raises(RuntimeError, match='build failed'):
        binary_cache.cached_build('go', output, ['go', 'build', './cmd/server'], {}, build)
    assert not (cache / key).exists()


def test_cache_write_error_falls_back_silently(tmp_path, source, monkeypatch):
    cache = tmp_path / 'cache'
    cache.write_text('not a directory')
    output = tmp_path / 'server'
    calls = []
    monkeypatch.setattr(binary_cache, 'cache_directory', lambda: cache)

    binary_cache.cached_build('cc', output, ['cc', 'image-codec'], {}, writer(output, calls=calls))

    assert len(calls) == 1
    assert output.read_bytes() == b'#!/bin/sh\nexit 0\n'


def test_prune_keeps_newest_key_directories(tmp_path):
    cache = tmp_path / 'cache'
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
    assert remaining == ['key-5', 'key-6', 'key-7']


def test_tree_digest_ignores_skipped_directories(tmp_path):
    root = tmp_path / 'tree'
    root.mkdir()
    (root / 'kept.txt').write_text('kept')
    digest = binary_cache.tree_digest(root)
    for name in ('.git', '__pycache__'):
        nested = root / name
        nested.mkdir()
        (nested / 'ignored.txt').write_text('ignored content')
    assert binary_cache.tree_digest(root) == digest
