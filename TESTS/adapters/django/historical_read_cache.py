"""Exact Redis DB1 observation for historical read-route acceptance.

The parent historical adapter injects one ``redis.Redis`` client configured for
its owned DB1. This module uses only the client's command interface: it imports
neither Django nor application code and never starts Redis.

Every key is observed. In particular, readiness-prefixed keys are not filtered:
their unexpected presence fails the comparison. Two deterministic synthetic
contrast keys prevent an empty cache from satisfying the read-only control.
Values are bound with Redis ``TYPE`` plus the exact ``DUMP`` payload, and
expiration is bound with absolute ``PEXPIRETIME`` milliseconds rather than a
relative TTL approximation.

Each snapshot is collected by one bounded Lua ``EVAL``. Redis executes that
script atomically, so no concurrent write can interleave between key
enumeration and per-key observation. The helper never retries with a weaker
multi-command observation when the atomic capability is unavailable.
"""
from dataclasses import dataclass
import hashlib
from types import MappingProxyType
import time


READINESS_PREFIX = b'assozeta:go:readiness:'
HISTORICAL_CACHE_CONTRAST_PREFIX = b'assozeta:contracts:historical-read-cache:'
PERSISTENT_CONTRAST_KEY = HISTORICAL_CACHE_CONTRAST_PREFIX + b'persistent:v1'
EXPIRING_CONTRAST_KEY = HISTORICAL_CACHE_CONTRAST_PREFIX + b'expiring:v1'
PERSISTENT_CONTRAST_VALUE = b'historical-read-cache:persistent:v1'
EXPIRING_CONTRAST_VALUE = b'historical-read-cache:expiring:v1'
DEFAULT_CONTRAST_LIFETIME_MS = 6 * 60 * 60 * 1000
MAX_SNAPSHOT_KEYS = 4096
MAX_SNAPSHOT_PAYLOAD_BYTES = 16 * 1024 * 1024
_PEXPIRETIME_PROBE_KEY = (
    HISTORICAL_CACHE_CONTRAST_PREFIX + b'pexpiretime-capability-probe:v1'
)

_ATOMIC_SNAPSHOT_SCRIPT = r"""
local keys = redis.call('KEYS', '*')
local max_keys = tonumber(ARGV[1])
local max_bytes = tonumber(ARGV[2])
if #keys > max_keys then
    return redis.error_reply(
        'ERR historical cache snapshot exceeds key bound'
    )
end
local entries = {}
local total_bytes = 0
for _, key in ipairs(keys) do
    local type_reply = redis.call('TYPE', key)
    local redis_type = type_reply['ok']
    if redis_type == 'none' or redis_type == false then
        return redis.error_reply(
            'ERR historical cache snapshot observed a disappearing key'
        )
    end
    local serialized_value = redis.call('DUMP', key)
    if serialized_value == false then
        return redis.error_reply(
            'ERR historical cache snapshot observed a disappearing value'
        )
    end
    local expires_at_ms = redis.call('PEXPIRETIME', key)
    if expires_at_ms == -2 or expires_at_ms < -1 then
        return redis.error_reply(
            'ERR historical cache snapshot observed an invalid absolute expiration'
        )
    end
    total_bytes = total_bytes + string.len(key) +
        string.len(redis_type) + string.len(serialized_value)
    if total_bytes > max_bytes then
        return redis.error_reply(
            'ERR historical cache snapshot exceeds payload bound'
        )
    end
    entries[#entries + 1] = {
        key,
        {redis_type, serialized_value, expires_at_ms},
    }
end
return entries
"""


class HistoricalReadCacheError(AssertionError):
    """The complete DB1 cache could not be observed or preserved exactly."""


class HistoricalReadCacheObservationUnsupported(HistoricalReadCacheError):
    """Atomic exact snapshot support is unavailable on this Redis server."""


@dataclass(frozen=True, repr=False)
class HistoricalReadCacheEntry:
    redis_type: bytes
    serialized_value: bytes
    expires_at_ms: int

    def __repr__(self):
        value_digest = hashlib.sha256(self.serialized_value).hexdigest()[:16]
        return (
            'HistoricalReadCacheEntry('
            f'redis_type={self.redis_type!r}, '
            f'serialized_value_sha256_prefix={value_digest!r}, '
            f'expires_at_ms={self.expires_at_ms})'
        )


@dataclass(frozen=True, repr=False)
class HistoricalReadCacheSnapshot:
    entries: MappingProxyType

    @property
    def digest(self):
        return _snapshot_digest(self)

    def __repr__(self):
        return (
            'HistoricalReadCacheSnapshot('
            f'keys={len(self.entries)}, digest={self.digest[:16]!r})'
        )


