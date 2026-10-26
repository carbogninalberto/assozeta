"""Inert, explicit actual-HTTP same-message DELETE concurrency characterization.

No service/fixture/environment discovery, connection, or HTTP at import time.
All private observations stay in the required parent-owned memory dictionary.
Only run() performs I/O. Offline doubles never establish actual overlap evidence.
"""
import copy
from dataclasses import dataclass
import http.client
import io
import ipaddress
import json
import math
import re
import socket
import threading
import time
from urllib.parse import urlsplit
from types import MappingProxyType

from TESTS.adapters.go.message_mutation_lifecycle import (
    PortableAssertions, _ResponseSocket, _response_body, _verify_effect,
)
from TESTS.adapters.django import message_connection_attribution as attribution

ATTRIBUTION_PENDING = 'connection-observation-finalization-pending'

GO_OWNER_QUERY = 'SELECT role,is_active,deleted FROM bakney_user WHERE user_id=$1 FOR UPDATE'
GO_MESSAGE_QUERY = ('SELECT message_id,sport_association_id,type,message,subject,created_at '
                    'FROM communications_message WHERE message_id=$1 AND sport_association_id=$2 FOR UPDATE')
# Parent-observed corrected Django SQL; only these two canonical literals vary.
DJANGO_MESSAGE_QUERY = (
    'SELECT "communications_message"."message_id", "communications_message"."sport_association_id", '
    '"communications_message"."type", "communications_message"."message", '
    '"communications_message"."subject", "communications_message"."created_at" '
    'FROM "communications_message" WHERE ("communications_message"."message_id" = '
    "'{target}'::uuid AND \"communications_message\".\"sport_association_id\" = "
    "'{association}'::uuid) ORDER BY \"communications_message\".\"message_id\" ASC LIMIT 1 FOR UPDATE")
_WAIT_FAILURE_CODES = (
    'inactive-wait', 'missing-backend-pid', 'missing-transaction-start', 'missing-backend-start',
    'missing-virtual-identity', 'ambiguous-virtual-identity', 'conflicting-virtual-identity',
    'conflicting-permanent-identity', 'ambiguous-transactions', 'lost-wait',
)


def _correlations(backend):
    return tuple(f'message-mutations-concurrency-{backend}-delete-{i}' for i in (1, 2))


def _require(condition, code):
    if not condition:
        raise AssertionError('message-concurrency.' + code)


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'ambiguous-json-key')
        result[key] = value
    return result


def _xid(value):
    return (type(value) is str and re.fullmatch(r'[1-9][0-9]{0,9}', value) is not None
            and int(value) < 2 ** 32)


def _vxid(value):
    return (type(value) is str and
            re.fullmatch(r'[1-9][0-9]{0,9}/[1-9][0-9]{0,9}', value) is not None and
            all(int(part) < 2 ** 32 for part in value.split('/')))


def _decode_locks(value):
    """Django's psycopg2 JSONB loader returns text; validate exact catalog shape.

    A driver-decoded list is supported too. Decoding cannot fabricate missing
    fields, coerce booleans/IDs, discard unknown fields or supply lock witnesses.
    """
    if type(value) is str:
        _require(len(value) <= 65536, 'lock-json-size')
        value = json.loads(value, object_pairs_hook=_unique_json_object)
    _require(type(value) is list and len(value) <= 64, 'lock-json-array')
    keys = {'kind', 'granted', 'mode', 'xid', 'relation', 'page', 'item',
            'virtualtransaction', 'virtualxid'}
    for lock in value:
        _require(type(lock) is dict and set(lock) == keys and type(lock['granted']) is bool,
                 'lock-json-fields')
        _require(_vxid(lock['virtualtransaction']), 'lock-json-virtualtransaction')
        _require(lock['kind'] == 'virtualxid' or lock['virtualxid'] is None, 'lock-json-virtualxid')
        if lock['kind'] == 'tuple':
            _require(lock['xid'] is None and lock['mode'] in
                     ('AccessShareLock', 'RowShareLock', 'ExclusiveLock', 'AccessExclusiveLock') and
                     type(lock['relation']) is int and 0 < lock['relation'] < 2 ** 32 and
                     type(lock['page']) is int and 0 <= lock['page'] < 2 ** 32 and
                     type(lock['item']) is int and 0 < lock['item'] < 2 ** 16, 'lock-json-tuple')
        elif lock['kind'] == 'transactionid':
            _require(lock['kind'] == 'transactionid' and lock['mode'] in ('ShareLock', 'ExclusiveLock') and
                     _xid(lock['xid']) and
                     all(lock[key] is None for key in ('relation', 'page', 'item')), 'lock-json-xid')
        else:
            _require(lock['kind'] == 'virtualxid' and lock['mode'] in ('ShareLock', 'ExclusiveLock') and
                     _vxid(lock['virtualxid']) and
                     all(lock[key] is None for key in ('xid', 'relation', 'page', 'item')),
                     'lock-json-virtualxid')
    return value


class ConcurrencyFailure(RuntimeError):
    """Fixed public failure; diagnostics contain only whitelisted safe fields."""
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__('message-concurrency-failed')


@dataclass(frozen=True)
class Bounds:
    connect: float = 2
    read: float = 15
    query: float = 1
    overlap: float = 5
    overall: float = 30
    cleanup: float = 10
    poll: float = .02

    def validate(self):
        _require(all(type(v) in (int, float) and math.isfinite(v) and 0 < v <= 60
                     for v in vars(self).values()), 'finite-bounds')
        _require(self.poll <= .1 and self.query <= 5 and 1 <= self.connect <= 5 and
                 int(self.connect) == self.connect and
                 self.overlap + 2 * self.query < self.read < self.overall and
                 self.connect + 2 * self.query < self.cleanup, 'separate-deadlines')
        return self


@dataclass(frozen=True)
class Blocker:
    pid: int
    xid: str
    backend: str
    rows: dict
    target: str
    deliveries: tuple
    xact: str
    vxid: str
    association: str


@dataclass(frozen=True)
class WaitProof:
    """Catalog transaction identity only; no HTTP request binding is implied."""
    pid: int
    xid: str | None
    xact: str
    backend: str
    vxid: str


def _identity(row):
    """Bind actual self-held virtual/permanent locks to this backend lifetime."""
    _require(type(row['pid']) is int and row['pid'] > 0, 'missing-backend-pid')
    _require(type(row['xact']) is str and bool(row['xact']), 'missing-transaction-start')
    _require(type(row['backend']) is str and bool(row['backend']), 'missing-backend-start')
    virtual = [lock for lock in row['locks'] if lock['kind'] == 'virtualxid']
    _require(bool(virtual), 'missing-virtual-identity')
    _require(len(virtual) == 1, 'ambiguous-virtual-identity')
    own = virtual[0]
    vxid = own['virtualxid']
    _require(_vxid(vxid) and own['granted'] is True and own['mode'] == 'ExclusiveLock' and
             all(lock.get('virtualtransaction') == vxid for lock in row['locks']),
             'conflicting-virtual-identity')
    xid = row['xid']
    permanent = [lock for lock in row['locks'] if lock['kind'] == 'transactionid' and lock['granted']]
    _require((xid is None and not permanent) or
             (_xid(xid) and len(permanent) == 1 and permanent[0]['xid'] == xid and
              permanent[0]['mode'] == 'ExclusiveLock'), 'conflicting-permanent-identity')
    return WaitProof(row['pid'], xid, row['xact'], row['backend'], vxid)


