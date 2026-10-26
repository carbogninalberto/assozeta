"""Two warm-session sequences inside the caller's existing private runtime.

No infrastructure, fixture seed, backend import, or mutation occurs on import.
The parent provides its existing fixture and a live process guard as documented
in quality-reports/message-session-lifecycle-progress.md. Reports contain only
allowlisted codes, booleans, hashes, counts and bounded HTTP statuses.
"""
import copy
import time
from types import MappingProxyType

from TESTS.contracts import test_message_session_lifecycle as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import historical_read_cache as cache


class ProcessFailure(AssertionError):
    pass


def runtime_manifest(source, tokens, sessions):
    result = copy.deepcopy(source)
    result['source_sha256'] = contract.source_digest(source)
    for scenario in result['scenarios']:
        for phase in scenario['phases']:
            phase['token'] = tokens[phase.pop('actor')]
            phase['headers']['X-Impersonation-Id'] = sessions[scenario['session']]
    return contract.validate_runtime(result, source)


def _session_cache_key(identifier):
    # Use the application's real key function and active Django cache prefix.
    from application.impersonation import session_key
    from django.core.cache import cache as django_cache
    value = django_cache.make_key(session_key(identifier))
    return value.encode() if isinstance(value, str) else value


def _process(guard):
    try:
        contract.require(callable(guard), 'process.guard-required')
        value = guard()
        contract.require(type(value) is dict and set(value) == {'pid', 'instance', 'running'}, 'process.fields')
        contract.require(value['running'] is True and type(value['pid']) is int and value['pid'] > 0
                         and type(value['instance']) is int and value['instance'] > 0, 'process.live')
        return dict(value)
    except BaseException:
        raise ProcessFailure('process') from None


def _same_process(guard, original):
    if _process(guard) != original:
        raise ProcessFailure('process')


def _state(apps, baseline):
    try:
        current = scopes.snapshot_state(apps)
        scopes.shared.assert_state_unchanged(baseline, current)
        return current
    except BaseException:
        raise scopes.StateFailure('state') from None


def _cache(raw, expected, contrasts):
    try:
        current = cache.snapshot_historical_read_cache(raw)
        scopes.shared.assert_cache_unchanged(expected, current, contrasts)
        return current
    except BaseException:
        raise scopes.CacheFailure('cache') from None


def _boundary(apps, raw, state, expected, contrasts, guard, process):
    # Independent checks: a failed state check must not skip cache/process checks.
    values, errors = {}, []
    for name, operation in (
        ('state', lambda: _state(apps, state)),
        ('cache', lambda: _cache(raw, expected, contrasts)),
        ('process', lambda: _same_process(guard, process)),
    ):
        try:
            values[name] = operation()
        except BaseException as error:
            errors.append(error)
    if errors:
        raise errors[0]
    return values


def _removed(original, owned_key):
    contract.require(type(owned_key) is bytes and owned_key in original.entries, 'transition.owned-key')
    entries = dict(original.entries)
    entries.pop(owned_key)
    return cache.HistoricalReadCacheSnapshot(MappingProxyType(entries))


def _diagnostic(error):
    if isinstance(error, ProcessFailure):
        return {'check': 'process'}
    return scopes.failure_diagnostic(error)


