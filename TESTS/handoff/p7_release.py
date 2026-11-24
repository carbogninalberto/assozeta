"""Phase 7 harness: the release gate's inputs, a clean candidate and the six mutations (CONTRACT P7 v1).

The gate under test is tools/release/qualify.py (implementer-owned). Everything here is test-owned:
  - load_gate(case): imports the gate; a missing gate is an assertion failure 'P7 contract red: ...',
  - candidate()/receipt()/passing_receipts(): a self-consistent candidate and one receipt per matrix
    lane that satisfies every lane requirement (the positive control),
  - MUTATIONS: the six release mutations of the plan, each a function that changes one fact in a
    clean receipt set, with the one gate reason code it must produce,
  - closed_ledger(root): a synthetic, fully verified compatibility ledger for a synthetic source tree.

No hosts, tokens or payloads: images are synthetic repository names with synthetic digests.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / 'tools/release/qualify.py'
MATRIX = Path(__file__).resolve().parent / 'release_matrix.json'
RED = 'P7 contract red'

IMAGES = ('backend', 'backend-go', 'web', 'renderer', 'updater')
FIXTURE_TREES = ('TESTS/handoff', 'selfhost/tests')
SHARED = ('postgres', 'redis', 'minio', 'renderer', 'web')
APP = {'django': ('api', 'worker', 'beat', 'realtime-relay'),
       'go': ('api-go', 'worker-go', 'scheduler-go', 'job-worker-go', 'realtime-relay-go')}
OTHER = {'django': 'go', 'go': 'django'}
KINDS = ('services', 'work', 'renders', 'inactive_runtime', 'provider_effects')
REASONS = ('candidate_invalid', 'candidate_mismatch', 'receipt_invalid', 'receipt_mismatch', 'lane_missing',
           'lane_open', 'test_missing', 'test_not_passed', 'observation_missing', 'missing_worker',
           'unresolved_work', 'pdf_render_failed', 'inactive_runtime_call', 'duplicate_effect', 'ledger_open',
           'expected_red_open')


class ContractCase:
    """Mixin: every assertion failure of a Phase 7 test reads 'P7 contract red: ...' (the expected-red signature)."""

    def _formatMessage(self, msg, standardMsg):
        text = super()._formatMessage(msg, standardMsg)
        return text if text.startswith(RED) else f'{RED}: ' + text


def load_gate(case):
    if not GATE.is_file():
        case.fail(f'{RED}: the release gate tools/release/qualify.py does not exist')
    spec = importlib.util.spec_from_file_location('assozeta_release_qualify', GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ('evaluate', 'published_mismatches'):
        if not callable(getattr(module, name, None)):
            case.fail(f'{RED}: tools/release/qualify.py has no {name}()')
    return module


def digest(text):
    return 'sha256:' + hashlib.sha256(text.encode()).hexdigest()


def canonical_sha256(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def matrix():
    return json.loads(MATRIX.read_text())


def candidate(commit='c' * 40):
    return {'format': 1, 'commit': commit, 'version': '9.9.9',
            'images': {name: f'registry.invalid/fixture/assozeta-{name}@{digest("image " + name)}' for name in IMAGES},
            'bundle_sha256': hashlib.sha256(b'fixture bundle').hexdigest(),
            'fixtures': {tree: hashlib.sha1(tree.encode()).hexdigest() for tree in FIXTURE_TREES}}


def observation(kind, runtime):
    if kind == 'services':
        return {'kind': kind, 'runtime': runtime, 'running': sorted(SHARED + APP[runtime])}
    if kind == 'work':
        return {'kind': kind, 'runtime': runtime, 'items': [
            {'id': f'{runtime}-due-now', 'state': 'completed', 'owner': runtime, 'eta_future': False},
            {'id': f'{runtime}-next-month', 'state': 'pending', 'owner': runtime, 'eta_future': True}]}
    if kind == 'renders':
        return {'kind': kind, 'runtime': runtime, 'renders': [{'document': 'pdf_probe', 'status': 'ok', 'reason': None}]}
    if kind == 'inactive_runtime':
        return {'kind': kind, 'runtime': runtime, 'started': [], 'running': [], 'requests': 0}
    if kind == 'provider_effects':
        return {'kind': kind, 'runtime': runtime, 'effects': [{'provider': 'whatsapp', 'item': f'{runtime}-1', 'count': 1}]}
    raise ValueError(kind)


def concrete_tests(spec):
    """Test ids that satisfy a lane's required entries (a '*' entry is a prefix: one matching id)."""
    return {entry[:-1] + 'fixture_case' if entry.endswith('*') else entry: 'passed' for entry in spec['tests']}