def _sql_rows(query, blocker, backend):
    if backend == 'go':
        _require(query in (GO_MESSAGE_QUERY, GO_OWNER_QUERY), 'unknown-lock-sql')
        return blocker.rows['message' if query == GO_MESSAGE_QUERY else 'owner']
    # Byte-exact measured SELECT, with scope captured from the held canonical
    # row in hold(). No whitespace/comment/UUID normalization or generic WHERE.
    uuid = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
    if (all(type(value) is str and re.fullmatch(uuid, value) is not None
            for value in (blocker.target, blocker.association)) and
            query == DJANGO_MESSAGE_QUERY.format(target=blocker.target, association=blocker.association)):
        return blocker.rows['message']
    # Exact Django collector DELETE grammar, not an arbitrary substring match.
    # SQL values remain private. Accept only the complete canonical ID set.
    match = re.fullmatch(r'DELETE FROM "(communications_message(?:transaction)?)" WHERE '
                         r'"\1"\."(message_id|message_transaction_id)" IN \((.+)\)', query)
    _require(match is not None, 'unknown-lock-sql')
    table, column, literals = match.groups()
    delivery = table == 'communications_messagetransaction'
    _require(column in ('message_transaction_id', 'message_id') if delivery else column == 'message_id',
             'lock-sql-column')
    pieces = literals.split(', ')
    identifiers = []
    for piece in pieces:
        identifier = re.fullmatch(r"'([a-zA-Z0-9-]+)'(?:::uuid)?", piece)
        _require(identifier is not None, 'lock-sql-literal')
        identifiers.append(identifier[1].lower())
    # Collector fast_deletes filters the reverse FK (message_id); the regular
    # collector path batches delivery PKs. Both must bind exact canonical IDs
    # while their tuple witness remains a DELIVERY tuple, never the parent row.
    expected = set(blocker.deliveries) if delivery and column == 'message_transaction_id' else {blocker.target}
    _require(len(identifiers) == len(expected) and set(identifiers) == expected, 'foreign-lock-sql')
    return blocker.rows['delivery' if delivery else 'message']


def _tuples(row, *, granted):
    return {(lock['relation'], lock['page'], lock['item']) for lock in row['locks']
            if lock['kind'] == 'tuple' and lock['granted'] is granted and
            lock['mode'] in ('AccessExclusiveLock', 'ExclusiveLock')}


def _overlap(rows, blocker, backend, *, previous=()):
    """Validate every reachable wait, including partial observations before pair.

    pg_blocking_pids alone is insufficient: each edge also needs an exact tuple
    witness and either the parent's XID wait or an actual tuple queue dependency.
    Unknown/ambiguous graphs fail closed. Once seen, no transaction may vanish.
    """
    _require(backend in ('go', 'django'), 'backend')
    by_pid = {row['pid']: row for row in rows}
    _require(len(by_pid) == len(rows), 'ambiguous-backend')
    reachable = {blocker.pid}
    while True:
        expanded = reachable | {row['pid'] for row in rows if set(row['blockers']) & reachable}
        if expanded == reachable:
            break
        reachable = expanded
    selected = [row for row in rows if row['pid'] in reachable - {blocker.pid}]
    _require(len(selected) <= 2, 'ambiguous-waiters')
    proofs = []
    for row in selected:
        _require(row['state'] == 'active' and row['wait'] == 'Lock', 'inactive-wait')
        proof = _identity(row)
        parents = row['blockers']
        _require(len(parents) == 1 and parents[0] in reachable and parents[0] != row['pid'],
                 'ambiguous-lock-chain')
        allowed = _sql_rows(row['query'], blocker, backend)
        held, waiting = _tuples(row, granted=True), _tuples(row, granted=False)
        _require(bool((held | waiting) & allowed) and (held | waiting) <= allowed, 'foreign-tuple')
        parent = parents[0]
        parent_xid = blocker.xid if parent == blocker.pid else by_pid[parent]['xid']
        waits = [lock for lock in row['locks'] if lock['granted'] is False]
        _require(len(waits) == 1 and (waits[0]['kind'] == 'transactionid' or
                 waits[0]['kind'] == 'tuple'), 'ambiguous-wait-lock')
        xid_edge = any(lock['kind'] == 'transactionid' and lock['granted'] is False and
                       lock['mode'] == 'ShareLock' and parent_xid is not None and lock['xid'] == parent_xid
                       for lock in row['locks'])
        if parent == blocker.pid:
            # Our blocker never locks the owner row. Its row lock must be one
            # of the exact selected message/deliveries, not a relation lock.
            _require(xid_edge and bool(held & allowed &
                (blocker.rows['message'] | blocker.rows['delivery'])), 'wrong-root-lock')
        else:
            tuple_edge = bool(waiting & _tuples(by_pid[parent], granted=True) & allowed)
            _require((xid_edge and bool(held & allowed)) or tuple_edge, 'unvalidated-lock-edge')
        # Walk to this root explicitly, rejecting cycles even with a root edge.
        chain, cursor = set(), row['pid']
        while cursor != blocker.pid:
            _require(cursor not in chain and cursor in by_pid and
                     len(by_pid[cursor]['blockers']) == 1, 'cyclic-lock-chain')
            chain.add(cursor)
            cursor = by_pid[cursor]['blockers'][0]
        proofs.append(proof)
    result = tuple(sorted(proofs, key=lambda proof: proof.pid))
    identities = (*result, blocker)
    permanent = [proof.xid for proof in identities if proof.xid is not None]
    _require(len(set(permanent)) == len(permanent) and
             len({proof.vxid for proof in identities}) == len(identities) and
             len({proof.backend for proof in identities}) == len(identities), 'ambiguous-transactions')
    current = {proof.pid: proof for proof in result}
    for old in previous:
        new = current.get(old.pid)
        _require(new is not None and (new.vxid, new.xact, new.backend) ==
                 (old.vxid, old.xact, old.backend) and (old.xid is None or new.xid == old.xid), 'lost-wait')
    return result


def _responses(responses):
    _require(len(responses) == 2 and all(row.get('complete') is True for row in responses),
             'incomplete-responses')
    _require(sorted(row['status'] for row in responses) == [200, 404], 'linearizable-statuses')
    for row in responses:
        expected = {'msg': 'message deleted.' if row['status'] == 200 else 'message not found.'}
        _require(json.loads(row['body'], object_pairs_hook=_unique_json_object) == expected, 'response-body')
        headers = {}
        for name, value in row['response_headers']:
            headers.setdefault(name.lower(), []).append(value)
        _require(headers.get('content-type', [''])[0].split(';')[0] == 'application/json' and
                 len(headers.get('content-type', [])) == 1 and
                 headers.get('content-language') == ['en'] and
                 len(headers.get('allow', [])) == 1 and
                 set(headers['allow'][0].replace(' ', '').split(',')) == {'DELETE', 'OPTIONS'} and
                 not headers.get('set-cookie') and not headers.get('www-authenticate'), 'response-headers')
    return next(i for i, row in enumerate(responses) if row['status'] == 200)


def _validate_wire(record):
    """Reparse captured original bytes, rejecting a hidden buffered frame tail."""
    class Buffer(io.BytesIO):
        def close(self):
            pass  # HTTPResponse closes its reader on fixed-length completion.
    buffer = Buffer(record['wire'])
    class MemorySocket:
        def makefile(self, mode):
            return buffer
    with http.client.HTTPResponse(MemorySocket()) as response:
        response.begin()
        body = _response_body(response)
        _require(buffer.tell() == len(record['wire']) and body == record['body'] and
                 response.status == record['status'] and response.version == record['http_version'] and
                 response.reason == record['reason'] and response.getheaders() == record['response_headers'],
                 'original-response-framing')


def _root(rows, blocker):
    roots = [row for row in rows if row['pid'] == blocker.pid]
    _require(len(roots) == 1, 'missing-or-ambiguous-blocker')
    root = roots[0]
    _require(root['state'] == 'idle in transaction' and not root['blockers'], 'wrong-blocker')
    proof = _identity(root)
    _require((proof.xid, proof.backend, proof.xact, proof.vxid) ==
             (blocker.xid, blocker.backend, blocker.xact, blocker.vxid), 'wrong-blocker')


