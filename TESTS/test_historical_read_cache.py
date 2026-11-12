"""Independent controls for exact historical-read Redis cache observation.

These tests use an in-memory command double. They do not launch Redis, import
Django, or establish runtime parity.
"""
from dataclasses import dataclass, replace
import unittest
from unittest import mock

from TESTS.adapters.django import historical_read_cache as cache


@dataclass
class FakeEntry:
    redis_type: bytes
    serialized_value: bytes
    expires_at_ms: int
    string_value: bytes = None


class UnsupportedCommand(Exception):
    pass


class ScriptBoundError(Exception):
    pass


_NO_EVAL_OVERRIDE = object()


class FakeRedis:
    """Small Redis command double with exact-key and absolute-expiry state."""

    def __init__(
        self,
        *,
        pexpiretime_supported=True,
        fail_pexpireat=False,
        eval_error=None,
        eval_result=_NO_EVAL_OVERRIDE,
    ):
        self.entries = {}
        self._pexpiretime_supported = pexpiretime_supported
        self._fail_pexpireat = fail_pexpireat
        self._eval_error = eval_error
        self._eval_result = eval_result
        self.commands = []

    @staticmethod
    def dump_for(value):
        assert isinstance(value, bytes)
        return b'FAKE-RDB-DUMP\x00' + len(value).to_bytes(4, 'big') + value

    def execute_command(self, command, *args):
        command = command.upper()
        self.commands.append((command, *args))
        if len(args) != 1:
            raise AssertionError('unexpected command arity')
        key = args[0]
        entry = self.entries.get(key)
        if command == 'TYPE':
            return b'none' if entry is None else entry.redis_type
        if command == 'GET':
            if entry is None:
                return None
            if entry.redis_type != b'string':
                raise AssertionError('GET requires a Redis string')
            if entry.string_value is not None:
                return entry.string_value
            return b'FAKE-GET-NOT-DUMP\x00' + entry.serialized_value
        if command == 'DUMP':
            return None if entry is None else entry.serialized_value
        if command == 'PEXPIRETIME':
            if not self._pexpiretime_supported:
                raise UnsupportedCommand('ERR unknown command PEXPIRETIME')
            return -2 if entry is None else entry.expires_at_ms
        raise AssertionError('unexpected command: ' + command)

    def scan_iter(self):
        self.commands.append(('SCAN',))
        return iter(tuple(self.entries))

    def exists(self, key):
        return int(key in self.entries)

    def set(self, key, value, *, nx=False):
        if nx and key in self.entries:
            return None
        self.entries[key] = FakeEntry(
            b'string', self.dump_for(value), -1, string_value=value,
        )
        return True

    def eval(self, script, numkeys, *args):
        self.commands.append(('EVAL', numkeys, *args))
        if not isinstance(script, str):
            raise AssertionError('atomic snapshot script must be text')
        if numkeys != 0 or len(args) != 2:
            raise AssertionError('unexpected atomic snapshot arguments')
        if self._eval_error is not None:
            raise self._eval_error
        if self._eval_result is not _NO_EVAL_OVERRIDE:
            return self._eval_result
        if not self._pexpiretime_supported:
            raise UnsupportedCommand(
                'ERR Unknown Redis command called from script: PEXPIRETIME',
            )

        max_keys, max_bytes = args
        if len(self.entries) > max_keys:
            raise ScriptBoundError(
                'ERR historical cache snapshot exceeds key bound',
            )
        result = []
        total_bytes = 0
        for key, entry in self.entries.items():
            total_bytes += len(key) + len(entry.redis_type)
            total_bytes += len(entry.serialized_value)
            if total_bytes > max_bytes:
                raise ScriptBoundError(
                    'ERR historical cache snapshot exceeds payload bound',
                )
            result.append([
                key,
                [
                    entry.redis_type,
                    entry.serialized_value,
                    entry.expires_at_ms,
                ],
            ])
        return result

    def persist(self, key):
        self.entries[key] = replace(self.entries[key], expires_at_ms=-1)
        return 1

    def pexpireat(self, key, expires_at_ms):
        if self._fail_pexpireat:
            return 0
        if key not in self.entries:
            return 0
        self.entries[key] = replace(
            self.entries[key], expires_at_ms=expires_at_ms,
        )
        return 1

    def delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.entries:
                del self.entries[key]
                removed += 1
        return removed


