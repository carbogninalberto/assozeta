"""Pure, consumer-specific verifier for the two private connection stream v1s.

Decoded records must come from parent-owned actual producers. This module cannot
establish source/process ownership, writer success or real PostgreSQL evidence.
No imports of Django, sockets, files, services or observer producers.
"""
import copy
from dataclasses import dataclass
from uuid import UUID


class AttributionError(ValueError):
    """Fixed safe error; never include untrusted event values."""


def _need(condition):
    if not condition:
        raise AttributionError('message-concurrency.connection-attribution')


COMMON_COUNTERS = frozenset(('request_starts request_finishes finish_calls active_requests '
    'query_starts query_ends active_queries active_bindings query_errors '
    'connection_violations post_close_events').split())
DJANGO_COUNTERS = COMMON_COUNTERS | {'unattributed_queries', 'request_errors'}
GO_COUNTERS = COMMON_COUNTERS | frozenset(('active_driver_operations unbound_starts unbound_ends '
    'violations late_starts late_ends duplicate_ends duplicate_finishes reused_contexts '
    'missing_contexts foreign_contexts context_mismatches driver_starts driver_ends driver_errors '
    'driver_unbound_starts driver_unbound_ends connect_starts connect_ends connect_errors '
    'reset_starts reset_ends reset_errors ping_starts ping_ends ping_errors reset_probes '
    'driver_violations unknown_driver_kinds invalid_reset_probes').split())
BASE_FIELDS = {'stream', 'version', 'run_id', 'sequence', 'type', 'error', 'complete'}
LIFECYCLE = {'request_starts', 'request_finishes', 'finish_calls', 'active_requests',
             'query_starts', 'query_ends', 'active_queries', 'active_bindings'}
DRIVER = {'active_driver_operations', 'driver_starts', 'driver_ends', 'connect_starts',
          'connect_ends', 'reset_starts', 'reset_ends', 'ping_starts', 'ping_ends', 'reset_probes'}
MAX_EVENTS = 65536


@dataclass(frozen=True)
class Parsed:
    backend: str
    run_id: str
    events: tuple
    requests: frozenset
    finished: frozenset
    active_requests: frozenset
    active_queries: dict  # query ID -> (nullable Django request ID, native PID)
    queries: dict
    finalized: bool


def _driver_counts(event, previous, *, drained):
    # Driver events use the other stream; they can advance between any two
    # connection records. Reconcile all observable totals, without inventing
    # missing driver callbacks or treating intermediate open operations as errors.
    for key in DRIVER - {'active_driver_operations'}:
        _need(event[key] >= previous.get(key, 0))
    starts = sum(event[k + '_starts'] for k in ('connect', 'reset', 'ping'))
    ends = sum(event[k + '_ends'] for k in ('connect', 'reset', 'ping'))
    _need(event['driver_starts'] == starts and event['driver_ends'] == ends)
    _need(all(event[k + '_ends'] <= event[k + '_starts'] for k in ('connect', 'reset', 'ping')))
    _need(event['active_driver_operations'] == starts - ends)
    _need(event['reset_probes'] <= event['reset_starts'])
    if drained:
        _need(starts == ends)