def _terminal_catalog(rows, blocker):
    """Actual last pre-close catalog: blocker released and every backend idle."""
    _require(type(rows) is list and 1 <= len(rows) <= 64 and
             len({row['pid'] for row in rows}) == len(rows), 'terminal-catalog-shape')
    roots = [row for row in rows if row['pid'] == blocker.pid]
    _require(len(roots) == 1 and roots[0]['backend'] == blocker.backend, 'terminal-blocker')
    for row in rows:
        _require(type(row['pid']) is int and 0 < row['pid'] < 2 ** 32 and
                 type(row['backend']) is str and bool(row['backend']) and
                 row['state'] == 'idle' and row['xact'] is None and row['xid'] is None and
                 row['blockers'] == [] and _decode_locks(row['locks_raw']) == row['locks'] == [],
                 'terminal-database-busy')


def _window(backend, events, cases, responses, *, pending=False):
    if backend == 'go':
        _require(events == [], 'go-hit-django-or-extra-asgi')
        return
    expected = {case['name']: case for case in cases}
    _require(len(events) == len(expected) == 2 and len({row['cid'] for row in events}) == 2 and
             {row['cid'] for row in events} == set(expected), 'asgi-correlations')
    for row in events:
        case = expected[row['cid']]
        index = cases.index(case)
        _require(row == dict(seq=row['seq'], method='DELETE', path=case['path'], cid=case['name'],
                 status=None if pending else responses[index]['status'], error_type=None), 'asgi-attribution')


def _summary(contract, case, before, after):
    if before is None or after is None:
        return None
    target = case['effect']['message_id']
    return dict(models_before=len(before[0]), models_after=len(after[0]),
        target_before=sum(contract.scalar(row['message_id']) == target for row in before[0][contract.MESSAGE]),
        target_after=sum(contract.scalar(row['message_id']) == target for row in after[0][contract.MESSAGE]),
        deliveries_before=sum(contract.scalar(row['message_id']) == target for row in before[0][contract.DELIVERY]),
        deliveries_after=sum(contract.scalar(row['message_id']) == target for row in after[0][contract.DELIVERY]),
        audit_delta=len(after[0][contract.AUDIT]) - len(before[0][contract.AUDIT]),
        index_delta=len(after[0][contract.INDEX]) - len(before[0][contract.INDEX]),
        changed_models=sum(before[0].get(key) != after[0].get(key) for key in set(before[0]) | set(after[0])),
        cache_changed=before[1] != after[1])


def _verify(portable, cases, responses, before, after, contrasts, *, started, finished, client_ip):
    """Check exact single effect even if status/body assertions already failed."""
    errors, winner = [], None
    try:
        winner = _responses(responses)
    except Exception:
        errors.append('http')
    # Without a single winner, do not invent attribution. Still verify the raw
    # observed audit against an actual request CID; never discard duplicate rows.
    if winner is None:
        events = after[0].get('auditlog.LogEntry', [])
        old_ids = {row['id'] for row in before[0].get('auditlog.LogEntry', [])}
        additions = [row for row in events if row['id'] not in old_ids]
        cid = additions[0].get('cid') if len(additions) == 1 else None
        selected = next((case for case in cases if case['name'] == cid), cases[0])
    else:
        selected = cases[winner]
    try:
        _verify_effect(portable, selected, before, after, contrasts, started=started,
                       finished=finished, client_ip=client_ip)
    except Exception:
        errors.append('effects')
    return errors


def _cases(contract, fixture, backend, *, canonical=None):
    # run reads the independent repository contract; pure finalization reuses
    # that separately retained source, never a caller-supplied success flag.
    contract.validate_runtime(fixture['runtime'], canonical)
    contract.validate_runtime(fixture['runtime'], fixture['authored'])
    selected = [case for case in fixture['runtime']['cases'] if case['name'] == 'message-mutations-delete-email']
    _require(len(selected) == 1, 'canonical-case')
    case = selected[0]
    _require(case['method'] == 'DELETE' and case['headers'] == {} and case['status'] == 200 and
             case['effect']['kind'] == 'delete' and case['effect']['delivery_count'] == 2 and
             case['audit_actor'] == fixture['authored']['ids']['owner'] and
             case['audit_additional'] is None and case['token'] == fixture['tokens']['owner'] and
             not case.get('fault') and not case.get('admission'), 'ordinary-canonical-delete')
    return [dict(copy.deepcopy(case), name=cid) for cid in _correlations(backend)]


def _nonretrying_redis(options):
    # Installed redis-py Retry uses _retries (negative means forever), without
    # get_retries(). Accept its concrete implementation only, never a duck-typed
    # accessor/default that could silently admit an unknown retry policy.
    from redis.retry import Retry
    retry = options.get('retry')
    _require(not options.get('retry_on_timeout') and not options.get('retry_on_error') and
             (retry is None or (type(retry) is Retry and
              type(retry._retries) is int and retry._retries == 0)), 'nonretrying-redis')


def _fresh(contract, case, state, ids):
    models = state[0]
    _require(len(models) == 128, 'requires-all-128-models')
    target = case['effect']['message_id']
    messages = [contract.row_values(row) for row in models[contract.MESSAGE]
                if contract.scalar(row['message_id']) == target]
    _require(len(messages) == 1 and messages[0]['sport_association_id'] == ids['own_association'], 'present-target')
    _require(any(contract.scalar(row['message_id']) == ids['foreign_email'] and
                 contract.scalar(row['sport_association_id']) == ids['foreign_association']
                 for row in models[contract.MESSAGE]), 'foreign-message-sentinel')
    deliveries = [contract.scalar(row['message_transaction_id']) for row in models[contract.DELIVERY]
                  if contract.scalar(row['message_id']) == target]
    _require(len(deliveries) == len(set(deliveries)) == 2 and any(
        contract.scalar(row['message_id']) != target for row in models[contract.DELIVERY]), 'real-deliveries-and-sentinel')
    content_types = {row['id'] for row in models['contenttypes.ContentType']
                     if row['app_label'] == 'communications' and row['model'] == 'message'}
    history = [row for row in models[contract.AUDIT] if contract.scalar(row['object_pk']) == target
               and row['content_type_id'] in content_types]
    _require({row['action'] for row in history} == {0, 1}, 'historical-sentinels')
    _require(all(any(index['log_entry_id'] == row['id'] for index in models[contract.INDEX])
                 for row in history), 'historical-index-sentinels')
    return tuple(sorted(deliveries))


