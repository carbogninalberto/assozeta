"""One plan job mints a transport manifest; each capture job runs exactly one shard."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_') + '_under_test', ROOT / 'docs/scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


ci = module('manuale-ci')
transport = module('manuale_capture_transport')

APP_REVISION = 'a' * 40
MANUAL_REVISION = 'b' * 40
CAPTURE_ID = '0123456789abcdef'
RELEASE = 'manuale-2026.09'
REFERENCE_DATE = '2026-09-30'
TOOLING = {'docs/scripts/manuale.py': 'c' * 64, 'selfhost/compose.dev.yml': 'd' * 64}


def recipe(identifier, checkpoints=2, fixture=None):
    value = {'id': identifier, 'script': identifier + '.mjs', 'capture_prefix': f'images/{identifier}/',
             'checkpoints': [{'id': f'step-{number}'} for number in range(1, checkpoints + 1)]}
    if fixture:
        value['fixture'] = fixture
    return value


REGISTRY = [recipe('alpha', 4), recipe('bravo', 1), recipe('charlie', 3, 'member-transfer'),
            recipe('delta', 2), recipe('echo', 5), recipe('foxtrot', 1), recipe('golf', 3),
            recipe('hotel', 2), recipe('india', 4)]
IDS = [item['id'] for item in REGISTRY]


class TransportCommandTests(unittest.TestCase):
    def transport(self, *, expect=None, message=None, fresh=None, reused=None, runners=1,
                  run_attempt=2, run_id='1234567890', plan_changes=None, state_changes=None,
                  environment=None):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            run = directory / 'run'
            run.mkdir()
            fresh = IDS[:5] if fresh is None else fresh
            reused = [identifier for identifier in IDS if identifier not in fresh] if reused is None else reused
            state = {'run': str(run), 'application': str(directory / 'application'),
                     'reference_date': REFERENCE_DATE, 'tooling_hashes': dict(TOOLING)}
            state.update(state_changes or {})
            plan = {'mode': 'full', 'fresh_workflows': list(fresh), 'reused_workflows': list(reused),
                    'application_revision': APP_REVISION, 'manual_revision': MANUAL_REVISION, 'release': RELEASE}
            plan.update(plan_changes or {})
            (run / 'run.json').write_text(json.dumps(state))
            (run / 'ci-plan.json').write_text(json.dumps(plan))
            output = directory / 'transport'
            args = SimpleNamespace(run=str(run), output=str(output), runners=runners, run_id=run_id,
                                   run_attempt=run_attempt)
            runner = SimpleNamespace(recipe_registry=Mock(return_value={'format': 1, 'recipes': list(REGISTRY)}))
            github_output = directory / 'github-output'
            values = {}
            with patch.dict(os.environ, {'GITHUB_OUTPUT': str(github_output), 'MANUALE_TIMING_FILE': '',
                                         **(environment or {})}):
                if expect is None:
                    ci.transport(args, runner)
                else:
                    with self.assertRaisesRegex(expect, message or '.'):
                        ci.transport(args, runner)
            written = output / 'transport.json'
            text = written.read_text() if written.is_file() else None
            if github_output.is_file():
                values = dict(line.split('=', 1) for line in github_output.read_text().splitlines())
            return SimpleNamespace(text=text, values=values, root=str(directory), runner=runner)

    def test_runner_counts_split_the_fresh_workflows(self):
        for runners in (1, 3):
            with self.subTest(runners=runners):
                result = self.transport(runners=runners)
                manifest = transport.loads(result.text)
                self.assertEqual(result.text, transport.dumps(manifest))
                self.assertEqual(len(manifest['shards']), runners)
                self.assertGreater(len(manifest['fresh']), 0)
                self.assertEqual(set(manifest['fresh']) | set(manifest['reused']), set(IDS))
                self.assertEqual(result.values['shard_count'], str(runners))
                self.assertEqual(json.loads(result.values['matrix']), transport.matrix(manifest))
                self.assertEqual(result.values['capture_id'], manifest['capture_id'])
                self.assertRegex(result.values['capture_id'], r'^[0-9a-f]{16}$')
                self.assertEqual(manifest['shards'][0]['artifact'],
                                 f"manuale-capture-{manifest['capture_id']}-s0")
                for shard in manifest['shards']:
                    self.assertEqual(transport.shard_workflows(manifest, shard['index']), shard['workflows'])

    def test_more_runners_than_fresh_workflows_never_creates_empty_shards(self):
        result = self.transport(fresh=['alpha', 'bravo'], runners=5)
        manifest = transport.loads(result.text)
        self.assertEqual(len(manifest['shards']), 2)
        self.assertEqual(result.values['shard_count'], '2')
        self.assertTrue(all(shard['workflows'] for shard in manifest['shards']))
        self.assertEqual(json.loads(result.values['matrix'])['include'],
                         transport.matrix(manifest)['include'])

    def test_a_reused_only_plan_has_no_shard_and_an_empty_matrix(self):
        result = self.transport(fresh=[], reused=IDS, runners=4)
        manifest = transport.loads(result.text)
        self.assertEqual(manifest['shards'], [])
        self.assertEqual(result.values['shard_count'], '0')
        self.assertEqual(json.loads(result.values['matrix']), {'include': []})
        self.assertRegex(result.values['capture_id'], r'^[0-9a-f]{16}$')

    def test_the_manifest_pins_the_plan_and_carries_no_local_data(self):
        result = self.transport(runners=2, run_attempt=3)
        manifest = transport.loads(result.text)
        self.assertEqual((manifest['run_id'], manifest['run_attempt']), ('1234567890', 3))
        self.assertEqual(manifest['application_revision'], APP_REVISION)
        self.assertEqual(manifest['manual_revision'], MANUAL_REVISION)
        self.assertEqual(manifest['release'], RELEASE)
        self.assertEqual(manifest['reference_date'], REFERENCE_DATE)
        self.assertEqual(manifest['tooling_hashes'], TOOLING)
        self.assertEqual(manifest['fresh'] + manifest['reused'], IDS)
        for leaked in (result.root, '/Users/', '/home/', 'GITHUB_'):
            self.assertNotIn(leaked, result.text)
        # A freshly minted id is never reused between plans.
        other = self.transport(runners=2)
        self.assertNotEqual(transport.loads(other.text)['capture_id'], manifest['capture_id'])

    def test_the_run_attempt_defaults_to_the_environment(self):
        result = self.transport(run_attempt=None, environment={'GITHUB_RUN_ATTEMPT': '4'})
        self.assertEqual(transport.loads(result.text)['run_attempt'], 4)
        self.transport(run_attempt=None, environment={'GITHUB_RUN_ATTEMPT': ''},
                       expect=RuntimeError, message='run id and run attempt')

    def test_a_plan_without_explicit_workflow_lists_fails_closed(self):
        for changes in ({'fresh_workflows': 'alpha'}, {'reused_workflows': ['alpha', 1]},
                        {'fresh_workflows': None}):
            with self.subTest(changes=changes):
                result = self.transport(plan_changes=changes, expect=RuntimeError,
                                        message='list of workflow ids')
                self.assertIsNone(result.text)
                self.assertEqual(result.values, {})

    def test_an_invalid_runner_count_is_rejected_before_writing(self):
        result = self.transport(runners=7, expect=ValueError, message='runners')
        self.assertIsNone(result.text)

    def test_the_recipe_registry_is_read_from_the_snapshotted_application(self):
        result = self.transport(runners=2)
        application = result.runner.recipe_registry.call_args.args[0]
        self.assertTrue(application.is_absolute())
        self.assertEqual(application.name, 'application')


class CaptureShardTests(unittest.TestCase):
    def manifest(self, runners=2):
        return transport.build_transport(
            run_id='1234567890', run_attempt=2, capture_id=CAPTURE_ID,
            application_revision=APP_REVISION, manual_revision=MANUAL_REVISION, release=RELEASE,
            reference_date=REFERENCE_DATE, tooling_hashes=dict(TOOLING), recipes=REGISTRY,
            fresh=IDS, reused=[], runners=runners)

    def shard(self, *, shard=0, manifest=None, changes=None, capture_error=None,
              env_file='env-file', services_removed=False, expect=None, message=None):
        manifest = manifest or self.manifest()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / 'transport.json'
            path.write_text(transport.dumps(manifest))
            run = directory / 'run'
            runner = Mock()
            runner.argument_parser.return_value.parse_args.side_effect = (
                lambda parameters: SimpleNamespace(parameters=parameters))
            if capture_error is not None:
                runner.capture_shard.side_effect = capture_error
            args = SimpleNamespace(transport=str(path), shard=shard, output=str(run),
                                   manual_repo=str(directory / 'manual'), release='ignored-by-the-shard')
            state, workflows = {}, []

            def execute():
                nonlocal state, workflows
                workflows = transport.shard_workflows(manifest, shard)
                state = {'run': str(run), 'application_input': {'revision': manifest['application_revision']},
                         'manual_input': {'revision': manifest['manual_revision'], 'repository': 'example/manuale'},
                         'tooling_hashes': dict(manifest['tooling_hashes']),
                         'reference_date': manifest['reference_date'], 'release': manifest['release'],
                         'selected_recipes': list(workflows), 'env_file': env_file,
                         'services_removed': services_removed}
                state.update(changes or {})
                runner.prepare.return_value = state
                ci.capture_shard(args, runner)

            with patch.dict(os.environ, {'MANUALE_TIMING_FILE': ''}):
                if expect is None:
                    execute()
                else:
                    with self.assertRaisesRegex(expect, message or '.'):
                        execute()
            written = run / 'run.json'
            record = json.loads(written.read_text()) if written.is_file() else None
            call = runner.argument_parser.return_value.parse_args.call_args
            parameters = call.args[0] if call else None
            return SimpleNamespace(runner=runner, state=state, written=record, parameters=parameters,
                                   manifest=manifest, workflows=workflows)

    def test_one_shard_pins_every_planned_input_cold(self):
        result = self.shard()
        parameters = result.parameters
        self.assertEqual(parameters[0], 'prepare')
        self.assertIn('--no-cache', parameters)
        self.assertNotIn('--reuse-evidence', parameters)
        self.assertNotIn('--reuse-store', parameters)
        for option, expected in (('--release', RELEASE), ('--reference-date', REFERENCE_DATE),
                                 ('--manual-ref', MANUAL_REVISION)):
            self.assertEqual(parameters[parameters.index(option) + 1], expected)
        selected = [parameters[index + 1] for index, value in enumerate(parameters) if value == '--workflow']
        self.assertEqual(selected, result.workflows)
        self.assertEqual(len(selected), len(set(selected)))
        self.assertEqual(result.runner.prepare.call_count, 1)

    def test_the_shard_captures_with_the_planned_capture_id(self):
        result = self.shard()
        self.assertEqual(result.written['preset_capture_id'], CAPTURE_ID)
        result.runner.capture_shard.assert_called_once_with(result.state)
        self.assertEqual(result.state['preset_capture_id'], CAPTURE_ID)
        result.runner.cleanup.assert_called_once_with(result.state)
        self.assertTrue(result.written['selected_recipes'])

    def test_each_pinned_input_is_asserted_before_capturing(self):
        chosen = transport.shard_workflows(self.manifest(), 0)
        other = next(identifier for identifier in IDS if identifier not in chosen)
        cases = {
            'application revision': {'application_input': {'revision': 'e' * 40}},
            'manual revision': {'manual_input': {'revision': 'e' * 40}},
            'tooling hashes': {'tooling_hashes': {**TOOLING, 'docs/scripts/manuale.py': 'e' * 64}},
            'reference date': {'reference_date': '2026-10-01'},
            'release': {'release': 'manuale-other'},
            'workflow selection': {'selected_recipes': [other]},
        }
        for label, changes in cases.items():
            with self.subTest(label):
                result = self.shard(changes=changes, expect=RuntimeError, message=label)
                self.assertIsNone(result.written)
                self.assertNotIn('preset_capture_id', result.state)
                result.runner.capture_shard.assert_not_called()
                result.runner.cleanup.assert_not_called()

    def test_cleanup_runs_when_the_capture_fails(self):
        result = self.shard(capture_error=RuntimeError('capture exploded'),
                            expect=RuntimeError, message='capture exploded')
        result.runner.capture_shard.assert_called_once()
        result.runner.cleanup.assert_called_once_with(result.state)

    def test_cleanup_is_skipped_without_services(self):
        self.shard(env_file=None).runner.cleanup.assert_not_called()
        self.shard(services_removed=True).runner.cleanup.assert_not_called()

    def test_an_unknown_shard_never_prepares_a_run(self):
        result = self.shard(shard=9, expect=ValueError, message='Unknown shard index')
        result.runner.prepare.assert_not_called()
        self.assertIsNone(result.written)


if __name__ == '__main__':
    unittest.main()
