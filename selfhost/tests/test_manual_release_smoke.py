"""Focused smoke wiring; real image/startup acceptance remains the appliance run."""
import contextlib
import importlib.util
import ast
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess, CalledProcessError

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('manual_release_smoke', Path(__file__).with_name('manual-release-smoke.py'))
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)

MERGE_PATH = Path(__file__).with_name('manual-release-merge.py')
MERGE_SPEC = importlib.util.spec_from_file_location('manual_release_merge', MERGE_PATH)
MERGE = importlib.util.module_from_spec(MERGE_SPEC)
MERGE_SPEC.loader.exec_module(MERGE)

CI_SPEC = importlib.util.spec_from_file_location('manuale_ci_merge_tests', ROOT / 'docs/scripts/manuale-ci.py')
CI = importlib.util.module_from_spec(CI_SPEC)
CI_SPEC.loader.exec_module(CI)


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


class UpgradeRejectionTests(unittest.TestCase):
    def test_cli_configuration_uses_the_locally_built_updater(self):
        tree = ast.parse(Path(SMOKE.__file__).read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertTrue(any(ast.unparse(call.func) == 'set_env'
                            and "'ASSOZETA_UPDATER_REF': updater" in ast.unparse(call) for call in calls),
                        'CLI installation env is missing the built updater reference')
        self.assertTrue(any(ast.unparse(call.func) == 'image' and ast.unparse(call.args[0]) == "'updater'"
                            and 'selfhost/updater/Dockerfile' in ast.unparse(call) for call in calls),
                        'rehearsal must build the updater from the checkout')

    def test_both_expected_failures_check_the_upgrade_stage(self):
        tree = ast.parse(Path(SMOKE.__file__).read_text())
        checks = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                  and ast.unparse(node.func) == 'expect_upgrade_rejection']
        self.assertEqual({ast.unparse(call.args[1]) for call in checks},
                         {"'manual-unavailable-candidate'", 'args.release'})
        historical = next(call for call in checks if ast.unparse(call.args[1]) == 'args.release')
        self.assertIn('manual_error', {kw.arg for kw in historical.keywords})

    def test_rejection_requires_the_candidate_rollback_and_requested_manual_error(self):
        rollback = '[assozeta] error: failed to download or validate version next; restored configured version old\n'
        manual = 'Invalid or unsupported manual index'
        for code, output, expected, accepted in (
            (1, 'runtime adoption preflight failed\n', None, False),
            (1, rollback, None, True),
            (1, rollback, manual, False),
            (1, manual, manual, False),
            (1, manual + '\n' + rollback, manual, True),
            (0, manual + '\n' + rollback, manual, False),
        ):
            with self.subTest(code=code, output=output, expected=expected):
                log = io.BytesIO()
                result = CompletedProcess([], code, output.encode())
                with patch.object(SMOKE.subprocess, 'run', return_value=result):
                    with contextlib.nullcontext() if accepted else self.assertRaises(RuntimeError):
                        SMOKE.expect_upgrade_rejection('cli', 'next', 'old', {}, log, manual_error=expected)
                self.assertEqual(log.getvalue(), result.stdout)


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


class ReleasePartArgumentTests(unittest.TestCase):
    """--part splits the full sequence; its default keeps one serial rehearsal."""

    def test_part_defaults_to_today_single_rehearsal(self):
        args = SMOKE.parse_args(['package', '1.2.3'])
        self.assertEqual((args.part, args.scope, args.previous_release), ('all', 'full', '1.0.17'))

    def test_every_part_is_accepted_for_the_full_scope(self):
        for part in ('all', 'current', 'historical'):
            with self.subTest(part=part):
                self.assertEqual(SMOKE.parse_args(['package', '1.2.3', '--part', part]).part, part)
        # The compatibility run stays a single pass under the default part.
        self.assertEqual(SMOKE.parse_args(['package', '1.2.3', '--scope', 'compatibility']).part, 'all')

    def test_historical_part_requires_the_published_full_release(self):
        for extra in (['--scope', 'compatibility'], ['--previous-package', 'previous-package']):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit) as raised:
                    SMOKE.parse_args(['package', '1.2.3', '--part', 'historical', *extra])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn('--part historical', errors.getvalue())

    def test_results_record_the_part_and_the_historical_block_is_never_in_current(self):
        source = Path(SMOKE.__file__).read_text()
        self.assertIn("'part': args.part", source)
        # The historical block runs for all and historical, never for current.
        self.assertIn("if not previous_bundle and args.part != 'current':", source)
        # Both parts keep the production-images record; each really resolves it.
        self.assertEqual(source.count("record('production images built and historical image digests resolved')"), 1)
        self.assertIn('corrupt_candidates(first_image, verify=True)', source)
        self.assertIn('corrupt_candidates(first_image, verify=False)', source)