@dataclass(frozen=True, repr=False)
class HistoricalReadCacheContrasts:
    persistent_key: bytes
    expiring_key: bytes
    expires_at_ms: int
    persistent_serialized_value: bytes
    expiring_serialized_value: bytes

    def __repr__(self):
        return (
            'HistoricalReadCacheContrasts('
            f'persistent_key={self.persistent_key!r}, '
            f'expiring_key={self.expiring_key!r}, '
            f'expires_at_ms={self.expires_at_ms})'
        )


def _fail(message):
    raise HistoricalReadCacheError(message)


def _require(condition, message):
    if not condition:
        _fail(message)


def _integer_command_result(command, result):
    _require(
        type(result) is int,
        f'Redis {command} returned a non-integer result',
    )
    return result


def _pexpiretime(raw, key):
    try:
        result = raw.execute_command('PEXPIRETIME', key)
    except Exception as error:
        raise HistoricalReadCacheObservationUnsupported(
            'Redis PEXPIRETIME is unavailable or failed; exact absolute '
            'cache expiration cannot be observed'
        ) from error
    result = _integer_command_result('PEXPIRETIME', result)
    _require(result >= -1, 'Redis PEXPIRETIME reported a missing key')
    return result


def _require_pexpiretime(raw):
    """Probe the command without writing or relying on a relative TTL."""
    try:
        result = raw.execute_command('PEXPIRETIME', _PEXPIRETIME_PROBE_KEY)
    except Exception as error:
        raise HistoricalReadCacheObservationUnsupported(
            'Redis PEXPIRETIME is unavailable or failed; exact absolute '
            'cache expiration cannot be observed'
        ) from error
    result = _integer_command_result('PEXPIRETIME', result)
    _require(
        result in (-2, -1) or result >= 0,
        'Redis PEXPIRETIME returned an invalid probe result',
    )


def _key_type(raw, key):
    try:
        result = raw.execute_command('TYPE', key)
    except Exception as error:
        raise HistoricalReadCacheError(
            'Redis TYPE failed while observing the cache'
        ) from error
    _require(
        isinstance(result, bytes),
        'Redis TYPE must return raw bytes for an exact typed snapshot',
    )
    _require(result != b'none', 'cache key disappeared while observing its type')
    return result


def _key_dump(raw, key):
    try:
        result = raw.execute_command('DUMP', key)
    except Exception as error:
        raise HistoricalReadCacheError(
            'Redis DUMP failed while observing an exact cache value'
        ) from error
    _require(
        isinstance(result, bytes),
        'Redis DUMP must return raw bytes for an exact typed snapshot',
    )
    return result


def _string_value(raw, key):
    try:
        result = raw.execute_command('GET', key)
    except Exception as error:
        raise HistoricalReadCacheError(
            'Redis GET failed while validating a string contrast key'
        ) from error
    _require(
        isinstance(result, bytes),
        'Redis GET must return raw bytes for a string contrast key',
    )
    return result


def _validate_snapshot_key(key):
    _require(
        isinstance(key, bytes),
        'Redis snapshot must return raw byte keys; disable response decoding',
    )


def _snapshot_digest(snapshot):
    digest = hashlib.sha256()
    for key in sorted(snapshot.entries):
        entry = snapshot.entries[key]
        for value in (
            key,
            entry.redis_type,
            entry.serialized_value,
            str(entry.expires_at_ms).encode('ascii'),
        ):
            digest.update(len(value).to_bytes(8, 'big'))
            digest.update(value)
    return digest.hexdigest()


def _key_label(key):
    return hashlib.sha256(key).hexdigest()[:16]


def _atomic_snapshot_failure(error):
    text = str(error).lower()
    if 'historical cache snapshot exceeds key bound' in text:
        _fail('Atomic historical cache snapshot exceeds key bound')
    if 'historical cache snapshot exceeds payload bound' in text:
        _fail('Atomic historical cache snapshot exceeds payload bound')
    capability_markers = (
        'unknown command',
        'unknown redis command',
        'not allowed from script',
        'not supported',
        'scripting disabled',
        'noperm',
        'pexpiretime',
    )
    if any(marker in text for marker in capability_markers):
        raise HistoricalReadCacheObservationUnsupported(
            'Atomic cache snapshot via EVAL with PEXPIRETIME is unavailable; '
            'no fallback observation is permitted'
        ) from error
    raise HistoricalReadCacheError(
        'Atomic historical cache snapshot failed; no fallback observation '
        'is permitted'
    ) from error