class _Control:
    def __init__(self, *, backend, apps, database, raw, fixture, base_url, process_guard,
                 observer, client_ip, private_evidence, bounds, portable, contract,
                 connection_observation=None):
        self.backend, self.apps, self.database, self.raw = backend, apps, database, raw
        self.fixture, self.endpoint, self.process_guard = fixture, urlsplit(base_url), process_guard
        self.asgi, self.client_ip, self.private = observer, client_ip, private_evidence
        self.bounds, self.portable, self.contract = bounds, portable, contract
        self.end = time.monotonic() + bounds.overall
        self.guard = copy.deepcopy(process_guard())
        self.monitor = self.blocking = self.blocker = None
        self.released, self.original_timeout = True, None
        self.threads, self.clients = [], [None, None]
        self.cancel, self.client_lock = threading.Event(), threading.Lock()
        self.interruption = None
        self.unclosed_connections = []
        self.responses = [dict(complete=False, status=None), dict(complete=False, status=None)]
        self.private.update(responses=self.responses, activity=[], guard_before=self.guard)
        self.before = self.after = None
        self.cursor = None
        self.websockets = None
        self.catalog_overlap_proved = False
        self.catalog_waits = dict(validated=0, pre_xid=0, permanent_xid=0)
        self.catalog_wait_failure = None
        self.connection_observation = connection_observation
        self.connection_failed = False
        self.connection_prefix = self.connection_baseline = None
        self.connection_samples = []
        self.owned_handles = {}
        self.cases = _cases(contract, fixture, backend)

    def _measure_owned(self, role, connection):
        """Pin only our three actual wrappers/native objects to self catalog rows."""
        _require(role in ('default', 'blocker', 'monitor'), 'owned-connection-role')
        native = connection.connection
        _require(native is not None, 'owned-connection-closed')
        pid = native.info.backend_pid
        _require(type(pid) is int and 0 < pid < 2 ** 32, 'owned-native-pid')
        owners = self.private.setdefault('connection_owners', {})
        if role in self.owned_handles:
            old_connection, old_native = self.owned_handles[role]
            _require(connection is old_connection and native is old_native and
                     owners[role]['native_pid'] == pid and owners[role]['end_cursor'] is None,
                     'owned-native-replaced')
        with connection.cursor() as cursor:
            cursor.execute('SELECT pid, backend_start::text FROM pg_stat_activity '
                           'WHERE pid = pg_backend_pid()')
            row = cursor.fetchone()
        _require(type(row) in (tuple, list) and len(row) == 2 and type(row[0]) is int and
                 row[0] == pid and type(row[1]) is str and bool(row[1]) and
                 connection.connection is native and native.info.backend_pid == pid,
                 'owned-catalog-identity')
        row = tuple(row)
        _require(all(other == role or (record['native_pid'] != pid and
                     record['catalogs'][0][1] != row[1]) for other, record in owners.items()),
                 'owned-connection-ambiguous')
        if role in owners:
            _require(row == owners[role]['catalogs'][0], 'owned-backend-recycled')
            owners[role]['catalogs'].append(row)
        else:
            owners[role] = dict(native_pid=pid, catalogs=[row], end_cursor=None)
            self.owned_handles[role] = (connection, native)

    def _seal_owned(self, roles, *, measure=True):
        for role in roles:
            connection, native = self.owned_handles[role]
            if measure:
                self._measure_owned(role, connection)
            else:
                _require(connection.connection is native and native.info.backend_pid ==
                         self.private['connection_owners'][role]['native_pid'], 'owned-native-replaced')
        current = self.connection_snapshot()
        for role in roles:
            self.private['connection_owners'][role]['end_cursor'] = len(current.events)

    def connection_snapshot(self):
        """Parent owns bounded actual reads; never infer proof from wrapper flags."""
        if self.connection_observation is None:
            return None
        try:
            _require(not self.connection_failed, 'connection-observation-failed')
            snapshot = self.connection_observation()
            current = (attribution.parse_observation(snapshot) if self.connection_prefix is None else
                       attribution.extend_observation(self.connection_prefix, snapshot))
            _require(current.backend == self.backend, 'connection-observation-backend')
            attribution.require_owned_queries(current, self.private.get('connection_owners'))
            if self.connection_baseline is not None:
                attribution.require_window(self.connection_baseline, current)
            self.connection_prefix = current
            self.private['connection_attribution']['captured'] = dict(
                backend=current.backend, run_id=current.run_id, events=current.events)
            return current
        except BaseException:
            self.connection_failed = True
            raise

    def start_connection_window(self):
        if self.connection_observation is not None:
            self.private['connection_attribution'] = dict(samples=self.connection_samples)
            baseline = self.connection_snapshot()
            attribution.require_baseline(baseline)
            self.connection_baseline = baseline
            self.private['connection_attribution']['baseline_cursor'] = len(baseline.events)

    def remember_interruption(self, error):
        if not isinstance(error, Exception):
            with self.client_lock:
                if self.interruption is None:
                    self.interruption = error

    def check_interruption(self):
        if self.interruption is not None:
            raise self.interruption from None

    def remaining(self, end=None):
        remaining = (self.end if end is None else end) - time.monotonic()
        _require(remaining > 0, 'deadline')
        return remaining

    def stable(self):
        observed = copy.deepcopy(self.process_guard())
        self.private['guard_last'] = observed
        _require(observed == self.guard, 'measured-guard-changed')

    def _connection(self, label):
        connection = self.database.copy(alias='message-concurrency-' + label)
        try:
            options = copy.deepcopy(connection.settings_dict.get('OPTIONS', {}))
            options['connect_timeout'] = max(1, math.ceil(self.bounds.connect))
            connection.settings_dict['OPTIONS'] = options
            connection.ensure_connection()
            _require((connection.connection.info.host, connection.connection.info.port) ==
                     (self.database.connection.info.host, self.database.connection.info.port),
                     'copied-database-server-identity')
            connection.set_autocommit(True)
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, false), "
                    "set_config('lock_timeout', %s, false), "
                    "set_config('idle_in_transaction_session_timeout', %s, false)",
                    [str(math.ceil(self.bounds.query * 1000))] * 2 +
                    [str(math.ceil((self.bounds.overall + self.bounds.cleanup) * 1000))])
                cursor.execute('SELECT current_database(), current_user')
                _require(cursor.fetchone() == (self.database.settings_dict['NAME'],
                                              self.database.settings_dict['USER']), 'database-identity')
            return connection
        except BaseException as error:
            self.remember_interruption(error)
            try:
                connection.close()
            except BaseException as close_error:
                self.unclosed_connections.append(connection)
                self.remember_interruption(close_error)
            self.check_interruption()
            raise

    def setup(self):
        self.stable()
        with self.database.cursor() as cursor:
            cursor.execute('SHOW statement_timeout')
            self.original_timeout = cursor.fetchone()[0]
            cursor.execute("SELECT set_config('statement_timeout', %s, false)",
                           [str(math.ceil(self.bounds.query * 1000))])
        if self.backend == 'django' and self.connection_observation is not None:
            self._measure_owned('default', self.database)
        self.blocking = self._connection('blocker')
        self.monitor = self._connection('observer')
        if self.backend == 'django' and self.connection_observation is not None:
            self._measure_owned('blocker', self.blocking)
            self._measure_owned('monitor', self.monitor)
        self.remaining()
        self.asgi.wait_idle(timeout=min(self.bounds.query, self.remaining()), include_websockets=True)
        self.cursor = self.asgi.cursor()
        self.websockets = (self.asgi.websocket_started, self.asgi.websocket_completed)
        _require(not any(row['cid'] in _correlations(self.backend) for row in self.asgi.delta(0)),
                 'reused-correlations')
        _require(not self.busy(self.activity()), 'database-already-busy')
        self.before = self.capture()
        self.private['before'] = self.before
        self.portable.assert_state_unchanged(self.fixture['baseline_state'], self.before[0])
        self.portable.assert_cache_unchanged(self.fixture['baseline_cache'], self.before[1], self.fixture['contrasts'])
        self.deliveries = _fresh(self.contract, self.cases[0], self.before, self.fixture['authored']['ids'])

    def capture(self, end=None):
        def bounded(execute, sql, params, many, context):
            self.remaining(end)
            result = execute(sql, params, many, context)
            self.remaining(end)
            return result
        with self.database.execute_wrapper(bounded):
            state = self.portable.snapshot_state(self.apps)
        cached = self.portable.snapshot_cache(self.raw)
        self.remaining(end)
        _require(isinstance(state, dict) and len(state) == 128, 'requires-all-128-models')
        return state, cached

    def hold(self):
        self.blocking.set_autocommit(False)
        self.released = False
        ids = self.fixture['authored']['ids']
        with self.blocking.cursor() as cursor:
            cursor.execute('SELECT message_id::text, sport_association_id::text, tableoid::oid, ctid::text '
                           'FROM communications_message '
                           'WHERE message_id = %s AND sport_association_id = %s FOR UPDATE',
                           [self.cases[0]['effect']['message_id'], ids['own_association']])
            message = cursor.fetchall()
            _require(len(message) == 1 and len(message[0]) == 4 and
                     message[0][0] == self.cases[0]['effect']['message_id'] and
                     message[0][1] == ids['own_association'], 'exact-target-blocker')
            cursor.execute('SELECT message_transaction_id::text, tableoid::oid, ctid::text '
                           'FROM communications_messagetransaction WHERE message_id = %s '
                           'ORDER BY message_transaction_id FOR UPDATE', [message[0][0]])
            deliveries = cursor.fetchall()
            _require(tuple(row[0] for row in deliveries) == self.deliveries, 'exact-delivery-blockers')
            cursor.execute('SELECT user_id::text, tableoid::oid, ctid::text FROM bakney_user WHERE user_id = %s',
                           [ids['owner']])
            owner = cursor.fetchall()
            _require(len(owner) == 1 and owner[0][0] == ids['owner'], 'exact-owner-identity')
            cursor.execute('SELECT a.pid, a.backend_xid::text, a.backend_start::text, a.xact_start::text, '
                '(SELECT l.virtualtransaction FROM pg_locks l WHERE l.pid = a.pid '
                "AND l.locktype = 'virtualxid' AND l.granted AND l.mode = 'ExclusiveLock' "
                'AND l.virtualxid::text = l.virtualtransaction) '
                'FROM pg_stat_activity a WHERE a.pid = pg_backend_pid()')
            pid, xid, backend, xact, vxid = cursor.fetchone()
        def identities(rows):
            result = set()
            for identifier, relation, ctid in rows:
                match = re.fullmatch(r'\((\d+),(\d+)\)', ctid)
                _require(match is not None and type(relation) is int, 'row-identity')
                result.add((relation, *map(int, match.groups())))
            _require(len(result) == len(rows), 'ambiguous-row-identity')
            return result
        _require(type(pid) is int and pid > 0 and _xid(xid) and _vxid(vxid) and
                 type(backend) is str and bool(backend) and type(xact) is str and bool(xact),
                 'blocker-transaction-identity')
        self.blocker = Blocker(pid, xid, backend,
            dict(message=identities([(message[0][0], *message[0][2:])]),
                 delivery=identities(deliveries), owner=identities(owner)),
            message[0][0], self.deliveries, xact, vxid, message[0][1])
        self.private['blocker'] = self.blocker
        _root(self.activity(), self.blocker)

    def release(self):
        if not self.released and self.blocking is not None:
            self.blocking.rollback()
            self.released = True
            self.blocking.set_autocommit(True)

    def activity(self):
        with self.monitor.cursor() as cursor:
            cursor.execute('SELECT pg_stat_clear_snapshot()')
            cursor.execute("SELECT a.pid, a.backend_xid::text, a.xact_start::text, a.backend_start::text, "
                "pg_blocking_pids(a.pid), a.state, a.wait_event_type, a.query, COALESCE((SELECT "
                "jsonb_agg(jsonb_build_object('kind', l.locktype, 'granted', l.granted, 'mode', l.mode, "
                "'xid', l.transactionid::text, 'relation', l.relation::bigint, 'page', l.page, 'item', l.tuple, "
                "'virtualtransaction', l.virtualtransaction, 'virtualxid', l.virtualxid::text)) "
                "FROM pg_locks l WHERE l.pid = a.pid AND l.locktype IN ('tuple', 'transactionid', 'virtualxid')), '[]'::jsonb) "
                "FROM pg_stat_activity a WHERE a.datname = current_database() "
                "AND a.pid <> pg_backend_pid() AND a.pid <> %s", [self.database.connection.info.backend_pid])
            rows = [dict(zip(('pid', 'xid', 'xact', 'backend', 'blockers', 'state', 'wait', 'query', 'locks'), row))
                    for row in cursor.fetchall()]
        # Bound private storage even if an unexpected noisy database is supplied.
        _require(len(rows) <= 64 and len(self.private['activity']) < 4000, 'activity-bound')
        self.private['activity'].append(rows)
        for row in rows:
            row['locks_raw'] = row['locks']
            row['locks'] = _decode_locks(row['locks_raw'])
        return rows

    def busy(self, rows):
        return [row for row in rows if (self.blocker is None or row['pid'] != self.blocker.pid)
                and (row['xact'] is not None or row['state'] != 'idle')]

    def open_requests(self):
        for index, case in enumerate(self.cases):
            thread = threading.Thread(target=self._request, args=(index, case),
                                      name='message-concurrency-' + str(index))
            self.threads.append(thread)
            thread.start()

    def _request(self, index, case):
        stream, response = None, None
        record = self.responses[index]
        record.update(cid=case['name'], method='DELETE', path=case['path'], request_body=b'', wire=bytearray())
        try:
            deadline = min(self.end, time.monotonic() + self.bounds.read)
            self.remaining(deadline)
            stream = socket.create_connection((self.endpoint.hostname, self.endpoint.port),
                timeout=min(self.bounds.connect, self.remaining(deadline)), source_address=(self.client_ip, 0))
            with self.client_lock:
                if self.cancel.is_set():
                    stream.close()
                    return
                self.clients[index] = stream
            record.update(peer=stream.getpeername()[:2], source=stream.getsockname()[0])
            _require(record['peer'] == (self.endpoint.hostname, self.endpoint.port) and
                     record['source'] == self.client_ip, 'actual-http-peer')
            headers = {'Host': self.endpoint.netloc, 'Authorization': 'Bearer ' + case['token'],
                       'Accept': 'application/json', 'Accept-Language': 'en',
                       'X-Correlation-ID': case['name'], 'Connection': 'close', 'Content-Length': '0'}
            payload = '\r\n'.join([f"DELETE {case['path']} HTTP/1.1",
                *[f'{key}: {value}' for key, value in headers.items()], '', '']).encode('ascii')
            record['request_wire'] = payload  # private, contains original signed token
            stream.settimeout(min(self.bounds.connect, self.remaining(deadline)))
            record['attempted_send'] = True
            stream.sendall(payload)
            record['sent'] = True
            class RecordingStream:
                def settimeout(inner, timeout):
                    stream.settimeout(min(timeout, self.remaining(deadline)))
                def recv_into(inner, buffer):
                    count = stream.recv_into(buffer)
                    record['wire'].extend(buffer[:count])
                    _require(len(record['wire']) <= 196608, 'wire-size')
                    return count
            adapter = _ResponseSocket(RecordingStream(), self.remaining(deadline))
            response = http.client.HTTPResponse(adapter)
            response.begin()
            record.update(status=response.status, reason=response.reason, http_version=response.version,
                          response_headers=response.getheaders())
            record['body'] = _response_body(response)
            # Connection: close plus EOF proves no unaccounted trailing response.
            # The buffered HTTP reader may already hold a tail beyond the frame.
            if response.fp is not None:
                _require(response.fp.read(1) == b'', 'response-trailing-bytes')
            stream.settimeout(self.remaining(deadline))
            tail = stream.recv(1)
            record['wire'].extend(tail)
            _require(tail == b'', 'response-trailing-bytes')
            self.remaining(deadline)
            _validate_wire(record)
            record['complete'] = True
        except BaseException as error:
            record['transport_failed'] = True
            # A worker cannot raise into its parent thread. Transfer the actual
            # control exception, without printing it through threading.excepthook.
            self.remember_interruption(error)
        finally:
            for resource in (response, stream):
                if resource is not None:
                    try:
                        resource.close()
                    except BaseException as error:
                        record['close_failed'] = True
                        self.remember_interruption(error)
            with self.client_lock:
                self.clients[index] = None
            record['wire'] = bytes(record['wire'])

    def outstanding(self):
        self.check_interruption()
        _require(len(self.threads) == 2 and all(thread.is_alive() for thread in self.threads) and
                 all(row.get('sent') is True and row['status'] is None and not row['complete'] and
                     not row.get('transport_failed') for row in self.responses), 'requests-not-outstanding')

    def observe_overlap(self):
        end = min(self.end, time.monotonic() + self.bounds.overlap)
        previous, complete_observations = (), 0
        while True:
            self.check_interruption()
            self.remaining(end)
            self.stable()
            _require(all(thread.is_alive() for thread in self.threads), 'early-http-completion')
            try:
                connection_before = self.connection_snapshot()
                rows = self.activity()
                _root(rows, self.blocker)
                proof = _overlap(rows, self.blocker, self.backend, previous=previous)
                if connection_before is not None:
                    connection_after = self.connection_snapshot()
                    attribution.bind_waits(self.connection_baseline, connection_before, connection_after, proof)
                    self.connection_samples.append(dict(before_cursor=len(connection_before.events),
                        after_cursor=len(connection_after.events), activity_index=len(self.private['activity']) - 1))
            except AssertionError as error:
                # Compare complete fixed strings; never expose exception text.
                self.catalog_wait_failure = next((code for code in _WAIT_FAILURE_CODES
                    if error.args == ('message-concurrency.' + code,)), 'catalog-validation')
                raise
            self.catalog_waits = dict(validated=len(proof),
                pre_xid=sum(item.xid is None for item in proof),
                permanent_xid=sum(item.xid is not None for item in proof))
            previous = proof
            self.remaining(end)
            if len(proof) == 2:
                self.outstanding()
                _window(self.backend, self.asgi.delta(self.cursor), self.cases, self.responses, pending=True)
                complete_observations += 1
                if complete_observations == 2:
                    self.private['overlap'] = proof
                    self.catalog_overlap_proved = True
                    return
            time.sleep(min(self.bounds.poll, self.remaining(end)))

    def idle(self, end):
        self.asgi.wait_idle(timeout=self.remaining(end), include_websockets=True)
        while True:
            self.remaining(end)
            if not self.busy(self.activity()):
                break
            time.sleep(min(self.bounds.poll, self.remaining(end)))
        self.remaining(end)

    def finish(self):
        self.release()
        for thread in self.threads:
            thread.join(timeout=self.remaining())
            self.check_interruption()
            _require(not thread.is_alive(), 'client-thread-timeout')
        self.idle(self.end)
        self.after = self.capture()
        self.private['after'] = self.after
        self.idle(self.end)
        self.stable()

    def cleanup(self):
        """Never cancel/terminate parent backends; unsafe reset is explicit."""
        end = time.monotonic() + self.bounds.cleanup
        status = dict(clients_closed=False, threads_joined=False, blocker_released=self.released,
                      connections_closed=False, database_quiescent=False, asgi_quiescent=False,
                      guard_stable=False, reset_safe=False, fixture_reset_owner='parent',
                      server_owner='parent')
        errors = []
        interruption = None
        def failed_operation(name, error):
            nonlocal interruption
            errors.append(name)
            if not isinstance(error, Exception) and interruption is None:
                interruption = error
        def attempt(name, operation):
            try:
                self.remaining(end)
                operation()
                self.remaining(end)
                return True
            except BaseException as error:
                failed_operation(name, error)
                return False
        attempt('blocker-release', self.release)
        status['blocker_released'] = self.released
        self.cancel.set()
        with self.client_lock:
            for stream in self.clients:
                if stream is not None:
                    try:
                        stream.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    except BaseException as error:
                        failed_operation('client-shutdown', error)
                    try:
                        stream.close()
                    except BaseException as error:
                        failed_operation('client-close', error)
        for thread in self.threads:
            attempt('thread-join', lambda: thread.join(timeout=self.remaining(end)))
        status['threads_joined'] = all(not thread.is_alive() for thread in self.threads)
        status['clients_closed'] = status['threads_joined'] and all(stream is None for stream in self.clients) and not any(
            row.get('close_failed') for row in self.responses)
        status['asgi_quiescent'] = attempt('asgi-idle', lambda: self.asgi.wait_idle(
            timeout=self.remaining(end), include_websockets=True))
        if self.monitor is not None:
            status['database_quiescent'] = attempt('database-idle', lambda: self.idle(end))
        else:
            status['database_quiescent'] = not self.threads
        # Preserve the final entire ASGI window even on failure, including tail.
        if self.cursor is not None:
            attempt('asgi-window', lambda: self.private.update(asgi=self.asgi.delta(self.cursor)))
        if self.before is not None and status['database_quiescent'] and status['asgi_quiescent']:
            def final_capture():
                self.after = self.capture(end)
                self.private['after'] = self.after
            attempt('failure-snapshot', final_capture)
        terminal_cursor = None
        if self.connection_baseline is not None and not self.connection_failed:
            def terminal_catalog():
                nonlocal terminal_cursor
                _terminal_catalog(self.activity(), self.blocker)
                terminal_cursor = len(self.private['activity'])
            attempt('terminal-catalog', terminal_catalog)
        if self.backend == 'django' and self.connection_baseline is not None and not self.connection_failed:
            attempt('owned-copied-connections', lambda: self._seal_owned(('blocker', 'monitor')))
        closed = True
        for connection in (self.blocking, self.monitor, *self.unclosed_connections):
            if connection is not None:
                # close must still be attempted when the cleanup budget expires.
                try:
                    connection.close()
                    closed = closed and connection.connection is None
                except BaseException as error:
                    failed_operation('connection-close', error)
                    closed = False
        status['connections_closed'] = closed
        if self.backend == 'django' and self.connection_baseline is not None and not self.connection_failed:
            # Keep the final self-catalog SQL under our temporary query timeout.
            attempt('owned-default-measurement', lambda: self._measure_owned('default', self.database))
        if self.original_timeout is not None:
            def restore_timeout():
                with self.database.cursor() as cursor:
                    cursor.execute("SELECT set_config('statement_timeout', %s, false)", [self.original_timeout])
            attempt('restore-timeout', restore_timeout)
        if self.backend == 'django' and self.connection_baseline is not None and not self.connection_failed:
            attempt('owned-default-connection', lambda: self._seal_owned(('default',), measure=False))
        status['guard_stable'] = attempt('guard-stable', self.stable)
        if self.cursor is not None:
            def final_window():
                with self.asgi.condition:
                    _require(self.asgi.active == self.asgi.active_websockets == self.asgi.websocket_errors == 0,
                             'final-asgi-not-idle')
                    self.private['asgi'] = self.asgi.delta(self.cursor)
                    _require(self.websockets == (self.asgi.websocket_started, self.asgi.websocket_completed),
                             'extra-websocket')
            status['asgi_quiescent'] = attempt('final-asgi-window', final_window) and status['asgi_quiescent']
        if terminal_cursor is not None:
            self.private['terminal_observation'] = dict(activity_cursor=terminal_cursor,
                                                       asgi=copy.deepcopy(self.private.get('asgi')))
        # An incomplete, unobserved request may still be queued before any DB
        # transaction/ASGI scope starts. Instantaneous idle catalogs cannot prove
        # that it will never run. Parent must stop its server before fixture reset.
        # Catalog waits have no independent request binding. They cannot account
        # for a still-queued intended request or make its reset safe.
        status['admission_accounted'] = all(
            not row.get('attempted_send') or row['complete'] for row in self.responses)
        status['reset_safe'] = not errors and all(status[key] is True for key in
            ('clients_closed', 'threads_joined', 'blocker_released', 'connections_closed',
             'database_quiescent', 'asgi_quiescent', 'guard_stable', 'admission_accounted'))
        status['cleanup_complete'] = status['reset_safe'] and not errors
        status['failures'] = errors
        self.private['cleanup'] = status
        if interruption is not None:
            raise interruption from None
        return status


