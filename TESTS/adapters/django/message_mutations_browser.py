"""Inert authored saved-message UI consumer contract; parent owns live resources.

No production implementation imports, fixture writes, HTTP, browser or services.
Raw checkpoint inputs are private. Acknowledgments and finish receipts are public.
"""
import copy
from datetime import datetime, timezone
import hashlib
import html
import json
import re
from urllib.parse import urlsplit

from TESTS.contracts import test_message_mutations as contract
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_reads_browser as reads

ACTORS = reads.ACTORS
READERS = ACTORS[:-1]
# READERS retains the shared runtime's existing three mutation-target actors.
# Restricted has frontend read visibility, but the unmapped backend LIST denies.
ACTIONS = ('login', 'read', 'create', 'create_reload', 'delete', 'delete_reload', 'closed')
CHECKPOINTS = tuple((actor, action) for actor in ACTORS
                    for action in (ACTIONS if actor in READERS else ('login', 'read', 'closed')))
RESPONSE_COUNTS = {'login': 4, 'list': 16, 'create': 3, 'delete': 3}
LIST = '/communications/messages/list'
LOGIN = '/oauth2/login'
LIST_DENIAL = {'detail': "Access denied: No permission mapping for endpoint 'communications/messages/list'. Contact administrator to grant access."}
LIST_ERROR = 'Impossibile caricare i dati.'
DENIAL_CASE_NAMES = ('message-mutations-create-restricted', 'message-mutations-delete-restricted',
                     'message-mutations-delete-foreign', 'message-mutations-delete-foreign-owner-denied-own')
OWNED = ('TESTS/browser/message-mutations.mjs', 'TESTS/browser/message-mutations.test.mjs',
         'TESTS/adapters/django/message_mutations_browser.py', 'TESTS/test_message_mutations_browser.py',
         'docs/architecture/go-migration/message_mutations_browser.md')
EXTRA_SOURCES = (*OWNED, 'TESTS/contracts/test_message_mutations.py', 'TESTS/fixtures/message_mutations.json',
                'TESTS/adapters/django/message_mutations.py', 'TESTS/adapters/django/message_read_scopes.py',
                'TESTS/adapters/django/message_reads_browser_runtime.py',
                'TESTS/browser/message-reads-runner.mjs',
                'TESTS/test_message_reads_browser_runtime.py',
                'TESTS/adapters/django/message_mutations_browser_runtime.py',
                'TESTS/test_message_mutations_browser_runtime.py')
need = contract.require


def authored_spec():
    source = contract.load_source()
    ids = source['ids']
    cases = {case['name']: case for case in source['cases']}
    actors = {}
    for actor, delete_name in zip(READERS, ('email', 'full', 'foreign-owner'), strict=True):
        association = ids['foreign_association' if actor == 'foreign_owner' else 'own_association']
        # UI constraints are narrower than the HTTP serializer (20/5 min chars).
        body = {'type': 'EMAIL', 'subject': 'Saved browser ' + actor,
                'message': 'Saved through the existing message form by ' + actor + '.'}
        create = copy.deepcopy(cases['message-mutations-create-email'])
        create.update(name='message-browser-create-' + actor, actor=actor, body=body,
                      audit_actor=ids[actor], audit_additional=None,
                      effect={'kind': 'create', 'values': dict(body, sport_association_id=association)})
        delete = copy.deepcopy(cases['message-mutations-delete-' + delete_name])
        delete['path'] = '/communications/messages/' + delete['effect']['message_id'] + '/delete'
        delete.pop('raw_body', None)
        delete['headers'] = {}
        actors[actor] = {'user_id': ids[actor], 'association_id': association,
                         'create': create, 'delete': delete}
    actors['restricted_collaborator'] = {'user_id': ids['restricted_collaborator'],
                                        'association_id': ids['own_association']}
    scopes = {shared._uid(row['number']): (ids['foreign_association'] if row['association'] == 'foreign'
               else ids['own_association'], row['type']) for row in shared.MESSAGE_SEED_RECORDS}
    scopes[ids['admin_message']] = (ids['admin_association'], 'EMAIL')
    for label in ('delete_email', 'delete_post', 'delete_legacy', 'delete_full', 'delete_admin',
                  'delete_owner_session', 'fault_delete', 'delete_foreign_owner', 'delete_foreign_admin'):
        scopes[ids[label]] = (ids['foreign_association'] if label.startswith('delete_foreign_')
                             else ids['own_association'], 'LEGACY_SMS' if label == 'delete_legacy'
                             else 'INSIDE_APP' if label == 'delete_post' else 'EMAIL')
    for name in ACTORS:
        actors[name]['forbidden_ids'] = sorted(identifier for identifier, (association, kind) in scopes.items()
            if association != actors[name]['association_id'] or kind not in ('EMAIL', 'INSIDE_APP'))
    return {'ids': copy.deepcopy(ids), 'actors': actors,
            'denials': [copy.deepcopy(cases[name]) for name in DENIAL_CASE_NAMES]}


