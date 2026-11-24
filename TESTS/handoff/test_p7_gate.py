"""Phase 7 part A: the release gate (CONTRACT P7 v1, tmp/go-handoff/work/t-p7/CONTRACT.md).

Boundary: tools/release/qualify.py, the one gate every publication entry point runs (part B).
Inputs: a candidate (commit, five image digests, distribution bundle digest, fixture tree versions),
one receipt per lane of TESTS/handoff/release_matrix.json, the compatibility ledger and
TESTS/handoff/expected_red.json. Output: a qualification document; `qualified` only when nothing
below applies. Reason codes are pinned (p7_release.REASONS).

  evaluate(candidate, receipts, *, matrix, expected_red, ledger, root) -> {
      'format': 1, 'qualified': bool, 'candidate': candidate,
      'reasons': [{'code': str, 'lane': str|None, 'detail': str}],
      'receipts': {lane: sha256 hex of json.dumps(receipt, sort_keys=True, separators=(',', ':'))}}
  published_mismatches(qualification, published: {image name: reference@sha256:...}) -> sorted names
      whose published digest differs from (or is absent next to) the qualified one.
  CLI: qualify.py check --root R --candidate C --receipts DIR --matrix M --expected-red E --ledger L
       --output Q   exit 0 iff qualified; also binds the candidate to the checkout R (commit = HEAD,
       clean tree, fixtures = git tree ids of TESTS/handoff and selfhost/tests at HEAD) else
       candidate_mismatch.

Red first (plan Phase 7): a candidate with a missing worker, one unresolved task, a denied PDF host,
a forced old-runtime HTTP call, a duplicate provider effect or a skipped release test is refused,
each mutation alone, in either runtime, with its own reason. The installed counterparts that show
the lanes record those facts are in test_p7_installed.py.

Run: python3 -B -m unittest -v TESTS.handoff.test_p7_gate   (seconds; no services)
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from TESTS.handoff import p7_release as r

RED = r.RED


class GateCase(r.ContractCase, unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.gate = r.load_gate(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger = r.closed_ledger(self.root)
        self.matrix = r.synthetic_matrix()
        self.candidate = r.candidate()
        self.receipts = r.passing_receipts(self.candidate, self.matrix)

    def evaluate(self, receipts=None, *, candidate=None, matrix=None, expected_red=None, ledger=None):
        return self.gate.evaluate(copy.deepcopy(candidate or self.candidate),
                                  copy.deepcopy(self.receipts if receipts is None else receipts),
                                  matrix=copy.deepcopy(matrix or self.matrix),
                                  expected_red=expected_red or r.closed_expected_red(),
                                  ledger=ledger or self.ledger, root=self.root)

    def codes(self, result):
        return sorted({reason['code'] for reason in result['reasons']})

    def assertRefused(self, result, code, message):
        self.assertFalse(result['qualified'], f'{message}: qualified anyway')
        self.assertEqual(self.codes(result), [code], f'{message}: reasons {result["reasons"]}')
        for reason in result['reasons']:
            self.assertIn(reason['code'], r.REASONS, 'unknown reason code')


class Gate(GateCase):
    def test_clean_candidate_qualifies_and_lists_every_receipt(self):
        result = self.evaluate()
        self.assertEqual((result['format'], result['qualified'], result['reasons']), (1, True, []),
                         'a clean candidate with every receipt (positive control)')
        self.assertEqual(result['candidate'], self.candidate)
        self.assertEqual(result['receipts'], {item['lane']: r.canonical_sha256(item) for item in self.receipts},
                         'the qualification binds each lane to the digest of its receipt')

    def assert_mutation_refused(self, name):
        _, code = r.MUTATIONS[name]
        for runtime in ('django', 'go'):
            with self.subTest(runtime=runtime):
                result = self.evaluate(r.mutated(self.receipts, name, runtime))
                self.assertRefused(result, code, f'{name} in {runtime} mode')
                self.assertTrue(any(reason['lane'] == 'steady_state' for reason in result['reasons']),
                                f'{name}: the reason must name the lane that observed it')

    def test_missing_worker_is_refused(self):
        self.assert_mutation_refused('missing_worker')

    def test_one_unresolved_task_is_refused(self):
        self.assert_mutation_refused('unresolved_task')

    def test_denied_pdf_host_is_refused(self):
        self.assert_mutation_refused('denied_pdf_host')

    def test_forced_old_runtime_http_call_is_refused(self):
        self.assert_mutation_refused('forced_old_runtime_http_call')

    def test_duplicate_provider_effect_is_refused(self):
        self.assert_mutation_refused('duplicate_provider_effect')

    def test_skipped_release_test_is_refused(self):
        self.assert_mutation_refused('skipped_release_test')
        for outcome in ('failed', 'error', 'expected_red'):
            with self.subTest(outcome=outcome):
                receipts = copy.deepcopy(self.receipts)
                name = next(iter(receipts[0]['tests']))
                receipts[0]['tests'][name] = outcome
                self.assertRefused(self.evaluate(receipts), 'test_not_passed', f'a {outcome} release test')

    def test_work_dispositions(self):
        cases = {'pending due now': {'state': 'pending', 'owner': 'go', 'eta_future': False},
                 'completed on the inactive runtime': {'state': 'completed', 'owner': 'django', 'eta_future': False},
                 'failed': {'state': 'failed', 'owner': 'go', 'eta_future': False},
                 'future work owned by the inactive runtime': {'state': 'pending', 'owner': 'django', 'eta_future': True}}
        for label, item in cases.items():
            with self.subTest(label):
                receipts = copy.deepcopy(self.receipts)
                r.find(receipts, 'work', 'go')['items'].append({'id': 'go-extra', **item})
                self.assertRefused(self.evaluate(receipts), 'unresolved_work', label)

    def test_a_running_inactive_runtime_is_refused_without_a_request(self):
        receipts = copy.deepcopy(self.receipts)
        r.find(receipts, 'inactive_runtime', 'go')['running'] = ['worker']
        self.assertRefused(self.evaluate(receipts), 'inactive_runtime_call', 'a Django worker running in Go mode')

    def test_empty_observations_are_no_evidence(self):
        for kind, field in (('work', 'items'), ('renders', 'renders')):
            with self.subTest(kind=kind):
                receipts = copy.deepcopy(self.receipts)
                r.find(receipts, kind, 'django')[field] = []
                self.assertRefused(self.evaluate(receipts), 'observation_missing',
                                   f'an empty {kind} observation (a dead fixture produces no traffic)')

    def test_absent_observation_is_refused(self):
        receipts = copy.deepcopy(self.receipts)
        for item in receipts:
            item['observations'] = [entry for entry in item['observations']
                                    if (entry['kind'], entry['runtime']) != ('renders', 'go')]
        self.assertRefused(self.evaluate(receipts), 'observation_missing', 'no PDF observation in Go mode')


class Binding(GateCase):
    def assert_mismatch(self, change, label):
        receipts = copy.deepcopy(self.receipts)
        change(receipts[-1])
        self.assertRefused(self.evaluate(receipts), 'receipt_mismatch', label)

    def test_receipt_from_another_commit(self):
        self.assert_mismatch(lambda item: item.update(commit='d' * 40), 'a receipt of another commit')

    def test_receipt_for_another_image_digest(self):
        for name in r.IMAGES:
            with self.subTest(image=name):
                self.assert_mismatch(lambda item: item['images'].update({name: item['images'][name].split('@')[0]
                                                                         + '@' + r.digest('other')}),
                                     f'a receipt for another {name} image')

    def test_receipt_for_another_bundle(self):
        self.assert_mismatch(lambda item: item.update(bundle_sha256='e' * 64), 'a receipt of another bundle')

    def test_receipt_with_other_fixture_versions(self):
        for tree in r.FIXTURE_TREES:
            with self.subTest(tree=tree):
                self.assert_mismatch(lambda item: item['fixtures'].update({tree: 'f' * 40}),
                                     f'a receipt run with other {tree} fixtures')

    def test_missing_lane_receipt(self):
        self.assertRefused(self.evaluate(self.receipts[:-1]), 'lane_missing', 'no receipt for a matrix lane')

    def test_open_lane_blocks_every_release(self):
        matrix = copy.deepcopy(self.matrix)
        matrix['lanes']['legacy'] = {'tests': [], 'observations': [], 'open': 'not authored yet'}
        self.assertRefused(self.evaluate(matrix=matrix), 'lane_open', 'an open matrix lane')

    def test_required_test_absent(self):
        receipts = copy.deepcopy(self.receipts)
        steady = next(item for item in receipts if item['lane'] == 'steady_state')
        steady['tests'].pop(next(iter(steady['tests'])))
        self.assertRefused(self.evaluate(receipts), 'test_missing', 'a required release test absent from its lane')
        receipts = copy.deepcopy(self.receipts)
        next(item for item in receipts if item['lane'] == 'contracts')['tests'] = {'TESTS.handoff.other.Case.test': 'passed'}
        self.assertRefused(self.evaluate(receipts), 'test_missing', 'no test of a prefix entry ran')

    def test_open_compatibility_ledger(self):
        for status in ('characterized', 'needs_review'):
            with self.subTest(status=status):
                root = self.root / status
                ledger = r.closed_ledger(root, status=status)
                result = self.gate.evaluate(copy.deepcopy(self.candidate), copy.deepcopy(self.receipts),
                                            matrix=self.matrix, expected_red=r.closed_expected_red(),
                                            ledger=ledger, root=root)
                self.assertRefused(result, 'ledger_open', f'a ledger row still {status}')

    def test_stale_compatibility_ledger(self):
        (self.root / 'BE/core/urls.py').write_text("urlpatterns = [path('healthz', health), path('new', new)]\n")
        self.assertRefused(self.evaluate(), 'ledger_open', 'a source declaration the ledger does not know')

    def test_expected_red_entries_block(self):
        expected = r.closed_expected_red()
        expected['tests']['TESTS.handoff.test_x.Case.test_y'] = {'phase': 7, 'owner': 't-p7', 'reason': 'fixture',
                                                                 'signatures': ['P7 contract red']}
        self.assertRefused(self.evaluate(expected_red=expected), 'expected_red_open', 'a remaining expected red')

    def test_published_digest_must_be_the_qualified_one(self):
        qualification = self.evaluate()
        self.assertEqual(self.gate.published_mismatches(qualification, dict(self.candidate['images'])), [])
        published = dict(self.candidate['images'], web=self.candidate['images']['web'].split('@')[0] + '@'
                         + r.digest('rebuilt web'))
        published.pop('updater')
        self.assertEqual(self.gate.published_mismatches(qualification, published), ['updater', 'web'],
                         'a rebuilt or missing published image')


class ReceiptSchema(GateCase):
    def assert_invalid(self, change, label, code='receipt_invalid'):
        receipts = copy.deepcopy(self.receipts)
        change(receipts[0])
        self.assertRefused(self.evaluate(receipts), code, label)

    def test_malformed_receipts(self):
        cases = {
            'format 2': lambda item: item.update(format=2),
            'short commit': lambda item: item.update(commit='c' * 12),
            'image by tag': lambda item: item['images'].update(web='registry.invalid/fixture/assozeta-web:9.9.9'),
            'unknown field': lambda item: item.update(environment={'NAME': 'value'}),
            'finished before started': lambda item: item.update(finished_at='2026-11-24T07:00:00Z'),
            'timestamp without zone': lambda item: item.update(started_at='2026-11-24T08:00:00'),
            'unknown outcome': lambda item: item['tests'].update({next(iter(item['tests'])): 'flaky'}),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.assert_invalid(change, label)

    def test_malformed_observations(self):
        cases = {
            'unknown kind': lambda receipts: r.find(receipts, 'renders', 'go').update(kind='screenshots'),
            'unknown runtime': lambda receipts: r.find(receipts, 'renders', 'go').update(runtime='node'),
            'unknown field': lambda receipts: r.find(receipts, 'renders', 'go').update(payload='raw'),
            'zero effect count': lambda receipts: r.find(receipts, 'provider_effects', 'go')['effects'][0].update(count=0),
        }
        for label, change in cases.items():
            with self.subTest(label):
                receipts = copy.deepcopy(self.receipts)
                change(receipts)
                self.assertRefused(self.evaluate(receipts), 'receipt_invalid', label)

    def test_duplicate_lane_receipts(self):
        self.assertRefused(self.evaluate(self.receipts + [copy.deepcopy(self.receipts[0])]), 'receipt_invalid',
                           'two receipts for one lane')

    def test_malformed_candidate(self):
        cases = {'missing image': lambda c: c['images'].pop('updater'),
                 'extra image': lambda c: c['images'].update(tools=c['images']['web']),
                 'short commit': lambda c: c.update(commit='c' * 7),
                 'bundle not sha256': lambda c: c.update(bundle_sha256='xyz')}
        for label, change in cases.items():
            with self.subTest(label):
                candidate = copy.deepcopy(self.candidate)
                change(candidate)
                result = self.evaluate(candidate=candidate)
                self.assertFalse(result['qualified'], label)
                self.assertIn('candidate_invalid', self.codes(result), label)


class RealMatrix(r.ContractCase, unittest.TestCase):
    """The checked-in matrix covers the plan's exit lanes and names only tests that exist."""

    def test_matrix_names_the_exit_lanes(self):
        lanes = r.matrix()['lanes']
        self.assertEqual(sorted(lanes), sorted(['contracts', 'fresh_round_trip', 'failure_recovery', 'go_mode_lifecycle',
                                                'django_mode_lifecycle', 'steady_state', 'legacy_mixed_upgrade']))
        self.assertEqual(sorted((o['kind'], o['runtime']) for o in lanes['steady_state']['observations']),
                         sorted((kind, runtime) for kind in r.KINDS for runtime in ('django', 'go')))

    def test_matrix_unittest_ids_exist(self):
        loader = unittest.TestLoader()
        for lane, spec in r.matrix()['lanes'].items():
            for entry in spec['tests']:
                if not entry.startswith('TESTS.'):
                    continue
                with self.subTest(lane=lane, test=entry):
                    if entry.endswith('*'):
                        prefix = entry[:-1].rstrip('.')
                        names = [p.stem for p in (r.ROOT / 'TESTS/handoff').glob('test_*.py')]
                        self.assertTrue(any(f'TESTS.handoff.{name}'.startswith(prefix) for name in names),
                                        f'no module for {entry}')
                    else:
                        suite = loader.loadTestsFromName(entry)
                        self.assertEqual(suite.countTestCases(), 1, f'{entry} is not one test')

    def test_the_real_matrix_with_clean_receipts_is_blocked_only_by_open_lanes(self):
        gate = r.load_gate(self)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = r.closed_ledger(root)
            matrix = r.matrix()
            candidate = r.candidate()
            result = gate.evaluate(candidate, r.passing_receipts(candidate, matrix), matrix=matrix,
                                   expected_red=r.closed_expected_red(), ledger=ledger, root=root)
        self.assertEqual(sorted({(x['code'], x['lane']) for x in result['reasons']}),
                         sorted(('lane_open', lane) for lane, spec in matrix['lanes'].items() if spec.get('open')))


