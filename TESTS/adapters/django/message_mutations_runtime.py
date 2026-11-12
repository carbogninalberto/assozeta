"""Parent-only actual HTTP runner; owns fixtures, never infrastructure or fallback.

CLI only prints metadata. Execute run() inside the parent's configured production
Python process with migrated private PostgreSQL/Redis and live endpoint guards.
A Django-only run is explicit evidence, not differential or complete-family parity.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path

from TESTS.adapters.django import message_mutations as adapter
from TESTS.contracts import test_message_mutations as contract

ROOT = adapter.ROOT
OWNED_SOURCES = (
    'BE/communications/views.py',
    'BE/communications/tests/test_message_deletion.py',
    'BE/communications/tests/test_message_mutation_atomicity.py',
    'TESTS/fixtures/message_mutations.json',
    'TESTS/contracts/test_message_mutations.py',
    'TESTS/adapters/django/message_mutations.py',
    'TESTS/adapters/django/message_mutations_runtime.py',
    'TESTS/test_message_mutations.py',
    'docs/architecture/go-migration/message_mutations_reference.md',
)


def source_hashes():
    # Bind the complete shared Python helper graph and Django/Go application source.
    from TESTS.adapters.django import message_reads_runtime as reuse
    paths = reuse.source_paths()
    paths.update((ROOT / 'TESTS').rglob('*.py'))
    paths.update((ROOT / 'TESTS/fixtures').rglob('*.json'))
    paths.update(ROOT / name for name in OWNED_SOURCES)
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def metadata():
    source = contract.load_source()
    return {'scope': source['scope'], 'authored_cases': len(source['cases']),
            'source_sha256': contract.source_digest(source),
            'interfaces': ['seed_fixture(apps, private_work, raw)',
                'fixture_context(apps, private_work, raw, *, cleanup_receipt=None)',
                'run_workload(label, backend, base_url, fixture, apps, raw, observer, *, remote_addr, process_guard, observations_path=None)',
                'run(*, apps, raw, observer, private_work, endpoints, go_server_factory=None, go_server_options=None)'],
            'runtime_executed': False, 'complete_message_family_parity': False}


def _endpoint(value):
    required = {'label', 'backend', 'base_url', 'remote_addr', 'process_guard'}
    contract.require(isinstance(value, dict) and required <= set(value), 'endpoint.explicit-fields')
    contract.require(value['backend'] in ('django', 'go') and callable(value['process_guard'])
                     and isinstance(value['base_url'], str) and value['base_url'].startswith(('http://127.0.0.1:', 'http://localhost:')),
                     'endpoint.local-live-service')
    import ipaddress
    ipaddress.ip_address(value['remote_addr'])
    # Versioned paths are parent-owned gateways; backend is never inferred from a response header.
    return value


def run(*, apps, raw, observer, private_work, endpoints, go_server_factory=None, go_server_options=None):
    """Run one expectation set against fresh fixtures on parent-supplied actual HTTP.

    Endpoints: {label, backend, base_url, remote_addr, process_guard, binary?}.
    Go endpoints must provide the actual prebuilt executable path. A supplied Go
    factory contextmanager must yield that same interface; wrap an existing native
    factory in the parent to add its real process guard and binary. No build/install
    or automatic native/HTTP/client fallback exists here.

    private_work must not exist, and must be below the parent's private directory.
    Only credential-free receipts are returned. Full raw state/cache/audit/SQL schema
    observations are kept in private 0600 files for the parent, never published here.
    Caller removes this evidence directory after inspecting or private retention.
    """
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    adapter.require_private_database()
    contract.require(__debug__ and callable(getattr(observer, 'delta', None))
                     and callable(getattr(observer, 'cursor', None)), 'runtime.observer-required')
    contract.require(isinstance(endpoints, (list, tuple)) and bool(endpoints), 'runtime.explicit-endpoints')
    work = Path(private_work)
    contract.require(not work.exists(), 'runtime.fresh-private-directory')
    contract.require(go_server_options is None or go_server_factory is not None, 'runtime.factory-options')
    selected = [_endpoint(dict(endpoint)) for endpoint in endpoints]
    report = {'scope': 'Saved-message CREATE/DELETE authored HTTP and complete state effects',
              'runtime_executed': False, 'complete_message_family_parity': False,
              'differential_executed': False, 'success': False, 'fixture_cleanup_verified': False,
              'private_evidence_removed': False, 'infrastructure_cleanup_owner': 'parent',
              'workloads': [], 'fixture_cleanup': [], 'source_sha256': source_hashes(),
              'source_sha256_after': None, 'schema': adapter.schema_evidence(),
              'authored_source_sha256': contract.source_digest(contract.load_source())}
    work.mkdir(mode=0o700, parents=False)
    private_files = []
    try:
        with ExitStack() as stack:
            if go_server_factory is not None:
                native = stack.enter_context(go_server_factory(**(go_server_options or {})))
                selected.append(_endpoint(dict(native)))
            contract.require(len({endpoint['label'] for endpoint in selected}) == len(selected), 'runtime.unique-labels')
            reference = None
            backends = set()
            for index, endpoint in enumerate(selected):
                if endpoint['backend'] == 'go':
                    binary = Path(endpoint['binary']).resolve(strict=True)
                    contract.require(binary.is_file(), 'runtime.prebuilt-go-binary')
                    artifact = hashlib.sha256(binary.read_bytes()).hexdigest()
                else:
                    artifact = None
                cleanup = {}
                report['fixture_cleanup'].append(cleanup)
                observation_path = work / f'observations-{index}.jsonl'
                private_files.append(observation_path)
                with adapter.fixture_context(apps, work / f'fixture-{index}', raw, cleanup_receipt=cleanup) as fixture:
                    workload = adapter.run_workload(endpoint['label'], endpoint['backend'], endpoint['base_url'],
                        fixture, apps, raw, observer, remote_addr=endpoint['remote_addr'],
                        process_guard=endpoint['process_guard'], observations_path=observation_path)
                    # Audit remote address has already been compared with the independently
                    # measured parent address for this transport. Normalize only for cross-route comparison.
                    effects = workload.pop('normalized_effects')
                    for effect in effects:
                        if 'audit' in effect:
                            effect['audit']['remote_addr'] = '$independently-measured-client-ip'
                    if reference is None:
                        reference = effects
                    else:
                        contract.require(reference == effects, 'runtime.independent-effect-projections-differ')
                    workload['go_binary_sha256'] = artifact
                    workload['normalized_effects_sha256'] = adapter.shared.digest(effects)
                    report['workloads'].append(workload)
                    backends.add(endpoint['backend'])
                # Preserve only raw private observations while tearing down tokenized fixture.
                contract.require(cleanup['verified'], 'runtime.fixture-cleanup')
            report['runtime_executed'] = True
            report['differential_executed'] = backends == {'django', 'go'}
            report['django_only'] = backends == {'django'}
            report['success'] = all(workload['success'] for workload in report['workloads'])
            report['fixture_cleanup_verified'] = all(item['verified'] for item in report['fixture_cleanup'])
            report['source_sha256_after'] = source_hashes()
            contract.require(report['source_sha256_after'] == report['source_sha256'], 'runtime.source-drift')
            return report
    finally:
        # Evidence contains full private cache serialization; the runner never publishes it.
        # On success and failure the parent owns this directory's lifecycle explicitly.
        (work / 'private-evidence.json').write_text(json.dumps({'private_files': [str(path) for path in private_files],
            'schema': report['schema'], 'runtime_complete': report['runtime_executed'],
            'fixture_cleanup': report['fixture_cleanup']}, indent=2))
        (work / 'private-evidence.json').chmod(0o600)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', action='store_true')
    args = parser.parse_args(argv)
    if not args.metadata:
        parser.error('Parent must invoke run() with explicit live endpoints/observers; no service launcher is provided')
    print(json.dumps(metadata(), indent=2))


if __name__ == '__main__':
    main()