CURRENT_CHECKS = [
    'production images built and historical image digests resolved',
    'fresh installation: reader, assets, search and real browser assistance without AI or subscription',
    'current distribution: exact healthy Go artifact serves manual HTTP/assets; stopped-Go v2 rejects fallback while Django remains available',
    'container recreation retains the image-matched manual',
    'normal CLI startup recovers activation interrupted between container creation and startup',
    'failed CLI candidate download restores services with the retained compatible manual',
    'missing/corrupt index and PNGs and mismatched version reject candidate startup',
    'failed candidate leaves valid package intact; original image recreates with compatible manual',
]
HISTORICAL_CHECKS = [
    'production images built and historical image digests resolved',
    'ordinary CLI rejects each missing/corrupt manual candidate before migrations or activation and restores the working historical release',
    'real published 1.0.17: upgrade preserves association data and delivers the manual with legacy branding and empty download volume',
    'backup rotation keeps the manual backup across repeated pre-upgrade backups',
    'historical backup rollback restores the original application/data pair and its honest absence of a manual',
]
RELEASE, REVISION = '1.0.18', 'a' * 40


def part_results(part, checks, *, status='passed', release=RELEASE, revision=REVISION, phase='images'):
    value = {'status': status, 'scope': 'full', 'part': part, 'release': release,
             'application_revision': revision, 'corpus_identity': 'identity-' + part,
             'previous_source': {'release': 'v1.0.17', 'manifest': 'manifest-url'}, 'checks': list(checks),
             'timings': [{'phase': phase, 'elapsed_seconds': 1.5}], 'elapsed_seconds': 10.0}
    # Each part also records evidence only it can produce, exactly as the real
    # runs do: the current Go artifact and the installed historical runtime.
    value['current_go_image'] = 'sha256:go' if part == 'current' else None
    value['historical_runtime_installed'] = {'compose.yml': '0' * 64} if part == 'historical' else None
    return {key: item for key, item in value.items() if item is not None}


