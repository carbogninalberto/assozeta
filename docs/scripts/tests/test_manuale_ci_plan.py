"""Execution-mode planning fails closed and only ever adds verification work."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import yaml

ROOT = Path(__file__).resolve().parents[3]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_') + '_under_test', ROOT / 'docs/scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


ci = module('manuale-ci')
IDS = ci.workflow_ids()


def report(mode='unaffected', files=(), **values):
    base = {'format': 1, 'mode': mode, 'full_verification_required': mode == 'full',
            'affected_workflows': [], 'reused_workflows': list(IDS), 'affected_pages': [],
            'checks': {'bot': False, 'mcp': False, 'security': False, 'runtime': False},
            'files': [{'path': path, 'category': category, 'workflows': list(workflows), 'reason': 'test'}
                      for path, category, workflows in files],
            'reasons': ['test']}
    base.update(values)
    return base


class ImpactValidationTests(unittest.TestCase):
    def test_unrelated_and_content_reports_are_kept(self):
        value = ci.validate_impact(report(files=[('README.md', 'unrelated', ())]), ['README.md'], IDS)
        self.assertEqual(value['mode'], 'unaffected')
        value = ci.validate_impact(report('content', files=[('manuale/docs/soci.mdx', 'docs-text', ())],
                                          affected_pages=['manuale/docs/soci.mdx']), ['manuale/docs/soci.mdx'], IDS)
        self.assertEqual(value['mode'], 'content')

    def test_inconsistent_or_unexplained_reports_escalate_to_full(self):
        cases = {
            'unclassified path': (report(files=[]), ['BE/application/views.py']),
            'unknown category': (report(files=[('x.py', 'unknown', ())]), ['x.py']),
            'security category': (report(files=[('BE/application/auth.py', 'security', ())]), ['BE/application/auth.py']),
            'shared contract': (report('behavioral', files=[('f.mjs', 'shared-contract', ())], affected_workflows=[IDS[0]]), ['f.mjs']),
            'behavior in unaffected': (report(files=[('UI/a.svelte', 'behavior', (IDS[0],))]), ['UI/a.svelte']),
            'behavioral without workflows': (report('behavioral', files=[('UI/a.svelte', 'behavior', ())]), ['UI/a.svelte']),
            'full flag in content mode': (report('content', full_verification_required=True), []),
        }
        for name, (value, changed) in cases.items():
            with self.subTest(name):
                result = ci.validate_impact(value, changed, IDS)
                self.assertEqual(result['mode'], 'full')
                self.assertTrue(result['full_verification_required'])
                self.assertEqual(result['affected_workflows'], IDS)
                self.assertEqual(result['reused_workflows'], [])
                self.assertTrue(result['checks']['runtime'])

    def test_behavioral_selection_includes_every_workflow_named_by_a_file(self):
        value = report('behavioral', affected_workflows=[IDS[0]],
                       files=[('UI/a.svelte', 'behavior', (IDS[0],)), ('selfhost/tests/browser/manuale/x.mjs',
                                                                      'capture-tooling', (IDS[1],))])
        result = ci.validate_impact(value, ['UI/a.svelte', 'selfhost/tests/browser/manuale/x.mjs'], IDS)
        self.assertEqual(result['mode'], 'behavioral')
        self.assertEqual(result['affected_workflows'], [IDS[0], IDS[1]])
        self.assertNotIn(IDS[0], result['reused_workflows'])

    def test_malformed_reports_are_rejected(self):
        for change in ({'format': 2}, {'mode': 'fast'}, {'checks': {'bot': True}}, {'files': {}},
                       {'affected_workflows': ['not-a-workflow']}, {'full_verification_required': 'no'},
                       {'files': [{'path': 'a', 'category': 'safe', 'workflows': []}]}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    ci.validate_impact({**report(), **change}, [], IDS)
        with self.assertRaises(ValueError):
            ci.validate_impact([], [], IDS)


class DecisionTests(unittest.TestCase):
    def test_modes_map_onto_jobs(self):
        unaffected = ci.ci_decision(ci.validate_impact(report(files=[('README.md', 'unrelated', ())]), ['README.md'], IDS),
                                    forced=False, changed=['README.md'])
        self.assertEqual((unaffected['verify'], unaffected['content'], unaffected['force_full']), (False, False, False))
        self.assertFalse(unaffected['browser_helpers'])
        bot = ci.ci_decision(report(checks={'bot': True, 'mcp': False, 'security': False, 'runtime': False}),
                             forced=False, changed=['BE/application/agent/core.py'])
        self.assertEqual((bot['verify'], bot['bot'], bot['mcp']), (False, True, False))
        content = ci.ci_decision(report('content'), forced=False, changed=['manuale/docs/a.mdx'])
        self.assertEqual((content['verify'], content['content'], content['bot'], content['mcp']), (False, True, True, True))
        behavioral = ci.ci_decision(report('behavioral', affected_workflows=[IDS[2]]), forced=False,
                                    changed=['selfhost/tests/browser/manuale/frame.test.mjs'])
        self.assertEqual((behavioral['verify'], behavioral['force_full'], behavioral['workflows']), (True, False, [IDS[2]]))
        self.assertTrue(behavioral['browser_helpers'])
        self.assertFalse(behavioral['appliance'])
        full = ci.ci_decision(report('full'), forced=False, changed=[])
        self.assertEqual((full['verify'], full['force_full'], full['appliance'], full['workflows']), (True, True, True, []))

    def test_forced_runs_override_any_incremental_mode(self):
        for mode in ('unaffected', 'content', 'behavioral'):
            decision = ci.ci_decision(report(mode, affected_workflows=[IDS[0]]), forced=True, changed=[])
            self.assertEqual((decision['mode'], decision['verify'], decision['force_full'], decision['content']),
                             ('full', True, True, False))

    def test_fallback_keeps_trusted_baseline_planning_but_verifies(self):
        decision = ci.ci_decision(ci.fallback_impact(['a'], 'no analyzer', IDS), forced=False, changed=['a'])
        self.assertEqual((decision['verify'], decision['force_full'], decision['appliance']), (True, False, True))


class PlanCommandTests(unittest.TestCase):
    def run_plan(self, analyzer=None, *, changed=('README.md',), force=False, base=None):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'impact.json'
            github_output = Path(directory) / 'github-output'
            args = SimpleNamespace(force_full=force, changed_file=list(changed), base=base, head='HEAD', output=str(output))
            with patch.object(ci, 'load_impact_module', return_value=analyzer), \
                    patch.dict(os.environ, {'GITHUB_OUTPUT': str(github_output), 'MANUALE_TIMING_FILE': ''}):
                ci.plan(args)
            values = dict(line.split('=', 1) for line in github_output.read_text().splitlines())
            return json.loads(output.read_text()), values

    def test_missing_analyzer_fails_closed(self):
        value, outputs = self.run_plan(None)
        self.assertTrue(value['fallback'])
        self.assertEqual(value['mode'], 'full')
        self.assertEqual(outputs['verify'], 'true')
        self.assertEqual(outputs['force_full'], 'false')
        self.assertEqual(value['files'][0]['category'], 'unknown')

    def test_raising_or_invalid_analyzer_fails_closed(self):
        for analyzer in (SimpleNamespace(analyze=Mock(side_effect=RuntimeError('/home/runner/secret path'))),
                         SimpleNamespace(analyze=Mock(return_value={'format': 1, 'mode': 'unaffected'}))):
            value, outputs = self.run_plan(analyzer)
            self.assertTrue(value['fallback'])
            self.assertEqual(outputs['verify'], 'true')
            self.assertNotIn('/home/runner', json.dumps(value))

    def test_analyzer_receives_the_contract_arguments(self):
        analyzer = SimpleNamespace(analyze=Mock(return_value=report(files=[('README.md', 'unrelated', ())])))
        value, outputs = self.run_plan(analyzer, base='abc123')
        analyzer.analyze.assert_called_once_with(ci.ROOT, ['README.md'], base='abc123', head='HEAD')
        self.assertEqual(outputs['mode'], 'unaffected')
        self.assertEqual(outputs['verify'], 'false')
        self.assertEqual(value['ci']['mode'], 'unaffected')

    def test_forced_plan_is_full_regardless_of_analysis(self):
        analyzer = SimpleNamespace(analyze=Mock(return_value=report(files=[('README.md', 'unrelated', ())])))
        value, outputs = self.run_plan(analyzer, force=True)
        self.assertEqual((outputs['mode'], outputs['verify'], outputs['force_full']), ('full', 'true', 'true'))
        self.assertIn('explicit or scheduled full verification', value['reasons'])

    def test_unknown_base_is_never_treated_as_no_change(self):
        for base in (None, '', '0' * 40, 'bad revision; rm'):
            with self.subTest(base=base), self.assertRaises(ValueError):
                ci.changed_files(base, 'HEAD')
        value, outputs = self.run_plan(SimpleNamespace(analyze=Mock()), changed=(), base='0' * 40)
        self.assertTrue(value['fallback'])
        self.assertEqual(outputs['verify'], 'true')

    def test_changed_files_lists_both_sides_of_a_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = {'GIT_AUTHOR_NAME': 'fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                        'GIT_COMMITTER_NAME': 'fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
            git = lambda *arguments: subprocess.run(['git', '-C', directory, '-c', 'commit.gpgsign=false', *arguments],
                                                    check=True, capture_output=True, text=True, env={**os.environ, **identity}).stdout.strip()
            git('init', '-q')
            Path(directory, 'a.txt').write_text('same\n' * 20)
            git('add', '-A'); git('commit', '-q', '-m', 'one')
            base = git('rev-parse', 'HEAD')
            git('mv', 'a.txt', 'b.txt'); git('commit', '-q', '-m', 'two')
            self.assertEqual(ci.changed_files(base, 'HEAD', root=directory), ['a.txt', 'b.txt'])


class PrepareTests(unittest.TestCase):
    def prepare(self, impact=None, **arguments):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            run = directory / 'run'
            run.mkdir()
            if impact is not None:
                (directory / 'impact.json').write_text(json.dumps(impact))
            runner = Mock()
            runner.argument_parser.return_value.parse_args.side_effect = lambda parameters: SimpleNamespace(parameters=parameters)
            runner.prepare.return_value = {'run': str(run), 'manual_input': {'revision': 'b' * 40}}
            policy = Mock()
            policy.verification_decision.return_value = arguments.pop('decision', {
                'mode': 'reuse', 'historical_rehearsal': False, 'fresh_workflows': [], 'reused_workflows': IDS,
                'reasons': ['reuse'], 'cache_decisions': []})
            args = SimpleNamespace(manual_repo='m', output=str(run), release='r', force_full=False,
                                   baseline=str(directory / 'baseline'), impact=str(directory / 'impact.json'),
                                   workflow=arguments.pop('workflow', None), content_only=arguments.pop('content_only', False))
            github_output = directory / 'github-output'
            cache = Mock(validate_application_sources=Mock(return_value=[]))
            with patch.object(ci, 'module', return_value=cache), patch.dict(os.environ, {
                    'GITHUB_OUTPUT': str(github_output), 'GITHUB_STEP_SUMMARY': '', 'MANUALE_TIMING_FILE': ''}):
                ci.prepare(args, runner, policy)
            parameters = runner.argument_parser.return_value.parse_args.call_args.args[0]
            values = dict(line.split('=', 1) for line in github_output.read_text().splitlines())
            return parameters, policy.verification_decision.call_args.kwargs, values, json.loads((run / 'ci-plan.json').read_text())

    def test_impact_workflows_are_added_to_baseline_selection(self):
        parameters, _, _, plan = self.prepare(report('behavioral', affected_workflows=[IDS[3]]), workflow=[IDS[1] + ',' + IDS[2]])
        selected = [parameters[index + 1] for index, value in enumerate(parameters) if value == '--workflow']
        self.assertEqual(selected, sorted([IDS[1], IDS[2], IDS[3]]))
        self.assertIn('--no-cache', parameters)  # no baseline directory in this fixture
        self.assertEqual(parameters[0], 'run')
        self.assertEqual(plan['impact']['selected_workflows'], sorted([IDS[1], IDS[2], IDS[3]]))

    def test_analyzed_full_disables_reuse_but_fallback_does_not(self):
        _, keywords, _, _ = self.prepare(report('full'))
        self.assertTrue(keywords['force_full'])
        _, keywords, _, _ = self.prepare(ci.fallback_impact([], 'x', IDS))
        self.assertFalse(keywords['force_full'])

    def test_content_only_escalates_whenever_a_capture_would_be_needed(self):
        _, _, values, plan = self.prepare(report('content'), content_only=True)
        self.assertEqual(values['escalate'], 'false')
        _, _, values, plan = self.prepare(report('content'), content_only=True, decision={
            'mode': 'affected', 'historical_rehearsal': False, 'fresh_workflows': [IDS[0]], 'reused_workflows': IDS[1:],
            'reasons': ['fresh'], 'cache_decisions': []})
        self.assertEqual(values['escalate'], 'true')
        self.assertTrue(plan['escalate'])


class OfflineTests(unittest.TestCase):
    def test_chromium_helper_tests_are_deferred_only_without_a_browser(self):
        without, deferred = ci.node_tests(with_browser=False)
        with_browser, none = ci.node_tests(with_browser=True)
        self.assertEqual(none, [])
        self.assertEqual(sorted(without + deferred), sorted(with_browser))
        self.assertTrue(deferred)
        for path in deferred:
            self.assertIn('chromium', path.read_text())
        for path in without:
            self.assertNotRegex(path.read_text(), r'\bchromium\b')

    def test_offline_backend_suites_exist_and_use_no_database(self):
        for relative in ci.OFFLINE_BACKEND_TESTS:
            text = (ROOT / 'BE' / relative).read_text()
            self.assertIn('SimpleTestCase', text, relative)
            self.assertNotRegex(text, r'class \w+\((?:TestCase|TransactionTestCase|BaseAPITestCase|BaseTestCase)\)', relative)
        for value in ci.OFFLINE_BACKEND_ENVIRONMENT.values():
            self.assertRegex(value, r'^(placeholder|manual-ci-offline-placeholder|localhost|false|admin/|9|http://127\.0\.0\.1:9)$')


class ReportTests(unittest.TestCase):
    def test_cache_report_records_hits_misses_and_sanitizes_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / 'ci-plan.json').write_text(json.dumps({'mode': 'affected', 'fresh_workflows': [IDS[0]],
                                                          'reused_workflows': IDS[1:]}))
            (run / 'render-reuse.json').write_text(json.dumps({'fresh_pages': ['docs/a'], 'reused_pages': ['docs/b']}))
            (run / 'run.json').write_text(json.dumps({'cache': {'decisions': [
                {'kind': 'frontend', 'result': 'rejected', 'reason': 'changed /home/runner/work/UI/x.svelte'}]}}))
            admission = run / 'admission.json'
            admission.write_text(json.dumps({'status': 'available', 'producer_run': 42}))
            value = ci.cache_report(run, admission)
        self.assertEqual(value['fresh_workflows'], [IDS[0]])
        self.assertEqual(value['fresh_pages'], ['docs/a'])
        self.assertIn({'name': 'trusted baseline', 'detail': 'producer run 42'}, value['hits'])
        self.assertIn('workflow evidence', [item['name'] for item in value['hits']])
        self.assertIn('frontend build', [item['name'] for item in value['misses']])
        self.assertNotIn('/home/runner', json.dumps(value))

    def test_build_cache_is_added_without_dropping_runtime_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / 'compose.override.json'
            override.write_text(json.dumps({'services': {'ui': {'image': 'ui:1', 'command': ['npm'], 'environment': {}}}}))
            ci.apply_build_cache(override)
            value = json.loads(override.read_text())
        self.assertEqual(value['services']['ui']['command'], ['npm'])
        for service in ('api', 'ui', 'renderer'):
            self.assertIn('ignore-error=true', value['services'][service]['build']['cache_to'][0])
            self.assertEqual(value['services'][service]['build']['cache_from'], [f'type=gha,scope=manuale-{service}'])


class FastModeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.main = yaml.safe_load((ROOT / '.github/workflows/manuale.yml').read_text())

    def test_fast_jobs_start_no_services_or_full_application(self):
        for name in ('plan', 'offline', 'content'):
            job = self.main['jobs'][name]
            text = json.dumps(job)
            for forbidden in ('prepare-storage', 'docker', 'compose', '--phase capture --', '--phase runtime',
                              '--phase render', '--phase package', 'manual-release-smoke'):
                self.assertNotIn(forbidden, text, (name, forbidden))
            for step in job['steps']:
                if 'playwright install' in step.get('run', ''):
                    self.assertIn('browser_helpers', step.get('if', ''), name)

    def test_plan_feeds_offline_content_and_verify(self):
        jobs = self.main['jobs']
        self.assertEqual(jobs['offline']['needs'], 'plan')
        self.assertEqual(jobs['content']['needs'], 'plan')
        self.assertEqual(set(jobs['verify']['needs']), {'plan', 'content'})
        self.assertIn("needs.plan.outputs.verify == 'true'", jobs['verify']['if'])
        self.assertIn("needs.content.outputs.escalate == 'true'", jobs['verify']['if'])
        self.assertIs(jobs['verify']['with']['comprehensive'], False)
        self.assertIn("needs.plan.outputs.force_full == 'true'", jobs['verify']['with']['force_full'])
        upload = next(step for step in jobs['plan']['steps'] if step.get('with', {}).get('name') == 'manuale-impact')
        self.assertEqual(upload['with']['if-no-files-found'], 'error')
        dispatch = self.main[True]['workflow_dispatch']['inputs']
        self.assertIn('force_full', dispatch)
        self.assertIn('inputs.force_full', jobs['plan']['steps'][-3]['env']['FORCE_FULL'])

    def test_offline_job_uploads_no_evidence_bundle(self):
        for step in self.main['jobs']['offline']['steps']:
            if step.get('uses', '').startswith('actions/upload-artifact'):
                self.assertNotIn('baseline', step['with']['path'])
                self.assertNotIn('export', step['with']['path'])


if __name__ == '__main__':
    unittest.main()