def browser_input(*, version, origin, identities, provenance):
    """Private form credentials only. No tokens/storage state accepted."""
    url = urlsplit(origin)
    need(version in ('v1', 'v2') and url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost')
         and url.port and not (url.username or url.password or url.path or url.query or url.fragment), 'input.origin')
    need(set(identities) == set(ACTORS), 'input.actors')
    spec = authored_spec()
    actors = {}
    for name in ACTORS:
        identity = identities[name]
        need(set(identity) == {'username', 'password', 'user_id'} and
             all(isinstance(v, str) and v for v in identity.values()) and
             identity['user_id'] == spec['actors'][name]['user_id'], 'input.identity')
        actors[name] = dict(identity)
        actors[name]['forbidden_ids'] = spec['actors'][name]['forbidden_ids']
        if name in READERS:
            actors[name].update(create_body=spec['actors'][name]['create']['body'],
                                delete_id=spec['actors'][name]['delete']['effect']['message_id'],
                                forbidden_ids=spec['actors'][name]['forbidden_ids'])
    return {'version': version, 'origin': origin, 'actors': actors,
            'artifact_sha256': copy.deepcopy(provenance['artifact_sha256'])}


def verify_response(case, row):
    """Validate complete independently observed bytes, not a replayed status."""
    need(row['method'] == case['method'] and row['path'] == case['path'], 'response.method-path')
    need(type(row['status']) is int and row['status'] == case['status'] and row['complete'] is True,
         'response.status-completion')
    need(isinstance(row['body'], bytes), 'response.bytes')
    try:
        body = json.loads(row['body'])
    except (ValueError, UnicodeError):
        raise AssertionError('response.json') from None
    need(type(body) is type(case['response']) and body == case['response'], 'response.body')
    headers = {k.lower(): v for k, v in row['headers'].items()}
    need(headers.get('content-type', '').split(';')[0] == 'application/json', 'response.type')
    need(headers.get('content-language') == 'en', 'response.language')
    allowed = {'POST', 'OPTIONS'} if case['method'] == 'POST' else {'DELETE', 'OPTIONS'}
    need(set(headers.get('allow', '').replace(' ', '').split(',')) == allowed, 'response.allow')
    need(headers.get('set-cookie') is None and headers.get('www-authenticate') is None, 'response.headers')


def verify_effect(case, before, after, **window):
    # Preserve the independently authored complete managed-state oracle, including
    # cascading deliveries, CREATE/UPDATE history, all audit/index fields and actor.
    return contract.assert_effect(case, before, after, **window)


