"""Consumer gate: store-materialized evidence enters a target run as explicit reuse.

Offline unit fixtures (temporary Git repositories, generated PNG bytes); they
exercise the real publish -> plan -> materialize -> admit -> retained-plan path
without a backend, browser or GitHub access.
"""
import io
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import zipfile

import test_manuale_evidence_store_cache as published

cache = published.cache
fixtures = published.fixtures
reuse = fixtures.reuse
REPOSITORY = published.REPOSITORY


class GateFixture(unittest.TestCase):
    def setUp(self):
        self.base = published.PublishValidatedRunTests('test_producer_must_be_the_evaluated_commit')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.f = self.base.f
        self.store = self.base.store
        self.evidence = self.base.evidence_store
        self.evidence.reuse = reuse
        self.producer = dict(self.base.producer)
        self.trusted = {'3001': dict(self.producer)}
        self.state = self.f.state
        self.state['selected_recipes'] = ['unit-members']
        # The runner admits store evidence for GITHUB_REPOSITORY; pin it to the
        # fixture repository so results do not depend on the CI environment.
        environment = patch.dict(os.environ, {'GITHUB_REPOSITORY': REPOSITORY})
        environment.start()
        self.addCleanup(environment.stop)

    def materialize(self, producer=None, *, trusted=None):
        cache.publish_store(self.f.old_run, self.store, producer=producer or self.producer)
        trusted = self.trusted if trusted is None else trusted
        plan = cache.plan_store(self.store, self.f.code, trusted_producers=trusted, reference_date='2026-09-30',
                                repository=REPOSITORY)
        output = self.f.root / 'selection'
        cache.materialize_store(self.store, plan, self.f.code, output, trusted_producers=trusted,
                                reference_date='2026-09-30', repository=REPOSITORY)
        return output

    def admit(self, selection, **options):
        return self.evidence.admit_selection(self.state, selection, repository=REPOSITORY, **options)


