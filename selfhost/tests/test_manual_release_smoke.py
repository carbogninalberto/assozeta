"""Focused smoke wiring; real image/startup acceptance remains the appliance run."""
import importlib.util
import ast
import io
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess, CalledProcessError

SPEC = importlib.util.spec_from_file_location('manual_release_smoke', Path(__file__).with_name('manual-release-smoke.py'))
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class ManualReleaseImagesTests(unittest.TestCase):
    def test_both_backends_receive_exact_package_and_required_version_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'package'
            package.mkdir()
            (package / 'index.json').write_bytes(b'authored-index')
            (package / 'screenshots').mkdir()
            (package / 'screenshots' / 'fixture.png').write_bytes(b'authored-image')
            for backend in ('BE', 'BE_GO'):
                target = root / backend / 'release-manual'
                target.mkdir(parents=True)
                (target / 'stale.json').write_text('must not survive package replacement')
            calls = []
            def image(name, context, *args):
                files = {str(p.relative_to(context / 'release-manual')): p.read_bytes()
                         for p in (context / 'release-manual').rglob('*') if p.is_file()}
                calls.append((name, context.name, args, files))
                return 'owned-' + name + ':test'
            result = SMOKE.build_manual_backends(root, package, 'target-version', 'exact-revision', image)
            self.assertEqual(set(result), {'backend', 'backend-go'})
            self.assertEqual(len(calls), 2)
            for _, context, args, files in calls:
                self.assertEqual(files, {'index.json': b'authored-index', 'screenshots/fixture.png': b'authored-image'})
                for value in ('VERSION=target-version', 'CODE_REVISION=exact-revision', 'REQUIRE_MANUAL=true'):
                    self.assertIn(value, args)
                self.assertEqual('--target' in args, context == 'BE')
            self.assertEqual({c[1] for c in calls}, {'BE', 'BE_GO'})

    def test_synthetic_previous_package_has_its_own_go_version_and_owned_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'previous'; package.mkdir()
            (package / 'index.json').write_bytes(b'previous-index')
            for backend in ('BE', 'BE_GO'):
                (root / backend).mkdir()
            calls = []
            def image(name, context, *args):
                calls.append((name, args))
                return name
            result = SMOKE.build_manual_backends(root, package, 'previous-version', 'exact-revision', image, prefix='previous-')
            self.assertEqual(set(result), {'backend', 'backend-go'})
            self.assertTrue(all(name.startswith('previous-') for name, _ in calls))
            self.assertTrue(all('VERSION=previous-version' in args and 'REQUIRE_MANUAL=true' in args for _, args in calls))

    def test_go_build_failure_propagates_before_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); package = root / 'package'; package.mkdir()
            for backend in ('BE', 'BE_GO'):
                (root / backend).mkdir()
            def image(name, context, *args):
                if context.name == 'BE_GO':
                    raise RuntimeError('Go manual gate rejected package')
                return 'owned-django'
            with self.assertRaisesRegex(RuntimeError, 'Go manual gate rejected package'):
                SMOKE.build_manual_backends(root, package, 'target-version', 'exact-revision', image)


class ConcurrentRunTests(unittest.TestCase):
    def test_jobs_overlap_and_results_keep_input_order(self):
        barrier = threading.Barrier(2, timeout=10)
        def paired(delay):
            def job():
                barrier.wait()
                time.sleep(delay)
                return delay
            return job
        self.assertEqual(SMOKE.run_concurrently([paired(.05), paired(0)]), [.05, 0])

    def test_first_failure_in_input_order_is_raised_after_every_job_finishes(self):
        finished = []
        def failing_first():
            finished.append('first')
            raise ValueError('first failure wins')
        def slower_success():
            time.sleep(.05)
            finished.append('second')
            return 'second'
        def failing_last():
            finished.append('third')
            raise RuntimeError('later failure must not be reported')
        with self.assertRaisesRegex(ValueError, 'first failure wins'):
            SMOKE.run_concurrently([failing_first, slower_success, failing_last])
        self.assertEqual(sorted(finished), ['first', 'second', 'third'])

    def test_single_and_empty_job_lists(self):
        self.assertEqual(SMOKE.run_concurrently([lambda: 'only']), ['only'])
        self.assertEqual(SMOKE.run_concurrently([]), [])


class ConcurrentImagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.stream = (self.work / 'execution.log').open('w+b')
        self.addCleanup(self.stream.close)
        self.images = []
        self.builds = SMOKE.ConcurrentImages(self.work, self.stream, self.images)

    def replay(self):
        self.stream.flush()
        self.stream.seek(0)
        return self.stream.read()

    @staticmethod
    def writing_run(delays=None):
        delays = delays or {}
        def run(argv, **kwargs):
            kwargs['stdout'].write((Path(argv[-1]).name + '\n').encode())
            time.sleep(delays.get(Path(argv[-1]).name, 0))
            return CompletedProcess(argv, 0, stdout='', stderr='')
        return run

    def test_private_logs_and_owned_tags_replay_in_job_order(self):
        def slow():
            return self.builds.image('slow', 'slow:test', ['docker', 'build', '-t', 'slow:test', self.work / 'slow'])
        def fast():
            return self.builds.image('fast', 'fast:test', ['docker', 'build', '-t', 'fast:test', self.work / 'fast'])
        with patch.object(SMOKE.subprocess, 'run', side_effect=self.writing_run({'slow': .1})):
            self.assertEqual(self.builds.run([slow, fast]), ['slow:test', 'fast:test'])
        self.assertEqual(self.replay(), b'slow\nfast\n')
        self.assertEqual(self.images, ['slow:test', 'fast:test'])
        # One private file per build: numbering follows whichever build got its
        # log first, so only the per-build content is deterministic.
        self.assertEqual({path.name.split('-', 2)[2]: path.read_bytes()
                          for path in self.work.glob('build-*.log')},
                         {'slow.log': b'slow\n', 'fast.log': b'fast\n'})

    def test_failed_build_log_is_replayed_without_owning_its_tag(self):
        def run(argv, **kwargs):
            kwargs['stdout'].write((Path(argv[-1]).name + '\n').encode())
            if Path(argv[-1]).name == 'broken':
                raise CalledProcessError(1, argv)
            return CompletedProcess(argv, 0, stdout='', stderr='')
        def broken():
            return self.builds.image('broken', 'broken:test', ['docker', 'build', '-t', 'broken:test', self.work / 'broken'])
        def healthy():
            return self.builds.image('healthy', 'healthy:test', ['docker', 'build', '-t', 'healthy:test', self.work / 'healthy'])
        with patch.object(SMOKE.subprocess, 'run', side_effect=run):
            with self.assertRaises(CalledProcessError):
                self.builds.run([broken, healthy])
        self.assertEqual(self.replay(), b'broken\nhealthy\n')
        self.assertEqual(self.images, ['healthy:test'])

    def test_concurrent_backends_keep_the_serial_log_and_cleanup_order(self):
        package = self.work / 'package'; package.mkdir()
        (package / 'index.json').write_bytes(b'index')
        for backend in ('BE', 'BE_GO', 'UI', 'renderer'):
            (self.work / backend).mkdir()
        def image(name, context, *args):
            return self.builds.image(name, name + ':test', ['docker', 'build', '-t', name + ':test', context])
        with patch.object(SMOKE.subprocess, 'run', side_effect=self.writing_run({'BE': .1, 'BE_GO': .05})):
            backends, web, renderer = self.builds.run([
                lambda: SMOKE.build_manual_backends(self.work, package, '1.2.3', 'revision', image, builds=self.builds),
                lambda: image('web', self.work / 'UI'),
                lambda: image('renderer', self.work / 'renderer'),
            ])
        self.assertEqual(backends, {'backend': 'bundled:test', 'backend-go': 'go-bundled:test'})
        self.assertEqual((web, renderer), ('web:test', 'renderer:test'))
        self.assertEqual(self.replay(), b'BE\nBE_GO\nUI\nrenderer\n')
        self.assertEqual(self.images, ['bundled:test', 'go-bundled:test', 'web:test', 'renderer:test'])


class HistoricalRuntimeTests(unittest.TestCase):
    def archive(self):
        files = {'compose.yml': b'services: {api: {image: historical}}\n',
                 'caddy/Caddyfile': b'reverse_proxy api:8000\n',
                 'config/required.env': b'DBNAME\n',
                 'bin/assozeta': b'historical CLI must not replace current gates',
                 'updater/engine.py': b'historical controller'}
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w:gz') as archive:
            for name, content in files.items():
                item = tarfile.TarInfo('selfhost/' + name); item.size = len(content); item.mode = 0o644
                archive.addfile(item, io.BytesIO(content))
        content = data.getvalue()
        return content, {'bundle_sha256': SMOKE.digest(content),
                         'files': {name: SMOKE.digest(value) for name, value in files.items()}}, files

    def test_exact_published_runtime_preserves_current_cli_and_controller(self):
        content, manifest, files = self.archive()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in files:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'current')
            runtime = SMOKE.verified_historical_runtime(content, manifest)
            changed = SMOKE.install_historical_runtime(root, runtime)
            self.assertEqual(set(changed), {'compose.yml', 'caddy/Caddyfile', 'config/required.env'})
            for name in changed:
                self.assertEqual((root/name).read_bytes(), files[name])
            for name in ('bin/assozeta', 'updater/engine.py'):
                self.assertEqual((root/name).read_bytes(), b'current')

    def test_archive_digest_mismatch_rejected_before_install(self):
        content, manifest, _ = self.archive()
        with self.assertRaisesRegex(ValueError, 'archive digest'):
            SMOKE.verified_historical_runtime(content + b'altered', manifest)

    def test_individual_manifest_mismatch_rejected_even_with_valid_archive_digest(self):
        content, manifest, _ = self.archive(); manifest['files']['compose.yml'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'file manifest'):
            SMOKE.verified_historical_runtime(content, manifest)