def parse_observation(snapshot, *, final=False):
    """Validate an exact cumulative decoded stream; open prefixes are provisional.

    No caller-supplied valid/complete/binding flags are part of the envelope.
    Go omits absent IDs; Django emits explicit null IDs. Pin both actual schemas.
    """
    _need(type(snapshot) is dict and set(snapshot) == {'backend', 'run_id', 'events'})
    backend, run_id, source = snapshot['backend'], snapshot['run_id'], snapshot['events']
    _need(type(backend) is str and backend in ('go', 'django') and type(run_id) is str)
    try:
        identifier = UUID(run_id)
    except (ValueError, AttributeError):
        raise AttributionError('message-concurrency.connection-attribution') from None
    _need(str(identifier) == run_id and identifier.int != 0)
    _need(type(source) in (list, tuple) and 1 <= len(source) <= MAX_EVENTS)
    counters = GO_COUNTERS if backend == 'go' else DJANGO_COUNTERS
    requests, finished, active, queries, live = set(), set(), set(), {}, {}
    totals = dict.fromkeys(LIFECYCLE, 0)
    unattributed = 0
    footer, previous = False, {}
    # Own copies: callers may reuse/mutate the callback's returned containers.
    events = []
    for sequence, record in enumerate(source, 1):
        _need(type(record) is dict)
        kind = record.get('type')
        _need(type(kind) is str and kind in
              ('header', 'request_begin', 'request_finish', 'query_start', 'query_end', 'footer'))
        _need(not footer and (kind == 'header') == (sequence == 1))
        fields = BASE_FIELDS | counters
        if backend == 'django':
            fields |= {'request_id', 'query_id'}
        elif kind in ('request_begin', 'request_finish', 'query_start', 'query_end'):
            fields |= {'request_id'}
            if kind in ('query_start', 'query_end'):
                fields |= {'query_id'}
        if kind in ('query_start', 'query_end'):
            fields |= {'backend_pid'}
        _need(set(record) == fields)
        _need(record['stream'] == 'query_connection' and type(record['stream']) is str and
              type(record['version']) is int and record['version'] == 1 and
              type(record['sequence']) is int and record['sequence'] == sequence and
              type(record['run_id']) is str and record['run_id'] == run_id)
        _need(type(record['error']) is bool and record['error'] is False and
              type(record['complete']) is bool and record['complete'] == (kind != 'header'))
        _need(all(type(record[key]) is int and 0 <= record[key] < 2 ** 64 for key in counters))
        if kind == 'header':
            _need(all(record[key] == 0 for key in counters))
        nonzero = {'unattributed_queries'} if backend == 'django' else set()
        _need(all(record[key] == 0 for key in counters - LIFECYCLE - DRIVER - nonzero))
        request, query, pid = record.get('request_id'), record.get('query_id'), record.get('backend_pid')
        if kind in ('header', 'footer'):
            _need(request is None and query is None)
        elif kind in ('request_begin', 'request_finish'):
            _need(type(request) is str and query is None)
        else:
            _need((type(request) is str or (backend == 'django' and request is None)) and
                  type(query) is str and type(pid) is int and 0 < pid < 2 ** 32)
        if kind == 'request_begin':
            _need(request == f'{run_id}-{len(requests) + 1}')
            requests.add(request)
            active.add(request)
            totals['request_starts'] += 1
        elif kind == 'request_finish':
            _need(request in active and not any(r == request for r, _ in live.values()))
            active.remove(request)
            finished.add(request)
            totals['request_finishes'] += 1
            totals['finish_calls'] += 1
        elif kind == 'query_start':
            _need((request in active or (backend == 'django' and request is None)) and
                  query == f'{run_id}-q{len(queries) + 1}' and
                  not any(p == pid for _, p in live.values()))
            live[query] = queries[query] = (request, pid)
            totals['query_starts'] += 1
            unattributed += int(request is None)
        elif kind == 'query_end':
            _need((request in active or (backend == 'django' and request is None)) and
                  query in live and live[query] == (request, pid))
            del live[query]
            totals['query_ends'] += 1
        elif kind == 'footer':
            _need(not active and not live and requests == finished)
            footer = True
        totals.update(active_requests=len(active), active_queries=len(live), active_bindings=len(live))
        _need(all(record[key] == totals[key] for key in LIFECYCLE))
        if backend == 'django':
            _need(record['unattributed_queries'] == unattributed)
        if backend == 'go':
            _driver_counts(record, previous, drained=kind in ('header', 'footer'))
        events.append(copy.deepcopy(record))
        previous = record
    _need(not final or footer)
    return Parsed(backend, run_id, tuple(events), frozenset(requests), frozenset(finished),
                  frozenset(active), dict(live), dict(queries), footer)


def require_owned_queries(parsed, owners, *, completed=False, activity=()):
    """Reconcile Django null-request SQL with internally measured owned handles.

    Each role is populated by the probe, never the callback: native PID plus
    repeated self-catalog (PID, backend_start) readings on the same native object.
    end_cursor seals that handle's last SQL before close/return. Finalization can
    recheck these retained measurements without reconnecting to PostgreSQL.
    """
    if parsed.backend != 'django':
        return
    _need(type(owners) is dict and set(owners) == {'default', 'blocker', 'monitor'})
    by_pid, starts = {}, set()
    for record in owners.values():
        _need(type(record) is dict and set(record) == {'native_pid', 'catalogs', 'end_cursor'})
        pid, catalogs, end = record['native_pid'], record['catalogs'], record['end_cursor']
        _need(type(pid) is int and 0 < pid < 2 ** 32 and pid not in by_pid)
        _need(type(catalogs) in (tuple, list) and len(catalogs) >= (2 if completed else 1))
        for row in catalogs:
            _need(type(row) in (tuple, list) and len(row) == 2 and type(row[0]) is int and
                  row[0] == pid and type(row[1]) is str and bool(row[1]))
            _need(tuple(row) == tuple(catalogs[0]))
        _need(catalogs[0][1] not in starts)
        starts.add(catalogs[0][1])
        _need((end is None and not completed) or
              (type(end) is int and 1 <= end <= len(parsed.events)))
        by_pid[pid] = record
    for event in parsed.events:
        if event['type'] not in ('query_start', 'query_end'):
            continue
        pid, request = event['backend_pid'], event.get('request_id')
        # Even sequential HTTP reuse of a parent connection is ambiguous.
        _need((pid in by_pid) == (request is None))
        if request is None:
            end = by_pid[pid]['end_cursor']
            _need(end is None or event['sequence'] <= end)
    for row in activity:
        if row['pid'] in by_pid:
            _need(row['backend'] == by_pid[row['pid']]['catalogs'][0][1])


