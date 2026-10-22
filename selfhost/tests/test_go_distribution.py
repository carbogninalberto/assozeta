"""Additive Go-image delivery contracts; no network, Docker or installation used."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')]

import build_manifest
import cleanup
import distribution
from common import read_env
from release_catalog import IMAGE_NAMES, ReleaseError

GO_REF = 'ghcr.io/carbogninalberto/assozeta-backend-go@sha256:' + 'b' * 64
OLD_GO_REF = 'ghcr.io/carbogninalberto/assozeta-backend-go@sha256:' + 'c' * 64


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as tar:
        for name, content in files.items():
            info = tarfile.TarInfo('selfhost/' + name)
            info.size = len(content)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(content))
    return output.getvalue()


def metadata(content, files):
    return {'schema_version': 1, 'version': '1.0.2',
            'bundle_sha256': distribution.digest(content),
            'images': {name: f'ghcr.io/carbogninalberto/assozeta-{name}@sha256:' + 'a' * 64
                       for name in ('backend', 'web', 'renderer', 'updater')},
            'extensions': {'go_backend': {'schema_version': 1, 'image': GO_REF}},
            'files': {name: distribution.digest(value) for name, value in files.items()}}


class GoManifestTests(unittest.TestCase):
    def setUp(self):
        self.files = {'compose.yml': b'target-compose'}
        self.content = archive(self.files)
        self.manifest = metadata(self.content, self.files)

    def test_four_core_images_remain_unchanged_and_historical_metadata_is_readable(self):
        self.assertEqual(IMAGE_NAMES, ('backend', 'web', 'renderer', 'updater'))
        historical = copy.deepcopy(self.manifest)
        historical.pop('extensions')
        self.assertIs(distribution.validate_manifest(historical, 'v1.0.2'), historical)
        self.assertIs(distribution.validate_manifest(self.manifest, 'v1.0.2', require_go_backend=True), self.manifest)

    def test_target_requires_extension_and_present_extension_is_always_validated(self):
        for extensions in (None, [], {}, {'go_backend': None}, {'go_backend': []},
                           {'go_backend': {'schema_version': 2, 'image': GO_REF}},
                           {'go_backend': {'schema_version': True, 'image': GO_REF}},
                           {'go_backend': {'schema_version': 1.0, 'image': GO_REF}},
                           {'go_backend': {'schema_version': 1, 'image': GO_REF, 'command': 'unsafe'}}):
            with self.subTest(extensions=extensions):
                candidate = {**self.manifest, 'extensions': extensions}
                with self.assertRaises(ReleaseError):
                    distribution.validate_manifest(candidate, 'v1.0.2', require_go_backend=True)
                if extensions != {}:
                    with self.assertRaises(ReleaseError):
                        distribution.validate_manifest(candidate, 'v1.0.2')
        historical = copy.deepcopy(self.manifest)
        historical.pop('extensions')
        with self.assertRaises(ReleaseError):
            distribution.validate_manifest(historical, 'v1.0.2', require_go_backend=True)

    def test_go_reference_is_exact_official_digest_not_arbitrary_registry_tag_or_command(self):
        for reference in (None, 1, GO_REF + '\n', GO_REF + ' ', GO_REF.replace('carbogninalberto', 'attacker'),
                          GO_REF.replace('backend-go', 'backend'), GO_REF.replace('@sha256:', ':sha256:'),
                          GO_REF[:-1], GO_REF.replace('b' * 64, 'B' * 64),
                          'ghcr.io/carbogninalberto/assozeta-backend-go:latest'):
            candidate = copy.deepcopy(self.manifest)
            candidate['extensions']['go_backend']['image'] = reference
            with self.subTest(reference=reference), self.assertRaises(ReleaseError):
                distribution.validate_manifest(candidate, 'v1.0.2')

    def test_builder_publishes_extension_only_after_all_five_immutable_refs_resolve(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'bundle.tgz'
            path.write_bytes(self.content)
            def reference(name, tag):
                return GO_REF if name == 'backend-go' else self.manifest['images'][name]
            with patch('build_manifest.image_reference', side_effect=reference) as resolve:
                result = build_manifest.build('v1.0.2', path)
            self.assertEqual(result, self.manifest)
            self.assertEqual(resolve.call_args_list, [call(name, 'v1.0.2') for name in (*IMAGE_NAMES, 'backend-go')])

    def test_builder_refuses_missing_or_invalid_go_image(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'bundle.tgz'
            path.write_bytes(self.content)
            for value in (None, 'ghcr.io/carbogninalberto/assozeta-backend-go:latest'):
                def reference(name, tag):
                    return value if name == 'backend-go' else self.manifest['images'][name]
                with self.subTest(value=value), patch('build_manifest.image_reference', side_effect=reference):
                    with self.assertRaises(ReleaseError):
                        build_manifest.build('v1.0.2', path)


class GoDistributionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = self.root / '.env'
        self.env.write_bytes(b'# operator settings\nASSOZETA_VERSION=1.0.1\nCUSTOM=preserve\n')
        self.env.chmod(0o640)
        (self.root / 'compose.yml').write_bytes(b'source-compose')
        (self.root / '.updater').mkdir()
        self.baseline = {'compose.yml': distribution.digest(b'source-compose')}
        (self.root / '.updater/managed.json').write_text(json.dumps(self.baseline))
        self.files = {'compose.yml': b'target-compose', 'bin/assozeta': b'target-cli'}
        self.content = archive(self.files)
        self.manifest = metadata(self.content, self.files)

    def prepare(self, pull=None):
        with patch('distribution.resolve_tag', return_value='v1.0.2'), \
                patch('distribution.read_json', return_value=self.manifest), \
                patch('distribution.download', return_value=self.content), \
                patch('distribution.subprocess.run', side_effect=pull) as docker:
            distribution.prepare(self.root, self.env, '1.0.2')
        return docker

    def test_missing_or_invalid_target_extension_aborts_before_pull_and_mutation(self):
        original = self.env.read_bytes()
        for extension in (None, {'schema_version': 1, 'image': 'untrusted:latest'}):
            if extension is None:
                self.manifest.pop('extensions', None)
            else:
                self.manifest['extensions'] = {'go_backend': extension}
            with self.subTest(extension=extension), patch('distribution.subprocess.run') as docker:
                with self.assertRaises(ReleaseError):
                    self.prepare(lambda *args, **kwargs: docker(*args, **kwargs))
                docker.assert_not_called()
                self.assertEqual(self.env.read_bytes(), original)
                self.assertEqual((self.root / 'compose.yml').read_bytes(), b'source-compose')
                self.assertFalse((self.root / '.updater/pending-distribution').exists())

    def test_five_pulls_and_manual_check_precede_files_environment_and_snapshot_publication(self):
        original = self.env.read_bytes()
        observed = []
        expected = [['docker', 'pull', reference]
                    for reference in (*self.manifest['images'].values(), GO_REF)]
        expected.append(['sh', str(ROOT / 'selfhost/bin/check-manual-image'),
                         self.manifest['images']['backend'], '1.0.2'])
        def pull(command, **kwargs):
            self.assertLess(len(observed), len(expected))
            self.assertEqual(command, expected[len(observed)])
            self.assertTrue(kwargs['check'])
            self.assertEqual(self.env.read_bytes(), original)
            self.assertEqual((self.root / 'compose.yml').read_bytes(), b'source-compose')
            self.assertFalse((self.root / '.updater/pending-distribution').exists())
            observed.append(command)
        self.prepare(pull)
        self.assertEqual(observed, expected)
        self.assertEqual(read_env(self.env)['ASSOZETA_BACKEND_GO_REF'], GO_REF)
        self.assertEqual(read_env(self.env)['CUSTOM'], 'preserve')
        self.assertEqual((self.root / '.updater/pending-distribution/environment').read_bytes(), original)
        transaction = json.loads((self.root / '.updater/pending-distribution/transaction.json').read_text())
        self.assertEqual(transaction['target_go_backend_ref'], GO_REF)
        distribution.rollback(self.root)
        self.assertEqual(self.env.read_bytes(), original)
        self.assertEqual(self.env.stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.root / 'compose.yml').read_bytes(), b'source-compose')
        self.assertFalse((self.root / 'bin/assozeta').exists())

    def test_failed_fifth_pull_leaves_source_retryable_and_without_pending_transaction(self):
        original = self.env.read_bytes()
        def pull(command, **kwargs):
            if command[-1] == GO_REF:
                raise subprocess.CalledProcessError(1, ['docker', 'pull', 'synthetic-go-image'])
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare(pull)
        self.assertEqual(self.env.read_bytes(), original)
        self.assertEqual((self.root / 'compose.yml').read_bytes(), b'source-compose')
        self.assertFalse((self.root / '.updater/pending-distribution').exists())
        self.prepare()
        self.assertEqual(read_env(self.env)['ASSOZETA_BACKEND_GO_REF'], GO_REF)

    def test_rollback_restores_previous_go_ref_and_legacy_source_metadata_bytes(self):
        self.env.write_text(self.env.read_text() + 'ASSOZETA_BACKEND_GO_REF=' + OLD_GO_REF + '\n')
        original = self.env.read_bytes()
        old_metadata = b'{"schema_version":1,"images":{},"legacy-source":true}\n'
        target = self.root / '.updater/target-manifest.json'
        target.write_bytes(old_metadata)
        self.prepare()
        self.assertEqual(read_env(self.env)['ASSOZETA_BACKEND_GO_REF'], GO_REF)
        distribution.rollback(self.root)
        self.assertEqual(self.env.read_bytes(), original)
        self.assertEqual(target.read_bytes(), old_metadata)

    def test_apply_failure_after_environment_write_restores_absent_go_ref(self):
        original = self.env.read_bytes()
        write = distribution.write_json
        def fail_target(path, value):
            if Path(path).name == 'target-manifest.json':
                self.assertEqual(read_env(self.env)['ASSOZETA_BACKEND_GO_REF'], GO_REF)
                raise OSError('synthetic metadata disk failure')
            return write(path, value)
        with patch('distribution.write_json', side_effect=fail_target), self.assertRaises(OSError):
            self.prepare()
        self.assertEqual(self.env.read_bytes(), original)
        self.assertFalse((self.root / '.updater/pending-distribution').exists())
        self.assertEqual((self.root / 'compose.yml').read_bytes(), b'source-compose')


class GoCleanupTests(unittest.TestCase):
    def test_historical_environment_has_four_refs_and_go_ref_is_opt_in(self):
        historical = {'ASSOZETA_VERSION': '1.0.1'}
        self.assertEqual(len(list(cleanup.references(historical))), 4)
        current = {**historical, 'ASSOZETA_BACKEND_GO_REF': GO_REF}
        self.assertEqual(list(cleanup.references(current))[-1], GO_REF)
        tagged = {**historical, 'ASSOZETA_BACKEND_GO_IMAGE': 'ghcr.io/carbogninalberto/assozeta-backend-go'}
        self.assertEqual(list(cleanup.references(tagged))[-1], 'ghcr.io/carbogninalberto/assozeta-backend-go:1.0.1')

    def test_protects_current_and_previous_go_and_skips_all_pending_cleanup(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = root / '.env'
            env.write_text('ASSOZETA_VERSION=1.0.2\nASSOZETA_BACKEND_GO_REF=' + GO_REF + '\n')
            history = root / '.updater/distribution-history/100'
            history.mkdir(parents=True)
            (history / 'environment').write_text('ASSOZETA_VERSION=1.0.1\nASSOZETA_BACKEND_GO_REF=' + OLD_GO_REF + '\n')
            def docker(*args):
                return '' if args == ('ps', '-aq') else json.dumps([{'Id': args[-1]}])
            with patch('cleanup.docker', side_effect=docker):
                protected = cleanup.protected_images(root, env)
            self.assertIn(GO_REF, protected)
            self.assertIn(OLD_GO_REF, protected)
            (root / '.updater/pending-distribution').mkdir()
            with patch('cleanup.docker') as docker, self.assertRaises(RuntimeError):
                cleanup.cleanup_images(root, env)
            docker.assert_not_called()

    def test_obsolete_go_image_is_removed_without_force_but_shared_image_is_retained(self):
        old = 'ghcr.io/carbogninalberto/assozeta-backend-go:1.0.0'
        images = {'old': {'RepoTags': [old]}, 'shared': {'RepoTags': [old, 'custom:keep']}}
        def docker(*args):
            if args[:2] == ('image', 'ls'):
                return 'current previous old shared'
            if args[:2] == ('image', 'inspect'):
                return json.dumps([images[args[-1]]])
            return ''
        with patch('cleanup.protected_images', return_value={'current', 'previous'}), \
                patch('cleanup.docker', side_effect=docker) as command:
            cleanup.cleanup_images('/unused', '/unused')
        self.assertEqual([item.args for item in command.call_args_list if item.args[:2] == ('image', 'rm')],
                         [('image', 'rm', old)])


if __name__ == '__main__':
    unittest.main()
