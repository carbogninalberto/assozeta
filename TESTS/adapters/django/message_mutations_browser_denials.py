"""Inert, parent-executed actual HTTP companion; never a browser shortcut.

Parent owns fresh prepared fixtures, services, finite DB/Redis I/O and provenance.
Only calling run_denials opens the existing strict proxy and four HTTP requests.
Private state, tokens and wire bytes are never written or returned.
"""
import copy
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from TESTS.adapters.django import message_mutations_browser as browser
from TESTS.adapters.django import message_mutations_browser_runtime as mutations
from TESTS.adapters.django import message_reads_browser_runtime as shared


class DenialCompanionError(RuntimeError):
    """Fixed public diagnostic; underlying exceptions and data stay private."""


class _Budget:
    def __init__(self, timeout):
        shared.require(type(timeout) is int and 10 <= timeout <= 180, 'denial-timeout')
        self.end = time.monotonic() + timeout

    def remaining(self):
        value = self.end - time.monotonic()
        shared.require(value > 0, 'denial-deadline')
        return min(value, 5)


def _identity(token):
    # Signature, expiry and access-token type are checked against actual Django
    # JWT settings. Decoding an unverified payload is never identity evidence.
    from rest_framework_simplejwt.tokens import AccessToken
    shared.require(isinstance(token, str) and len(token) <= 8192 and
                   re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token),
                   'denial-token')
    return str(AccessToken(token)['user_id'])


def _preflight(version, cases):
    """Check the fixed denial profile before private DB access or HTTP."""
    shared.require(tuple(case['name'] for case in cases) == browser.DENIAL_CASE_NAMES,
                   'denial-authored-order')
    backend = 'django-v1' if version == 'v1' else 'go-v2'
    cids = []
    for number, case in enumerate(cases, 1):
        route = shared.route_target('/api' + case['path'], case['method'],
            version=version, artifacts={}, consumer='mutation-denials')
        shared.require(route == {'kind': 'selected', 'backend': backend, 'path': case['path']},
                       'denial-route')
        action = 'create' if case['method'] == 'POST' else 'delete'
        cid = f'message-mutations-{version}-{case["actor"]}-{action}-{number}'
        shared.validate_correlation(cid, route, 'mutation-denials')
        cids.append(cid)
    return cids


def source_hashes():
    """Parent must independently bind these source hashes to its reviewed inputs."""
    names = set(browser.EXTRA_SOURCES) | {
        'TESTS/adapters/django/message_mutations_browser_denials.py',
        'TESTS/test_message_mutations_browser_denials.py',
        'TESTS/adapters/django/message_reads.py',
        'TESTS/adapters/django/historical_read_cache.py',
        'TESTS/adapters/django/production_runtime.py',
    }
    return {name: shared.sha(shared.ROOT / name) for name in sorted(names)}


