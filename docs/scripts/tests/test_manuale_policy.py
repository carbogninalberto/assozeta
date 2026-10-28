"""Exercise CI trust separately from byte compatibility, with real Git/ZIP inputs."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[3]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'docs/scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


policy, runner = module('manuale-policy'), module('manuale')


class ManualPolicyTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads((ROOT / 'docs/manuale/recipes.json').read_text())
        self.ids = [recipe['id'] for recipe in self.registry['recipes']]
        self.state = {'application': str(ROOT), 'application_input': {'revision': 'a' * 40},
                      'manual_input': {'revision': 'b' * 40}, 'release': '1.2.3',
                      'selected_recipes': [], 'evidence_reuse': {'reused_recipes': self.ids},
                      'cache': {'decisions': [{'result': 'checked', 'selected': True,
                                               'changed_inputs': [], 'input_decisions': []}]}}

    def decision_for(self, paths):
        decisions = []
        fresh = runner.affected_recipes(self.registry, paths, root=ROOT, decisions=decisions)
        state = copy.deepcopy(self.state)
        state['selected_recipes'] = fresh
        state['evidence_reuse']['reused_recipes'] = [item for item in self.ids if item not in fresh]
        state['cache']['decisions'][0].update(changed_inputs=paths, input_decisions=decisions)
        return policy.verification_decision(state)

    def test_source_portability_preflight_stops_before_run_preparation(self):
        ci = module('manuale-ci')
        cache = module('manuale-cache')
        with patch.object(ci, 'module', return_value=cache), patch.object(
                cache, 'validate_application_sources', side_effect=ValueError('private tracked source')):
            run = Mock()
            with self.assertRaisesRegex(ValueError, 'private tracked source'):
                ci.prepare(SimpleNamespace(), run, policy)
            run.prepare.assert_not_called()

    def test_unrelated_restore_fix_reuses_all_procedures(self):
        result = self.decision_for(['BE/instance/restore/relations.py', 'BE/application/tests/test_restore.py'])
        self.assertEqual(result['mode'], 'reuse')
        self.assertEqual(result['fresh_workflows'], [])
        self.assertEqual(result['reused_workflows'], self.ids)
        self.assertFalse(result['historical_rehearsal'])
        self.assertIn('authentication and authorization', result['runtime_checks'])

    def test_independent_browser_unit_tests_do_not_recapture_workflows(self):
        result = self.decision_for(['selfhost/tests/browser/manuale/frame.test.mjs'])
        self.assertEqual(result['mode'], 'reuse')
        self.assertEqual(result['fresh_workflows'], [])
        self.assertEqual(result['reused_workflows'], self.ids)

    def test_documented_export_change_only_selects_its_consumers(self):
        result = self.decision_for(['BE/application/services/export_service.py'])
        self.assertEqual(result['mode'], 'affected')
        self.assertEqual(result['fresh_workflows'], ['organization-exports'])
        self.assertEqual(len(result['reused_workflows']), len(self.ids) - 1)
        self.assertFalse(result['historical_rehearsal'])

    def test_infrastructure_shared_fixtures_and_unknown_production_require_full(self):
        for path in ('BE/application/manuale/bundle.py', 'selfhost/bin/assozeta',
                     'BE/application/management/commands/seed_manuale.py',
                     'selfhost/tests/browser/manuale/frame.mjs', 'selfhost/tests/browser/manuale/actor-session.mjs',
                     'BE/application/unknown.py',
                     '.github/workflows/manuale-verify.yml', 'docs/manuale/requirements-offline.txt',
                     '.github/actions/prepare-storage/action.yml'):
            with self.subTest(path=path):
                result = self.decision_for([path])
                self.assertEqual(result['mode'], 'full')
                self.assertEqual(result['fresh_workflows'], self.ids)
                self.assertTrue(result['historical_rehearsal'])

    def test_scheduled_force_and_missing_baseline_are_full_but_rejected_candidate_does_not_poison_selection(self):
        self.assertEqual(policy.verification_decision(self.state, force_full=True)['mode'], 'full')
        state = copy.deepcopy(self.state)
        state['cache']['decisions'].insert(0, {'result': 'checked', 'changed_inputs': ['BE/application/manuale/index.py']})
        self.assertEqual(policy.verification_decision(state)['mode'], 'reuse')
        state['cache']['decisions'] = []
        self.assertEqual(policy.verification_decision(state)['mode'], 'full')

    def test_release_number_and_git_sha_do_not_invalidate_content(self):
        before = policy.verification_decision(self.state)
        self.state['release'] = '9.8.7'
        self.state['application_input']['revision'] = 'c' * 40
        after = policy.verification_decision(self.state)
        self.assertEqual(before['reused_workflows'], after['reused_workflows'])
        self.assertEqual(after['mode'], 'reuse')
        self.assertNotEqual(before['application_revision'], after['application_revision'])

    def test_vendored_manual_pin_rejects_external_sources_and_overrides(self):
        with tempfile.TemporaryDirectory() as temporary:
            vendored, external = [Path(temporary) / name for name in ('vendored.json', 'external.json')]
            vendored.write_text(json.dumps({'repository': 'carbogninalberto/assozeta', 'path': 'manuale'}))
            external.write_text(json.dumps({'repository': 'Bakney/manuale', 'revision': 'a' * 40}))
            self.assertEqual(policy.pinned_source(vendored), {'repository': 'carbogninalberto/assozeta', 'path': 'manuale'})
            with self.assertRaisesRegex(ValueError, 'vendored'):
                policy.pinned_source(external)
            for override in ('a' * 40, 'main', 'v1.2.3'):
                with self.assertRaisesRegex(ValueError, 'override'):
                    policy.pinned_source(vendored, override=override)

    def test_vendored_manual_materializes_one_deterministic_clean_revision(self):
        spec = importlib.util.spec_from_file_location('manuale_ci_pin', Path(__file__).resolve().parents[1] / 'manuale-ci.py')
        ci = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ci)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'application'
            (root / 'manuale/docs').mkdir(parents=True)
            (root / 'manuale/docs/page.mdx').write_text('# Pagina è\n')
            (root / 'manuale/.manuale-evidence.json').write_text('{}\n')
            (root / 'docs/manuale').mkdir(parents=True)
            (root / 'docs/manuale/source.json').write_text(json.dumps({'repository': 'carbogninalberto/assozeta', 'path': 'manuale'}))
            identity = {'GIT_AUTHOR_NAME': 'fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                        'GIT_COMMITTER_NAME': 'fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
            run = lambda *arguments: subprocess.run(['git', '-C', str(root), *arguments], check=True, capture_output=True, env={**os.environ, **identity})
            run('init', '-q'); run('add', '-A'); run('-c', 'commit.gpgsign=false', 'commit', '-q', '-m', 'fixture')
            # An uncommitted workspace file must not enter the materialized manual.
            (root / 'manuale/docs/draft.mdx').write_text('draft\n')
            ci.ROOT = root
            revisions = []
            for name in ('one', 'two'):
                args = type('Args', (), {'manual_repo': str(Path(temporary) / name), 'manual_revision': '', 'output': str(Path(temporary) / (name + '.json'))})
                with patch.object(policy, 'ROOT', root):
                    ci.pin(args, type('Policy', (), {'pinned_source': staticmethod(lambda override=None: policy.pinned_source(root / 'docs/manuale/source.json', override))}))
                revisions.append(json.loads(Path(args.output).read_text())['revision'])
                files = subprocess.run(['git', '-C', args.manual_repo, 'ls-files'], check=True, capture_output=True, text=True).stdout.split()
                self.assertEqual(sorted(files), ['.manuale-evidence.json', 'docs/page.mdx'])
            self.assertEqual(revisions[0], revisions[1])
            self.assertRegex(revisions[0], '^[0-9a-f]{40}$')

    def trusted_producer(self):
        repo = 'example/application'
        run = {'id': 12, 'status': 'completed', 'conclusion': 'success',
               'repository': {'full_name': repo}, 'head_repository': {'full_name': repo},
               'path': '.github/workflows/manuale.yml', 'event': 'pull_request'}
        artifact = {'expired': False, 'workflow_run': {'id': 12}, 'digest': 'sha256:' + 'a' * 64}
        return repo, run, artifact

    def test_failed_fork_unmerged_wrong_workflow_expired_and_unbound_producers_are_rejected(self):
        repo, run, artifact = self.trusted_producer()
        self.assertIsNone(policy.producer_rejection(run, artifact, repo, accepted_source=True))
        for change in ({'status': 'in_progress'}, {'conclusion': 'failure'}, {'conclusion': 'cancelled'},
                       {'head_repository': {'full_name': 'fork/application'}},
                       {'path': '.github/workflows/arbitrary.yml'}, {'event': 'pull_request_target'}):
            self.assertIsNotNone(policy.producer_rejection({**run, **change}, artifact, repo, accepted_source=True))
        self.assertIsNotNone(policy.producer_rejection(run, artifact, repo, accepted_source=False))
        for change in ({'expired': True}, {'workflow_run': {'id': 13}}, {'digest': ''}):
            self.assertIsNotNone(policy.producer_rejection(run, {**artifact, **change}, repo, accepted_source=True))

    def test_source_admission_requires_real_default_branch_ancestry_or_merged_pr(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
            git('init', '-q')
            git('config', 'user.name', 'Offline Unit Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            git('commit', '--allow-empty', '-qm', 'Accepted fixture')
            accepted = git('rev-parse', 'HEAD')
            git('update-ref', 'refs/remotes/origin/main', accepted)
            git('commit', '--allow-empty', '-qm', 'Unmerged fixture')
            unmerged = git('rev-parse', 'HEAD')
            run = {'event': 'pull_request', 'head_sha': unmerged}
            args = {'root': root, 'api': lambda endpoint: []}
            self.assertFalse(policy.accepted_source(run, 'example/application', 'origin/main', **args))
            self.assertTrue(policy.accepted_source({**run, 'head_sha': accepted}, 'example/application', 'origin/main', **args))
            pr = {'merged_at': 'fixture', 'merge_commit_sha': accepted,
                  'head': {'sha': unmerged, 'repo': {'full_name': 'example/application'}},
                  'base': {'repo': {'full_name': 'example/application'}}}
            args['api'] = lambda endpoint: [pr]
            self.assertTrue(policy.accepted_source(run, 'example/application', 'origin/main', **args))
            pr['merged_at'] = None
            self.assertFalse(policy.accepted_source(run, 'example/application', 'origin/main', **args))

    def test_authenticated_artifact_digest_and_exact_archive_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'artifact.zip'
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr('manuale-baseline.tar.gz', b'explicit offline archive fixture')
            artifact = {'digest': 'sha256:' + hashlib.sha256(archive.read_bytes()).hexdigest()}
            policy.verify_archive_download(archive, artifact, root / 'valid.tar.gz')
            self.assertEqual((root / 'valid.tar.gz').read_bytes(), b'explicit offline archive fixture')
            with self.assertRaisesRegex(ValueError, 'authenticated'):
                policy.verify_archive_download(archive, {'digest': 'sha256:' + '0' * 64}, root / 'invalid')
            with zipfile.ZipFile(archive, 'a') as output:
                output.writestr('../extra', b'untrusted')
            artifact['digest'] = 'sha256:' + hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'contents'):
                policy.verify_archive_download(archive, artifact, root / 'invalid')