def expected_list(models, association):
    """Screen projection only AFTER exact state verification; never a state oracle.

    Tied timestamps are unordered in the existing API, so compare membership and
    descending timestamp groups, then compare DOM against that actual valid order.
    """
    result = []
    for raw in models[contract.MESSAGE]:
        row = contract.row_values(raw)
        if row['sport_association_id'] != association or row['type'] not in ('EMAIL', 'INSIDE_APP'):
            continue
        result.append({'message_id': row['message_id'], 'sport_association': association,
                       'type': row['type'], 'message': html.escape(str(row['message'])),
                       'subject': html.escape(str(row['subject'])),
                       'created_at': row['created_at']})
    return result


def verify_view(view, models, association):
    need(isinstance(view, dict) and set(view) == {'response', 'rows', 'absent_ids',
         'create_control', 'delete_enabled', 'create_controls', 'delete_controls',
         'sidebar_visible', 'hash'}, 'view.schema')
    expected = expected_list(models, association)
    rows = view['response']
    need(isinstance(rows, list) and len(rows) == len(expected) and
         all(isinstance(row, dict) and set(row) == {'message_id', 'sport_association', 'type',
             'message', 'subject', 'created_at'} for row in rows), 'view.count-schema')
    keyed = lambda values: {row['message_id']: row for row in values}
    need(len(keyed(rows)) == len(rows) and keyed(rows) == keyed(expected), 'view.exact-membership')
    # PostgreSQL DESC has NULLS FIRST; ISO UTC values are compared as instants.
    times = [(row['created_at'] is None, datetime.fromisoformat(row['created_at'].replace('Z', '+00:00')).timestamp()
              if row['created_at'] else 0) for row in rows]
    need(times == sorted(times, reverse=True), 'view.order')
    need(view['rows'] == [reads.visible_row(row) for row in rows], 'view.rendered-contents')
    need(view['sidebar_visible'] is True and view['hash'] == '#/communication/messages', 'view.navigation')
    need(view['create_control'] is True and view['delete_enabled'] is True and
         type(view['create_controls']) is int and view['create_controls'] == 2 and
         type(view['delete_controls']) is int and view['delete_controls'] == len(rows), 'view.controls')


def verify_denied_view(view):
    """Closed DOM projection of BKNDatatable's actual failed-load branch."""
    need(isinstance(view, dict) and set(view) == {'response', 'rows', 'error_text',
         'error_visible', 'create_controls', 'delete_controls', 'enabled_delete_controls',
         'sidebar_visible', 'hash'}, 'denied-view.schema')
    need(view['response'] == LIST_DENIAL and view['rows'] == [], 'denied-view.body-rows')
    need(view['error_visible'] is True and view['error_text'] == LIST_ERROR, 'denied-view.error')
    need(view['sidebar_visible'] is True and view['hash'] == '#/communication/messages',
         'denied-view.navigation')
    need(all(type(view[key]) is int and view[key] == 0 for key in
             ('create_controls', 'delete_controls', 'enabled_delete_controls')), 'denied-view.controls')


def public_failure(_error):
    return {'success': False, 'error': 'checkpoint-verification-failed'}


