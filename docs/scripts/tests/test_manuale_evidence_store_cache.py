"""manuale-cache.py publishes validated runs into the per-workflow evidence store."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_manuale_cache as portable

cache = portable.cache
fixtures = portable.fixtures
REPOSITORY = 'example/assozeta'


class PublishValidatedRunTests(unittest.TestCase):
    def setUp(self):
        self.portable = portable.VerifiedPortableCacheTests(
            'test_verified_baseline_relocates_with_original_provenance_and_public_inputs')
        self.portable.setUp()
        self.addCleanup(self.portable.doCleanups)
        self.f = self.portable.fixture
        self.store = self.f.root / 'evidence-store'
        self.revision = self.portable.application_revision
        self.producer = {'repository': REPOSITORY, 'workflow': '.github/workflows/manuale.yml', 'run_id': '3001',
                         'ref': 'refs/heads/main', 'head_sha': self.revision, 'event': 'push',
                         'conclusion': 'success'}
        evidence_store = cache._evidence_store()
        self.evidence_store = evidence_store
        patcher = patch.object(cache, '_evidence_store', return_value=evidence_store)
        patcher.start()
        self.addCleanup(patcher.stop)
        impact = patch.object(evidence_store, '_impact_module', return_value=None)
        impact.start()
        self.addCleanup(impact.stop)

    def test_export_publishes_fresh_workflows_with_original_provenance(self):
        result = cache.export_cache(self.f.old_run, self.portable.archive, store=self.store, producer=self.producer)
        self.assertTrue(self.portable.archive.is_file(), 'whole-run archive export is unchanged')
        published = result['evidence_store']['published']
        self.assertEqual([item['workflow_id'] for item in published], ['unit-members'])
        trusted = {'3001': dict(self.producer)}
        # The target checkout is a later commit with identical workflow inputs.
        target = self.f.code
        plan = cache.plan_store(self.store, target, trusted_producers=trusted, reference_date='2026-09-30',
                                repository=REPOSITORY, manifest_only=False)
        self.assertEqual(plan['reused_workflows'], ['unit-members'], plan['workflows'])
        self.assertNotEqual(fixtures.command(target, 'rev-parse', 'HEAD'), self.revision)
        output = self.f.root / 'materialized'
        selection = cache.materialize_store(self.store, plan, target, output, trusted_producers=trusted,
                                            reference_date='2026-09-30', repository=REPOSITORY)
        self.assertEqual(len(selection['materialized']), 1)
        self.assertEqual((output / 'unit-members.json').read_bytes(), (self.f.old_run / 'unit-members.json').read_bytes())
        report = json.loads((output / 'unit-members.json').read_text())
        self.assertEqual(report['application_revision'], self.revision)
        evidence = json.loads(next(self.store.glob('workflows/unit-members/*/evidence.json')).read_text())
        self.assertEqual([item['id'] for item in evidence['sections']], ['elenco'])
        self.assertIn('docs/members.mdx#elenco', evidence['editorial_bindings'])

    def test_producer_must_be_the_evaluated_commit(self):
        with self.assertRaisesRegex(ValueError, 'producer commit'):
            cache.publish_store(self.f.old_run, self.store, producer={**self.producer, 'head_sha': 'f' * 40})
        self.assertFalse(self.store.exists())

    def test_retained_scenarios_are_not_reattributed(self):
        manifest = json.loads((self.f.old_run / 'manifest.json').read_text())
        state = json.loads((self.f.old_run / 'run.json').read_text())
        validated = {'application_revision': self.revision, 'manual_revision': self.portable.manual_revision,
                     'manifest': {**manifest, 'scenarios': [{**manifest['scenarios'][0], 'reuse': {'origin': 'older'}}]},
                     'registry': json.loads((self.f.old_code / 'docs/manuale/recipes.json').read_text())}
        result = cache._publish_validated_store(self.f.old_run, state, validated, self.store, producer=self.producer)
        self.assertEqual(result['published'], [])
        self.assertEqual(result['skipped'][0]['workflow_id'], 'unit-members')

    def test_cli_keeps_export_import_contract_and_plans_cold(self):
        script = str(fixtures.ROOT / 'docs/scripts/manuale-cache.py')
        missing = subprocess.run([sys.executable, script, 'export'], capture_output=True, text=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('--run and --archive are required', missing.stderr)
        output = self.f.root / 'plan.json'
        cold = subprocess.run([sys.executable, script, 'store-plan', '--store', str(self.f.root / 'absent'),
                               '--application', str(fixtures.ROOT), '--output', str(output),
                               '--workflow', 'account-two-factor'], capture_output=True, text=True)
        self.assertEqual(cold.returncode, 0, cold.stderr)
        plan = json.loads(output.read_text())
        self.assertEqual(plan['capture_workflows'], ['account-two-factor'])
        self.assertFalse(plan['store_available'])


if __name__ == '__main__':
    unittest.main()
