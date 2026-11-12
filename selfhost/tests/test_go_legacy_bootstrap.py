"""Offline released-controller admission tests for the additional Go image.

Frozen v1.0.17 code checks the actual archive and launches its verified target
CLI. Only release catalog/download responses are synthetic. The harmless target
CLI records the invocation: this is not an application upgrade/rollback test.
"""
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

FIXTURE = Path(__file__).with_name('fixtures') / 'go-legacy-bootstrap-v1.0.17'
RELEASE_COMMIT = '14a23c36d2f03ee453f903c07b503460717a79f1'


def historical_namespace():
    """Load reviewed excerpts without importing mutable current updater code."""
    provenance = json.loads((FIXTURE / 'provenance.json').read_text())
    if provenance['commit'] != RELEASE_COMMIT or provenance['release_tag'] != 'v1.0.17':
        raise AssertionError('Unexpected historical fixture revision')
    namespace = {'re': re, 'hashlib': hashlib, 'io': io, 'tarfile': tarfile,
                 'Path': Path, 'PurePosixPath': PurePosixPath, 'os': os,
                 'tempfile': tempfile, 'TemporaryDirectory': tempfile.TemporaryDirectory,
                 'subprocess': subprocess}
    for name in ('release_catalog.py', 'distribution.py', 'common.py', 'bootstrap.py'):
        source = (FIXTURE / name).read_bytes()
        if hashlib.sha256(source).hexdigest() != provenance['files'][name]['frozen_sha256']:
            raise AssertionError('Historical fixture changed: ' + name)
        exec(compile(source, str(FIXTURE / name), 'exec'), namespace)
    return namespace


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as target:
        for name, content in files.items():
            entry = tarfile.TarInfo('selfhost/' + name)
            entry.size, entry.mode = len(content), 0o755
            target.addfile(entry, io.BytesIO(content))
    return output.getvalue()


class HistoricalGoBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.legacy = historical_namespace()
        self.directory = tempfile.TemporaryDirectory(prefix='assozeta-old-bootstrap-')
        self.addCleanup(self.directory.cleanup)
        self.work = Path(self.directory.name)
        self.root = self.work / 'installation'
        (self.root / 'bin').mkdir(parents=True)
        (self.root / 'bin/assozeta').write_text('unchanged installed CLI')
        self.environment = self.root / 'instance.env'
        self.environment.write_text('ASSOZETA_VERSION=1.0.17\nCUSTOM_SETTING=preserved\n')
        self.receipt = self.work / 'target-launch.json'
        self.cli = b'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ['BOOTSTRAP_TEST_RECEIPT']).write_text(json.dumps({
    'argv': sys.argv[1:],
    'updater': os.environ['ASSOZETA_VERIFIED_UPDATER_REF'],
    'installation': os.environ['ASSOZETA_INSTALL_ROOT'],
    'environment': os.environ['ASSOZETA_ENV_FILE'],
}))
'''
        self.files = {'bin/assozeta': self.cli}
        self.bundle = archive(self.files)
        self.metadata = {
            'schema_version': 1, 'version': '1.0.18',
            'bundle_sha256': self.legacy['digest'](self.bundle),
            'files': {name: self.legacy['digest'](value) for name, value in self.files.items()},
            'images': {name: f'ghcr.io/carbogninalberto/assozeta-{name}@sha256:' + 'a' * 64
                       for name in ('backend', 'web', 'renderer', 'updater')},
            'extensions': {'go_backend': {'schema_version': 1,
                'image': 'ghcr.io/carbogninalberto/assozeta-backend-go@sha256:' + 'b' * 64}},
        }

    def launch(self, metadata=None, bundle=None):
        metadata = self.metadata if metadata is None else metadata
        bundle = self.bundle if bundle is None else bundle
        def read_metadata(url):
            self.assertEqual(url, self.legacy['RELEASES_URL'] + '/download/v1.0.18/assozeta-update.json')
            return metadata
        def download(url):
            self.assertEqual(url, self.legacy['RELEASES_URL'] + '/download/v1.0.18/assozeta-selfhost-v1.0.18.tar.gz')
            return bundle
        def resolve(version):
            self.assertEqual(version, '1.0.18')
            return 'v1.0.18'
        with patch.dict(self.legacy, resolve_tag=resolve, read_json=read_metadata, download=download), \
             patch.dict(os.environ, BOOTSTRAP_TEST_RECEIPT=str(self.receipt)):
            self.legacy['bootstrap'](self.root, self.environment, '1.0.18')

    def assert_installation_unchanged(self):
        self.assertEqual((self.root / 'bin/assozeta').read_text(), 'unchanged installed CLI')
        self.assertEqual(self.environment.read_text(), 'ASSOZETA_VERSION=1.0.17\nCUSTOM_SETTING=preserved\n')
        self.assertFalse((self.root / '.updater/pending-distribution').exists())
        self.assertEqual(list((self.root / '.updater').glob('bootstrap-*')), [])

    def test_fifth_core_image_is_rejected_before_target_launch(self):
        metadata = copy.deepcopy(self.metadata)
        metadata['images']['backend-go'] = metadata['extensions']['go_backend']['image']
        with self.assertRaisesRegex(self.legacy['ReleaseError'], 'exactly the supported image'):
            self.launch(metadata)
        self.assertFalse(self.receipt.exists())
        self.assert_installation_unchanged()

    def test_four_core_images_with_go_extension_launch_verified_target_cli(self):
        self.launch()
        self.assertEqual(json.loads(self.receipt.read_text()), {
            'argv': ['upgrade', '1.0.18'], 'updater': self.metadata['images']['updater'],
            'installation': str(self.root.resolve()), 'environment': str(self.environment.resolve()),
        })
        self.assert_installation_unchanged()

    def test_historical_four_image_manifest_still_launches(self):
        metadata = copy.deepcopy(self.metadata)
        del metadata['extensions']
        self.launch(metadata)
        self.assertEqual(json.loads(self.receipt.read_text())['updater'], metadata['images']['updater'])
        self.assert_installation_unchanged()

    def test_extension_does_not_bypass_validation_of_any_core_image(self):
        for name in ('backend', 'web', 'renderer', 'updater'):
            with self.subTest(image=name):
                metadata = copy.deepcopy(self.metadata)
                metadata['images'][name] = 'untrusted.invalid/arbitrary:latest'
                with self.assertRaisesRegex(self.legacy['ReleaseError'], 'invalid image reference'):
                    self.launch(metadata)
                self.assertFalse(self.receipt.exists())
                self.assert_installation_unchanged()

    def test_historical_loader_does_not_validate_extension_target_must_do_so(self):
        metadata = copy.deepcopy(self.metadata)
        metadata['extensions']['go_backend']['image'] = 'untrusted.invalid/arbitrary:latest'
        self.launch(metadata)
        self.assertTrue(self.receipt.is_file())
        self.assert_installation_unchanged()

    def test_bundle_digest_failure_prevents_target_launch(self):
        metadata = copy.deepcopy(self.metadata)
        metadata['bundle_sha256'] = '0' * 64
        with self.assertRaisesRegex(self.legacy['ReleaseError'], 'checksum mismatch'):
            self.launch(metadata)
        self.assertFalse(self.receipt.exists())
        self.assert_installation_unchanged()

    def test_file_manifest_failure_prevents_target_launch(self):
        metadata = copy.deepcopy(self.metadata)
        metadata['files']['bin/assozeta'] = '0' * 64
        with self.assertRaisesRegex(self.legacy['ReleaseError'], 'verification failed'):
            self.launch(metadata)
        self.assertFalse(self.receipt.exists())
        self.assert_installation_unchanged()

    def test_unsafe_archive_path_prevents_target_launch(self):
        bundle = archive({'../escape': b'unsafe', 'bin/assozeta': self.cli})
        metadata = copy.deepcopy(self.metadata)
        metadata['bundle_sha256'] = self.legacy['digest'](bundle)
        with self.assertRaisesRegex(self.legacy['ReleaseError'], 'Unsafe release archive path'):
            self.launch(metadata, bundle)
        self.assertFalse(self.receipt.exists())
        self.assertFalse((self.work / 'escape').exists())
        self.assert_installation_unchanged()


if __name__ == '__main__':
    unittest.main()