class StoreGateTests(GateFixture):

    def test_store_selection_becomes_explicit_reused_provenance(self):
        selection = self.materialize()
        result = self.admit(selection)
        self.assertEqual(sorted(result['admitted']), ['unit-members'], result['rejected'])
        self.state['selected_recipes'] = []
        plan = reuse.build_reuse_plan(self.state, None, stage=True, admitted=result['admitted'])
        entry = plan['recipes']['unit-members']
        self.assertEqual(plan['reused_recipes'], ['unit-members'])
        self.assertEqual(entry['source'], 'evidence-store')
        # Original application revision and capture id, never the target's.
        self.assertEqual(entry['origin']['application_revision'], self.base.revision)
        self.assertNotEqual(entry['origin']['application_revision'], self.state['application_input']['revision'])
        self.assertEqual(entry['origin']['capture_id'], 'unit-capture')
        self.assertEqual(entry['evidence_store']['producer']['run_id'], '3001')
        run = Path(self.state['run'])
        self.assertEqual((run / 'unit-members.json').read_bytes(), (self.f.old_run / 'unit-members.json').read_bytes())
        self.assertTrue((run / 'evidence-origin/unit-members.manifest.json').is_file())
        # The independent plan gate (also used by the corpus index) accepts it.
        self.state['evidence_reuse'] = {'path': 'evidence-reuse.json',
                                        'sha256': reuse.file_hash(run, 'evidence-reuse.json'),
                                        'reused_recipes': ['unit-members']}
        reuse.validate_reuse_plan(self.state)

    def test_plan_gate_rejects_a_store_entry_whose_provenance_was_altered(self):
        selection = self.materialize()
        admitted = self.admit(selection)['admitted']
        self.state['selected_recipes'] = []
        plan = reuse.build_reuse_plan(self.state, None, stage=True, admitted=admitted)
        for change in ({'origin': {**plan['recipes']['unit-members']['origin'], 'run_id': 'github-actions-run-9'}},
                       {'evidence_store': {**plan['recipes']['unit-members']['evidence_store'], 'manifest_sha256': '0' * 64}},
                       {'source': 'baseline'}):
            with self.subTest(change=sorted(change)):
                altered = json.loads(json.dumps(plan))
                altered['recipes']['unit-members'].update(change)
                with self.assertRaisesRegex(ValueError, 'store-origin-binding-invalid'):
                    reuse.validate_reuse_plan(self.state, altered)

    def test_tampered_selection_bytes_are_rejected(self):
        selection = self.materialize()
        cases = {
            'evidence-store/unit-members.manifest.json': lambda data: data.replace(b'"push"', b'"push" '),
            'evidence-store/unit-members.evidence.json': lambda data: data + b' ',
            'unit-members.json': lambda data: data + b'\n',
            'captures/images/unit-members/1.png': lambda data: data[:-1] + b'\0',
        }
        for relative, mutate in cases.items():
            with self.subTest(relative=relative):
                path = selection / relative
                original = path.read_bytes()
                path.write_bytes(mutate(original))
                result = self.admit(selection)
                self.assertEqual(result['admitted'], {})
                self.assertEqual(result['rejected'][0]['workflow_id'], 'unit-members')
                path.write_bytes(original)
        self.assertEqual(sorted(self.admit(selection)['admitted']), ['unit-members'])

    def test_pull_request_producer_is_never_admitted(self):
        producer = {**self.producer, 'event': 'pull_request', 'ref': 'refs/pull/7/merge', 'run_id': '3002'}
        cache.publish_store(self.f.old_run, self.store, producer=producer)
        fingerprint = next(self.store.glob('workflows/unit-members/*')).name
        # Integrity-only materialization: the consumer gate must still refuse it.
        entry = self.evidence._verify(self.store, 'unit-members', fingerprint, schema_version=1, trust=None)
        selection = self.f.root / 'pr-selection'
        record = self.evidence.materialize(entry, selection)
        value = {'format': 1, 'purpose': 'reused-workflow-evidence-not-fresh-capture', 'schema_version': 1,
                 'materialized': [record], 'rejected': [], 'capture_workflows': []}
        (selection / 'evidence-store-selection.json').write_text(json.dumps(value))
        # Hand-made selection without an authenticated producer record: nothing is trusted.
        with self.assertRaisesRegex(self.evidence.UntrustedProducer, 'trusted-producers'):
            self.admit(selection)
        # Even a record that lists the PR run cannot make it a trusted producer.
        self.write_trust(selection, value, {'3002': producer})
        result = self.admit(selection)
        self.assertEqual(result['admitted'], {})
        self.assertIn('UntrustedProducer', result['rejected'][0]['reason'])

    def write_trust(self, selection, value, trusted, repository=REPOSITORY):
        record = self.evidence._json_bytes(self.evidence.trusted_producers_record(trusted, repository=repository))
        (selection / self.evidence.TRUSTED_PRODUCERS_FILE).write_bytes(record)
        value['trusted_producers_sha256'] = self.evidence.digest(record)
        (selection / 'evidence-store-selection.json').write_text(json.dumps(value))

    def test_selection_without_an_authenticated_producer_record_is_never_trusted(self):
        selection = self.materialize()
        self.assertEqual(sorted(self.admit(selection)['admitted']), ['unit-members'])
        record_path = selection / self.evidence.TRUSTED_PRODUCERS_FILE
        original_record = record_path.read_bytes()
        value = json.loads((selection / 'evidence-store-selection.json').read_text())
        # Removed record (e.g. a selection copied or assembled by hand): the whole selection is refused.
        record_path.unlink()
        with self.assertRaisesRegex(self.evidence.UntrustedProducer, 'no authenticated trusted-producers'):
            self.admit(selection)
        # Edited record bytes no longer match the sha256 bound into the selection.
        record_path.write_bytes(original_record.replace(b'"3001"', b'"3001" '))
        with self.assertRaisesRegex(self.evidence.CorruptEvidence, 'trusted-producers'):
            self.admit(selection)
        # A consistent record for another repository is refused.
        self.write_trust(selection, dict(value), self.trusted, repository='someone/else')
        with self.assertRaisesRegex(self.evidence.UntrustedProducer, 'another repository'):
            self.admit(selection)
        # A consistent record that does not authenticate this producer run admits nothing.
        self.write_trust(selection, dict(value), {})
        result = self.admit(selection)
        self.assertEqual(result['admitted'], {})
        self.assertIn('not an authenticated trusted producer', result['rejected'][0]['reason'])
        # A record whose authenticated run differs from the manifest claim admits nothing.
        self.write_trust(selection, dict(value), {'3001': {**self.producer, 'head_sha': 'c' * 40}})
        result = self.admit(selection)
        self.assertEqual(result['admitted'], {})
        self.assertIn('differs from its authenticated run', result['rejected'][0]['reason'])
        # The runner treats a refused selection as a cold miss: every workflow is captured fresh.
        record_path.unlink()
        self.state['manual_repository'] = str(self.f.root / 'manual-source')
        args = fixtures.SimpleNamespace(command='run', changed_since=None, manual_changed_since=None,
                                        no_cache=True, reuse_store=str(selection))
        runner = fixtures.runner
        with patch.object(runner, 'retained_evidence_helper', return_value=reuse), \
                patch.object(runner, 'evidence_store_gate', return_value=self.evidence):
            runner.reuse_inputs(self.state, args)
        self.assertEqual(self.state['selected_recipes'], ['unit-members'])
        self.assertNotIn('evidence_reuse', self.state)

    def test_changed_dependency_or_explicit_selection_keeps_the_workflow_fresh(self):
        selection = self.materialize()
        self.assertIn('selected for fresh capture',
                      self.admit(selection, exclude={'unit-members'})['rejected'][0]['reason'])
        fixtures.write(self.f.code, 'BE/members/views.py', 'def members(): return [1]\n')
        fixtures.command(self.f.code, 'commit', '-qam', 'Behavior change')
        self.state['application_input']['revision'] = fixtures.command(self.f.code, 'rev-parse', 'HEAD')
        result = self.admit(selection)
        self.assertEqual(result['admitted'], {})
        self.assertIn('fingerprint', result['rejected'][0]['reason'])

    def test_runner_admits_the_store_and_captures_only_the_rest(self):
        selection = self.materialize()
        self.state['manual_repository'] = str(self.f.root / 'manual-source')
        args = fixtures.SimpleNamespace(command='run', changed_since=None, manual_changed_since=None,
                                        no_cache=True, reuse_store=str(selection))
        runner = fixtures.runner
        with patch.object(runner, 'retained_evidence_helper', return_value=reuse), \
                patch.object(runner, 'evidence_store_gate', return_value=self.evidence), \
                patch.object(runner, 'execute', side_effect=AssertionError('store admission started runtime')):
            runner.reuse_inputs(self.state, args)
        self.assertEqual(self.state['selected_recipes'], [])
        self.assertEqual(self.state['evidence_reuse']['reused_recipes'], ['unit-members'])
        self.assertEqual(self.state['evidence_store']['reused_workflows'], ['unit-members'])
        decision = next(item for item in self.state['cache']['decisions'] if item.get('kind') == 'evidence-store')
        self.assertEqual(decision['admitted'], ['unit-members'])

    def test_runner_refuses_store_evidence_of_another_repository(self):
        selection = self.materialize()
        self.state['manual_repository'] = str(self.f.root / 'manual-source')
        args = fixtures.SimpleNamespace(command='run', changed_since=None, manual_changed_since=None,
                                        no_cache=True, reuse_store=str(selection))
        runner = fixtures.runner
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'someone/else'}), \
                patch.object(runner, 'retained_evidence_helper', return_value=reuse), \
                patch.object(runner, 'evidence_store_gate', return_value=self.evidence):
            runner.reuse_inputs(self.state, args)
        self.assertEqual(self.state['selected_recipes'], ['unit-members'])
        self.assertNotIn('evidence_reuse', self.state)

    def test_runner_treats_an_unusable_selection_as_a_cold_miss(self):
        selection = self.f.root / 'broken'
        selection.mkdir()
        (selection / 'evidence-store-selection.json').write_text('{"format": 1}')
        self.state['manual_repository'] = str(self.f.root / 'manual-source')
        args = fixtures.SimpleNamespace(command='run', changed_since=None, manual_changed_since=None,
                                        no_cache=True, reuse_store=str(selection))
        runner = fixtures.runner
        with patch.object(runner, 'retained_evidence_helper', return_value=reuse), \
                patch.object(runner, 'evidence_store_gate', return_value=self.evidence):
            runner.reuse_inputs(self.state, args)
        self.assertEqual(self.state['selected_recipes'], ['unit-members'])
        self.assertNotIn('evidence_reuse', self.state)

    def test_release_binding_marks_store_evidence_reused_for_this_identity_only(self):
        selection = self.materialize()
        admitted = self.admit(selection)['admitted']
        self.state['selected_recipes'] = []
        reuse.build_reuse_plan(self.state, None, stage=True, admitted=admitted)
        run = Path(self.state['run'])
        self.state['evidence_reuse'] = {'path': 'evidence-reuse.json',
                                        'sha256': reuse.file_hash(run, 'evidence-reuse.json'),
                                        'reused_recipes': ['unit-members']}
        self.state['manual_input'] = {'revision': 'b' * 40}
        fixtures.write(run, 'run.json', self.state)
        with patch.object(cache, 'reuse_module', return_value=reuse):
            binding = cache.bind_run(run)
        self.assertEqual(binding['evidence'][0]['provenance'], 'reused')
        self.assertEqual(binding['evidence'][0]['origin_application_revision'], self.base.revision)
        self.assertEqual(binding['sources']['evidence_store'], ['unit-members'])
        self.assertEqual(binding['release_identity']['release'], self.state['release'])
        self.evidence.verify_release_binding(binding, application_revision=self.state['application_input']['revision'],
                                             manual_revision='b' * 40, release=self.state['release'])
        with self.assertRaises(ValueError):
            self.evidence.verify_release_binding(binding, application_revision=self.state['application_input']['revision'],
                                                 manual_revision='b' * 40, release='another-release')

    def test_release_verification_requires_the_exact_binding_of_this_run(self):
        selection = self.materialize()
        admitted = self.admit(selection)['admitted']
        self.state['selected_recipes'] = []
        reuse.build_reuse_plan(self.state, None, stage=True, admitted=admitted)
        run = Path(self.state['run'])
        self.state['evidence_reuse'] = {'path': 'evidence-reuse.json',
                                        'sha256': reuse.file_hash(run, 'evidence-reuse.json'),
                                        'reused_recipes': ['unit-members']}
        self.state['manual_input'] = {'revision': 'b' * 40}
        fixtures.write(run, 'run.json', self.state)
        identity = {'application_revision': self.state['application_input']['revision'],
                    'manual_revision': 'b' * 40, 'release': self.state['release']}
        path = run / 'release-binding.json'
        with patch.object(cache, 'reuse_module', return_value=reuse):
            with self.assertRaisesRegex(ValueError, 'Missing'):
                cache.verify_run_binding(run, path, **identity)
            binding = cache.bind_run(run)
            path.write_text(json.dumps(binding))
            result = cache.verify_run_binding(run, path, **identity)
            self.assertEqual((result['status'], result['release_identity']), ('verified', identity))
            for change in ({'release': 'another-release'}, {'manual_revision': 'c' * 40},
                           {'application_revision': 'd' * 40}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    cache.verify_run_binding(run, path, **{**identity, **change})
            # Intact bytes for this identity, but evidence that differs from the run's own.
            forged = json.loads(json.dumps(binding))
            forged['evidence'][0]['provenance'] = 'fresh'
            body = {key: value for key, value in forged.items() if key != 'binding_sha256'}
            forged['binding_sha256'] = self.evidence.digest(self.evidence.canonical(body))
            path.write_text(json.dumps(forged))
            with self.assertRaisesRegex(ValueError, "differs from the run's verified evidence"):
                cache.verify_run_binding(run, path, **identity)
            # Edited bytes.
            path.write_text(json.dumps({**binding, 'sources': {}}))
            with self.assertRaisesRegex(ValueError, 'changed'):
                cache.verify_run_binding(run, path, **identity)


class StoreMaintenanceTests(GateFixture):
    """Merge and compact around the same fixture."""

    def test_merge_accepts_only_this_runs_candidate_entries(self):
        candidate = self.f.root / 'candidate'
        cache.publish_store(self.f.old_run, candidate, producer=self.producer)
        target = self.f.root / 'merged'
        self.assertEqual(cache.merge_store(candidate, target, run_id='9999')['merged'], [])
        result = cache.merge_store(candidate, target, run_id='3001')
        self.assertEqual(len(result['merged']), 1)
        fingerprint = result['merged'][0].split('/')[1]
        entry = self.evidence.verify(target, 'unit-members', fingerprint, trusted_producers=self.trusted,
                                     repository=REPOSITORY)
        self.assertEqual(entry.verified, 'full')
        pr_candidate = self.f.root / 'pr-candidate'
        cache.publish_store(self.f.old_run, pr_candidate,
                            producer={**self.producer, 'event': 'pull_request', 'ref': 'refs/pull/1/merge'})
        self.assertIn('UntrustedProducer', cache.merge_store(pr_candidate, self.f.root / 'other', run_id='3001')
                      ['rejected'][0]['reason'])

    def test_compact_keeps_one_trusted_entry_per_current_fingerprint(self):
        cache.publish_store(self.f.old_run, self.store, producer=self.producer)
        stale = self.store / 'workflows/unit-members' / ('e' * 64)
        shutil.copytree(next(self.store.glob('workflows/unit-members/*')), stale)
        index = json.loads((self.store / 'index.json').read_text())
        index['entries']['unit-members/' + 'e' * 64] = next(iter(index['entries'].values()))
        (self.store / 'index.json').write_text(json.dumps(index))
        orphan = self.store / 'blobs/sha256' / ('d' * 64)
        orphan.write_bytes(b'orphan')
        result = cache.compact_store(self.store, self.f.code, trusted_producers=self.trusted,
                                     reference_date='2026-09-30', repository=REPOSITORY)
        self.assertEqual(len(result['kept']), 1)
        self.assertEqual([item['entry'] for item in result['dropped']], ['unit-members/' + 'e' * 64])
        self.assertFalse(stale.exists())
        self.assertFalse(orphan.exists())
        untrusted = cache.compact_store(self.store, self.f.code, trusted_producers={},
                                        reference_date='2026-09-30', repository=REPOSITORY)
        self.assertEqual(untrusted['kept'], [])
        self.assertEqual(list((self.store / 'blobs/sha256').iterdir()), [])


def artifact_zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class StoreFetchTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archives = {}
        self.runs = {}

    def artifact(self, identifier, run_id, members, *, name='manuale-evidence-store-index', digest=None, event='push',
                 ref_branch='main'):
        data = artifact_zip(members)
        self.archives[identifier] = data
        self.runs[run_id] = {'id': int(run_id), 'status': 'completed', 'conclusion': 'success', 'event': event,
                             'path': '.github/workflows/manuale.yml', 'head_branch': ref_branch, 'head_sha': 'a' * 40,
                             'repository': {'full_name': REPOSITORY}, 'head_repository': {'full_name': REPOSITORY}}
        return {'id': identifier, 'name': name, 'expired': False, 'workflow_run': {'id': int(run_id)},
                'digest': digest or 'sha256:' + cache._digest_bytes(data), 'size_in_bytes': len(data)}

    def fetch(self, artifacts, **options):
        def api(endpoint):
            if endpoint == f'repos/{REPOSITORY}':
                return {'default_branch': 'main'}
            if '/actions/artifacts?' in endpoint:
                return {'artifacts': artifacts}
            return self.runs[endpoint.rsplit('/', 1)[1]]

        def download(repository, identifier, destination):
            Path(destination).write_bytes(self.archives[identifier])
        return cache.fetch_store(REPOSITORY, self.root / 'store', api=api, download=download, **options)

    def test_newest_trusted_artifact_with_matching_digest_is_restored(self):
        index = {'index.json': json.dumps({'format': 1, 'entries': {}})}
        artifacts = [self.artifact(3, '30', index, event='pull_request'),
                     self.artifact(2, '20', index, digest='sha256:' + '0' * 64),
                     self.artifact(1, '10', index)]
        report = self.fetch(artifacts)
        self.assertEqual(report['status'], 'available')
        self.assertEqual(report['producer_run'], '10')
        self.assertEqual([item['result'] for item in report['candidates']], ['rejected', 'rejected', 'accepted'])
        self.assertIn('pull request', report['candidates'][0]['reason'])
        self.assertIn('digest', report['candidates'][1]['reason'])
        self.assertTrue((self.root / 'store/index.json').is_file())

    def test_unexpected_members_and_feature_branches_are_rejected(self):
        artifacts = [self.artifact(5, '50', {'index.json': '{}', '../escape.json': '{}'}),
                     self.artifact(4, '40', {'index.json': '{}', 'workflows/x/notahash/manifest.json': '{}'}),
                     self.artifact(3, '30', {'index.json': '{}'}, ref_branch='feature')]
        report = self.fetch(artifacts)
        self.assertEqual(report['status'], 'missing')
        self.assertFalse((self.root / 'store').exists() and any((self.root / 'store').rglob('*.json')))

    def test_blobs_come_from_the_index_producer_and_must_match_their_names(self):
        blob = fixtures.png()
        good = {'sha256/' + cache._digest_bytes(blob): blob}
        bad = {'sha256/' + 'f' * 64: blob}
        artifacts = [self.artifact(8, '80', bad, name='manuale-evidence-store-blobs'),
                     self.artifact(7, '70', good, name='manuale-evidence-store-blobs')]
        self.assertEqual(self.fetch(artifacts, kind='blobs', producer_run='80')['status'], 'missing')
        report = self.fetch(artifacts, kind='blobs', producer_run='70')
        self.assertEqual(report['status'], 'available')
        self.assertTrue((self.root / 'store/blobs/sha256' / cache._digest_bytes(blob)).is_file())


class FingerprintCoverageTests(unittest.TestCase):
    def test_shard_tooling_and_the_capture_import_closure_are_fingerprinted(self):
        store = cache._evidence_store()
        self.assertIn('docs/scripts/manuale_capture_shards.py', store.SHARED_CAPTURE_TOOLING)
        inputs = store.fingerprint_inputs(fixtures.ROOT, 'account-two-factor', manual_revision='x', schema_version=1)
        self.assertRegex(inputs['shared_tooling']['docs/scripts/manuale_capture_shards.py'], r'^[a-f0-9]{64}$')
        recipe = store._recipe(fixtures.ROOT, 'account-two-factor')[1]
        covered = set(inputs['dependencies']) | set(inputs['shared_tooling'])
        self.assertLessEqual(reuse.closure(fixtures.ROOT, recipe), covered | {'docs/manuale/recipes.json'})


if __name__ == '__main__':
    unittest.main()
