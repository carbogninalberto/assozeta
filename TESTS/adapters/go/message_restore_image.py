"""Mandatory real restore controls within the existing packaged server lifetime.

Import is inert. All runtime owners, canonical fixtures and measured peer
addresses come from the packaged mutation gate. No fallback or optional skip.
"""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import time

from TESTS.adapters.go import message_mutation_restore as helper
from TESTS.adapters.go.message_image_execution import begin_cleanup, defer_interruption

CASE_NAMES = helper.CASE_NAMES
_COMMON = {'name', 'success', 'models', 'exact_effects', 'full_redis', 'backend',
           'transport', 'fixture_reset_owned_by_parent'}
_LEASE = {'exact_owner_row', 'blocked_backend', 'shared_lease_while_blocked',
          'exclusive_after_completed_effects'}
_RUNNING = {'running_admission', 'running_row_removed'}


def need(condition, code):
    if not condition:
        raise AssertionError('mutation-image.restore-' + code)


def validate_result(case, result, *, public=False):
    """Validate exact frozen-helper output before publishing any of its fields."""
    need(case in CASE_NAMES, 'case')
    keys = _COMMON | (_LEASE if case.endswith('lease') else _RUNNING)
    if public and case.endswith('lease'):
        keys = keys - {'blocked_backend'} | {'blocked_backend_sha256'}
    need(type(result) is dict and set(result) == keys, 'result-fields')
    need(result['name'] == 'message-mutations-restore-' + case
         and result['backend'] == 'go' and result['transport'] == 'packaged-actual-http'
         and type(result['models']) is int and result['models'] == 128, 'result-binding')
    booleans = keys - {'name', 'backend', 'transport', 'models', 'blocked_backend', 'blocked_backend_sha256'}
    need(all(result[key] is True for key in booleans), 'result-checks')
    if case.endswith('lease'):
        if public:
            from TESTS.adapters.go.message_mutations_image import hashed
            need(hashed(result['blocked_backend_sha256']), 'backend-hash')
        else:
            need(type(result['blocked_backend']) is int and result['blocked_backend'] > 0, 'backend')


def probe_receipt(case, values):
    need(type(values) is list and all(type(value) is bool for value in values), 'probe-types')
    if case.endswith('lease'):
        need(len(values) >= 4 and values[0] is True and values[-1] is True
             and all(value is False for value in values[1:-1]), 'lease-probes')
    else:
        need(values == [True, True], 'running-probes')
    return dict(before=True, blocked=len(values) - 2, after=True)


def receipt_valid(receipt, native):
    """Independent public gate; success flags cannot replace four actual results."""
    from TESTS.adapters.go import message_mutations_image as image
    try:
        need(type(receipt) is dict and set(receipt) == {'verified', 'backend', 'transport',
             'process_sha256', 'identity_sha256', 'go_binary_sha256', 'client_ip', 'baseline', 'results'}
             and receipt['verified'] is True and receipt['backend'] == 'go'
             and receipt['transport'] == 'packaged-actual-http', 'receipt')
        need(receipt['process_sha256'] == native['process_sha256']
             and image.hashed(receipt['process_sha256']) and image.hashed(receipt['identity_sha256'])
             and receipt['go_binary_sha256'] == native['go_binary_sha256']
             and image.hashed(receipt['go_binary_sha256'])
             and receipt['baseline'] == native['baseline']
             and set(receipt['baseline']) == {'state_sha256', 'cache_sha256', 'sequences_sha256'}
             and all(image.hashed(value) for value in receipt['baseline'].values())
             and receipt['client_ip'] == native['client_ip']
             and image.ip_valid(receipt['client_ip'], 'owned-container-tcp-peer', receipt['process_sha256']),
             'receipt-binding')
        rows = receipt['results']
        need(type(rows) is list and len(rows) == len(CASE_NAMES), 'receipt-count')
        for case, row in zip(CASE_NAMES, rows, strict=True):
            need(type(row) is dict and set(row) == {'case', 'result', 'fixture_cleanup', 'restored', 'probes'}
                 and row['case'] == case, 'receipt-case')
            validate_result(case, row['result'], public=True)
            need(image.cleanup_valid(row['fixture_cleanup'], image.FIXTURE_CLEANUP)
                 and type(row['restored']) is dict and set(row['restored']) == {'state', 'cache', 'sequences'}
                 and all(value is True for value in row['restored'].values()), 'receipt-restoration')
            probes = row['probes']
            need(type(probes) is dict and set(probes) == {'before', 'blocked', 'after'}
                 and probes['before'] is True and probes['after'] is True
                 and type(probes['blocked']) is int
                 and (probes['blocked'] >= 2 if case.endswith('lease') else probes['blocked'] == 0),
                 'receipt-probes')
        return True
    except (AssertionError, KeyError, TypeError, ValueError, AttributeError):
        return False