class CheckpointVerifier:
    """Ordered parent checkpoint ledger; instantiate afresh for each v1/v2 fixture.

    Pass initial_models/cache before opening the first login. Each observe call
    needs independently captured traffic, process evidence and snapshot window.
    Exceptions stay private; use public_failure at the RPC boundary. A failed
    observation poisons the ledger: retry cannot erase failed evidence.
    """
    def __init__(self, *, version, contrasts, initial_models=None, initial_cache=None):
        need(version in ('v1', 'v2'), 'version')
        self.version, self.contrasts = version, contrasts
        self.spec = authored_spec()
        self.previous, self.cache = copy.deepcopy(initial_models), initial_cache
        self.verified, self.failed, self.guard = 0, False, None
        self.requests, self.acks = [], []

    def observe(self, payload, *, models, cache, traffic, started, finished, remote_addr,
                process_before=None, process_after=None):
        try:
            return self._observe(payload, models=models, cache=cache, traffic=traffic,
                                 started=started, finished=finished, remote_addr=remote_addr,
                                 process_before=process_before, process_after=process_after)
        except BaseException:
            self.failed = True
            raise

    def _observe(self, payload, *, models, cache, traffic, started, finished, remote_addr,
                 process_before, process_after):
        need(not self.failed and self.verified < len(CHECKPOINTS), 'checkpoint.closed')
        need(isinstance(payload, dict), 'checkpoint.payload')
        expected_action = CHECKPOINTS[self.verified][1]
        fields = {'actor', 'action', 'requests'} | ({'identity'} if expected_action == 'login'
                 else set() if expected_action == 'closed' else {'view'})
        need(set(payload) == fields and isinstance(payload['requests'], list), 'checkpoint.payload')
        actor, action = payload['actor'], payload['action']
        need((actor, action) == CHECKPOINTS[self.verified], 'checkpoint.order')
        need(self.previous is not None and len(self.previous) == len(models) == 128 and
             set(self.previous) == set(models), 'checkpoint.full-schema')
        need(started <= finished and isinstance(remote_addr, str) and bool(remote_addr), 'checkpoint.window')
        need(isinstance(process_before, dict) and process_before.get('running') is True and
             type(process_before.get('pid')) is int and process_before['pid'] > 0 and
             process_before.get('instance') is not None and process_after == process_before, 'checkpoint.process')
        need(self.guard is None or self.guard == process_before, 'checkpoint.process-changed')
        expected = self.spec['actors'][actor]
        if action == 'login':
            plan = [('POST', LOGIN)]
        elif action == 'closed':
            plan = []
        else:
            plan = ([(expected[action]['method'], expected[action]['path'])]
                    if action in ('create', 'delete') else []) + [('GET', LIST)]
        need([(row['method'], row['path']) for row in traffic] == plan, 'checkpoint.traffic-plan')
        need(len(payload['requests']) == len(traffic), 'checkpoint.observed-requests')
        backend = 'django-v1' if self.version == 'v1' else 'go-v2'
        for proof, browser in zip(traffic, payload['requests'], strict=True):
            need(proof['complete'] is True and isinstance(proof['body'], bytes), 'checkpoint.body-complete')
            need(browser == {key: proof[key] for key in ('cid', 'method', 'path', 'status')} |
                 {'body_sha256': hashlib.sha256(proof['body']).hexdigest()}, 'checkpoint.browser-binding')
            cid = proof['cid']
            need(re.fullmatch(f'message-mutations-{self.version}-{actor}-{action}-[1-9][0-9]*', cid)
                 and cid not in [row['cid'] for row in self.requests], 'checkpoint.correlation')
            need(proof['proxy'] == {key: proof[key] for key in ('cid', 'method', 'path', 'status')} |
                 {'backend': backend, 'fallback': False}, 'checkpoint.proxy-attribution')
            shared.assert_attribution('django' if self.version == 'v1' else 'go',
                                      proof['method'], proof['path'], proof['status'], proof['django_events'], cid)
        if action == 'login':
            from TESTS.adapters.django.message_reads_browser_runtime import validate_login_projection
            validate_login_projection({'models': self.previous}, {'models': models}, expected['user_id'],
                                      datetime.fromtimestamp(started, timezone.utc),
                                      datetime.fromtimestamp(finished, timezone.utc))
            login = json.loads(traffic[0]['body'])
            need(traffic[0]['status'] == 200 and login['user_data']['user_id'] == expected['user_id'] and
                 login['user_data']['requires_welcome'] is False and bool(login.get('access_token')), 'login.identity')
            need(payload['identity'] == {'user_id': expected['user_id'], 'requires_welcome': False}, 'login.browser-identity')
            # Login is allowed its existing cache effects. Parent keeps both raw
            # snapshots privately; all subsequent checkpoints compare full DB1.
        else:
            shared.assert_cache_unchanged(self.cache, cache, self.contrasts)
            if action in ('create', 'delete'):
                case, mutation = expected[action], traffic[0]
                verify_response(case, mutation)
                sent = mutation['request_body']
                if action == 'create':
                    need(json.loads(sent) == case['body'], 'request.authored-create')
                else:
                    need(sent in (None, b'', ''), 'request.delete-body')
                verify_effect(case, self.previous, models, started=started, finished=finished,
                              remote_addr=remote_addr, cid=mutation['cid'])
            else:
                shared.assert_state_unchanged(self.previous, models)
            if action != 'closed':
                listing = traffic[-1]
                need(isinstance(payload['view'], dict) and 'response' in payload['view'], 'view.schema')
                need(json.loads(listing['body']) == payload['view']['response'], 'view.body-binding')
                if actor == 'restricted_collaborator':
                    need(action == 'read' and listing['status'] == 403, 'denied-view.status')
                    need(listing['request_body'] in (None, b'', ''), 'denied-view.request-body')
                    headers = {key.lower(): value for key, value in listing['headers'].items()}
                    need(headers.get('content-type', '').split(';')[0] == 'application/json',
                         'denied-view.content-type')
                    verify_denied_view(payload['view'])
                else:
                    need(listing['status'] == 200, 'view.status')
                    verify_view(payload['view'], models, expected['association_id'])
                    # Each successful actor must lack ALL still-existing foreign rows.
                    absent = sorted(contract.scalar(row['message_id']) for row in models[contract.MESSAGE]
                                    if contract.scalar(row['sport_association_id']) != expected['association_id']
                                    or contract.scalar(row['type']) not in ('EMAIL', 'INSIDE_APP'))
                    need(payload['view']['absent_ids'] == absent, 'view.foreign-hidden')
        self.previous, self.cache = copy.deepcopy(models), cache
        self.guard = copy.deepcopy(process_before)
        self.requests.extend(copy.deepcopy(payload['requests']))
        self.acks.append({'actor': actor, 'action': action})
        self.verified += 1
        return {'success': True, 'checkpoint': self.verified}

    def finish(self, receipt, *, provenance_before=None, provenance_after=None, build_observation=None):
        need(not self.failed and self.verified == len(CHECKPOINTS), 'checkpoint.incomplete')
        need(len(self.requests) == sum(RESPONSE_COUNTS.values()) and
             {category: sum(row['path'] == path for row in self.requests) for category, path in
              (('login', LOGIN), ('list', LIST), ('create', '/communications/messages/add'))} ==
             {key: RESPONSE_COUNTS[key] for key in ('login', 'list', 'create')} and
             sum(row['method'] == 'DELETE' for row in self.requests) == RESPONSE_COUNTS['delete'],
             'receipt.response-counts')
        need(receipt['success'] is True and receipt['version'] == self.version and
             receipt['contexts_closed'] is True and receipt['contexts_created'] == len(ACTORS) and
             receipt['failures'] == [] and receipt['checkpoints'] == self.acks and
             receipt['requests'] == self.requests, 'receipt.fabricated-or-incomplete')
        for key, value in (('browser_owned_by_parent', True), ('service_workers_blocked', True),
                           ('business_responses_mocked', False), ('authentication_state_seeded', False),
                           ('all_response_bodies_complete', True), ('external_integrations_excluded', True),
                           ('font_parity_excluded', True), ('complete_message_family_parity', False)):
            need(receipt.get(key) is value, 'receipt.' + key)
        need(receipt.get('transport') == reads.TRANSPORT, 'receipt.transport')
        validate_provenance(receipt, provenance_before, provenance_after, build_observation)
        return {'success': True, 'version': self.version, 'checkpoints_verified': self.verified,
                'selected_requests_verified': len(self.requests), 'complete_message_family_parity': False,
                'http_denials_verified': False}