def _inputs(*, backend, apps, database, raw, fixture, base_url, process_guard, observer,
            client_ip, private_evidence, bounds):
    bounds.validate()
    _require(__debug__ and backend in ('django', 'go') and type(private_evidence) is dict and
             not private_evidence and callable(process_guard), 'explicit-inputs')
    from django.db import connections
    from TESTS.adapters.django import message_mutations as adapter
    from TESTS.adapters.django import message_read_scopes as scopes
    from TESTS.adapters.django import message_reads as shared
    from TESTS.adapters.django import historical_read_cache as cache
    from TESTS.adapters.django.message_reads_browser_runtime import CompleteAsgiObserver
    from TESTS.contracts import test_message_mutations as contract
    _require(isinstance(observer, CompleteAsgiObserver), 'complete-asgi-observer')
    endpoint = urlsplit(base_url)
    _require(endpoint.scheme == 'http' and endpoint.hostname == '127.0.0.1' and endpoint.port and
             endpoint.path in ('', '/') and not any((endpoint.query, endpoint.fragment,
             endpoint.username, endpoint.password)), 'explicit-native-http-origin')
    _require(str(ipaddress.ip_address(client_ip)) == client_ip and
             ipaddress.ip_address(client_ip).is_loopback, 'independent-native-peer-ip')
    _require(database is connections['default'] and database.connection is not None and
             getattr(apps, 'ready', False) is True and database.vendor == 'postgresql' and
             database.get_autocommit() and not database.in_atomic_block, 'connected-default-database')
    config = database.settings_dict
    _require(all(config.get(key) for key in ('NAME', 'USER', 'HOST', 'PORT')) and
             config['NAME'].startswith('assozeta_contract_') and config['NAME'].isidentifier() and
             not config.get('OPTIONS', {}).get('pool') and not getattr(database, 'pool', None),
             'private-unpooled-postgresql')
    # libpq multiple hosts and service files can create extra, unbounded attempts.
    host = config['HOST']
    _require(isinstance(host, str) and (host.startswith('/') or ipaddress.ip_address(host).is_loopback) and
             ',' not in host and str(config['PORT']).isdigit() and 0 < int(config['PORT']) < 65536 and
             database.connection.info.host == host and database.connection.info.port == int(config['PORT']) and
             not any(key in config.get('OPTIONS', {}) for key in
                 ('service', 'host', 'hostaddr', 'port', 'dbname', 'database', 'user', 'password', 'passfile', 'dsn')),
             'explicit-single-postgresql-host')
    adapter.require_private_database()  # Includes production Python, no DSN lookup.
    options = raw.connection_pool.connection_kwargs
    _require(options.get('db') == 1 and all(type(options.get(key)) in (int, float) and
             math.isfinite(options[key]) and 0 < options[key] <= bounds.query
             for key in ('socket_timeout', 'socket_connect_timeout')), 'finite-raw-db1')
    _nonretrying_redis(options)
    pool_timeout = getattr(raw.connection_pool, 'timeout', bounds.query)
    _require(type(pool_timeout) in (int, float) and math.isfinite(pool_timeout) and
             0 < pool_timeout <= bounds.query and not options.get('health_check_interval'), 'finite-redis-pool')
    _cases(contract, fixture, backend)
    from TESTS.adapters.django.message_mutations_browser_denials import _identity
    _require(_identity(fixture['tokens']['owner']) == fixture['authored']['ids']['owner'], 'signed-owner-identity')
    guard = process_guard()
    _require(isinstance(guard, dict) and guard.get('running') is True and
             type(guard.get('pid')) is int and guard['pid'] > 0 and guard.get('instance') is not None,
             'actual-parent-process-guard')
    portable = PortableAssertions.from_modules(contract=contract, scopes=scopes, shared=shared, cache=cache, adapter=adapter)
    return portable, contract, guard