def _parse_atomic_snapshot_result(result):
    _require(
        type(result) is list,
        'Atomic historical cache snapshot returned a malformed binary result',
    )
    _require(
        len(result) <= MAX_SNAPSHOT_KEYS,
        'Atomic historical cache snapshot exceeds key bound',
    )
    entries = {}
    total_bytes = 0
    for item in result:
        _require(
            type(item) is list and len(item) == 2,
            'Atomic historical cache snapshot returned a malformed entry',
        )
        key, observed = item
        _require(
            type(observed) is list and len(observed) == 3,
            'Atomic historical cache snapshot returned a malformed observation',
        )
        redis_type, serialized_value, expires_at_ms = observed
        _require(
            type(key) is bytes,
            'Atomic historical cache snapshot returned a malformed binary key',
        )
        _require(
            key not in entries,
            'Atomic historical cache snapshot returned a duplicate key',
        )
        _require(
            type(redis_type) is bytes and redis_type != b'none',
            'Atomic historical cache snapshot returned a malformed Redis type',
        )
        _require(
            type(serialized_value) is bytes,
            'Atomic historical cache snapshot returned a malformed exact value',
        )
        _require(
            type(expires_at_ms) is int and expires_at_ms >= -1,
            'Atomic historical cache snapshot returned a malformed absolute expiration',
        )
        total_bytes += len(key) + len(redis_type) + len(serialized_value)
        _require(
            total_bytes <= MAX_SNAPSHOT_PAYLOAD_BYTES,
            'Atomic historical cache snapshot exceeds payload bound',
        )
        entries[key] = HistoricalReadCacheEntry(
            redis_type=redis_type,
            serialized_value=serialized_value,
            expires_at_ms=expires_at_ms,
        )
    return entries


def _validate_snapshot(snapshot):
    _require(
        isinstance(snapshot, HistoricalReadCacheSnapshot),
        'cache comparison requires historical-read cache snapshots',
    )
    for key, entry in snapshot.entries.items():
        _validate_snapshot_key(key)
        _require(
            isinstance(entry, HistoricalReadCacheEntry),
            'cache snapshot contains an invalid entry',
        )
        _require(
            isinstance(entry.redis_type, bytes) and entry.redis_type != b'none',
            'cache snapshot contains an invalid Redis type',
        )
        _require(
            isinstance(entry.serialized_value, bytes),
            'cache snapshot contains an invalid exact value',
        )
        _require(
            type(entry.expires_at_ms) is int and entry.expires_at_ms >= -1,
            'cache snapshot contains a relative or invalid absolute expiration',
        )


def seed_historical_read_cache_contrasts(
    raw,
    *,
    now_ms=None,
    lifetime_ms=DEFAULT_CONTRAST_LIFETIME_MS,
):
    """Create one persistent and one absolutely expiring private contrast.

    Both keys are synthetic and scoped to the parent's disposable DB1. Existing
    contrast keys are never overwritten. If second-key setup fails, only keys
    created by this call are removed.
    """
    _require_pexpiretime(raw)
    _require(
        type(lifetime_ms) is int and lifetime_ms >= 60_000,
        'contrast lifetime must be at least one minute',
    )
    if now_ms is None:
        now_ms = time.time_ns() // 1_000_000
    _require(
        type(now_ms) is int and now_ms >= 0,
        'contrast now_ms must be a non-negative integer',
    )

    keys = (PERSISTENT_CONTRAST_KEY, EXPIRING_CONTRAST_KEY)
    existing = [key for key in keys if raw.exists(key)]
    _require(
        not existing,
        'historical-read cache contrast key already exists; refusing to '
        'overwrite private state',
    )
    expires_at_ms = now_ms + lifetime_ms
    created = []
    try:
        if not raw.set(PERSISTENT_CONTRAST_KEY, PERSISTENT_CONTRAST_VALUE, nx=True):
            _fail('persistent historical-read cache contrast key was not created')
        created.append(PERSISTENT_CONTRAST_KEY)
        raw.persist(PERSISTENT_CONTRAST_KEY)
        _require(
            _pexpiretime(raw, PERSISTENT_CONTRAST_KEY) == -1,
            'persistent historical-read cache contrast has an absolute expiration',
        )
        _require(
            _string_value(raw, PERSISTENT_CONTRAST_KEY)
            == PERSISTENT_CONTRAST_VALUE,
            'persistent historical-read cache contrast value differs',
        )
        _require(
            _key_type(raw, PERSISTENT_CONTRAST_KEY) == b'string',
            'persistent historical-read cache contrast is not a Redis string',
        )
        persistent_serialized_value = _key_dump(raw, PERSISTENT_CONTRAST_KEY)

        if not raw.set(EXPIRING_CONTRAST_KEY, EXPIRING_CONTRAST_VALUE, nx=True):
            _fail('expiring historical-read cache contrast key was not created')
        created.append(EXPIRING_CONTRAST_KEY)
        _require(
            bool(raw.pexpireat(EXPIRING_CONTRAST_KEY, expires_at_ms)),
            'expiring historical-read cache contrast expiration was not set',
        )
        _require(
            _pexpiretime(raw, EXPIRING_CONTRAST_KEY) == expires_at_ms,
            'expiring historical-read cache contrast does not expose its '
            'exact absolute expiration',
        )
        _require(
            _string_value(raw, EXPIRING_CONTRAST_KEY) == EXPIRING_CONTRAST_VALUE,
            'expiring historical-read cache contrast value differs',
        )
        _require(
            _key_type(raw, EXPIRING_CONTRAST_KEY) == b'string',
            'expiring historical-read cache contrast is not a Redis string',
        )
        expiring_serialized_value = _key_dump(raw, EXPIRING_CONTRAST_KEY)
    except BaseException:
        if created:
            try:
                raw.delete(*created)
            except Exception:
                pass
        raise
    return HistoricalReadCacheContrasts(
        persistent_key=PERSISTENT_CONTRAST_KEY,
        expiring_key=EXPIRING_CONTRAST_KEY,
        expires_at_ms=expires_at_ms,
        persistent_serialized_value=persistent_serialized_value,
        expiring_serialized_value=expiring_serialized_value,
    )


