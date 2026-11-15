"""Transport manifest for distributing the manual capture across CI runners."""
import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
transport_spec = importlib.util.spec_from_file_location('manuale_capture_transport',
                                                       SCRIPTS / 'manuale_capture_transport.py')
transport = importlib.util.module_from_spec(transport_spec)
transport_spec.loader.exec_module(transport)

REVISION = 'a' * 40
CAPTURE_ID = '0123456789abcdef'
TOOLING = {'docs/scripts/manuale.py': 'b' * 64, 'selfhost/compose.dev.yml': 'c' * 64}


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


def build(*, recipes=None, fresh=None, reused=None, runners=3, **overrides):
    """A deliberately valid transport over the synthetic registry by default."""
    recipes = REGISTRY if recipes is None else recipes
    ids = [item['id'] for item in recipes]
    options = {'run_id': '1234567890', 'run_attempt': 2, 'capture_id': CAPTURE_ID,
               'application_revision': REVISION, 'manual_revision': REVISION,
               'release': 'manuale-2026.09', 'reference_date': '2026-09-30',
               'tooling_hashes': dict(TOOLING), 'recipes': recipes,
               'fresh': ids if fresh is None else fresh, 'reused': [] if reused is None else reused,
               'runners': runners}
    options.update(overrides)
    return transport.build_transport(**options)