def _captured_attribution(private, *, final_observation=None):
    """Revalidate original wire and every saved catalog sample; no success flags."""
    evidence = private['connection_attribution']
    captured = attribution.parse_observation(evidence['captured'])
    terminal = private['terminal_observation']
    _require(type(terminal) is dict and set(terminal) == {'activity_cursor', 'asgi'} and
             type(terminal['activity_cursor']) is int and
             1 <= terminal['activity_cursor'] == len(private['activity']) and
             terminal['asgi'] == private['asgi'], 'terminal-observation-boundary')
    _terminal_catalog(private['activity'][terminal['activity_cursor'] - 1], private['blocker'])
    owned = private.get('connection_owners')
    attribution.require_owned_queries(captured, owned, completed=True,
                                     activity=[row for rows in private['activity'] for row in rows])
    if captured.backend == 'django':
        blocker = owned['blocker']
        _require((private['blocker'].pid, private['blocker'].backend) ==
                 blocker['catalogs'][0], 'owned-blocker-identity')
    if final_observation is not None:
        finalized = attribution.extend_observation(captured, final_observation, final=True)
        attribution.require_owned_queries(finalized, owned, completed=True)
    baseline = attribution.prefix(captured, evidence['baseline_cursor'])
    samples, previous, last_index = [], (), -1
    for sample in evidence['samples']:
        _require(type(sample) is dict and set(sample) ==
                 {'before_cursor', 'after_cursor', 'activity_index'}, 'attribution-sample')
        index = sample['activity_index']
        _require(type(index) is int and last_index < index < len(private['activity']),
                 'attribution-catalog-order')
        _require(index < terminal['activity_cursor'] - 1, 'terminal-before-overlap')
        rows = private['activity'][index]
        for row in rows:
            _require(_decode_locks(row['locks_raw']) == row['locks'], 'attribution-original-locks')
        _root(rows, private['blocker'])
        proof = _overlap(rows, private['blocker'], captured.backend, previous=previous)
        samples.append((sample['before_cursor'], sample['after_cursor'], proof))
        previous, last_index = proof, index
    for response in private['responses']:
        _require(response.get('complete') is True and response.get('sent') is True and
                 response.get('attempted_send') is True, 'incomplete-responses')
        _validate_wire(response)
    return attribution.verify_lifecycle(baseline, captured, samples, private['responses'],
                                        final_observation=final_observation)