class HistoricalReadCacheControls(unittest.TestCase):
    def test_seed_creates_documented_persistent_and_expiring_contrasts(self):
        raw = FakeRedis()
        contrasts = cache.seed_historical_read_cache_contrasts(
            raw,
            now_ms=1_800_000_000_000,
            lifetime_ms=3_600_000,
        )
        self.assertEqual(
            contrasts.persistent_key,
            b'assozeta:contracts:historical-read-cache:persistent:v1',
        )
        self.assertEqual(
            contrasts.expiring_key,
            b'assozeta:contracts:historical-read-cache:expiring:v1',
        )
        self.assertEqual(contrasts.expires_at_ms, 1_800_003_600_000)
        snapshot = cache.snapshot_historical_read_cache(raw)
        self.assertEqual(
            snapshot.entries[contrasts.persistent_key].expires_at_ms, -1,
        )
        self.assertEqual(
            snapshot.entries[contrasts.expiring_key].expires_at_ms,
            contrasts.expires_at_ms,
        )
        self.assertEqual(
            snapshot.entries[contrasts.persistent_key].serialized_value,
            raw.dump_for(cache.PERSISTENT_CONTRAST_VALUE),
        )
        self.assertEqual(
            snapshot.entries[contrasts.expiring_key].serialized_value,
            raw.dump_for(cache.EXPIRING_CONTRAST_VALUE),
        )

    def test_get_and_dump_are_distinct_and_snapshot_records_exact_dump(self):
        raw = FakeRedis()
        value = b'value-that-differs-from-its-dump'
        raw.entries[b'cache-key'] = FakeEntry(
            b'string',
            FakeRedis.dump_for(value),
            -1,
            string_value=value,
        )
        self.assertNotEqual(
            raw.execute_command('GET', b'cache-key'),
            raw.execute_command('DUMP', b'cache-key'),
        )

        snapshot = cache.snapshot_historical_read_cache(raw)

        self.assertEqual(
            snapshot.entries[b'cache-key'].serialized_value,
            FakeRedis.dump_for(value),
        )
        self.assertNotEqual(
            snapshot.entries[b'cache-key'].serialized_value,
            value,
        )

    def test_snapshot_uses_one_atomic_server_execution_without_scan_or_key_commands(self):
        raw = FakeRedis()
        raw.entries[b'cache:hash'] = FakeEntry(
            b'hash', b'exact-hash-dump', 1_800_000_123_456,
        )
        raw.entries[b'cache:list'] = FakeEntry(
            b'list', b'exact-list-dump', -1,
        )

        snapshot = cache.snapshot_historical_read_cache(raw)

        self.assertEqual(set(snapshot.entries), {b'cache:hash', b'cache:list'})
        self.assertEqual([command[0] for command in raw.commands], ['EVAL'])
        self.assertFalse(any(
            command[0] in ('SCAN', 'TYPE', 'DUMP', 'PEXPIRETIME')
            for command in raw.commands
        ))

    def test_atomic_snapshot_enforces_key_and_payload_bounds(self):
        raw = FakeRedis()
        for index in range(3):
            raw.entries[f'key:{index}'.encode()] = FakeEntry(
                b'string', b'dump', -1, string_value=b'value',
            )
        with mock.patch.object(cache, 'MAX_SNAPSHOT_KEYS', 2):
            with self.assertRaisesRegex(
                cache.HistoricalReadCacheError,
                'snapshot.*key bound',
            ):
                cache.snapshot_historical_read_cache(raw)
        with mock.patch.object(cache, 'MAX_SNAPSHOT_PAYLOAD_BYTES', 2):
            with self.assertRaisesRegex(
                cache.HistoricalReadCacheError,
                'snapshot.*payload bound',
            ):
                cache.snapshot_historical_read_cache(raw)

    def test_atomic_snapshot_capability_failure_is_explicit_and_has_no_fallback(self):
        raw = FakeRedis(pexpiretime_supported=False)
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheObservationUnsupported,
            'Atomic.*PEXPIRETIME.*unavailable',
        ):
            cache.snapshot_historical_read_cache(raw)
        self.assertEqual([command[0] for command in raw.commands], ['EVAL'])

    def test_missing_eval_capability_is_an_explicit_atomic_failure(self):
        raw = FakeRedis(
            eval_error=UnsupportedCommand('ERR unknown command EVAL'),
        )
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheObservationUnsupported,
            'Atomic.*unavailable',
        ):
            cache.snapshot_historical_read_cache(raw)
        self.assertEqual([command[0] for command in raw.commands], ['EVAL'])

    def test_atomic_script_error_is_not_retried_with_a_weaker_observation(self):
        raw = FakeRedis(
            eval_error=ScriptBoundError('ERR atomic snapshot execution failed'),
        )
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheError,
            'Atomic.*no fallback',
        ):
            cache.snapshot_historical_read_cache(raw)
        self.assertEqual([command[0] for command in raw.commands], ['EVAL'])

    def test_malformed_atomic_results_are_rejected_without_disclosure(self):
        malformed_results = (
            b'not-a-list',
            [b'not-an-entry'],
            [[b'key', [b'string', b'secret-dump']]],
            [[b'key', [b'none', b'secret-dump', -1]]],
            [[b'key', [b'string', 'secret-text', -1]]],
            [[b'key', [b'string', b'secret-dump', True]]],
            [[b'key', [b'string', b'secret-dump', -2]]],
            [
                [b'key', [b'string', b'secret-dump', -1]],
                [b'key', [b'string', b'secret-dump', -1]],
            ],
        )
        for result in malformed_results:
            with self.subTest(result_type=type(result).__name__):
                raw = FakeRedis(eval_result=result)
                with self.assertRaisesRegex(
                    cache.HistoricalReadCacheError,
                    'malformed|duplicate',
                ) as raised:
                    cache.snapshot_historical_read_cache(raw)
                self.assertNotIn(b'secret', str(raised.exception).encode())

    def test_snapshot_records_every_key_type_exact_value_and_absolute_expiry(self):
        raw = FakeRedis()
        raw.entries[b'session:one'] = FakeEntry(b'string', b'exact-session-dump', -1)
        raw.entries[b'cache:one'] = FakeEntry(b'hash', b'exact-hash-dump', 1_800_000_123_456)
        snapshot = cache.snapshot_historical_read_cache(raw)
        self.assertEqual(set(snapshot.entries), {b'session:one', b'cache:one'})
        self.assertEqual(snapshot.entries[b'session:one'].redis_type, b'string')
        self.assertEqual(snapshot.entries[b'session:one'].serialized_value, b'exact-session-dump')
        self.assertEqual(snapshot.entries[b'session:one'].expires_at_ms, -1)
        self.assertEqual(snapshot.entries[b'cache:one'].redis_type, b'hash')
        self.assertEqual(snapshot.entries[b'cache:one'].serialized_value, b'exact-hash-dump')
        self.assertEqual(snapshot.entries[b'cache:one'].expires_at_ms, 1_800_000_123_456)

    def test_unchanged_nonvacuous_contrast_snapshot_passes(self):
        raw = FakeRedis()
        contrasts = cache.seed_historical_read_cache_contrasts(
            raw, now_ms=1_800_000_000_000,
        )
        before = cache.snapshot_historical_read_cache(raw)
        after = cache.snapshot_historical_read_cache(raw)
        evidence = cache.compare_historical_read_cache(before, after, contrasts)
        self.assertEqual(evidence['keys_compared'], 2)
        self.assertEqual(evidence['contrast_keys_verified'], 2)
        self.assertTrue(evidence['typed_values_exact'])
        self.assertTrue(evidence['absolute_expirations_exact'])
        self.assertFalse(evidence['readiness_prefix_excluded'])
        self.assertEqual(evidence['unexpected_readiness_keys'], 0)

    def test_private_cache_values_are_redacted_from_repr_and_evidence(self):
        raw = FakeRedis()
        private_value = b'private-synthetic-cache-value'
        raw.entries[b'private-cache-key'] = FakeEntry(
            b'string', private_value, -1,
        )
        contrasts = cache.seed_historical_read_cache_contrasts(
            raw, now_ms=1_800_000_000_000,
        )
        before = cache.snapshot_historical_read_cache(raw)
        after = cache.snapshot_historical_read_cache(raw)
        evidence = cache.compare_historical_read_cache(before, after, contrasts)
        self.assertNotIn(private_value, repr(before).encode())
        self.assertNotIn(private_value, repr(before.entries[b'private-cache-key']).encode())
        self.assertNotIn(private_value, str(evidence).encode())

    def test_unexpected_readiness_key_is_rejected_without_prefix_exclusion(self):
        raw = FakeRedis()
        contrasts = cache.seed_historical_read_cache_contrasts(
            raw, now_ms=1_800_000_000_000,
        )
        before = cache.snapshot_historical_read_cache(raw)
        raw.entries[b'assozeta:go:readiness:unexpected'] = FakeEntry(
            b'string', b'must-not-pass', 1_800_000_010_000,
        )
        after = cache.snapshot_historical_read_cache(raw)
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheError,
            'Unexpected readiness-prefixed cache key',
        ):
            cache.compare_historical_read_cache(before, after, contrasts)

    def test_readiness_key_present_in_both_snapshots_is_still_rejected(self):
        raw = FakeRedis()
        contrasts = cache.seed_historical_read_cache_contrasts(
            raw, now_ms=1_800_000_000_000,
        )
        raw.entries[b'assozeta:go:readiness:persistent-leak'] = FakeEntry(
            b'string', b'leaked', -1,
        )
        before = cache.snapshot_historical_read_cache(raw)
        after = cache.snapshot_historical_read_cache(raw)
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheError,
            'Unexpected readiness-prefixed cache key',
        ):
            cache.compare_historical_read_cache(before, after, contrasts)

    def test_expiry_only_change_is_rejected(self):
        raw = FakeRedis()
        contrasts = cache.seed_historical_read_cache_contrasts(
            raw, now_ms=1_800_000_000_000,
        )
        before = cache.snapshot_historical_read_cache(raw)
        raw.entries[contrasts.expiring_key] = replace(
            raw.entries[contrasts.expiring_key],
            expires_at_ms=contrasts.expires_at_ms + 1,
        )
        after = cache.snapshot_historical_read_cache(raw)
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheError,
            'absolute expiration changed',
        ):
            cache.compare_historical_read_cache(before, after, contrasts)

    def test_value_change_with_same_redis_type_is_rejected(self):
        raw = FakeRedis()
        contrasts = cache.seed_historical_read_cache_contrasts(
            raw, now_ms=1_800_000_000_000,
        )
        before = cache.snapshot_historical_read_cache(raw)
        raw.entries[contrasts.persistent_key] = replace(
            raw.entries[contrasts.persistent_key],
            serialized_value=b'different-exact-dump',
        )
        after = cache.snapshot_historical_read_cache(raw)
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheError,
            'typed value changed',
        ):
            cache.compare_historical_read_cache(before, after, contrasts)

    def test_contrast_changed_before_baseline_cannot_pass_as_a_new_zero_point(self):
        raw = FakeRedis()
        contrasts = cache.seed_historical_read_cache_contrasts(
            raw, now_ms=1_800_000_000_000,
        )
        raw.entries[contrasts.persistent_key] = replace(
            raw.entries[contrasts.persistent_key],
            serialized_value=b'replaced-before-baseline',
        )
        before = cache.snapshot_historical_read_cache(raw)
        after = cache.snapshot_historical_read_cache(raw)
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheError,
            'contrast cache key exact typed value differs from its seed',
        ):
            cache.compare_historical_read_cache(before, after, contrasts)

    def test_redis_type_change_is_rejected(self):
        raw = FakeRedis()
        contrasts = cache.seed_historical_read_cache_contrasts(
            raw, now_ms=1_800_000_000_000,
        )
        before = cache.snapshot_historical_read_cache(raw)
        raw.entries[contrasts.persistent_key] = replace(
            raw.entries[contrasts.persistent_key],
            redis_type=b'hash',
        )
        after = cache.snapshot_historical_read_cache(raw)
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheError,
            'Redis type changed',
        ):
            cache.compare_historical_read_cache(before, after, contrasts)

    def test_deleted_expiring_contrast_is_rejected_specifically(self):
        raw = FakeRedis()
        contrasts = cache.seed_historical_read_cache_contrasts(
            raw, now_ms=1_800_000_000_000,
        )
        before = cache.snapshot_historical_read_cache(raw)
        del raw.entries[contrasts.expiring_key]
        after = cache.snapshot_historical_read_cache(raw)
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheError,
            'contrast cache key was deleted',
        ):
            cache.compare_historical_read_cache(before, after, contrasts)

    def test_deleted_noncontrast_key_is_rejected(self):
        raw = FakeRedis()
        contrasts = cache.seed_historical_read_cache_contrasts(
            raw, now_ms=1_800_000_000_000,
        )
        raw.entries[b'application-cache-key'] = FakeEntry(
            b'string', b'exact-value', -1,
        )
        before = cache.snapshot_historical_read_cache(raw)
        del raw.entries[b'application-cache-key']
        after = cache.snapshot_historical_read_cache(raw)
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheError,
            'cache key set changed',
        ):
            cache.compare_historical_read_cache(before, after, contrasts)

    def test_missing_pexpiretime_fails_explicitly_instead_of_using_ttl(self):
        raw = FakeRedis(pexpiretime_supported=False)
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheObservationUnsupported,
            'PEXPIRETIME.*unavailable',
        ):
            cache.seed_historical_read_cache_contrasts(
                raw, now_ms=1_800_000_000_000,
            )
        self.assertFalse(raw.entries)
        self.assertFalse(any(command[0] in ('PTTL', 'TTL') for command in raw.commands))

    def test_seed_refuses_to_overwrite_private_keys(self):
        raw = FakeRedis()
        raw.entries[cache.PERSISTENT_CONTRAST_KEY] = FakeEntry(
            b'string', b'existing-private-state', -1,
        )
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheError,
            'contrast key already exists',
        ):
            cache.seed_historical_read_cache_contrasts(
                raw, now_ms=1_800_000_000_000,
            )
        self.assertEqual(
            raw.entries[cache.PERSISTENT_CONTRAST_KEY].serialized_value,
            b'existing-private-state',
        )
        self.assertNotIn(cache.EXPIRING_CONTRAST_KEY, raw.entries)

    def test_seed_removes_only_created_keys_after_setup_failure(self):
        raw = FakeRedis(fail_pexpireat=True)
        raw.entries[b'unrelated-private-key'] = FakeEntry(
            b'string', b'must-remain', -1,
        )
        with self.assertRaisesRegex(
            cache.HistoricalReadCacheError,
            'expiration was not set',
        ):
            cache.seed_historical_read_cache_contrasts(
                raw, now_ms=1_800_000_000_000,
            )
        self.assertEqual(set(raw.entries), {b'unrelated-private-key'})


if __name__ == '__main__':
    unittest.main()