class BuildTests(unittest.TestCase):
    def test_zero_fresh_workflows_produce_an_empty_plan(self):
        built = build(fresh=[], reused=IDS, runners=4)
        self.assertEqual(built['fresh'], [])
        self.assertEqual(built['reused'], IDS)
        self.assertEqual(built['shards'], [])
        self.assertEqual(transport.matrix(built), {'include': []})
        with self.assertRaisesRegex(transport.TransportError, 'Unknown shard index'):
            transport.shard_workflows(built, 0)

    def test_reused_only_run_needs_no_runner(self):
        built = build(fresh=[], reused=list(reversed(IDS)), runners=1)
        self.assertEqual(built['reused'], IDS)
        self.assertEqual(built['shards'], [])
        self.assertEqual(transport.loads(transport.dumps(built))['shards'], [])

    def test_one_fresh_workflow_gets_one_canonical_shard(self):
        built = build(fresh=['delta'], reused=[item for item in IDS if item != 'delta'], runners=6)
        self.assertEqual(built['fresh'], ['delta'])
        self.assertEqual(built['shards'], [{'index': 0, 'workflows': ['delta'],
                                            'artifact': f'manuale-capture-{CAPTURE_ID}-s0',
                                            'diagnostics_artifact': f'manuale-capture-diag-{CAPTURE_ID}-s0-a2'}])
        self.assertEqual(transport.matrix(built), {'include': [
            {'index': 0, 'workflows': 'delta', 'artifact': f'manuale-capture-{CAPTURE_ID}-s0'}]})
        self.assertEqual(transport.shard_workflows(built, 0), ['delta'])

    def test_seven_fresh_workflows_on_five_runners_are_uneven_but_complete(self):
        fresh = ['hotel', 'alpha', 'india', 'charlie', 'golf', 'bravo', 'echo']
        built = build(fresh=fresh, reused=['delta', 'foxtrot'], runners=5)
        shards = built['shards']
        self.assertEqual(len(shards), 5)
        self.assertEqual([shard['index'] for shard in shards], [0, 1, 2, 3, 4])
        assigned = [identifier for shard in shards for identifier in shard['workflows']]
        self.assertEqual(sorted(assigned), sorted(fresh))
        self.assertEqual(len(set(assigned)), len(assigned))
        self.assertTrue(all(shard['workflows'] for shard in shards))
        for shard in shards:
            self.assertEqual(shard['workflows'], sorted(shard['workflows'], key=IDS.index))
            self.assertEqual(shard['artifact'], f"manuale-capture-{CAPTURE_ID}-s{shard['index']}")

    def test_all_workflows_fresh_fill_every_runner(self):
        built = build(fresh=IDS, reused=[], runners=6)
        self.assertEqual(len(built['shards']), 6)
        self.assertEqual(sorted(identifier for shard in built['shards'] for identifier in shard['workflows']),
                         sorted(IDS))
        self.assertTrue(all(shard['workflows'] for shard in built['shards']))

    def test_one_runner_gets_the_whole_batch_in_registry_order(self):
        built = build(fresh=IDS, reused=[], runners=1)
        self.assertEqual(len(built['shards']), 1)
        self.assertEqual(built['shards'][0]['workflows'], IDS)

    def test_shards_are_contiguous_registry_order_blocks(self):
        built = build(fresh=IDS, reused=[], runners=3)
        self.assertEqual([identifier for shard in built['shards'] for identifier in shard['workflows']], IDS)
        self.assertTrue(all(shard['workflows'] for shard in built['shards']))
        self.assertEqual(build(fresh=list(reversed(IDS)), reused=[], runners=3), built)
        # Reused workflows leave holes; the fresh blocks still follow registry order.
        picked = ['india', 'charlie', 'alpha', 'golf', 'echo']
        partial = build(fresh=picked, reused=[identifier for identifier in IDS if identifier not in picked], runners=4)
        self.assertEqual([identifier for shard in partial['shards'] for identifier in shard['workflows']],
                         ['alpha', 'charlie', 'echo', 'golf', 'india'])
        self.assertTrue(all(shard['workflows'] for shard in partial['shards']))

    def test_runner_count_must_be_a_bounded_integer(self):
        for invalid in (0, 7, -1, True, False, '3', 3.0, None):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(transport.TransportError, 'runners'):
                build(runners=invalid)

    def test_fresh_and_reused_must_be_unique_and_cover_the_registry(self):
        for options, pattern in (({'fresh': ['alpha', 'alpha'], 'reused': IDS[1:]}, 'Duplicate workflow ids in fresh'),
                                 ({'fresh': ['alpha', 'missing'], 'reused': IDS[1:]}, 'Unknown workflows in fresh'),
                                 ({'fresh': ['alpha'], 'reused': IDS}, 'both fresh and reused'),
                                 ({'fresh': ['alpha'], 'reused': IDS[2:]}, 'neither fresh nor reused: bravo'),
                                 ({'fresh': 'alpha', 'reused': IDS[1:]}, 'fresh workflows must be a list')):
            with self.subTest(pattern=pattern), self.assertRaisesRegex(transport.TransportError, pattern):
                build(**options)
        with self.assertRaisesRegex(transport.TransportError, 'Duplicate workflow ids in the recipe registry'):
            build(recipes=[recipe('alpha'), recipe('alpha')], fresh=['alpha'], reused=[])

    def test_the_manifest_is_deterministic(self):
        first, second = build(fresh=IDS, runners=4), build(fresh=list(reversed(IDS)), runners=4)
        self.assertEqual(transport.dumps(first), transport.dumps(second))
        self.assertEqual(transport.dumps(first), transport.dumps(first))
        text = transport.dumps(first)
        self.assertTrue(text.endswith('\n'))
        self.assertTrue(text.startswith('{\n  '))
        self.assertEqual(text, json.dumps(json.loads(text), sort_keys=True, indent=2, ensure_ascii=False) + '\n')

    def test_a_manifest_round_trips_through_loads(self):
        built = build(fresh=IDS, runners=5)
        self.assertEqual(transport.loads(transport.dumps(built)), built)
        for invalid in ('', '{', '[]', 'null', json.dumps({**built, 'fresh': []})):
            with self.subTest(invalid=invalid), self.assertRaises(transport.TransportError):
                transport.loads(invalid)
        with self.assertRaises(transport.TransportError):
            transport.loads(built)

    def test_the_manifest_carries_no_private_material(self):
        text = transport.dumps(build(fresh=IDS, runners=6))
        for forbidden in ('token', 'password', 'secret', '/home', '/Users'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_the_registry_digest_tracks_ids_fixtures_and_checkpoints(self):
        digest = transport.registry_sha256(REGISTRY)
        self.assertEqual(digest, hashlib.sha256(json.dumps(
            [[item['id'], item.get('fixture') or 'baseline',
              sorted(point['id'] for point in item['checkpoints'])] for item in REGISTRY],
            separators=(',', ':')).encode()).hexdigest())
        self.assertEqual(digest, build()['registry_sha256'])
        self.assertNotEqual(digest, transport.registry_sha256([recipe('alpha', 5), *REGISTRY[1:]]))
        self.assertNotEqual(digest, transport.registry_sha256([recipe('alpha', 4, 'member-transfer'), *REGISTRY[1:]]))


class RealRegistryTests(unittest.TestCase):
    def test_real_registry_partitions_into_a_complete_transport(self):
        registry = json.loads((ROOT / 'docs/manuale/recipes.json').read_text())['recipes']
        ids = [item['id'] for item in registry]
        built = build(recipes=registry, fresh=ids, reused=[], runners=transport.MAX_RUNNERS)
        self.assertEqual(built['registry_sha256'], transport.registry_sha256(registry))
        self.assertEqual(len(built['shards']), transport.MAX_RUNNERS)
        assigned = sorted(identifier for shard in built['shards'] for identifier in shard['workflows'])
        self.assertEqual(assigned, sorted(ids))
        self.assertTrue(all(shard['workflows'] for shard in built['shards']))
        partial = build(recipes=registry, fresh=ids[:7], reused=ids[7:], runners=5)
        self.assertEqual(transport.loads(transport.dumps(partial)), partial)
        self.assertEqual(len(transport.matrix(partial)['include']), 5)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.base = build()

    def assertRejected(self, mutate, pattern=None, *, recipes=REGISTRY):
        value = copy.deepcopy(self.base)
        mutate(value)
        self.assertRejectedValue(value, pattern, recipes=recipes)

    def assertRejectedValue(self, value, pattern=None, *, recipes=REGISTRY):
        with self.assertRaises(transport.TransportError) as raised:
            transport.validate_transport(value, recipes=recipes)
        if pattern:
            self.assertRegex(str(raised.exception), pattern)
        return raised.exception

    def test_only_objects_with_exactly_the_known_keys_are_accepted(self):
        self.assertRejectedValue([])
        self.assertRejectedValue('transport')
        self.assertRejected(lambda value: value.pop('release'), 'missing keys: release')
        self.assertRejected(lambda value: value.update(token='x'), 'unknown keys: token')
        self.assertRejected(lambda value: value.update(shard='x'), 'unknown keys: shard')
        self.assertRejected(lambda value: value['shards'][0].pop('artifact'), 'missing keys: artifact')
        self.assertRejected(lambda value: value['shards'][0].update(workflow='x'), 'unknown keys: workflow')
        self.assertRejected(lambda value: value.update(shards={'index': 0}), 'shards must be a list')
        self.assertRejected(lambda value: value['shards'].append('shard'), 'must be an object')

    def test_every_scalar_is_format_checked(self):
        self.assertRejected(lambda value: value.update(format=2), 'format')
        self.assertRejected(lambda value: value.update(format=True), 'format')
        self.assertRejected(lambda value: value.update(run_attempt='2'), 'run_attempt')
        self.assertRejected(lambda value: value.update(run_attempt=0), 'run_attempt')
        self.assertRejected(lambda value: value.update(run_attempt=True), 'run_attempt')
        for run_id in ('', '12a', 1234, ' 12'):
            with self.subTest(run_id=run_id):
                self.assertRejected(lambda value, run_id=run_id: value.update(run_id=run_id), 'run_id')
        for capture_id in ('0123456789ABCDEF', '0123456789abcde', '0123456789abcdef0', 'g123456789abcdef'):
            with self.subTest(capture_id=capture_id):
                self.assertRejected(lambda value, capture_id=capture_id: value.update(capture_id=capture_id),
                                    'capture_id')
        for revision in ('a' * 39, 'a' * 41, 'A' * 40, 'g' * 64):
            with self.subTest(revision=revision):
                self.assertRejected(lambda value, revision=revision: value.update(application_revision=revision),
                                    'application_revision')
                self.assertRejected(lambda value, revision=revision: value.update(manual_revision=revision),
                                    'manual_revision')
        self.assertRejected(lambda value: value.update(release=''), 'release')
        self.assertRejected(lambda value: value.update(release='x' * 65), 'release')
        self.assertRejected(lambda value: value.update(release='manuale 2026'), 'release')
        self.assertRejected(lambda value: value.update(release='manuale/2026'), 'release')
        self.assertRejected(lambda value: value.update(reference_date='2026-02-30'), 'reference_date')
        self.assertRejected(lambda value: value.update(reference_date='2026-9-30'), 'reference_date')
        self.assertRejected(lambda value: value.update(reference_date='20260930'), 'reference_date')
        self.assertRejected(lambda value: value.update(reference_date='2026-09-30T00:00:00'), 'reference_date')
        self.assertRejected(lambda value: value.update(registry_sha256='d' * 63), 'registry_sha256')
        self.assertRejected(lambda value: value.update(registry_sha256='D' * 64), 'registry_sha256')

    def test_tooling_hashes_are_relative_paths_with_sha256_digests(self):
        self.assertRejected(lambda value: value.update(tooling_hashes=[]), 'tooling_hashes must be an object')
        self.assertRejected(lambda value: value.update(tooling_hashes={'/etc/passwd': 'b' * 64}),
                            'tooling_hashes path')
        self.assertRejected(lambda value: value.update(tooling_hashes={'docs/../../secret': 'b' * 64}),
                            'escapes the repository')
        self.assertRejected(lambda value: value.update(tooling_hashes={'docs//scripts/manuale.py': 'b' * 64}),
                            'tooling_hashes path')
        self.assertRejected(lambda value: value.update(tooling_hashes={'./manuale.py': 'b' * 64}),
                            'tooling_hashes path')
        self.assertRejected(lambda value: value.update(tooling_hashes={'docs\\scripts\\manuale.py': 'b' * 64}),
                            'tooling_hashes path')
        self.assertRejected(lambda value: value.update(tooling_hashes={'': 'b' * 64}), 'tooling_hashes')
        self.assertRejected(lambda value: value.update(tooling_hashes={'docs/scripts/manuale.py': 'B' * 64}),
                            'digest')
        self.assertRejected(lambda value: value.update(tooling_hashes={'docs/scripts/manuale.py': 'b' * 63}),
                            'digest')
        self.assertRejected(lambda value: value.update(tooling_hashes={'docs/scripts/manuale.py': None}), 'digest')

    def test_fresh_and_reused_lists_must_be_unique_and_disjoint(self):
        self.assertRejected(lambda value: value.update(fresh=['alpha', 'alpha']), 'Duplicate workflow ids in fresh')
        self.assertRejected(lambda value: value.update(fresh=[]), 'although no workflow is fresh')
        self.assertRejected(lambda value: value.update(reused=['alpha']), 'both fresh and reused')
        self.assertRejected(lambda value: value.update(fresh='alpha'), 'fresh workflows must be a list')
        self.assertRejected(lambda value: value.update(reused={}), 'reused workflows must be a list')
        self.assertRejected(lambda value: value.update(fresh=[*IDS, 'missing']), 'not assigned to any shard')
        self.assertRejected(lambda value: value.update(fresh=[*IDS, 'Missing']), 'Invalid workflow id in fresh')
        self.assertRejected(lambda value: value.update(fresh=list(reversed(IDS))), 'not in registry order')
        self.assertRejected(lambda value: value.update(reused=['missing']), 'Unknown workflows in reused')

    def test_reused_workflows_must_be_in_registry_order(self):
        base = build(fresh=['alpha'], reused=IDS[1:])
        value = copy.deepcopy(base)
        value['reused'] = list(reversed(value['reused']))
        with self.assertRaisesRegex(transport.TransportError, 'reused workflows are not in registry order'):
            transport.validate_transport(value, recipes=REGISTRY)
        self.assertEqual(transport.validate_transport(copy.deepcopy(base), recipes=REGISTRY), base)

    def test_shard_indexes_are_exactly_zero_to_n_minus_one(self):
        self.assertRejected(lambda value: value['shards'][0].update(index=1), 'exactly 0..n-1')
        self.assertRejected(lambda value: value['shards'][1].update(index=5), 'exactly 0..n-1')
        self.assertRejected(lambda value: value['shards'][0].update(index='0'), 'index must be an integer')
        self.assertRejected(lambda value: value['shards'].reverse(), 'exactly 0..n-1')

    def test_every_shard_has_workflows_and_every_workflow_one_shard(self):
        self.assertRejected(lambda value: value['shards'][0].update(workflows=[]), 'has no workflows')
        self.assertRejected(lambda value: value['shards'][1]['workflows'].append(value['shards'][0]['workflows'][0]),
                            'assigned to several shards')
        self.assertRejected(lambda value: value['shards'][0]['workflows'].pop(), 'not assigned to any shard')
        self.assertRejected(lambda value: value['shards'][0].update(workflows=[1]), 'Invalid workflow id')
        self.assertRejected(lambda value: value.update(shards=[]), 'not assigned to any shard')

    def test_a_shard_only_runs_fresh_workflows(self):
        base = build(fresh=IDS[:-1], reused=[IDS[-1]])
        value = copy.deepcopy(base)
        value['shards'][0]['workflows'][0] = IDS[-1]
        with self.assertRaisesRegex(transport.TransportError, 'not fresh'):
            transport.validate_transport(value, recipes=REGISTRY)

    def test_shards_must_be_contiguous_blocks_of_the_fresh_workflows(self):
        def swap(value):
            first, second = value['shards'][0]['workflows'], value['shards'][1]['workflows']
            first[-1], second[0] = second[0], first[-1]

        value = copy.deepcopy(self.base)
        swap(value)
        # Every fresh workflow is still assigned exactly once: only the block order is wrong.
        self.assertEqual(sorted(identifier for shard in value['shards'] for identifier in shard['workflows']),
                         sorted(IDS))
        self.assertNotEqual([identifier for shard in value['shards'] for identifier in shard['workflows']],
                            value['fresh'])
        with self.assertRaisesRegex(transport.TransportError, 'contiguous'):
            transport.validate_transport(value, recipes=REGISTRY)
        # The swapped manifest is not a valid transport, so it never reaches a matrix.
        with self.assertRaises(transport.TransportError):
            transport.matrix(value)

    def test_shards_must_match_the_fresh_workflows(self):
        value = copy.deepcopy(build(fresh=[], reused=IDS))
        value['shards'] = [{'index': 0, 'workflows': ['alpha'],
                            'artifact': f'manuale-capture-{CAPTURE_ID}-s0',
                            'diagnostics_artifact': f'manuale-capture-diag-{CAPTURE_ID}-s0-a2'}]
        with self.assertRaisesRegex(transport.TransportError, 'although no workflow is fresh'):
            transport.validate_transport(value, recipes=REGISTRY)

    def test_artifact_names_are_canonical(self):
        self.assertRejected(lambda value: value['shards'][0].update(artifact='manuale-capture-s0'), 'artifact must be exactly')
        self.assertRejected(lambda value: value['shards'][1].update(
            artifact=f'manuale-capture-{CAPTURE_ID}-s2'), 'artifact must be exactly')
        self.assertRejected(lambda value: value['shards'][0].update(
            artifact=f'manuale-capture-{"f" * 16}-s0'), 'artifact must be exactly')
        self.assertRejected(lambda value: value['shards'][1].update(
            diagnostics_artifact=f'manuale-capture-diag-{CAPTURE_ID}-s1-a3'), 'diagnostics_artifact must be exactly')
        self.assertRejected(lambda value: value['shards'][1].update(
            diagnostics_artifact=f'manuale-capture-{CAPTURE_ID}-s1-a2'), 'diagnostics_artifact must be exactly')
        self.assertRejected(lambda value: value['shards'][0].update(diagnostics_artifact=7), 'diagnostics_artifact')

    def test_with_recipes_the_registry_digest_and_membership_must_match(self):
        self.assertRejected(lambda value: value.update(registry_sha256='d' * 64),
                            'registry_sha256 does not match')
        changed = [recipe('alpha', 5), *REGISTRY[1:]]
        with self.assertRaisesRegex(transport.TransportError, 'registry_sha256 does not match'):
            transport.validate_transport(copy.deepcopy(self.base), recipes=changed)
        value = copy.deepcopy(build(fresh=[], reused=IDS))
        value['reused'] = value['reused'][:-1]
        with self.assertRaisesRegex(transport.TransportError, 'do not match the recipe registry'):
            transport.validate_transport(value, recipes=REGISTRY)
        # Without the registry the same manifest is structurally sound.
        self.assertEqual(transport.validate_transport(copy.deepcopy(value))['fresh'], [])

    def test_matrix_and_shard_workflows_validate_first(self):
        value = copy.deepcopy(self.base)
        value['shards'][0]['artifact'] = 'manuale-capture-s0'
        with self.assertRaises(transport.TransportError):
            transport.matrix(value)
        with self.assertRaises(transport.TransportError):
            transport.shard_workflows(value, 0)
        built = build(fresh=IDS, runners=3)
        self.assertEqual(transport.matrix(built), {'include': [
            {'index': shard['index'], 'workflows': ','.join(shard['workflows']), 'artifact': shard['artifact']}
            for shard in built['shards']]})
        for index in (3, -1, '0', True):
            with self.subTest(index=index), self.assertRaises(transport.TransportError):
                transport.shard_workflows(built, index)


if __name__ == '__main__':
    unittest.main()