def _attribution_fixture(fixture):
    """Retain only pure oracle inputs, excluding live fixture ownership handles."""
    from TESTS.adapters.django.historical_read_cache import HistoricalReadCacheSnapshot
    projected = copy.deepcopy({key: fixture[key] for key in
        ('authored', 'runtime', 'tokens', 'baseline_state', 'contrasts')})
    # Production cache snapshots deliberately contain immutable mappingproxy;
    # deepcopy/pickle is unsupported. Entries themselves are frozen raw values.
    projected['baseline_cache'] = HistoricalReadCacheSnapshot(
        MappingProxyType(dict(fixture['baseline_cache'].entries)))
    return projected


def finalize_attribution(private_evidence, final_observation):
    """Pure post-shutdown verifier; return fresh safe diagnostics, never change inputs.

    Parent must first stop/join every producer, successfully close observation,
    and validate actual process/source ownership, writer Sync/Close and BOTH Go
    streams. This connection-only verifier cannot measure those external facts.
    Missing footer is a finalization failure, never a reason to skip run cleanup.
    """
    diagnostics = dict(success=False, stage='connection-observation-finalization',
        checks_failed=[], request_transaction_attribution_verified=False,
        overlap_verified=False, full_concurrency_acceptance=False,
        attribution_finalized=False, pending_reason=None)
    try:
        _require(type(private_evidence) is dict, 'private-evidence')
        record = private_evidence['attribution_verification']
        # Failure latches veto acceptance; they never supply positive proof.
        _require(record['execution_failed'] is False, 'probe-execution-failed')
        summary = _captured_attribution(private_evidence, final_observation=final_observation)
        _require(summary['stream_finalized'] is True, 'connection-footer')
        from TESTS.contracts import test_message_mutations as contract
        from TESTS.adapters.django import message_reads as shared
        backend = private_evidence['connection_attribution']['captured']['backend']
        fixture = record['fixture']
        _require(fixture['authored'] == record['canonical_source'], 'final-canonical-source')
        cases = _cases(contract, fixture, backend, canonical=record['canonical_source'])
        before, after = private_evidence['before'], private_evidence['after']
        _require(private_evidence['blocker'].target == cases[0]['effect']['message_id'] and
                 private_evidence['blocker'].association == fixture['authored']['ids']['own_association'],
                 'final-canonical-target')
        portable = PortableAssertions(None, contract.assert_effect, None,
            shared.assert_state_unchanged, shared.assert_cache_unchanged, None, None)
        portable.assert_state_unchanged(fixture['baseline_state'], before[0])
        portable.assert_cache_unchanged(fixture['baseline_cache'], before[1], fixture['contrasts'])
        _require(_fresh(contract, cases[0], before, fixture['authored']['ids']) ==
                 private_evidence['blocker'].deliveries, 'final-canonical-deliveries')
        _require(not _verify(portable, cases, private_evidence['responses'], before, after,
            fixture['contrasts'], started=record['started'], finished=record['finished'],
            client_ip=record['client_ip']), 'final-effects')
        _window(backend, private_evidence['asgi'], cases, private_evidence['responses'])
        cleanup = private_evidence['cleanup']
        cleanup_keys = ('clients_closed', 'threads_joined', 'blocker_released', 'connections_closed',
            'database_quiescent', 'asgi_quiescent', 'guard_stable', 'admission_accounted',
            'reset_safe', 'cleanup_complete')
        _require(cleanup['failures'] == [] and all(cleanup[key] is True for key in cleanup_keys),
                 'final-cleanup')
        _require(private_evidence['guard_before'] == private_evidence['guard_last'], 'final-guard')
        diagnostics.update(success=True, request_transaction_attribution_verified=True,
            overlap_verified=True, full_concurrency_acceptance=True, attribution_finalized=True,
            statuses=[row['status'] for row in private_evidence['responses']],
            counts=_summary(contract, cases[0], before, after),
            cleanup=dict({key: cleanup[key] for key in cleanup_keys}, failures=[],
                         fixture_reset_owner='parent', server_owner='parent'), attribution=summary)
    except Exception:
        diagnostics['checks_failed'] = ['connection-observation-finalization']
    return diagnostics


