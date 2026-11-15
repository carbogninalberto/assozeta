"""A capture shard job exports sanitized results; the verify job merges every shard."""
import hashlib
import importlib.util
import io
import json
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_') + '_under_test',
                                                  ROOT / 'docs/scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


ci = module('manuale-ci')
transport = module('manuale_capture_transport')

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + b'\x00\x00\x07\x80\x00\x00\x048' + b'fixture'
APP_REVISION = 'a' * 40
MANUAL_REVISION = 'b' * 40
CAPTURE_ID = '0123456789abcdef'
RELEASE = 'manuale-2026.09'
REFERENCE_DATE = '2026-09-30'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def recipe(identifier, checkpoints=2, fixture=None):
    value = {'id': identifier, 'script': identifier + '.mjs', 'capture_prefix': f'images/{identifier}/',
             'checkpoints': [{'id': f'step-{number}'} for number in range(1, checkpoints + 1)]}
    if fixture:
        value['fixture'] = fixture
    return value


REGISTRY = [recipe('alpha', 4), recipe('bravo', 1), recipe('charlie', 3, 'member-transfer'),
            recipe('delta', 2), recipe('echo', 5), recipe('foxtrot', 1)]
BY_ID = {item['id']: item for item in REGISTRY}
FRESH = ['alpha', 'bravo', 'charlie', 'delta']
REUSED = ['echo', 'foxtrot']


def write_capture(directory, item, *, capture_id=CAPTURE_ID, tooling_hashes=None, extra=None):
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
    report = {'id': item['id'], 'status': 'passed', 'application_revision': APP_REVISION, 'capture_id': capture_id,
              'tooling_hashes': dict(tooling_hashes or {}), 'source_hashes': {}, 'fixture_version': 8,
              'reference_date': REFERENCE_DATE, 'fixture_profile': item.get('fixture', 'baseline'),
              'viewport': {'width': 1920, 'height': 1080}, 'locale': 'it-IT', 'timezone': 'Europe/Rome',
              'theme': 'light', 'device_scale_factor': 1, 'backend': 'real', 'capture_format': 'full-hd-v1',
              'screenshots': screenshots, 'checks': ['fixture'], 'expected_denials': []}
    if extra:
        report.update(extra(directory))
    (directory / (item['id'] + '.json')).write_text(json.dumps(report, indent=2))
    return report


def write_execution(directory, workflows, *, capture_id=CAPTURE_ID, status='passed', scenarios=None):
    value = {'format': 1, 'status': status, 'capture_id': capture_id,
             'scenarios': scenarios if scenarios is not None else
             [{'id': identifier, 'status': 'passed'} for identifier in workflows]}
    (Path(directory) / 'capture-execution.json').write_text(json.dumps(value, indent=2))
    return value


def expected_paths(workflows):
    """Every run-relative file the reports of these workflows own."""
    paths = set()
    for identifier in workflows:
        item = BY_ID[identifier]
        paths.add(identifier + '.json')
        for number in range(1, len(item['checkpoints']) + 1):
            relative = f"{item['capture_prefix']}{number}.png"
            paths.update({'captures/' + relative, 'masters/' + relative, 'masters/repeats/' + relative})
    return paths


@contextmanager
def stable_modules():
    """One module instance per lazy import so a test can patch the code under test."""
    loaded, original = {}, ci.module

    def load(name):
        if name not in loaded:
            loaded[name] = original(name)
        return loaded[name]

    load('manuale_capture_shards')
    load('manuale_capture_transport')
    with patch.object(ci, 'module', side_effect=load):
        yield loaded


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = transport.build_transport(
            run_id='1234567890', run_attempt=2, capture_id=CAPTURE_ID,
            application_revision=APP_REVISION, manual_revision=MANUAL_REVISION, release=RELEASE,
            reference_date=REFERENCE_DATE, tooling_hashes={}, recipes=REGISTRY,
            fresh=FRESH, reused=REUSED, runners=2)
        self.workflows = [transport.shard_workflows(self.manifest, shard['index'])
                          for shard in self.manifest['shards']]
        self.transport_path = self.root / 'transport.json'
        self.transport_path.write_text(transport.dumps(self.manifest))
        self.runner = SimpleNamespace(recipe_registry=Mock(return_value={'format': 1, 'recipes': list(REGISTRY)}))