def _restore(raw, original, key, apps, baseline):
    """Attempt restoration and every verification independently, including failures.

    Restore the owned session with its original DUMP and absolute expiry first.
    The shared whole-cache helper also repairs accidental extra-key mutations on
    failure; those mutations still fail acceptance and are never exclusions.
    Durable state is verified, never rewritten by this read-only adapter.
    """
    checks = {'session': False, 'cache': False, 'state': False}
    result = {'verified': False, 'checks': checks, 'models': 0,
              'state_sha256': None, 'cache_sha256': None}
    session_restored = False
    try:
        entry = original.entries[key]
        # The known key can be repaired even when whole-cache observation fails.
        # RESTORE success is only an attempt; the independent snapshot below must
        # still verify the exact session and every other key before acceptance.
        contract.require(entry.expires_at_ms > time.time_ns() // 1_000_000, 'restore.original-expired')
        raw.restore(key, entry.expires_at_ms, entry.serialized_value, replace=True, absttl=True)
        session_restored = True
    except BaseException:
        pass
    try:
        scopes.restore_cache(raw, original)
    except BaseException:
        pass
    try:
        current = cache.snapshot_historical_read_cache(raw)
        checks['session'] = session_restored and key in original.entries and current.entries.get(key) == original.entries[key]
        checks['cache'] = current.entries == original.entries
        result['cache_sha256'] = current.digest
    except BaseException:
        pass
    try:
        state = scopes.snapshot_state(apps)
        result.update(models=len(state), state_sha256=scopes.shared.digest(state))
        scopes.shared.assert_state_unchanged(baseline, state)
        checks['state'] = True
    except BaseException:
        pass
    result['verified'] = all(checks.values())
    return result


def _read(case, authored, *, label, backend, base_url, apps, raw, observer,
          fixture, expected_cache, guard, process):
    _boundary(apps, raw, fixture['baseline_state'], expected_cache, fixture['contrasts'], guard, process)
    try:
        # This observes the actual Django ASGI cursor delta, or requires an empty
        # delta for Go; it checks state/cache even on request/assertion failure.
        result = scopes.observed_case(case, label=label, backend=backend, base_url=base_url,
            observer=observer, snapshot_state=lambda: scopes.snapshot_state(apps),
            snapshot_cache=lambda: cache.snapshot_historical_read_cache(raw),
            contrasts=fixture['contrasts'], request=contract.assert_case)
    finally:
        _boundary(apps, raw, fixture['baseline_state'], expected_cache, fixture['contrasts'], guard, process)
    result.update(phase='warm' if case['status'] == 200 else 'denied',
        authored_sha256=contract.source_digest(authored), process_sha256=contract.source_digest(process))
    result['checks']['process'] = True
    return result


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    """Return bounded safe evidence, restoring baseline in finally on every exit.

    The entire workload stays within the caller's process/application lifetime;
    no reset, reseed, token change, endpoint change or process restart occurs
    between a warm read and its denial. A process guard is mandatory.
    """
    report = {'label': label if label in contract.LABELS else 'unknown',
        'backend': backend if backend in ('django', 'go') else 'unknown',
        'source_sha256': {}, 'manifest_sha256': None, 'success': False, 'failure': None,
        'baseline': None, 'process_sha256': None, 'scenarios': [], 'restoration': None}
    original, baseline, key = fixture.get('baseline_cache'), fixture.get('baseline_state'), None
    try:
        contract.require(__debug__, 'optimized-python')
        contract.require(contract.LABELS.get(label) == backend, 'runtime.label')
        source = contract.load_source()
        report['source_sha256'] = contract.source_hashes()
        report['manifest_sha256'] = contract.source_digest(source)
        contract.require(len(baseline) == 128, 'state.requires-128-models')
        report['baseline'] = {'models': len(baseline), 'state_sha256': scopes.shared.digest(baseline),
                              'cache_sha256': original.digest, 'cache_keys': len(original.entries)}
        scopes.contract.validate_runtime(fixture['runtime'])
        runtime = runtime_manifest(source, fixture['tokens'], fixture['sessions'])
        # Bind private credentials/headers to the already validated parent seed.
        reference = {case['name']: case for case in fixture['runtime']['cases']}
        for scenario, route in zip(runtime['scenarios'], ('list', 'detail'), strict=True):
            warm = scenario['phases'][0]
            existing = reference['message-scopes-admin-owner-' + route]
            contract.require(warm['token'] == existing['token'] and warm['headers'] == existing['headers'], 'runtime.seed-binding')
        key = _session_cache_key(fixture['sessions']['admin_owner'])
        contract.require(key in original.entries and original.entries[key].redis_type == b'string'
            and original.entries[key].expires_at_ms > time.time_ns() // 1_000_000, 'runtime.live-owned-session')
        guard = fixture.get('message_session_lifecycle_process')
        process = _process(guard)
        report['process_sha256'] = contract.source_digest(process)
        changed = _removed(original, key)
        for authored, scenario in zip(source['scenarios'], runtime['scenarios'], strict=True):
            result = {'scenario': scenario['name'], 'process_sha256': report['process_sha256'],
                      'phases': [], 'transition': None, 'restoration': None}
            report['scenarios'].append(result)
            try:
                result['phases'].append(_read(scenario['phases'][0], authored['phases'][0],
                    label=label, backend=backend, base_url=base_url, apps=apps, raw=raw,
                    observer=observer, fixture=fixture, expected_cache=original, guard=guard, process=process))
                # Change exactly the issued session without invoking management
                # endpoints/audits. Expiry is Redis PEXPIREAT, never DEL fallback.
                if scenario['transition'] == 'revoke':
                    count = raw.delete(key)
                    contract.require(type(count) is int and count == 1, 'transition.delete-count')
                else:
                    expired = raw.pexpireat(key, time.time_ns() // 1_000_000 - 1)
                    contract.require(expired is True or type(expired) is int and expired == 1, 'transition.expire-count')
                _boundary(apps, raw, baseline, changed, fixture['contrasts'], guard, process)
                result['transition'] = {'code': scenario['transition'], 'removed_keys': 1,
                    'unchanged_keys': len(original.entries) - 1, 'models': 128,
                    'state_sha256': report['baseline']['state_sha256'], 'cache_sha256': changed.digest,
                    'checks': {'state': True, 'cache': True, 'process': True}}
                result['phases'].append(_read(scenario['phases'][1], authored['phases'][1],
                    label=label, backend=backend, base_url=base_url, apps=apps, raw=raw,
                    observer=observer, fixture=fixture, expected_cache=changed, guard=guard, process=process))
            finally:
                result['restoration'] = _restore(raw, original, key, apps, baseline)
            contract.require(result['restoration']['verified'], 'runtime.restore')
        contract.require(contract.source_hashes() == report['source_sha256'], 'runtime.source-drift')
    except BaseException as error:
        report['failure'] = _diagnostic(error)
    finally:
        report['restoration'] = _restore(raw, original, key, apps, baseline)
    if report['failure'] is None and report['restoration']['verified']:
        report['success'] = True
        report['success'] = contract.acceptance(report)
    return report


def acceptance(report):
    return contract.acceptance(report)