def make_identity_guard(proof, expected_process):
    """One bounded inspect per call; no zero-argument/90-second inspect fallback.

    The process identity is derived from the inspected immutable container using
    exactly the existing container_liveness_guard algorithm, then checked against
    the actual executing-server guard. All lock/path/UID checks remain in the
    existing _linux_lock_identity validator. No global Commands changes.
    """
    from TESTS.adapters.go import message_mutations_image as image
    expected = deepcopy(expected_process)
    baseline = None

    def observe(*, timeout):
        nonlocal baseline
        need(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 20, 'identity-budget')
        deadline = time.monotonic() + timeout
        container = baseline[0]['id'] if baseline is not None else proof.identifier + '-server'
        left = deadline - time.monotonic()
        need(left > 0, 'identity-deadline')
        output = proof.commands(['docker', 'inspect', container], timeout=left)
        need(time.monotonic() < deadline, 'identity-deadline')
        values = json.loads(output)
        need(type(values) is list and len(values) == 1, 'identity-inspect')
        info = values[0]
        identity = image.packaged._container_identity(info, proof.image_id, proof.identifier)
        process = dict(pid=identity['pid'], instance=int(image.contract.source_digest(identity), 16) or 1,
                       running=True)
        need(process == expected, 'identity-process')
        # The process above is independently derived from this same real inspect,
        # not a fabricated Popen or an unbounded call through the old inspector.
        value = image._linux_lock_identity(proof, lambda: process, info=info)
        need(time.monotonic() < deadline, 'identity-deadline')
        if baseline is None:
            baseline = deepcopy(value)
        need(value == baseline, 'identity-drift')
        return deepcopy(value)
    return observe


def _abort_unquiesced(*, receipt=None, container=None):
    """Fail closed if Docker cannot prove stop: never unwind into fixture reset.

    The outer gate initially writes an unsuccessful/unverified receipt. Its
    cleanup grace also hard-exits when ownership cannot be resolved. Retaining
    an error while running automatic DB resets would be unsafe here.
    """
    try:
        # This replaces only this invocation's initial unsuccessful receipt.
        # Whitelist fixed fields: never serialize the private in-memory report.
        if (receipt is not None and type(container) is str and len(container) == 64
                and all(character in '0123456789abcdef' for character in container)):
            Path(receipt).write_text(json.dumps(dict(success=False, cleanup_verified=False,
                private_directory_removed=False, errors=['mutation-image.restore-quiescence-unverified'],
                recovery=dict(container_id=container))) + '\n')
    finally:
        try:
            os.write(2, b'mutation-image.restore-quiescence-unverified; fixture reset forbidden\n')
        finally:
            os._exit(124)