class CaptureExportTests(Fixture):
    def export(self, *, shard=0, state_changes=None, execution=None, extra=None,
               output_content=None, capture_error=None):
        workflows = transport.shard_workflows(self.manifest, shard)
        self.calls = getattr(self, 'calls', 0) + 1
        run = self.root / f'run-{self.calls}'
        run.mkdir()
        state = {'run': str(run), 'application': str(self.root / 'application'), 'capture_id': CAPTURE_ID,
                 'status': 'captured', 'application_input': {'revision': APP_REVISION},
                 'manual_input': {'revision': MANUAL_REVISION}, 'tooling_hashes': {},
                 'reference_date': REFERENCE_DATE, 'release': RELEASE, 'selected_recipes': list(workflows)}
        state.update(state_changes or {})
        (run / 'run.json').write_text(json.dumps(state))
        # Run state and private inputs always exist, and are never part of an export.
        (run / 'browser-input.json').write_text('{}')
        (run / '.env.dev').write_text('SECRET=1\n')
        (run / 'execution.log').write_text('log\n')
        for identifier in workflows:
            write_capture(run, BY_ID[identifier], extra=extra)
        payload = {'format': 1, 'status': 'passed', 'capture_id': CAPTURE_ID,
                   'scenarios': [{'id': identifier, 'status': 'passed'} for identifier in workflows]}
        payload.update(execution or {})
        (run / 'capture-execution.json').write_text(json.dumps(payload))
        output = self.root / f'export-{self.calls}'
        if output_content is not None:
            output.mkdir(parents=True)
            for name, data in output_content.items():
                (output / name).write_bytes(data)
        args = SimpleNamespace(run=str(run), transport=str(self.transport_path), shard=shard, output=str(output))
        if capture_error is None:
            ci.capture_export(args, self.runner)
        else:
            with self.assertRaisesRegex(*capture_error):
                ci.capture_export(args, self.runner)
        files = {path.relative_to(output).as_posix() for path in output.rglob('*') if path.is_file()} \
            if output.is_dir() else set()
        return SimpleNamespace(run=run, output=output, files=files, workflows=workflows, state=state)

    def test_one_shard_exports_only_its_declared_artifacts(self):
        result = self.export()
        self.assertEqual(result.files, expected_paths(result.workflows) | {'capture-execution.json'})
        for private in ('run.json', 'browser-input.json', '.env.dev', 'execution.log', 'capture-shards.json'):
            self.assertNotIn(private, result.files)
        execution = json.loads((result.output / 'capture-execution.json').read_text())
        self.assertEqual(execution['capture_id'], CAPTURE_ID)
        self.assertEqual(execution['status'], 'passed')
        self.assertEqual([item['id'] for item in execution['scenarios']], result.workflows)

    def test_a_shard_that_did_not_capture_under_this_id_is_never_exported(self):
        for changes in ({'capture_id': 'f' * 16}, {'status': 'capturing'}):
            with self.subTest(changes=changes):
                result = self.export(state_changes=changes,
                                     capture_error=(RuntimeError, 'another capture id|capturing'))
                self.assertFalse(result.output.exists())
                self.assertEqual(result.files, set())

    def test_an_execution_report_of_another_capture_is_rejected(self):
        result = self.export(execution={'capture_id': 'f' * 16},
                             capture_error=(RuntimeError, 'another capture id'))
        self.assertFalse(result.output.exists())

    def test_a_failed_or_incomplete_execution_is_rejected(self):
        workflows = self.workflows[0]
        failed = [{'id': identifier, 'status': 'passed'} for identifier in workflows]
        failed[0]['status'] = 'failed'
        for execution, pattern in (({'scenarios': failed}, 'not a passed scenario list'),
                                   ({'status': 'failed'}, 'did not pass'),
                                   ({'scenarios': [{'id': 'echo', 'status': 'passed'}]},
                                    'does not match its workflows')):
            with self.subTest(execution=execution):
                result = self.export(execution=execution, capture_error=(RuntimeError, pattern))
                self.assertFalse(result.output.exists())

    def test_a_private_file_in_an_owned_list_is_never_exported(self):
        def private(directory):
            target = directory / 'captures/.env'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'SECRET=1\n')
            return {'private_output': {'path': 'captures/.env', 'sha256': digest(b'SECRET=1\n')}}

        result = self.export(extra=private, capture_error=(RuntimeError, 'private file'))
        self.assertFalse(result.output.exists())

    def test_an_existing_non_empty_output_is_refused_untouched(self):
        result = self.export(output_content={'keep.txt': b'keep'},
                             capture_error=(RuntimeError, 'new or empty directory'))
        self.assertEqual({path.name for path in result.output.iterdir()}, {'keep.txt'})
        self.assertEqual((result.output / 'keep.txt').read_bytes(), b'keep')