def run(*, backend, apps, database, raw, fixture, base_url, process_guard, observer, client_ip,
        private_evidence, bounds=Bounds(), connection_observation=None):
    """Two native actual-HTTP DELETEs; parent owns fixture reset and server lifetime.

    Required private_evidence is an empty parent-owned dict, populated with full
    original HTTP (including tokens), DB1/models, SQL/lock and ASGI observations.
    NEVER publish it. Error diagnostics are count-only. On ANY failure,
    inspect error.diagnostics['cleanup']['reset_safe'] before parent fixture reset.
    False requires the parent to stop/quiesce its server before resetting.
    Separate connect/read/query/overlap/overall/cleanup bounds have no fallback.
    Missing independently measured request-to-PID/XID binding always withholds
    full acceptance. Catalog/status/effect characterization remains inspectable.
    Control BaseExceptions re-raise unchanged after cleanup with safe diagnostics.
    """
    control, failed, stage = None, False, 'inputs'
    interruption = None
    errors = []
    def remember(error):
        nonlocal interruption, failed
        failed = True
        if not isinstance(error, Exception) and interruption is None:
            interruption = error
    try:
        _require(connection_observation is None or callable(connection_observation),
                 'connection-observation-callback')
        portable, contract, guard = _inputs(backend=backend, apps=apps, database=database, raw=raw,
            fixture=fixture, base_url=base_url, process_guard=process_guard, observer=observer,
            client_ip=client_ip, private_evidence=private_evidence, bounds=bounds)
        control = _Control(backend=backend, apps=apps, database=database, raw=raw, fixture=fixture,
            base_url=base_url, process_guard=process_guard, observer=observer, client_ip=client_ip,
            private_evidence=private_evidence, bounds=bounds, portable=portable, contract=contract,
            connection_observation=connection_observation)
        _require(control.guard == guard, 'guard-changed-during-inputs')
        stage = 'baseline'
        control.setup()
        stage = 'blocker'
        control.hold()
        stage = 'overlap'
        control.private['started'] = time.time()
        control.start_connection_window()
        control.open_requests()
        control.observe_overlap()
        stage = 'responses-and-quiescence'
        control.finish()
    except BaseException as error:
        remember(error)
    if control is not None and control.interruption is not None:
        remember(control.interruption)
    if control is None:
        # No probe clients/connections were created by failed input validation.
        cleanup = dict(reset_safe=True, cleanup_complete=True, clients_closed=True,
            threads_joined=True, blocker_released=True, connections_closed=True,
            database_quiescent=None, asgi_quiescent=None, guard_stable=None,
            fixture_reset_owner='parent', server_owner='parent', failures=[])
    else:
        try:
            cleanup = control.cleanup()
        except BaseException as error:
            remember(error)
            cleanup = control.private.get('cleanup') or dict(reset_safe=False, cleanup_complete=False, clients_closed=False,
                threads_joined=False, blocker_released=False, connections_closed=False,
                database_quiescent=False, asgi_quiescent=False, guard_stable=False,
                fixture_reset_owner='parent', server_owner='parent', failures=['cleanup-exception'])
        if not cleanup['cleanup_complete']:
            failed = True
            errors.append('cleanup')
        if control.before is not None and control.after is not None:
            try:
                verification = _verify(control.portable, control.cases, control.responses,
                    control.before, control.after, fixture['contrasts'],
                    started=control.private.get('started', time.time()), finished=time.time(), client_ip=client_ip)
                errors.extend(verification)
            except BaseException as error:
                remember(error)
                errors.append('verification')
        if control.cursor is not None:
            try:
                _window(backend, control.private.get('asgi', []), control.cases, control.responses)
            except BaseException as error:
                remember(error)
                errors.append('attribution')
        if control.interruption is not None:
            remember(control.interruption)
        failed = failed or bool(errors) or not control.catalog_overlap_proved
    counts = None
    if control is not None and control.before is not None and control.after is not None:
        try:
            counts = _summary(control.contract, control.cases[0], control.before, control.after)
        except BaseException as error:
            remember(error)
            errors.append('count-summary')
    attribution_verified = False
    if control is not None and control.connection_observation is not None:
        try:
            control.connection_snapshot()
            _captured_attribution(control.private)
            attribution_verified = True
        except BaseException as error:
            remember(error)
        record = dict(execution_failed=True)
        try:
            record.update(fixture=_attribution_fixture(fixture), client_ip=client_ip,
                canonical_source=control.contract.load_source(),
                started=control.private.get('started'), finished=time.time())
            record['execution_failed'] = failed
        except BaseException as error:
            remember(error)
            errors.append('connection-observation-verification-inputs')
        control.private['attribution_verification'] = record
    # Open healthy prefixes are provisional. Observer finalization belongs to
    # parent service shutdown and must never be awaited while services run here.
    if control is not None:
        if not failed:
            stage = 'request-transaction-attribution'
        errors.append(ATTRIBUTION_PENDING if attribution_verified else 'request-transaction-attribution')
    diagnostics = dict(success=False, stage=stage, checks_failed=errors,
        statuses=[row['status'] for row in control.responses] if control is not None else [None, None],
        responses_complete=[row['complete'] for row in control.responses] if control is not None else [False, False],
        catalog_overlap_verified=control.catalog_overlap_proved if control is not None else False,
        catalog_waits=control.catalog_waits if control is not None else
            dict(validated=0, pre_xid=0, permanent_xid=0),
        catalog_wait_failure=control.catalog_wait_failure if control is not None else None,
        request_transaction_attribution_verified=attribution_verified,
        overlap_verified=attribution_verified and control.catalog_overlap_proved,
        attribution_finalized=False,
        pending_reason=ATTRIBUTION_PENDING if attribution_verified else None,
        full_concurrency_acceptance=False,
        characterization_scope=('request-transaction-attribution-provisional' if attribution_verified else
                                'exclusive-two-request-isolation'),
        counts=counts, cleanup=cleanup)
    if type(private_evidence) is dict:
        private_evidence['diagnostics'] = diagnostics
    if interruption is not None:
        # Preserve exact cancellation identity/args. Only whitelisted diagnostics
        # are added; the parent must not publish the original exception's text.
        interruption.diagnostics = diagnostics
        raise interruption from None
    raise ConcurrencyFailure(diagnostics) from None