def capture_provenance(artifact, *, root=reads.ROOT):
    proof = reads.capture_provenance(artifact, root=root)
    from pathlib import Path
    from TESTS.adapters.django.message_reads_runtime import source_hashes
    proof['source_sha256'].update(source_hashes(root=root))
    for name in EXTRA_SOURCES:
        proof['source_sha256'][name] = hashlib.sha256((Path(root) / name).read_bytes()).hexdigest()
    return proof


def validate_provenance(receipt, before, after, build):
    need(isinstance(before, dict) and isinstance(after, dict) and isinstance(build, dict), 'provenance.required')
    reads._validate_provenance(before, after)
    need(set(EXTRA_SOURCES) <= set(before['source_sha256']), 'provenance.consumer-sources')
    need(build.get('invocation') and all(isinstance(v, str) and v for v in build['invocation']) and
         re.fullmatch('[a-f0-9]{64}', build.get('record_sha256', '')), 'provenance.build-record')
    need(build.get('source_sha256') == {k: v for k, v in before['source_sha256'].items() if k.startswith('UI/')}
         and build.get('artifact_sha256') == before['artifact_sha256'], 'provenance.build-binding')
    need(receipt.get('artifact_sha256') == before['artifact_sha256'], 'provenance.artifact-binding')
    files = set(receipt['static_verified'])
    need('index.html' in files and files <= set(before['artifact_sha256']), 'provenance.served-static')
    for maps in before['workflow_source_maps'].values():
        need(any(set(before['source_map_bindings'][name]) <= files for name in maps), 'provenance.served-executable')


