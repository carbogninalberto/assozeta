"""Selective and sharded browser capture: partitioning, merge validation and cleanup."""
import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
runner_spec = importlib.util.spec_from_file_location('manuale_runner_shards', SCRIPTS / 'manuale.py')
runner = importlib.util.module_from_spec(runner_spec)
runner_spec.loader.exec_module(runner)
# The same module object the runner uses, so exception classes and patches agree.
shards = runner.capture_shards

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + b'\x00\x00\x07\x80\x00\x00\x048' + b'fixture'
REVISION = 'a' * 40


def digest(data):
    return hashlib.sha256(data).hexdigest()


def recipe(identifier, checkpoints=2, fixture=None):
    value = {'id': identifier, 'script': identifier + '.mjs', 'capture_prefix': f'images/{identifier}/',
             'checkpoints': [{'id': f'step-{number}'} for number in range(1, checkpoints + 1)]}
    if fixture:
        value['fixture'] = fixture
    return value


REGISTRY = [recipe('alpha', 4), recipe('bravo', 1), recipe('charlie', 3, 'member-transfer'),
            recipe('delta', 2), recipe('echo', 5)]


def write_capture(directory, item, *, capture_id, extra=None, viewport=None):
    """The files and report a real scenario writes into its ASSOZETA_MANUAL_RUN directory."""
    directory = Path(directory)
    screenshots = []
    for number, point in enumerate(item['checkpoints'], start=1):
        relative = f"{item['capture_prefix']}{number}.png"
        image = PNG + (item['id'] + str(number)).encode()
        master = PNG + ('master' + item['id'] + str(number)).encode()
        for path, data in ((directory / 'captures' / relative, image), (directory / 'masters' / relative, master),
                           (directory / 'masters/repeats' / relative, master)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        screenshots.append({'path': relative, 'sha256': digest(image), 'checkpoint': point['id'],
                            'master': {'path': 'masters/' + relative, 'sha256': digest(master)},
                            'stability': {'path': 'masters/repeats/' + relative, 'sha256': digest(master)}})
    report = {'id': item['id'], 'status': 'passed', 'application_revision': REVISION, 'capture_id': capture_id,
              'tooling_hashes': {}, 'source_hashes': {}, 'fixture_version': 8, 'reference_date': '2026-09-30',
              'fixture_profile': item.get('fixture', 'baseline'), 'viewport': viewport or {'width': 1920, 'height': 1080},
              'locale': 'it-IT', 'timezone': 'Europe/Rome', 'theme': 'light', 'device_scale_factor': 1,
              'backend': 'real', 'capture_format': 'full-hd-v1', 'screenshots': screenshots, 'checks': ['fixture'],
              'expected_denials': []}
    if extra:
        report.update(extra(directory))
    (directory / (item['id'] + '.json')).write_text(json.dumps(report, indent=2))
    return report


def capture_state(directory, selected):
    run = Path(directory)
    (run / 'application/selfhost').mkdir(parents=True)
    env = run / 'application/selfhost/.env.dev'
    env.write_text('COMPOSE_PROJECT_NAME=assozeta-manuale-abcdef012345\nDBNAME=manuale_abcdef012345\n'
                   'DEV_UI_PORT=5100\nAPP_URL=http://127.0.0.1:5100\nDBUSER=fixture\n')
    override = {'services': {name: {'image': 'assozeta-manuale-backend:abcdef012345',
                                    'volumes': [f'{run}:/manual-run', f'{run}/manual:/manual-source',
                                                f'{run}/application:/manual-code:ro']}
                             for name in ('api', 'migrate', 'worker', 'beat')}}
    (run / 'compose.override.json').write_text(json.dumps(override))
    return {'run': str(run), 'run_id': 'abcdef012345', 'project': 'assozeta-manuale-abcdef012345',
            'application': str(run / 'application'), 'application_input': {'revision': REVISION},
            'env_file': str(env), 'override': str(run / 'compose.override.json'),
            'selected_recipes': list(selected), 'tooling_hashes': {}, 'origin': 'http://127.0.0.1:5100',
            'reference_date': '2026-09-30'}


class FakeServices:
    """Executes nothing; scenario commands write the outputs a real browser run produces."""

    def __init__(self, fail=(), fail_labels=(), teardown_fails=False):
        self.labels, self.fail, self.fail_labels, self.teardown_fails = [], set(fail), fail_labels, teardown_fails
        self.lock = __import__('threading').Lock()

    def execute(self, command, run, label, env=None, **kwargs):
        with self.lock:
            self.labels.append(label)
        if any(re.search(pattern, label) for pattern in self.fail_labels):
            raise RuntimeError('fixture failure: ' + label)
        if self.teardown_fails and label.endswith('remove its services and data volumes'):
            raise RuntimeError('fixture teardown failure')
        match = re.search(r'[Vv]erify real application scenario (\S+)$', label)
        if match:
            directory = Path(env['ASSOZETA_MANUAL_RUN'])
            state = json.loads((directory / 'run.json').read_text())
            item = next(entry for entry in REGISTRY if entry['id'] == match.group(1))
            if item['id'] in self.fail:
                diagnostics = directory / 'diagnostics' / item['id'] / 'owner.png'
                diagnostics.parent.mkdir(parents=True, exist_ok=True)
                diagnostics.write_bytes(PNG)
                (directory / (item['id'] + '.json')).write_text(json.dumps({'id': item['id'], 'status': 'failed'}))
                raise RuntimeError('browser failure ' + item['id'])
            write_capture(directory, item, capture_id=state['capture_id'])


def tree(run):
    run = Path(run)
    files = {}
    for root in ('captures', 'masters', 'diagnostics'):
        for path in sorted((run / root).rglob('*')):
            if path.is_file():
                files[str(path.relative_to(run))] = digest(path.read_bytes())
    for path in sorted(run.glob('*.json')):
        if path.stem in {item['id'] for item in REGISTRY}:
            report = json.loads(path.read_text())
            report.pop('capture_id', None)
            files[path.name] = report
    return files


def run_capture(state, services, concurrency=None):
    if concurrency is not None:
        state['capture_concurrency'] = concurrency
    with patch.object(runner, 'validate_retained_evidence', return_value=None), \
            patch.object(runner, 'recipe_registry', return_value={'recipes': REGISTRY}), \
            patch.object(runner, 'execute', side_effect=services.execute), \
            patch.object(runner, 'remember_run'), \
            patch.object(shards.subprocess, 'check_output', return_value=''):
        runner.capture(state)


class SelectionTests(unittest.TestCase):
    def test_workflow_subset_from_cli_or_environment(self):
        self.assertEqual(shards.parse_workflow_list(['bravo,alpha', 'alpha  charlie']), ['alpha', 'bravo', 'charlie'])
        self.assertEqual(shards.selected_workflows(['delta'], {shards.WORKFLOWS_ENV: 'alpha'}), ['delta'])
        self.assertEqual(shards.selected_workflows([], {shards.WORKFLOWS_ENV: 'echo, alpha'}), ['alpha', 'echo'])
        self.assertEqual(shards.selected_workflows([], {}), [])
        for invalid in ('../alpha', 'Alpha', 'alpha;rm'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                shards.parse_workflow_list(invalid)

    def test_cli_workflows_option_merges_with_repeated_workflow(self):
        captured = {}

        def prepare(args):
            captured['workflow'] = args.workflow
            raise ValueError('stop after selection')
        for argv, environ, expected in (
                (['prepare', '--workflows', 'echo,alpha', '--workflow', 'bravo'], {}, ['alpha', 'bravo', 'echo']),
                (['prepare'], {shards.WORKFLOWS_ENV: 'delta,alpha'}, ['alpha', 'delta']),
                (['prepare'], {}, None)):
            with self.subTest(argv=argv), patch.object(runner.sys, 'argv', ['manuale.py', *argv]), \
                    patch.dict(runner.os.environ, environ, clear=False), \
                    patch.object(runner, 'prepare', side_effect=prepare):
                if shards.WORKFLOWS_ENV not in environ:
                    runner.os.environ.pop(shards.WORKFLOWS_ENV, None)
                with self.assertRaises(SystemExit), patch.object(runner.sys, 'stderr'):
                    runner.main()
                self.assertEqual(captured['workflow'], expected)

    def test_concurrency_defaults_to_sequential_and_auto_is_bounded(self):
        self.assertEqual(shards.resolve_concurrency(None, environ={}), 1)
        self.assertEqual(shards.resolve_concurrency(None, environ={shards.CONCURRENCY_ENV: '3'}), 3)
        self.assertEqual(shards.resolve_concurrency('2', environ={shards.CONCURRENCY_ENV: '3'}), 2)
        gib = 1024 ** 3
        self.assertEqual(shards.resolve_concurrency('auto', environ={}, resources=(4, 16 * gib)), 2)
        self.assertEqual(shards.resolve_concurrency('auto', environ={}, resources=(4, 7 * gib)), 1)
        self.assertEqual(shards.resolve_concurrency('auto', environ={}, resources=(64, 256 * gib)), shards.MAX_CONCURRENCY)
        self.assertEqual(shards.resolve_concurrency('auto', environ={}, resources=(1, gib)), 1)
        for invalid in ('0', '-1', '5', 'many', '1.5'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                shards.resolve_concurrency(invalid, environ={})


class PartitionTests(unittest.TestCase):
    def test_partition_is_deterministic_complete_and_registry_ordered(self):
        selected = ['echo', 'alpha', 'charlie', 'delta', 'bravo']
        plan = shards.partition(REGISTRY, selected, 2)
        self.assertEqual(plan, shards.partition(REGISTRY, list(reversed(selected)), 2))
        self.assertEqual(sorted(sum(plan, [])), sorted(selected))
        order = [item['id'] for item in REGISTRY]
        for shard in plan:
            self.assertEqual(shard, sorted(shard, key=order.index))
        loads = [sum(shards.workflow_weight(next(r for r in REGISTRY if r['id'] == i)) for i in shard) for shard in plan]
        self.assertLessEqual(max(loads) - min(loads), max(shards.workflow_weight(r) for r in REGISTRY))
        self.assertEqual(plan, [['bravo', 'delta', 'echo'], ['alpha', 'charlie']])

    def test_partition_never_creates_empty_shards_and_rejects_unknown_workflows(self):
        self.assertEqual(shards.partition(REGISTRY, ['delta'], 4), [['delta']])
        self.assertEqual(shards.partition(REGISTRY, [], 2), [])
        self.assertEqual(len(shards.partition(REGISTRY, ['alpha', 'bravo', 'delta'], 4)), 3)
        with self.assertRaisesRegex(ValueError, 'Unknown workflows: missing'):
            shards.partition(REGISTRY, ['missing'], 2)
        with self.assertRaises(shards.ShardError):
            shards.partition([recipe('alpha'), recipe('alpha')], ['alpha'], 2)

    def test_real_registry_partition_is_stable(self):
        registry = json.loads((ROOT / 'docs/manuale/recipes.json').read_text())['recipes']
        ids = [item['id'] for item in registry]
        plan = shards.partition(registry, ids, 2)
        self.assertEqual(plan, shards.partition(registry, sorted(ids), 2))
        self.assertEqual(sorted(sum(plan, [])), sorted(ids))
        self.assertEqual(len(plan), 2)


class IsolationTests(unittest.TestCase):
    def test_shard_configuration_owns_project_ports_origin_and_run_mount(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = capture_state(temporary, ['alpha'])
            shard_dir = Path(temporary) / 'capture-shards/1'
            ports = {'DEV_UI_PORT': 6101, 'DEV_API_PORT': 6102, 'DEV_MINIO_CONSOLE_PORT': 6103}
            text, origin = shards.shard_env(Path(state['env_file']).read_text(), project=state['project'] + '-s1',
                                            ports=ports, shard_dir=shard_dir, env_file=shard_dir / 'shard.env')
            values = dict(shards.read_env(text))
            self.assertEqual(origin, 'http://127.0.0.1:6101')
            self.assertEqual(values['COMPOSE_PROJECT_NAME'], 'assozeta-manuale-abcdef012345-s1')
            self.assertEqual(values['APP_URL'], origin)
            self.assertEqual(values['DEV_API_PORT'], '6102')
            # Same disposable-database guard name, inside the shard's own Postgres service.
            self.assertEqual(values['DBNAME'], 'manuale_abcdef012345')
            self.assertEqual(values['ASSOZETA_ENV_FILE'], str(shard_dir / 'shard.env'))
            self.assertEqual(values['ASSOZETA_SELFHOST_DIR'], str(shard_dir))
            override = shards.shard_override(json.loads(Path(state['override']).read_text()), run=temporary,
                                             shard_dir=shard_dir, cpus='1.5', memory='3g')
            for name in ('api', 'migrate', 'worker', 'beat'):
                volumes = override['services'][name]['volumes']
                self.assertIn(f'{shard_dir}:/manual-run', volumes)
                self.assertNotIn(f'{temporary}:/manual-run', volumes)
                self.assertIn(f'{temporary}/application:/manual-code:ro', volumes)
            self.assertEqual(override['services']['api']['cpus'], 1.5)
            self.assertEqual(override['services']['postgres']['mem_limit'], '3g')
            with self.assertRaises(shards.ShardError):
                shards.shard_override({'services': {'api': {'volumes': ['/elsewhere:/manual-run']}}},
                                      run=temporary, shard_dir=shard_dir)

    def test_teardown_refuses_unowned_projects_and_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = capture_state(temporary, ['alpha'])
            good = {'index': 1, 'project': state['project'] + '-s1', 'dir': str(Path(temporary) / 'capture-shards/1')}
            shards.check_owned(state, good)
            for record in ({**good, 'project': 'assozeta-dev'}, {**good, 'project': state['project']},
                           {**good, 'dir': temporary}):
                with self.subTest(record=record), self.assertRaises(shards.ShardError):
                    shards.check_owned(state, record)


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = {'capture_id': 'c' * 16, 'application_input': {'revision': REVISION}, 'tooling_hashes': {}}
        self.by_id = {item['id']: item for item in REGISTRY}

    def tearDown(self):
        self.temporary.cleanup()

    def shard(self, index, workflows, **options):
        directory = self.root / str(index)
        directory.mkdir(exist_ok=True)
        for identifier in workflows:
            write_capture(directory, self.by_id[identifier], capture_id=self.state['capture_id'], **options)
        return {'index': index, 'dir': str(directory), 'workflows': list(workflows),
                'scenarios': [{'id': identifier, 'status': 'passed'} for identifier in workflows]}

    def test_valid_shards_merge_in_registry_order_with_every_artifact(self):
        plan = [self.shard(0, ['alpha', 'delta']), self.shard(1, ['bravo', 'charlie'])]
        scenarios, copies = shards.validate_merge(REGISTRY, plan, self.state)
        self.assertEqual([item['id'] for item in scenarios], ['alpha', 'bravo', 'charlie', 'delta'])
        files = copies[1][1]
        self.assertIn('bravo.json', files)
        self.assertIn('captures/images/charlie/3.png', files)
        self.assertIn('masters/repeats/images/charlie/3.png', files)
        target = self.root / '0'  # shard 0 runs in the primary run directory
        shards.copy_validated(target, copies)
        self.assertEqual((target / 'captures/images/bravo/1.png').read_bytes(),
                         (self.root / '1/captures/images/bravo/1.png').read_bytes())
        self.assertTrue((target / 'charlie.json').is_file())
        shards.validate_merge(REGISTRY, [{**plan[0], 'workflows': ['alpha', 'bravo', 'charlie', 'delta'],
                                          'scenarios': scenarios}], self.state)

    def assertRejected(self, pattern, plan):
        with self.assertRaisesRegex(shards.ShardError, pattern):
            shards.validate_merge(REGISTRY, plan, self.state)

    def test_duplicate_unassigned_and_missing_results_are_rejected(self):
        first, second = self.shard(0, ['alpha']), self.shard(1, ['bravo'])
        self.assertRejected('Duplicate workflow result', [first, {**second, 'scenarios': second['scenarios'] * 2}])
        self.assertRejected('assigned to several shards', [first, {**second, 'workflows': ['alpha', 'bravo']}])
        self.assertRejected('unassigned shard', [first, {**second, 'scenarios': [*second['scenarios'],
                                                                                {'id': 'echo', 'status': 'passed'}]}])
        self.assertRejected('Incomplete shard results: bravo', [first, {**second, 'scenarios': []}])
        self.assertRejected('Capture batch failed for bravo',
                            [first, {**second, 'scenarios': [{'id': 'bravo', 'status': 'failed'}]}])
        (Path(second['dir']) / 'bravo.json').unlink()
        self.assertRejected('Missing capture report for bravo', [first, second])

    def test_missing_or_altered_screenshots_are_rejected(self):
        plan = [self.shard(0, ['alpha']), self.shard(1, ['bravo'])]
        image = Path(plan[1]['dir']) / 'masters/repeats/images/bravo/1.png'
        image.write_bytes(PNG + b'altered')
        self.assertRejected('hash mismatch.*masters/repeats/images/bravo/1.png', plan)
        image.unlink()
        self.assertRejected('Missing artifact.*masters/repeats/images/bravo/1.png', plan)
        report_file = Path(plan[0]['dir']) / 'alpha.json'
        report = json.loads(report_file.read_text())
        report['screenshots'] = report['screenshots'][:-1]
        report_file.write_text(json.dumps(report))
        self.assertRejected('Incomplete screenshots for alpha', plan)

    def test_mismatched_schema_identity_or_environment_is_rejected(self):
        plan = [self.shard(0, ['alpha']), self.shard(1, ['bravo'], viewport={'width': 1280, 'height': 720})]
        self.assertRejected('environments differ: viewport', plan)
        plan = [self.shard(0, ['alpha']), self.shard(1, ['delta'])]
        report_file = Path(plan[1]['dir']) / 'delta.json'
        for mutate, pattern in ((lambda report: report.pop('capture_format'), 'schema mismatch for delta'),
                                (lambda report: report.update(capture_id='d' * 16), 'disagrees on capture_id'),
                                (lambda report: report.update(application_revision='b' * 40), 'disagrees on application_revision'),
                                (lambda report: report.update(id='alpha'), 'identity mismatch'),
                                (lambda report: report.update(fixture_profile='member-transfer'), 'Wrong fixture profile')):
            write_capture(Path(plan[1]['dir']), self.by_id['delta'], capture_id=self.state['capture_id'])
            report = json.loads(report_file.read_text())
            mutate(report)
            report_file.write_text(json.dumps(report))
            with self.subTest(pattern=pattern):
                self.assertRejected(pattern, plan)

    def test_conflicting_or_unregistered_artifacts_are_rejected(self):
        def pdf(directory):
            target = directory / 'receipt-pdfs/shared.pdf'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'%PDF-fixture')
            return {'receipt_pdf': {'path': 'receipt-pdfs/shared.pdf', 'sha256': digest(b'%PDF-fixture')}}
        self.assertRejected('claimed by alpha and bravo', [self.shard(0, ['alpha'], extra=pdf), self.shard(1, ['bravo'], extra=pdf)])

        def unknown(directory):
            (directory / 'untracked-root').mkdir(exist_ok=True)
            (directory / 'untracked-root/file.bin').write_bytes(b'x')
            return {'output': {'path': 'untracked-root/file.bin', 'sha256': digest(b'x')}}
        self.assertRejected('Unregistered run artifact root', [self.shard(2, ['charlie'], extra=unknown)])

        def outside(directory):
            return {'output': {'path': 'downloads/../../secret', 'sha256': digest(b'x')}}
        self.assertRejected('Unowned artifact path', [self.shard(3, ['delta'], extra=outside)])

    def test_application_inputs_referenced_by_reports_are_not_run_outputs(self):
        def upload(directory):
            return {'upload': {'path': 'selfhost/tests/browser/manuale/fixtures/members-import.csv', 'sha256': 'e' * 64}}
        scenarios, copies = shards.validate_merge(REGISTRY, [self.shard(0, ['echo'], extra=upload)], self.state)
        self.assertNotIn('selfhost/tests/browser/manuale/fixtures/members-import.csv', copies[0][1])


class ShardedCaptureTests(unittest.TestCase):
    def test_sharded_capture_produces_the_sequential_evidence(self):
        selected = [item['id'] for item in REGISTRY]
        results = {}
        for concurrency in ('1', '2', '3'):
            with tempfile.TemporaryDirectory() as temporary:
                state = capture_state(temporary, selected)
                services = FakeServices()
                run_capture(state, services, concurrency)
                self.assertEqual(state['status'], 'captured')
                self.assertEqual(state['fixture_reset'], 'passed')
                batch = json.loads((Path(temporary) / 'capture-execution.json').read_text())
                self.assertEqual(batch['status'], 'passed')
                self.assertEqual(batch['scenarios'], [{'id': item, 'status': 'passed'} for item in selected])
                self.assertEqual(services.labels[-1], 'Restore baseline after the capture batch')
                self.assertFalse((Path(temporary) / shards.SHARDS_DIRECTORY).exists())
                if concurrency == '1':
                    self.assertFalse(any(label.startswith('Shard') for label in services.labels))
                    self.assertFalse((Path(temporary) / shards.MANIFEST).exists())
                else:
                    count = int(concurrency)
                    teardowns = [label for label in services.labels if label.endswith('remove its services and data volumes')]
                    self.assertEqual(len(teardowns), count - 1)
                    self.assertEqual(len([label for label in services.labels if label.endswith('migrate private database')]), count - 1)
                    manifest = json.loads((Path(temporary) / shards.MANIFEST).read_text())
                    self.assertEqual(sorted(sum((shard['workflows'] for shard in manifest['shards']), [])), sorted(selected))
                    self.assertFalse(any(record['active'] for record in state['capture_shards']['shards']))
                results[concurrency] = tree(temporary)
        self.assertEqual(results['1'], results['2'])
        self.assertEqual(results['1'], results['3'])

    def test_single_selected_workflow_keeps_the_sequential_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = capture_state(temporary, ['delta'])
            services = FakeServices()
            run_capture(state, services, '4')
            self.assertEqual(services.labels, ['Reset owned scenario state and browser session for delta',
                                               'Verify real application scenario delta',
                                               'Restore baseline after the capture batch'])

    def test_browser_failure_in_a_shard_fails_the_batch_but_cleans_every_shard(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = capture_state(temporary, [item['id'] for item in REGISTRY])
            services = FakeServices(fail={'charlie'})
            with self.assertRaisesRegex(RuntimeError, 'Capture batch failed for charlie'):
                run_capture(state, services, '2')
            run = Path(temporary)
            batch = json.loads((run / 'capture-execution.json').read_text())
            self.assertEqual(batch['status'], 'failed')
            self.assertIn({'id': 'charlie', 'status': 'failed'}, batch['scenarios'])
            self.assertNotEqual(state['status'], 'captured')
            self.assertTrue(any(label.endswith('remove its services and data volumes') for label in services.labels))
            self.assertEqual(services.labels[-1], 'Restore baseline after the capture batch')
            self.assertFalse((run / shards.SHARDS_DIRECTORY).exists())
            self.assertFalse((run / shards.MANIFEST).exists())
            # Failed report and diagnostics are retained in the primary run, like the sequential path.
            self.assertEqual(json.loads((run / 'charlie.json').read_text())['status'], 'failed')
            self.assertTrue((run / 'diagnostics/charlie/owner.png').is_file())
            self.assertFalse((run / 'captures/images/charlie').exists())

    def test_shard_startup_failure_aborts_other_shards_and_still_cleans_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = capture_state(temporary, [item['id'] for item in REGISTRY])
            services = FakeServices(fail_labels=(r'^Shard 1: start private dependencies$',))
            with self.assertRaisesRegex(RuntimeError, 'fixture failure: Shard 1: start private dependencies'):
                run_capture(state, services, '2')
            self.assertIn('Shard 1: remove its services and data volumes', services.labels)
            self.assertFalse(any('verify real application scenario charlie' in label for label in services.labels))
            self.assertEqual(json.loads((Path(temporary) / 'capture-execution.json').read_text())['status'], 'failed')
            self.assertFalse((Path(temporary) / shards.SHARDS_DIRECTORY).exists())

    def test_teardown_failure_is_reported_and_recorded_for_later_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = capture_state(temporary, [item['id'] for item in REGISTRY])
            services = FakeServices(teardown_fails=True)
            with self.assertRaisesRegex(shards.ShardError, 'cleanup failed'):
                run_capture(state, services, '2')
            saved = json.loads((Path(temporary) / 'run.json').read_text())
            active = [record for record in saved['capture_shards']['shards'] if record['active']]
            self.assertEqual([record['index'] for record in active], [1])
            self.assertEqual(json.loads((Path(temporary) / 'capture-execution.json').read_text())['status'], 'failed')
            # The cleanup command retries recorded stacks before removing the primary services.
            retry = FakeServices()
            with patch.object(runner, 'execute', side_effect=retry.execute), \
                    patch.object(runner.capture_shards.subprocess, 'check_output', return_value=''), \
                    patch.object(runner.subprocess, 'check_output', return_value=''):
                runner.cleanup(saved)
            self.assertEqual(retry.labels, ['Shard 1: remove its services and data volumes',
                                            'Remove only this run’s services and data volumes'])
            self.assertFalse(saved['capture_shards']['shards'][1]['active'])
            self.assertFalse((Path(temporary) / shards.SHARDS_DIRECTORY / '1').exists())

    def test_shard_credentials_and_configuration_never_remain_after_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = capture_state(temporary, [item['id'] for item in REGISTRY])
            run_capture(state, FakeServices(), '2')
            leftovers = [path for path in Path(temporary).rglob('shard.env')]
            self.assertEqual(leftovers, [])


if __name__ == '__main__':
    unittest.main()