def quiesce(proof, identity):
    """Stop all owned container work, including execs, before fixture teardown.

    Keep Commands' existing cleanup grace; an uncertain exec never becomes
    verified merely because the server was subsequently stopped.
    """
    from TESTS.adapters.go import message_mutations_image as image
    container = identity[0]['id']
    begin_cleanup()
    with defer_interruption():
        for command in (['docker', 'stop', '--time', '5', container],
                        ['docker', 'kill', '--signal', 'KILL', container]):
            try:
                proof.commands(command, timeout=10)
            except BaseException:
                pass  # A failed stop still needs independent stopped-state proof.
            try:
                rows = json.loads(proof.commands(['docker', 'inspect', container], timeout=5))
                need(type(rows) is list and len(rows) == 1, 'quiesce-inspect')
                info = rows[0]
                need(info['Id'] == container and info['Image'] == proof.image_id
                     and info['Config']['Labels'][image.packaged.LABEL] == proof.identifier
                     and info['State']['Running'] is False
                     and type(info['State']['Pid']) is int and info['State']['Pid'] == 0,
                     'quiesce-state')
                return
            except BaseException:
                pass
        proof.commands.cleanup_verified = False
        _abort_unquiesced(receipt=getattr(proof, 'restore_failure_receipt', None), container=container)
        raise AssertionError('mutation-image.restore-quiescence-unverified')


def run_controls(*, apps, database, raw, base_url, process_guard, proof, work,
                 private_values, binary):
    """Four required frozen-helper calls, within the existing Go server lifetime."""
    from TESTS.adapters.go import message_mutations_image as image
    # Missing/unfrozen probe is a dependency failure, never a skipped control.
    from TESTS.adapters.go.message_restore_lock_probe import make_exclusive_probe
    from TESTS.adapters.django import historical_read_cache as cache
    from TESTS.adapters.django import message_reads as shared
    expected_process = image.lifecycle._process(process_guard)
    identity_guard = make_identity_guard(proof, expected_process)
    identity = identity_guard(timeout=5)

    def guard():
        return identity_guard(timeout=5)[1]

    exclusive = make_exclusive_probe(commands=proof.commands, container=identity[0]['id'],
        expected_image=proof.image_id, identity_guard=identity_guard)
    portable = helper.PortableAssertions.from_modules(contract=image.contract, scopes=image.scopes,
        shared=shared, cache=cache, adapter=image.adapter)
    current = image.baseline(apps, raw)
    address, address_proof = image.measure_address('go-packaged', base_url, guard, proof=proof)
    result = dict(verified=False, backend='go', transport='packaged-actual-http',
        process_sha256=image.contract.source_digest(expected_process),
        identity_sha256=image.contract.source_digest(identity), go_binary_sha256=binary,
        client_ip=address_proof, baseline=current, results=[])
    for case in CASE_NAMES:
        cleanup, observations = {}, []

        def probe(*, timeout):
            value = exclusive(timeout=timeout)
            need(type(value) is bool, 'probe-result')
            observations.append(value)
            return value

        with image.adapter.fixture_context(apps, work / ('fixture-restore-' + case), raw,
                                          cleanup_receipt=cleanup) as fixture:
            try:
                private_values.update(fixture['tokens'].values())
                private_values.update(fixture['sessions'].values())
                actual = helper.run(case=case, apps=apps, database=database, raw=raw, fixture=fixture,
                    base_url=base_url, process_guard=guard, client_ip=address, portable=portable,
                    exclusive_probe=probe, bounds=helper.Bounds(io=5, probe=5))
                validate_result(case, actual)
                probes = probe_receipt(case, observations)
                need(proof.commands.cleanup_verified is True and guard() == expected_process,
                     'case-process')
            except BaseException:
                # This must execute before fixture_context.__exit__, including
                # helper teardown/response failure and KeyboardInterrupt.
                quiesce(proof, identity)
                raise
        need(image.cleanup_valid(cleanup, image.FIXTURE_CLEANUP)
             and image.baseline(apps, raw) == current, 'fixture-restoration')
        after_address, after_proof = image.measure_address('go-packaged', base_url, guard, proof=proof)
        need(after_address == address and after_proof == address_proof and guard() == expected_process,
             'case-provenance')
        public = deepcopy(actual)
        if 'blocked_backend' in public:
            public['blocked_backend_sha256'] = image.contract.source_digest(public.pop('blocked_backend'))
        result['results'].append(dict(case=case, result=public, fixture_cleanup=cleanup,
            restored=dict(state=True, cache=True, sequences=True), probes=probes))
    result['verified'] = True
    need(receipt_valid(result, dict(process_sha256=result['process_sha256'], go_binary_sha256=binary,
         baseline=current, client_ip=address_proof)), 'final-receipt')
    return result