def snapshot_historical_read_cache(raw):
    """Capture every DB1 key atomically with exact typed value and expiry."""
    try:
        result = raw.eval(
            _ATOMIC_SNAPSHOT_SCRIPT,
            0,
            MAX_SNAPSHOT_KEYS,
            MAX_SNAPSHOT_PAYLOAD_BYTES,
        )
    except Exception as error:
        _atomic_snapshot_failure(error)
    entries = _parse_atomic_snapshot_result(result)
    return HistoricalReadCacheSnapshot(MappingProxyType(entries))


def compare_historical_read_cache(before, after, contrasts):
    """Require exact typed values and absolute expirations across a read run."""
    _validate_snapshot(before)
    _validate_snapshot(after)
    _require(
        isinstance(contrasts, HistoricalReadCacheContrasts),
        'cache comparison requires historical-read cache contrasts',
    )
    _require(
        contrasts.persistent_key == PERSISTENT_CONTRAST_KEY
        and contrasts.expiring_key == EXPIRING_CONTRAST_KEY,
        'cache comparison received unknown contrast keys',
    )

    readiness_keys = {
        key for snapshot in (before, after)
        for key in snapshot.entries if key.startswith(READINESS_PREFIX)
    }
    _require(
        not readiness_keys,
        'Unexpected readiness-prefixed cache key(s): '
        f'{len(readiness_keys)}; no readiness-prefix exclusion is permitted',
    )

    for name, key in (
        ('persistent', contrasts.persistent_key),
        ('expiring', contrasts.expiring_key),
    ):
        _require(
            key in before.entries,
            f'{name} contrast cache key is missing from the baseline snapshot',
        )
        _require(
            key in after.entries,
            f'{name} contrast cache key was deleted',
        )
    _require(
        set(before.entries) == set(after.entries),
        'cache key set changed during read-only workloads',
    )

    for key in sorted(before.entries):
        previous = before.entries[key]
        current = after.entries[key]
        label = _key_label(key)
        _require(
            previous.redis_type == current.redis_type,
            f'cache Redis type changed for key sha256:{label}',
        )
        _require(
            previous.serialized_value == current.serialized_value,
            f'cache typed value changed for key sha256:{label}',
        )
        _require(
            previous.expires_at_ms == current.expires_at_ms,
            f'cache absolute expiration changed for key sha256:{label}',
        )

    persistent = after.entries[contrasts.persistent_key]
    expiring = after.entries[contrasts.expiring_key]
    _require(
        persistent.redis_type == b'string',
        'persistent contrast cache key no longer has Redis string type',
    )
    _require(
        expiring.redis_type == b'string',
        'expiring contrast cache key no longer has Redis string type',
    )
    _require(
        persistent.serialized_value == contrasts.persistent_serialized_value,
        'persistent contrast cache key exact typed value differs from its seed',
    )
    _require(
        expiring.serialized_value == contrasts.expiring_serialized_value,
        'expiring contrast cache key exact typed value differs from its seed',
    )
    _require(
        persistent.expires_at_ms == -1,
        'persistent contrast cache key acquired an absolute expiration',
    )
    _require(
        expiring.expires_at_ms != -1,
        'expiring contrast cache key lost its absolute expiration',
    )
    _require(
        expiring.expires_at_ms == contrasts.expires_at_ms,
        'expiring contrast cache key absolute expiration differs from its seed',
    )

    return {
        'keys_compared': len(after.entries),
        'contrast_keys_verified': 2,
        'persistent_contrast_expires_at_ms': persistent.expires_at_ms,
        'expiring_contrast_expires_at_ms': expiring.expires_at_ms,
        'typed_values_exact': True,
        'absolute_expirations_exact': True,
        'readiness_prefix_excluded': False,
        'unexpected_readiness_keys': 0,
        'cache_sha256': after.digest,
    }
