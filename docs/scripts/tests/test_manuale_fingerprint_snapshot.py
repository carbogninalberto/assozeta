"""Dependency snapshots: one repository analysis per fingerprint batch.

The fingerprint is unchanged by batching: only how many times the impact
analysis derives the whole repository graph changes. These tests run against
the real checkout (the real impact map, registry and sources).
"""
import importlib.util
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import test_manuale_reuse as fixtures

ROOT = fixtures.ROOT
REFERENCE_DATE = '2026-09-30'
spec = importlib.util.spec_from_file_location('manuale_evidence_store_snapshot',
                                              ROOT / 'docs/scripts/manuale_evidence_store.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)
impact_spec = importlib.util.spec_from_file_location('manuale_impact_for_snapshot',
                                                     ROOT / 'docs/scripts/manuale_impact.py')
impact = importlib.util.module_from_spec(impact_spec)
impact_spec.loader.exec_module(impact)


def fingerprint(workflow_id, manual_revision):
    return store.workflow_fingerprint(ROOT, workflow_id, manual_revision=manual_revision,
                                      schema_version=store.SCHEMA_VERSION, reference_date=REFERENCE_DATE)


class SnapshotEquivalenceTests(unittest.TestCase):
    """Snapshot and per-call analysis must produce identical evidence identity."""

    @classmethod
    def setUpClass(cls):
        # Real workflows of this checkout, with the revision computed per call.
        cls.workflows = sorted(store._registry(ROOT)[1])[:5]
        assert len(cls.workflows) == 5, cls.workflows
        cls.revisions = {item: store.workflow_manual_revision(ROOT, item) for item in cls.workflows}
        cls.baseline = {item: (store.workflow_dependencies(ROOT, item), fingerprint(item, cls.revisions[item]))
                        for item in cls.workflows}

    def test_snapshot_matches_per_call_dependencies_and_fingerprints(self):
        with store.dependency_snapshot(ROOT):
            for identifier in self.workflows:
                dependencies, expected = self.baseline[identifier]
                self.assertEqual(store.workflow_dependencies(ROOT, identifier), dependencies, identifier)
                self.assertEqual(fingerprint(identifier, self.revisions[identifier]), expected, identifier)

    def test_snapshot_computes_all_dependencies_once(self):
        counts = {'all': 0, 'one': 0}

        def all_dependencies(root):
            counts['all'] += 1
            return impact.all_workflow_dependencies(root)

        def dependencies(root, workflow_id):
            counts['one'] += 1
            return impact.workflow_dependencies(root, workflow_id)

        spy = types.SimpleNamespace(workflow_dependencies=dependencies,
                                    all_workflow_dependencies=all_dependencies)
        with patch.object(store, '_impact_module', return_value=spy):
            with store.dependency_snapshot(ROOT):
                for identifier in self.workflows:
                    store.workflow_dependencies(ROOT, identifier)
        self.assertEqual(counts, {'all': 1, 'one': 0})

    def test_nested_snapshots_for_one_root_reuse_the_outer_analysis(self):
        counts = {'all': 0, 'one': 0}

        def all_dependencies(root):
            counts['all'] += 1
            return impact.all_workflow_dependencies(root)

        def dependencies(root, workflow_id):
            counts['one'] += 1
            return impact.workflow_dependencies(root, workflow_id)

        spy = types.SimpleNamespace(workflow_dependencies=dependencies,
                                    all_workflow_dependencies=all_dependencies)
        with patch.object(store, '_impact_module', return_value=spy):
            with store.dependency_snapshot(ROOT):
                first = store.workflow_dependencies(ROOT, self.workflows[0])
                with store.dependency_snapshot(ROOT):
                    second = store.workflow_dependencies(ROOT, self.workflows[1])
                    self.assertEqual(store.workflow_dependencies(ROOT, self.workflows[0]), first)
                self.assertEqual(store.workflow_dependencies(ROOT, self.workflows[1]), second)
        self.assertEqual(counts, {'all': 1, 'one': 0})

    def test_outside_a_snapshot_every_call_uses_the_per_workflow_analysis(self):
        counts = {'all': 0, 'one': 0}

        def all_dependencies(root):
            counts['all'] += 1
            return impact.all_workflow_dependencies(root)

        def dependencies(root, workflow_id):
            counts['one'] += 1
            return impact.workflow_dependencies(root, workflow_id)

        spy = types.SimpleNamespace(workflow_dependencies=dependencies,
                                    all_workflow_dependencies=all_dependencies)
        with patch.object(store, '_impact_module', return_value=spy):
            first = store.workflow_dependencies(ROOT, self.workflows[0])
            second = store.workflow_dependencies(ROOT, self.workflows[0])
        self.assertEqual(first, second)
        self.assertEqual(counts, {'all': 0, 'one': 2})
        self.assertEqual(store._SNAPSHOTS, {})

    def test_the_snapshot_is_released_after_the_block(self):
        with store.dependency_snapshot(ROOT):
            store.workflow_dependencies(ROOT, self.workflows[0])
            self.assertIn(Path(ROOT).resolve(), store._SNAPSHOTS)
        self.assertEqual(store._SNAPSHOTS, {})

    def test_unknown_workflow_fails_identically_inside_a_snapshot(self):
        missing = 'not-a-registered-workflow'
        with self.assertRaises(KeyError) as outside:
            store.workflow_dependencies(ROOT, missing)
        with store.dependency_snapshot(ROOT):
            with self.assertRaises(KeyError) as inside:
                store.workflow_dependencies(ROOT, missing)
            self.assertIn(Path(ROOT).resolve(), store._SNAPSHOTS)
        self.assertEqual(inside.exception.args, outside.exception.args)
        self.assertEqual(inside.exception.args[0], 'Unknown manual workflow: ' + missing)

    def test_impact_module_without_the_batch_entry_point_stays_per_call(self):
        calls = []

        def dependencies(root, workflow_id):
            calls.append((Path(root), workflow_id))
            return ['BE/members/views.py']

        with patch.object(store, '_impact_module',
                          return_value=types.SimpleNamespace(workflow_dependencies=dependencies)):
            with store.dependency_snapshot(ROOT):
                first = store.workflow_dependencies(ROOT, self.workflows[0])
                second = store.workflow_dependencies(ROOT, self.workflows[0])
        self.assertEqual(first, second)
        self.assertEqual(calls, [(Path(ROOT), self.workflows[0]), (Path(ROOT), self.workflows[0])])


class ImpactModuleCacheTests(unittest.TestCase):
    """The impact module is executed once per process until its bytes change."""

    def test_unchanged_module_is_loaded_once_and_changed_bytes_reload(self):
        source = (ROOT / 'docs/scripts/manuale_impact.py').read_bytes()
        cached = dict(store._IMPACT_MODULES)
        self.addCleanup(store._IMPACT_MODULES.update, cached)
        self.addCleanup(store._IMPACT_MODULES.clear)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'docs/scripts').mkdir(parents=True)
            copy = root / 'docs/scripts/manuale_impact.py'
            copy.write_bytes(source)
            with patch.object(store, 'ROOT', root):
                first = store._impact_module()
                self.assertIsNotNone(first)
                self.assertIs(first, store._impact_module(), 'unchanged bytes are served from the cache')
                before = store._impact_cache_key(copy)
                self.assertEqual(before[0], str(copy.resolve()))
                copy.write_bytes(source + b'\nCACHE_MARKER = 2\n')
                self.assertNotEqual(before, store._impact_cache_key(copy), 'content change alters the key')
                second = store._impact_module()
                self.assertIsNot(first, second, 'changed bytes are reloaded')
                self.assertEqual(second.CACHE_MARKER, 2)


if __name__ == '__main__':
    unittest.main()