def extend_observation(previous, snapshot, *, final=False):
    current = parse_observation(snapshot, final=final)
    _need(current.backend == previous.backend and current.run_id == previous.run_id and
          current.events[:len(previous.events)] == previous.events)
    return current


def prefix(parsed, cursor):
    _need(type(cursor) is int and 1 <= cursor <= len(parsed.events))
    return parse_observation(dict(backend=parsed.backend, run_id=parsed.run_id,
                                 events=parsed.events[:cursor]))


def require_baseline(baseline):
    _need(not baseline.finalized and not baseline.active_requests and not baseline.active_queries)
    if baseline.backend == 'go':
        _need(baseline.events[-1]['active_driver_operations'] == 0)


def require_window(baseline, observed, *, completed=False):
    require_baseline(baseline)
    _need(observed.backend == baseline.backend and observed.run_id == baseline.run_id and
          observed.events[:len(baseline.events)] == baseline.events)
    begun = observed.requests - baseline.requests
    _need(len(begun) <= 2 and observed.active_requests <= begun)
    if completed:
        _need(len(begun) == 2 and not observed.active_requests and not observed.active_queries)
    return begun


def bind_waits(baseline, before, after, proofs):
    """Join already validated catalog WaitProofs to active callbacks on both sides.

    Native stream PIDs alone have no transaction identity; the returned tuples
    preserve every catalog incarnation/transaction field for later comparison.
    """
    require_window(baseline, before)
    require_window(baseline, after)
    _need(after.events[:len(before.events)] == before.events and not after.finalized)
    _need(len(proofs) <= 2 and len({p.pid for p in proofs}) == len(proofs))
    bindings = []
    for proof in proofs:
        matches = [(q, r) for q, (r, pid) in before.active_queries.items() if pid == proof.pid]
        _need(len(matches) == 1)
        query, request = matches[0]
        _need(type(request) is str and request in before.active_requests and
              request in after.active_requests and request in before.requests - baseline.requests)
        _need(after.active_queries.get(query) == (request, proof.pid))
        bindings.append((request, query, proof))
    _need(len({r for r, _, _ in bindings}) == len(bindings))
    return tuple(bindings)


def response_ids(responses):
    """Call only after independent original-wire revalidation by the probe."""
    _need(type(responses) in (list, tuple) and len(responses) == 2)
    result = []
    for response in responses:
        headers = response.get('response_headers')
        _need(type(headers) in (list, tuple))
        values = []
        for header in headers:
            _need(type(header) in (list, tuple) and len(header) == 2 and
                  all(type(part) is str for part in header))
            name, value = header
            if name.lower() == 'x-query-observation-id':
                values.append(value)
        _need(len(values) == 1)
        result.append(values[0])
    _need(len(set(result)) == 2)
    return tuple(result)


def verify_lifecycle(baseline, captured, samples, responses, *, final_observation=None):
    """Recompute binding continuity and ends from retained prefixes and catalog IDs.

    Samples are (before_cursor, after_cursor, exact validated WaitProof tuple).
    This never accepts catalog-proof or response-validation success flags.
    """
    require_window(baseline, captured, completed=True)
    observed = (extend_observation(captured, final_observation, final=True)
                if final_observation is not None else captured)
    begun = require_window(baseline, observed, completed=True)
    _need(set(response_ids(responses)) == begun)
    previous, pairs, last_cursor = {}, 0, len(baseline.events)
    for before_cursor, after_cursor, proofs in samples:
        _need(last_cursor <= before_cursor <= after_cursor <= len(captured.events))
        before, after = prefix(captured, before_cursor), prefix(captured, after_cursor)
        bindings = bind_waits(baseline, before, after, proofs)
        current = {proof.pid: (request, query, proof) for request, query, proof in bindings}
        for pid, (old_request, old_query, old) in previous.items():
            _need(pid in current)
            request, query, new = current[pid]
            _need((request, query, new.backend, new.xact, new.vxid) ==
                  (old_request, old_query, old.backend, old.xact, old.vxid) and
                  (old.xid is None or new.xid == old.xid))
        previous = current
        if len(bindings) == 2:
            pairs += 1
        last_cursor = after_cursor
    _need(pairs >= 2 and len(previous) == 2)
    _need({r for r, _, _ in previous.values()} == begun)
    # Parser already matched exactly one identical native PID/query end inside
    # each finished request lifetime, including all work after blocker release.
    _need(all(q not in observed.active_queries and r in observed.finished
              for r, q, _ in previous.values()))
    return dict(requests=2, waiters=2, catalog_samples=pairs,
                stream_finalized=observed.finalized)