class MergeTests(Fixture):
    def setUp(self):
        super().setUp()
        self.shards_dir = self.root / 'shards'
        self.shards_dir.mkdir()
        for shard in self.manifest['shards']:
            directory = self.shards_dir / shard['artifact']
            directory.mkdir()
            for identifier in shard['workflows']:
                write_capture(directory, BY_ID[identifier])
            write_execution(directory, shard['workflows'])
        self.run = self.root / 'run'
        self.run.mkdir()
        self.state = {'run': str(self.run), 'application': str(self.root / 'application'),
                      'selected_recipes': list(FRESH), 'application_input': {'revision': APP_REVISION},
                      'manual_input': {'revision': MANUAL_REVISION}, 'tooling_hashes': {},
                      'reference_date': REFERENCE_DATE, 'release': RELEASE, 'status': 'capturing'}
        self.plan = {'mode': 'full', 'fresh_workflows': list(FRESH), 'reused_workflows': list(REUSED),
                     'application_revision': APP_REVISION, 'manual_revision': MANUAL_REVISION,
                     'release': RELEASE}

    def shard_dir(self, index):
        return self.shards_dir / self.manifest['shards'][index]['artifact']

    def merge(self, *, state_changes=None, plan_changes=None, capture_error=None, shards_dir=None, prepare=None):
        state = json.loads(json.dumps(self.state))
        state.update(state_changes or {})
        plan = json.loads(json.dumps(self.plan))
        plan.update(plan_changes or {})
        (self.run / 'run.json').write_text(json.dumps(state))
        (self.run / 'ci-plan.json').write_text(json.dumps(plan))

        @contextmanager
        def timed(target, name, **kwargs):
            yield

        runner = SimpleNamespace(timed=timed, capture=Mock(), recipe_registry=self.runner.recipe_registry)
        args = SimpleNamespace(run=str(self.run), phase='capture', transport=str(self.transport_path),
                               shards=str(shards_dir or self.shards_dir))
        with stable_modules() as loaded, patch.dict(os.environ, {'MANUALE_TIMING_FILE': ''}):
            if prepare is not None:
                prepare(loaded)
            if capture_error is None:
                ci.phase(args, runner)
            else:
                with self.assertRaisesRegex(*capture_error):
                    ci.phase(args, runner)
        return SimpleNamespace(runner=runner, state=json.loads((self.run / 'run.json').read_text()),
                               run=self.run, args=args)

    def assertFailed(self, pattern, **options):
        result = self.merge(capture_error=(RuntimeError, pattern), **options)
        self.assertEqual(result.state['status'], 'failed')
        self.assertFalse((self.run / 'captures').exists())
        self.assertFalse((self.run / 'capture-execution.json').exists())
        self.assertFalse((self.run / 'capture-shards.json').exists())
        result.runner.capture.assert_not_called()
        return result

    def test_two_shards_merge_into_the_verify_run(self):
        result = self.merge()
        self.assertEqual(len(self.workflows), 2)
        self.assertEqual(result.state['status'], 'captured')
        self.assertEqual(result.state['capture_mode'], 'distributed')
        self.assertEqual(result.state['capture_id'], CAPTURE_ID)
        self.assertEqual((self.run / 'capture-execution.json').read_text(),
                         json.dumps({'format': 1, 'status': 'passed', 'capture_id': CAPTURE_ID,
                                     'scenarios': [{'id': identifier, 'status': 'passed'} for identifier in FRESH]},
                                    ensure_ascii=False, indent=2) + '\n')
        manifest = json.loads((self.run / 'capture-shards.json').read_text())
        self.assertEqual(manifest['format'], 1)
        self.assertEqual(manifest['capture_id'], CAPTURE_ID)
        self.assertEqual(manifest['concurrency'], 2)
        self.assertEqual([entry['index'] for entry in manifest['shards']], [0, 1])
        self.assertEqual([entry['workflows'] for entry in manifest['shards']], self.workflows)
        for workflow in FRESH:
            self.assertTrue((self.run / (workflow + '.json')).is_file())
            self.assertTrue((self.run / f'captures/images/{workflow}/1.png').is_file())
            self.assertTrue((self.run / f'masters/images/{workflow}/1.png').is_file())
            self.assertTrue((self.run / f'masters/repeats/images/{workflow}/1.png').is_file())
        for entry in manifest['shards']:
            self.assertIn(f"captures/images/{entry['workflows'][0]}/1.png", entry['files'])
            self.assertNotIn(entry['workflows'][0] + '.json', entry['files'])
        # Only declared files and the two run-level reports exist; no shard directory.
        self.assertFalse((self.run / 'browser-input.json').exists())
        self.assertFalse((self.run / 'capture-shards').exists())
        result.runner.capture.assert_not_called()

    def test_a_missing_or_extra_shard_directory_is_rejected(self):
        shutil.rmtree(self.shard_dir(0))
        self.assertFailed('differ from the transport plan: missing')
        allowed = self.shards_dir / self.manifest['shards'][0]['artifact']
        allowed.mkdir()
        for identifier in self.workflows[0]:
            write_capture(allowed, BY_ID[identifier])
        write_execution(allowed, self.workflows[0])
        (self.shards_dir / ('manuale-capture-' + CAPTURE_ID + '-s2')).mkdir()
        self.assertFailed('differ from the transport plan: extra')

    def test_undeclared_private_and_symlinked_files_are_rejected(self):
        directory = self.shard_dir(0)
        identifier = self.workflows[0][0]
        undeclared = directory / f'captures/images/{identifier}/99.png'
        undeclared.write_bytes(PNG)
        self.assertFailed('Undeclared file in shard: ' + f'captures/images/{identifier}/99.png')
        undeclared.unlink()
        (directory / 'browser-input.json').write_text('{}')
        self.assertFailed('Private file in shard: browser-input.json')
        (directory / 'browser-input.json').unlink()
        link = directory / 'copied.png'
        link.symlink_to(directory / f'captures/images/{identifier}/1.png')
        self.assertFailed('Symlink in shard: copied.png')

    def test_duplicate_and_unassigned_workflow_results_are_rejected(self):
        first, second = self.workflows[0][0], self.workflows[1]
        scenarios = [{'id': identifier, 'status': 'passed'} for identifier in second]
        write_execution(self.shard_dir(1), second,
                        scenarios=[*scenarios, {'id': first, 'status': 'passed'}])
        self.assertFailed('Duplicate workflow result: ' + first)
        write_execution(self.shard_dir(1), second,
                        scenarios=[*scenarios, {'id': 'echo', 'status': 'passed'}])
        self.assertFailed('Workflow result from an unassigned shard: echo')

    def test_a_shard_belonging_to_another_capture_is_rejected(self):
        write_execution(self.shard_dir(1), self.workflows[1], capture_id='f' * 16)
        self.assertFailed('another capture id')

    def test_a_report_of_another_capture_is_rejected(self):
        identifier = self.workflows[1][0]
        path = self.shard_dir(1) / (identifier + '.json')
        report = json.loads(path.read_text())
        report['capture_id'] = 'd' * 16
        path.write_text(json.dumps(report))
        self.assertFailed('disagrees on capture_id')

    def test_a_failed_scenario_never_merges(self):
        identifier = self.workflows[1][0]
        scenarios = [{'id': item, 'status': 'failed' if item == identifier else 'passed'}
                     for item in self.workflows[1]]
        write_execution(self.shard_dir(1), self.workflows[1], scenarios=scenarios)
        self.assertFailed('Capture batch failed for ' + identifier)

    def test_the_verify_run_must_match_the_transport_and_the_plan(self):
        cases = ({'tooling_hashes': {'docs/scripts/manuale.py': 'c' * 64}}, {'release': 'manuale-other'},
                 {'selected_recipes': list(reversed(FRESH))})
        for changes in cases:
            with self.subTest(state=changes):
                self.assertFailed('does not match its transport plan', state_changes=changes)
        for changes in ({'fresh_workflows': FRESH[:-1]}, {'reused_workflows': REUSED[:-1]}):
            with self.subTest(plan=changes):
                self.assertFailed('does not match its transport plan', plan_changes=changes)
        with self.subTest(plan='alpha'):
            self.assertFailed('list of workflow ids', plan_changes={'fresh_workflows': 'alpha'})

    def test_a_byte_changed_after_validation_is_rejected(self):
        identifier = self.workflows[0][0]
        changed = self.shard_dir(0) / f'captures/images/{identifier}/1.png'

        def prepare(loaded):
            capture_shards = loaded['manuale_capture_shards']
            original = capture_shards.copy_validated

            def copy_validated(run, copies):
                changed.write_bytes(PNG + b'altered')
                return original(run, copies)

            patcher = patch.object(capture_shards, 'copy_validated', side_effect=copy_validated)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.assertFailed('changed during merge', prepare=prepare)

    def test_the_phase_command_requires_both_transport_and_shards(self):
        for extra in (['--transport', str(self.transport_path)], ['--shards', str(self.shards_dir)]):
            with self.subTest(extra=extra), \
                    patch.object(ci.sys, 'argv', ['manuale-ci.py', 'phase', '--run', str(self.run),
                                                  '--phase', 'capture', *extra]), \
                    patch.object(ci.sys, 'stderr', new_callable=io.StringIO), \
                    self.assertRaises(SystemExit):
                ci.main()


if __name__ == '__main__':
    unittest.main()