class RegistryCleanupTests(unittest.TestCase):
    def test_partial_start_registers_cleanup_before_external_docker_command(self):
        tree = ast.parse(Path(SMOKE.__file__).read_text())
        block = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for start, statement in enumerate(node.body):
                if ast.unparse(statement) != "registry_data.mkdir(mode=448)":
                    continue
                for stop in range(start, len(node.body)):
                    candidate = node.body[stop]
                    if (isinstance(candidate, ast.Expr) and isinstance(candidate.value, ast.Call)
                            and ast.unparse(candidate.value.func) == 'command'
                            and "'docker', 'run'" in ast.unparse(candidate)):
                        block = node.body[start:stop + 1]
                        break
        self.assertIsNotNone(block)
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def failed_start(args):
                calls.append(args)
                raise CalledProcessError(125, args)
            state = {'registry_created': False, 'registry_data': Path(directory)/'data',
                     'registry_user': '1001:1001', 'registry_name': 'owned-registry',
                     'registry_port': 12345, 'result': {}, 'command': failed_start}
            with self.assertRaises(CalledProcessError):
                exec(compile(ast.Module(body=block, type_ignores=[]), '<registry admission>', 'exec'), state)
            self.assertTrue(state['registry_created'])
            self.assertEqual(state['result']['registry_resources']['container'], 'owned-registry')
            self.assertEqual(calls[0][calls[0].index('--user') + 1], '1001:1001')
            self.assertEqual(state['registry_data'].stat().st_mode & 0o777, 0o700)

    def test_exact_owned_registry_removes_anonymous_volumes_and_checks_absence(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return CompletedProcess(args, 0, stdout='unrelated-volume\n', stderr='')
        with patch.object(SMOKE.subprocess, 'run', side_effect=run):
            result = SMOKE.cleanup_registry('owned-registry', ['owned-volume'], io.StringIO())
        self.assertTrue(result['verified'])
        self.assertEqual(calls[0], ['docker', 'rm', '-f', '-v', 'owned-registry'])
        self.assertEqual(result['volume_ids'], ['owned-volume'])

    def test_owned_bind_directory_is_removed_only_after_successful_container_removal(self):
        for code in (0, 1):
            with self.subTest(removal_exit=code), tempfile.TemporaryDirectory() as directory:
                data = Path(directory) / 'registry-data'; data.mkdir(); (data/'owned').write_bytes(b'blob')
                responses = [CompletedProcess([], code), CompletedProcess([], 0, stdout='', stderr='')]
                with patch.object(SMOKE.subprocess, 'run', side_effect=responses):
                    result = SMOKE.cleanup_registry('owned-registry', [], io.StringIO(), data_directory=data)
                self.assertEqual(result['verified'], code == 0)
                self.assertEqual(data.exists(), code != 0)

    def test_leftover_owned_volume_fails_cleanup(self):
        responses = [CompletedProcess([], 0), CompletedProcess([], 0, stdout='owned-volume\n', stderr='')]
        with patch.object(SMOKE.subprocess, 'run', side_effect=responses):
            result = SMOKE.cleanup_registry('owned-registry', ['owned-volume'], io.StringIO())
        self.assertFalse(result['verified'])
        self.assertEqual(result['remaining_volume_ids'], ['owned-volume'])

    def test_removal_failure_cannot_report_success_even_if_listing_is_empty(self):
        responses = [CompletedProcess([], 1), CompletedProcess([], 0, stdout='', stderr='')]
        with patch.object(SMOKE.subprocess, 'run', side_effect=responses):
            result = SMOKE.cleanup_registry('owned-registry', ['owned-volume'], io.StringIO())
        self.assertFalse(result['verified'])


if __name__ == '__main__':
    unittest.main()