def _request(origin, case, token, cid, remote_addr, budget):
    """One real HTTP connection, complete bounded response, no redirect/retry."""
    url = urlsplit(origin)
    shared.require(url.scheme == 'http' and url.hostname == '127.0.0.1' and url.port and
                   not (url.username or url.password or url.path or url.query or url.fragment),
                   'denial-proxy-origin')
    body = json.dumps(case['body']).encode() if 'body' in case else None
    shared.require(case['headers'] == {} and 'raw_body' not in case, 'denial-authored-headers')
    headers = {'Authorization': 'Bearer ' + token, 'X-Correlation-ID': cid,
               'Accept-Language': 'en', 'Accept': 'application/json', 'Connection': 'close'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    connection = http.client.HTTPConnection('127.0.0.1', url.port, timeout=budget.remaining())
    response = None
    try:
        connection.connect()
        transport = connection.sock
        shared.require(transport.getpeername()[:2] == ('127.0.0.1', url.port) and
                       transport.getsockname()[0] == remote_addr, 'denial-client-peer')
        connection.request(case['method'], '/api' + case['path'], body=body, headers=headers)
        transport.settimeout(budget.remaining())
        response = connection.getresponse()
        received = response.getheaders()
        lengths = [v for k, v in received if k.lower() == 'content-length']
        shared.require(len(lengths) == 1 and lengths[0].isdigit() and
            not any(k.lower() == 'transfer-encoding' for k, _ in received) and
            int(lengths[0]) <= shared.MAX_BODY and not 300 <= response.status < 400,
            'denial-response-framing')
        length, data = int(lengths[0]), bytearray()
        while len(data) < length:
            # Proxy emits fixed-length responses. read1 bounds each blocking read
            # and allows checking the total deadline between pieces.
            transport.settimeout(budget.remaining())
            chunk = response.read1(min(65536, length - len(data)))
            shared.require(bool(chunk), 'denial-response-incomplete')
            data.extend(chunk)
        # read1 closes HTTPResponse on consuming its declared length (also handle
        # the zero-length case). No private data is exposed on framing failure.
        response.read(0)
        shared.require(response.length == 0 and response.isclosed(), 'denial-response-incomplete')
        budget.remaining()
        return {'status': response.status, 'wire_body': bytes(data),
                'response_headers': received, 'request_body': body}
    finally:
        # With Connection: close, HTTPConnection relinquishes the socket while
        # HTTPResponse still owns its file. Explicitly close BOTH on every exit,
        # attempting connection cleanup even if response cleanup raises.
        shared.cleanup_all((lambda: response.close() if response is not None else None,
                            connection.close), 'denial-client-cleanup')


def _idle(journal, observer, budget):
    # The client may receive the last byte just before handler.finally decrements
    # active_http. Wait for that real completion rather than excluding the tail.
    while True:
        budget.remaining()
        with journal.lock:
            shared.require(not journal.failures, 'denial-proxy-failed')
            if journal.active_http == 0:
                break
        time.sleep(min(.005, budget.remaining()))
    observer.wait_idle(timeout=budget.remaining(), include_websockets=True)


def _window(journal, observer, offsets, version, expected, port):
    events = journal.snapshot()
    delta = events[offsets['events']:]
    private = journal.private_traffic[offsets['private']:]
    asgi = observer.delta(offsets['asgi'])
    traffic = mutations.observed_traffic(private, delta, asgi, version=version, consumer='mutation-denials')
    shared.require([(row['method'], row['path'], row['cid']) for row in delta] == expected and
                   len(traffic) == len(expected), 'denial-exact-traffic-window')
    shared.require(all(row['peer'] == row['intended_peer'] == ['127.0.0.1', port]
                       for row in delta), 'denial-backend-peer')
    offsets.update(events=len(events), private=len(journal.private_traffic),
                   asgi=offsets['asgi'] + len(asgi))
    return traffic


def _bind_response(client, proof):
    expected = shared.clean_headers(proof['response_headers'], response=True) + [
        ('Content-Length', str(len(proof['wire_body']))),
        ('X-Fixture-Backend', proof['proxy']['backend']), ('Connection', 'close')]
    normalize = lambda headers: [(key.lower(), value) for key, value in headers]
    shared.require(client['status'] == proof['status'] and
                   client['wire_body'] == proof['wire_body'] and
                   client['request_body'] == proof['request_body'] and
                   normalize(client['response_headers']) == normalize(expected),
                   'denial-client-private-binding')


def run_denials(*, version, apps, raw, fixture, observer, django_port, go_port,
                process_guard, remote_addr, timeout=120):
    """Verify all FOUR authored denials on ONE fresh parent-prepared version.

    Call separately for v1/v2, before successful deletes, outside any browser run.
    This function never resets/reseeds/restores fixtures or owns backend services.
    No UI files are served (empty artifact allowlist); no UI build is needed here.
    Parent must bound synchronous DB/Redis I/O and independently review provenance.
    Exceptions expose only a fixed stage; success is returned only after cleanup.
    """
    journal, tokens = None, {}
    stage = 'inputs'
    try:
        from TESTS.adapters.django.message_mutations import require_private_database
        budget = _Budget(timeout)
        shared.require(version in ('v1', 'v2') and callable(process_guard) and
            isinstance(observer, shared.CompleteAsgiObserver) and
            all(type(port) is int and 0 < port < 65536 for port in (django_port, go_port)) and
            django_port != go_port and str(ipaddress.ip_address(remote_addr)) == '127.0.0.1',
            'denial-inputs')
        stage = 'route-preflight'
        cases = browser.authored_spec()['denials']
        cids = _preflight(version, cases)
        stage = 'environment'
        # Even private database connection access follows pure route preflight.
        require_private_database()  # Includes production interpreter and __debug__.
        stage = 'identity'
        for case in cases:
            token = fixture['tokens'][case['actor']]
            shared.require(_identity(token) == case['audit_actor'], 'denial-signed-identity')
            tokens[case['actor']] = token
        shared.require(len(set(tokens.values())) == len(tokens), 'denial-token-alias')
        hashes = source_hashes()
        stage = 'baseline'
        guard = copy.deepcopy(process_guard())
        shared.require(isinstance(guard, dict) and guard.get('running') is True and
            type(guard.get('pid')) is int and guard['pid'] > 0 and guard.get('instance') is not None,
            'denial-live-process')
        observer.wait_idle(timeout=budget.remaining(), include_websockets=True)
        start_cursor = observer.cursor()
        websocket_baseline = (observer.websocket_started, observer.websocket_completed)
        baseline = shared.durable_snapshot(apps, raw)
        shared.require(len(baseline['models']) == 128, 'denial-full-models')
        browser.shared.assert_cache_unchanged(baseline['cache'], baseline['cache'], fixture['contrasts'])
        # Nonvacuous targets must already exist before the first HTTP request.
        targets = {browser.contract.scalar(row['message_id']) for row in
                   baseline['models'][browser.contract.MESSAGE]}
        shared.require(all(case['method'] != 'DELETE' or case['path'].split('/')[-2] in targets
                           for case in cases), 'denial-fresh-targets')
        journal = shared.ProxyJournal()
        offsets = {'events': 0, 'private': 0, 'asgi': start_cursor}
        selected_port = django_port if version == 'v1' else go_port
        receipts = []

        def stable(state):
            budget.remaining()
            shared.require(process_guard() == guard, 'denial-process-changed')
            shared.require(len(state['models']) == 128 and
                           set(state['models']) == set(baseline['models']), 'denial-full-schema')
            browser.shared.assert_state_unchanged(baseline['models'], state['models'])
            browser.shared.assert_cache_unchanged(baseline['cache'], state['cache'], fixture['contrasts'])
            shared.require(websocket_baseline ==
                (observer.websocket_started, observer.websocket_completed), 'denial-extra-websocket')

        stage = 'proxy'
        # Fixed Python-only denial profile; no browser/static/coexistence routes.
        with shared.owned_http_server(shared.proxy_handler(ui=Path(shared.ROOT / 'UI'), artifacts={},
                version=version, django_port=django_port, go_port=go_port,
                journal=journal, consumer='mutation-denials'), journal=journal) as origin:
            for case, cid in zip(cases, cids, strict=True):
                stage = 'before-request'
                _idle(journal, observer, budget)
                with journal.checkpoint():
                    before = shared.durable_snapshot(apps, raw)
                    stable(before)
                    _window(journal, observer, offsets, version, [], selected_port)
                    identity = _identity(tokens[case['actor']])
                    shared.require(identity == case['audit_actor'], 'denial-signed-identity')
                    started = time.time()
                stage = 'http'
                client = _request(origin, case, tokens[case['actor']], cid, remote_addr, budget)
                stage = 'verify'
                _idle(journal, observer, budget)
                with journal.checkpoint():
                    after = shared.durable_snapshot(apps, raw)
                    finished = time.time()
                    stable(after)
                    proof, = _window(journal, observer, offsets, version,
                        [(case['method'], case['path'], cid)], selected_port)
                    _bind_response(client, proof)
                    verdict = browser.verify_denial(case['name'], before=before['models'], after=after['models'],
                        cache_before=before['cache'], cache_after=after['cache'], contrasts=fixture['contrasts'],
                        response=proof, observed=proof['django_events'], version=version,
                        authenticated_user_id=identity, process_before=guard,
                        process_after=copy.deepcopy(process_guard()), remote_addr=remote_addr,
                        started=started, finished=finished)
                    shared.require(verdict == {'verified': True, 'case': case['name'],
                        'status': case['status'], 'source': 'parent-http'}, 'denial-verifier-result')
                    receipts.append(dict(verdict,
                        response_sha256=hashlib.sha256(proof['body']).hexdigest(),
                        wire_sha256=hashlib.sha256(proof['wire_body']).hexdigest(),
                        state_sha256=browser.shared.digest(after['models']), cache_sha256=after['cache'].digest))
                client = proof = before = after = None
            stage = 'proxy-cleanup'
        stage = 'final-window'
        _idle(journal, observer, budget)
        with journal.checkpoint():
            shared.require(hashes == source_hashes(), 'denial-source-drift')
            _idle(journal, observer, budget)
            _window(journal, observer, offsets, version, [], selected_port)
            # Reconcile the entire window through shutdown and source hashing.
            mutations.observed_traffic(journal.private_traffic, journal.snapshot(),
                observer.delta(start_cursor), version=version, consumer='mutation-denials')
            # Hashing and reconciliation must precede fresh final state evidence:
            # neither an earlier snapshot nor an earlier guard can close this tail.
            final = shared.durable_snapshot(apps, raw)
            _idle(journal, observer, budget)
            with observer.condition:
                shared.require(observer.active == observer.active_websockets ==
                               observer.websocket_errors == 0, 'denial-final-asgi-idle')
                # Include traffic from the final snapshot; prevent new ASGI scopes
                # while checking its counters and the actual parent process guard.
                _window(journal, observer, offsets, version, [], selected_port)
                shared.require(len(journal.events) == len(journal.private_traffic) == len(cases) == 4,
                               'denial-total-traffic')
                stable(final)
        return {'success': True, 'version': version, 'http_denials_verified': True,
                'cases': receipts, 'models': 128, 'proxy_cleanup_verified': True,
                'source_sha256': hashes, 'fixture_cleanup_owner': 'parent',
                'browser_acceptance': False, 'complete_message_family_parity': False}
    except Exception:
        raise DenialCompanionError('denial-companion-' + stage) from None
    finally:
        tokens.clear()
        if journal is not None:
            with journal.lock:
                journal.private_traffic.clear()