def verify_denial(case_name, *, before, after, cache_before, cache_after, contrasts,
                  response, observed, version, authenticated_user_id,
                  process_before, process_after, remote_addr, started, finished):
    """Separate parent actual-HTTP evidence; NEVER a hidden browser API shortcut.

    Call before successful deletes using a separate fresh fixture. The UI cannot
    submit these cases. Portable response/effect/attribution/cache oracles remain
    independent. Parent must retain matching proxy/process/identity evidence too.
    """
    cases = {case['name']: case for case in authored_spec()['denials']}
    need(case_name in cases and len(before) == len(after) == 128 and version in ('v1', 'v2'), 'denial.case-schema')
    case = cases[case_name]
    need(authenticated_user_id == case['audit_actor'], 'denial.transport-identity')
    need(isinstance(process_before, dict) and process_before.get('running') is True and
         type(process_before.get('pid')) is int and process_before['pid'] > 0 and
         process_before.get('instance') is not None and process_before == process_after, 'denial.process')
    need(response['proxy'] == {key: response[key] for key in ('cid', 'method', 'path', 'status')} |
         {'backend': 'django-v1' if version == 'v1' else 'go-v2', 'fallback': False}, 'denial.proxy')
    user = [row for row in before['application.User'] if contract.scalar(row['user_id']) == authenticated_user_id]
    need(len(user) == 1, 'denial.user-exists')
    if case['method'] == 'DELETE':
        identifier = case['path'].split('/')[-2]
        target = [contract.row_values(row) for row in before[contract.MESSAGE]
                  if contract.scalar(row['message_id']) == identifier]
        need(len(target) == 1, 'denial.live-target')
        association = authored_spec()['actors'][case['actor']]['association_id']
        need((target[0]['sport_association_id'] == association) == (case['status'] == 403), 'denial.ownership')
        need(response['request_body'] in (None, b'', ''), 'denial.delete-body')
    else:
        need(json.loads(response['request_body']) == case['body'], 'denial.request-body')
    verify_response(case, response)
    shared.assert_attribution('django' if version == 'v1' else 'go', case['method'], case['path'],
                              case['status'], observed, response['cid'])
    shared.assert_cache_unchanged(cache_before, cache_after, contrasts)
    verify_effect(case, before, after, started=started, finished=finished,
                  remote_addr=remote_addr, cid=response['cid'])
    return {'verified': True, 'case': case_name, 'status': case['status'], 'source': 'parent-http'}
