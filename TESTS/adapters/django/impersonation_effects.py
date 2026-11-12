"""Exact retained-state assertions for the private impersonation HTTP oracle.

New audit/index row contents and new session payloads are validated by the
caller. These checks prove that those additions cannot conceal changes to
previously existing rows or Redis bytes. Redis expiry is outside this scope.
"""


def _identity(value):
    return type(value), value


def _equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if {_identity(key) for key in left} != {_identity(key) for key in right}:
            return False
        return all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _identities(values, label):
    values = list(values)
    identities = {_identity(value) for value in values}
    if len(values) != len(identities):
        raise AssertionError(label + ': duplicate identities')
    return identities


def _rows(rows, key, label):
    indexed = {}
    for row in rows:
        identity = _identity(row[key])
        if identity in indexed:
            raise AssertionError(label + ': duplicate row identity')
        indexed[identity] = row
    return indexed


def assert_retained_rows(before_rows, after_rows, *, key, created=()):
    """Require every old full row unchanged and exactly the approved new IDs."""
    before = _rows(before_rows, key, 'before')
    after = _rows(after_rows, key, 'after')
    new = _identities(created, 'created rows')
    if new & before.keys():
        raise AssertionError('Created row already existed')
    if after.keys() != before.keys() | new:
        raise AssertionError('Unexpected retained/new row identity set')
    for identity, row in before.items():
        if not _equal(row, after[identity]):
            raise AssertionError('Preexisting row changed')


def assert_cache_transition(before_bytes, after_bytes, *, created=(), deleted=()):
    """Require exact key transitions and byte-identical retained Redis values."""
    before = {_identity(key): value for key, value in before_bytes.items()}
    after = {_identity(key): value for key, value in after_bytes.items()}
    new = _identities(created, 'created cache keys')
    removed = _identities(deleted, 'deleted cache keys')
    if new & before.keys() or new & removed:
        raise AssertionError('Created cache key was not new')
    if not removed <= before.keys():
        raise AssertionError('Deleted cache key did not exist')
    if after.keys() != (before.keys() - removed) | new:
        raise AssertionError('Unexpected cache key transition')
    if any(type(value) is not bytes for value in (*before.values(), *after.values())):
        raise AssertionError('Cache snapshots must contain raw bytes')
    for identity in before.keys() - removed:
        if before[identity] != after[identity]:
            raise AssertionError('Retained cache bytes changed')