class ApplianceMergeTests(unittest.TestCase):
    """The gate reads one artifact: both parts must merge into that shape."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_merge(self, current, historical):
        for name, value in (('current', current), ('historical', historical)):
            directory = self.root / name
            directory.mkdir(exist_ok=True)
            (directory / 'results.json').write_text(json.dumps(value))
            # Only results.json may be copied; these must never reach the output.
            (directory / 'private-execution.log').write_text('private')
            (directory / 'fresh' / 'browser').mkdir(parents=True, exist_ok=True)
            (directory / 'fresh' / 'browser' / 'trace.json').write_text('{}')
        output = self.root / 'merged'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
            code = MERGE.main(['--current', str(self.root / 'current'),
                               '--historical', str(self.root / 'historical'), '--output', str(output)])
        self.errors = errors.getvalue()
        return code, output

    def merged(self, current=None, historical=None):
        code, output = self.run_merge(current or part_results('current', CURRENT_CHECKS),
                                      historical or part_results('historical', HISTORICAL_CHECKS))
        self.assertEqual(code, 0, self.errors)
        return json.loads((output / 'results.json').read_text()), output

    def test_merged_result_keeps_the_shape_the_gate_reads(self):
        value, output = self.merged()
        self.assertEqual(sorted(path.name for path in output.iterdir()), ['results.json'])
        self.assertEqual(value['status'], 'passed')
        self.assertEqual((value['release'], value['application_revision'], value['scope']), (RELEASE, REVISION, 'full'))
        self.assertEqual(value['part'], 'merged')
        self.assertEqual(value['previous_source'], part_results('current', CURRENT_CHECKS)['previous_source'])
        self.assertEqual(value['corpus_identity'], 'identity-current')

    def test_part_specific_evidence_from_each_half_is_preserved(self):
        value, _ = self.merged()
        # The current-only Go artifact and the historical-only installed runtime
        # are both evidence of this one release, so neither may be dropped.
        self.assertEqual(value['current_go_image'], 'sha256:go')
        self.assertEqual(value['historical_runtime_installed'], {'compose.yml': '0' * 64})
        self.assertEqual(value['release'], RELEASE)

    def test_checks_are_current_then_historical_with_one_production_images_entry(self):
        value, _ = self.merged()
        self.assertEqual(value['checks'], CURRENT_CHECKS + HISTORICAL_CHECKS[1:])
        self.assertEqual(value['checks'].count(MERGE.DUPLICATED_CHECK), 1)
        self.assertEqual(value['checks'][0], MERGE.DUPLICATED_CHECK)

    def test_merged_checks_satisfy_every_required_release_check(self):
        value, _ = self.merged()
        for check in CI.REQUIRED_RELEASE_CHECKS:
            with self.subTest(check=check):
                self.assertTrue(any(check in item for item in value['checks']), value['checks'])

    def test_timings_keep_both_parts_in_order_and_summarize_them(self):
        value, _ = self.merged()
        self.assertEqual([(entry['part'], entry['phase']) for entry in value['timings']],
                         [('current', 'images'), ('historical', 'images')])
        self.assertEqual(value['parts'], {
            'current': {'part': 'current', 'status': 'passed', 'elapsed_seconds': 10.0,
                        'checks_recorded': len(CURRENT_CHECKS)},
            'historical': {'part': 'historical', 'status': 'passed', 'elapsed_seconds': 10.0,
                           'checks_recorded': len(HISTORICAL_CHECKS)}})

    def test_every_mismatch_fails_without_writing_a_merged_result(self):
        cases = {
            'another release': (part_results('current', CURRENT_CHECKS),
                                part_results('historical', HISTORICAL_CHECKS, release='1.0.17')),
            'another revision': (part_results('current', CURRENT_CHECKS),
                                 part_results('historical', HISTORICAL_CHECKS, revision='b' * 40)),
            'failed part': (part_results('current', CURRENT_CHECKS),
                            part_results('historical', HISTORICAL_CHECKS, status='failed')),
            'swapped parts': (part_results('historical', CURRENT_CHECKS),
                              part_results('current', HISTORICAL_CHECKS)),
            'single part repeated': (part_results('current', CURRENT_CHECKS),
                                     part_results('current', HISTORICAL_CHECKS)),
        }
        for name, (current, historical) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                for part, value in (('current', current), ('historical', historical)):
                    (Path(directory) / part).mkdir()
                    (Path(directory) / part / 'results.json').write_text(json.dumps(value))
                output = Path(directory) / 'merged'
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
                    code = MERGE.main(['--current', str(Path(directory) / 'current'),
                                       '--historical', str(Path(directory) / 'historical'),
                                       '--output', str(output)])
                self.assertEqual(code, 1, name)
                self.assertIn('Merge rejected', errors.getvalue())
                self.assertFalse((output / 'results.json').exists())

    def test_compatibility_and_unreadable_parts_are_rejected(self):
        compatibility = part_results('current', CURRENT_CHECKS)
        compatibility['scope'] = 'compatibility'
        cases = {'compatibility scope': (compatibility, part_results('historical', HISTORICAL_CHECKS)),
                 'missing results': (part_results('current', CURRENT_CHECKS), None)}
        for name, (current, historical) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                for part, value in (('current', current), ('historical', historical)):
                    (Path(directory) / part).mkdir()
                    if value is not None:
                        (Path(directory) / part / 'results.json').write_text(json.dumps(value))
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = MERGE.main(['--current', str(Path(directory) / 'current'),
                                       '--historical', str(Path(directory) / 'historical'),
                                       '--output', str(Path(directory) / 'merged')])
                self.assertEqual(code, 1, name)

    def test_the_command_exits_non_zero_on_a_mismatch(self):
        for part, value in (('current', part_results('current', CURRENT_CHECKS)),
                            ('historical', part_results('historical', HISTORICAL_CHECKS, status='failed'))):
            (self.root / part).mkdir()
            (self.root / part / 'results.json').write_text(json.dumps(value))
        completed = subprocess.run([sys.executable, str(MERGE_PATH), '--current', str(self.root / 'current'),
                                    '--historical', str(self.root / 'historical'),
                                    '--output', str(self.root / 'merged')],
                                   capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('historical part did not pass', completed.stderr)
        self.assertFalse((self.root / 'merged' / 'results.json').exists())


if __name__ == '__main__':
    unittest.main()