def receipt(lane, spec, cand):
    return {'format': 1, 'lane': lane, 'commit': cand['commit'], 'images': dict(cand['images']),
            'bundle_sha256': cand['bundle_sha256'], 'fixtures': dict(cand['fixtures']),
            'started_at': '2026-11-24T08:00:00Z', 'finished_at': '2026-11-24T09:00:00Z', 'run': f'fixture-{lane}',
            'tests': concrete_tests(spec),
            'observations': [observation(item['kind'], item['runtime']) for item in spec['observations']]}


def synthetic_matrix():
    """Two lanes, as the real matrix shapes them: plain tests, and tests plus every observation for both runtimes."""
    return {'format': 1, 'lanes': {
        'contracts': {'tests': ['TESTS.handoff.test_fixture.*'], 'observations': [], 'open': None},
        'steady_state': {'tests': ['TESTS.handoff.test_fixture_installed.SteadyState.test_django',
                                   'TESTS.handoff.test_fixture_installed.SteadyState.test_go'],
                         'observations': [{'kind': kind, 'runtime': runtime} for runtime in ('django', 'go')
                                          for kind in KINDS],
                         'open': None}}}


def passing_receipts(cand, lanes):
    return [receipt(lane, spec, cand) for lane, spec in sorted(lanes['lanes'].items()) if not spec.get('open')]


def find(receipts, kind, runtime):
    for item in receipts:
        for entry in item['observations']:
            if entry['kind'] == kind and entry['runtime'] == runtime:
                return entry
    raise LookupError(f'{kind}/{runtime} not in the fixture receipts')


# -- the six mutations of the plan (Phase 7 "Red first") ----------------------------------------------------------
def missing_worker(receipts, runtime):
    entry = find(receipts, 'services', runtime)
    worker = 'worker-go' if runtime == 'go' else 'worker'
    entry['running'] = [name for name in entry['running'] if name != worker]


def unresolved_task(receipts, runtime):
    find(receipts, 'work', runtime)['items'].append(
        {'id': f'{runtime}-unresolved', 'state': 'blocked', 'owner': runtime, 'eta_future': False})


def denied_pdf_host(receipts, runtime):
    find(receipts, 'renders', runtime)['renders'][0].update(status='failed', reason='target_host_not_allowed')


def forced_old_runtime_http_call(receipts, runtime):
    entry = find(receipts, 'inactive_runtime', runtime)
    entry.update(started=['api' if runtime == 'go' else 'api-go'], requests=1)


def duplicate_provider_effect(receipts, runtime):
    find(receipts, 'provider_effects', runtime)['effects'][0]['count'] = 2


def skipped_release_test(receipts, runtime):
    for item in receipts:
        if item['lane'] == 'steady_state':
            name = next(name for name in item['tests'] if name.endswith('test_' + runtime))
            item['tests'][name] = 'skipped'


MUTATIONS = {
    'missing_worker': (missing_worker, 'missing_worker'),
    'unresolved_task': (unresolved_task, 'unresolved_work'),
    'denied_pdf_host': (denied_pdf_host, 'pdf_render_failed'),
    'forced_old_runtime_http_call': (forced_old_runtime_http_call, 'inactive_runtime_call'),
    'duplicate_provider_effect': (duplicate_provider_effect, 'duplicate_effect'),
    'skipped_release_test': (skipped_release_test, 'test_not_passed'),
}


def mutated(receipts, name, runtime):
    copied = copy.deepcopy(receipts)
    MUTATIONS[name][0](copied, runtime)
    return copied


# -- compatibility ledger ---------------------------------------------------------------------------------------
def closed_ledger(root, status='verified'):
    """A synthetic source tree with one route and a ledger whose only row has the given status."""
    from TESTS.inventory.catalog import discover
    source = Path(root) / 'BE/core/urls.py'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("urlpatterns = [path('healthz', health)]\n")
    entries = discover(Path(root))
    for entry in entries:
        entry.update(status=status, go=['BE_GO/internal/handler/health.go'], tests=['TESTS/contracts/test_health.py'],
                     evidence=[{'command': 'synthetic-fixture-contract', 'revision': 'a' * 40, 'result': 'passed',
                                'artifact': 'synthetic-fixture-results.json'}])
        for field in ('authentication', 'permissions', 'resource_scope', 'side_effects', 'transaction'):
            entry['contract'][field] = 'Explicit synthetic fixture contract'
    path = Path(root) / 'ledger.json'
    path.write_text(json.dumps({'format': 1, 'entries': entries}, indent=1))
    return path


def closed_expected_red():
    return {'format': 1, 'release_evidence': False, 'rule': 'fixture', 'baseline_revision': '2284ed46', 'tests': {}}