class Cli(r.ContractCase, unittest.TestCase):
    """`qualify.py check` binds the candidate to the exact checkout it qualifies."""

    def setUp(self):
        if not r.GATE.is_file():
            self.fail(f'{RED}: the release gate tools/release/qualify.py does not exist')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.repo = self.work / 'repo'
        for tree in r.FIXTURE_TREES:
            (self.repo / tree).mkdir(parents=True)
            (self.repo / tree / 'fixture.txt').write_text(tree + '\n')
        self.ledger = r.closed_ledger(self.repo)
        environment = {**os.environ, 'GIT_AUTHOR_NAME': 'fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                       'GIT_COMMITTER_NAME': 'fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
                       'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
        self.git_env = environment
        for command in (['init', '-q'], ['add', '-A'], ['commit', '-q', '-m', 'fixture']):
            subprocess.run(['git', '-C', self.repo, *command], check=True, env=environment, capture_output=True)
        self.head = self.git('rev-parse', 'HEAD')
        self.matrix = r.synthetic_matrix()
        self.candidate = r.candidate(self.head)
        self.candidate['fixtures'] = {tree: self.git('rev-parse', f'HEAD:{tree}') for tree in r.FIXTURE_TREES}

    def git(self, *arguments):
        return subprocess.run(['git', '-C', self.repo, *arguments], check=True, capture_output=True, text=True,
                              env=self.git_env).stdout.strip()

    def check(self, candidate=None, receipts=None):
        candidate = candidate or self.candidate
        receipts_dir = self.work / 'receipts'
        receipts_dir.mkdir(exist_ok=True)
        for old in receipts_dir.glob('*.json'):
            old.unlink()
        for item in (r.passing_receipts(candidate, self.matrix) if receipts is None else receipts):
            (receipts_dir / f'{item["lane"]}.json').write_text(json.dumps(item))
        paths = {'candidate': self.work / 'candidate.json', 'matrix': self.work / 'matrix.json',
                 'expected-red': self.work / 'expected-red.json', 'output': self.work / 'qualification.json'}
        paths['candidate'].write_text(json.dumps(candidate))
        paths['matrix'].write_text(json.dumps(self.matrix))
        paths['expected-red'].write_text(json.dumps(r.closed_expected_red()))
        paths['output'].unlink(missing_ok=True)
        result = subprocess.run([sys.executable, '-B', r.GATE, 'check', '--root', self.repo, '--candidate',
                                 paths['candidate'], '--receipts', receipts_dir, '--matrix', paths['matrix'],
                                 '--expected-red', paths['expected-red'], '--ledger', self.ledger,
                                 '--output', paths['output']], capture_output=True, text=True, timeout=120)
        document = json.loads(paths['output'].read_text()) if paths['output'].exists() else None
        return result, document

    def test_check_qualifies_the_candidate_of_this_checkout(self):
        result, document = self.check()
        self.assertEqual(result.returncode, 0, f'check of a clean candidate: {result.stderr[-800:]}')
        self.assertTrue(document and document['qualified'], 'qualification document')

    def test_check_refuses_another_commit_fixture_drift_and_a_dirty_tree(self):
        other = dict(self.candidate, commit='a' * 40)
        drift = copy.deepcopy(self.candidate)
        drift['fixtures']['selfhost/tests'] = 'b' * 40
        for label, candidate in (('candidate of another commit', other), ('fixtures not those of HEAD', drift)):
            with self.subTest(label):
                result, document = self.check(candidate)
                self.assertEqual(result.returncode, 1, label)
                self.assertIn('candidate_mismatch', {x['code'] for x in (document or {}).get('reasons', [])}, label)
        (self.repo / 'TESTS/handoff/fixture.txt').write_text('changed\n')
        result, document = self.check()
        self.assertEqual(result.returncode, 1, 'a modified checkout')
        self.assertIn('candidate_mismatch', {x['code'] for x in (document or {}).get('reasons', [])})

    def test_check_exits_nonzero_on_a_mutation(self):
        receipts = r.mutated(r.passing_receipts(self.candidate, self.matrix), 'missing_worker', 'go')
        result, document = self.check(receipts=receipts)
        self.assertEqual(result.returncode, 1, 'a missing worker must fail the release job')
        self.assertEqual({x['code'] for x in document['reasons']}, {'missing_worker'})


if __name__ == '__main__':
    unittest.main()
