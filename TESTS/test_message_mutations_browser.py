"""Offline negative controls only; no browser, HTTP, Django or services."""
import copy
import json
from datetime import datetime, timezone
from types import MappingProxyType
import unittest

from TESTS.adapters.django import message_mutations_browser as browser
from TESTS import test_message_mutations as effect_controls
from TESTS.adapters.django import message_reads_browser as reads
from TESTS import test_message_reads_browser as read_controls
from TESTS.adapters.django.historical_read_cache import (
    HistoricalReadCacheContrasts, HistoricalReadCacheSnapshot, HistoricalReadCacheEntry,
    PERSISTENT_CONTRAST_KEY, EXPIRING_CONTRAST_KEY,
)

START, END = 1793008800, 1793008801
STAMP = '2026-10-26T10:00:00Z'
LIST_DENIAL = {'detail': "Access denied: No permission mapping for endpoint 'communications/messages/list'. Contact administrator to grant access."}
LIST_ERROR = 'Impossibile caricare i dati.'


def cache_control():
    contrasts = HistoricalReadCacheContrasts(PERSISTENT_CONTRAST_KEY, EXPIRING_CONTRAST_KEY,
        2000000000000, b'persistent-dump', b'expiring-dump')
    snapshot = HistoricalReadCacheSnapshot(MappingProxyType({
        PERSISTENT_CONTRAST_KEY: HistoricalReadCacheEntry(b'string', b'persistent-dump', -1),
        EXPIRING_CONTRAST_KEY: HistoricalReadCacheEntry(b'string', b'expiring-dump', 2000000000000)}))
    return snapshot, contrasts


def state_control():
    """Explicit synthetic state, never a runtime fixture or observation."""
    spec = browser.authored_spec()
    _, baseline, _ = effect_controls.FullStateProjectionControls().create_states()
    baseline.pop('unrelated.Sentinel')
    baseline['application.User'] = [{'user_id': {'type': 'uuid', 'value': spec['actors'][actor]['user_id']},
        'email': actor + '@example.test', 'username': actor.upper(), 'password': 'pbkdf2_sha256$offline',
        'last_login': None} for actor in browser.ACTORS]
    for index, actor in enumerate(browser.READERS):
        target = spec['actors'][actor]['delete']['effect']['message_id']
        baseline['communications.Message'].append({'message_id': target, 'type': 'EMAIL',
            'sport_association_id': spec['actors'][actor]['association_id'], 'subject': 'Target subject',
            'message': 'Historical target message with deliveries', 'created_at': STAMP})
        baseline['communications.MessageTransaction'].extend([
            {'message_transaction_id': str(index * 2 + n), 'message_id': target, 'recipient': 'offline@example.test'}
            for n in (1, 2)])
        for action in (0, 1):
            number = 100 + index * 2 + action
            baseline['auditlog.LogEntry'].append({'id': number, 'object_pk': target,
                'content_type_id': 30, 'action': action, 'sentinel': 'Preserve every historical field'})
            baseline['application.AuditLogIndex'].append({'audit_log_index_id': f'00000000-0000-4000-8000-{number:012d}',
                'log_entry_id': number, 'sport_association_id': spec['actors'][actor]['association_id']})
    baseline['communications.Message'].sort(key=lambda r: r['message_id'])
    sentinel = spec['ids']['foreign_email']
    baseline['communications.Message'].append({'message_id': sentinel, 'type': 'EMAIL',
        'sport_association_id': spec['ids']['foreign_association'], 'subject': 'Foreign sentinel',
        'message': 'Preserve the unrelated delivery', 'created_at': STAMP})
    baseline['communications.Message'].sort(key=lambda r: r['message_id'])
    baseline['communications.MessageTransaction'].append({'message_transaction_id': 'sentinel', 'message_id': sentinel})
    for n in range(128 - len(baseline)):
        baseline['offline.Model' + str(n)] = [{'id': n, 'sentinel': 'unchanged'}]
    return baseline


def mutate_control(before, case, cid):
    """Author synthetic event fields using the existing offline shape fixture."""
    after = copy.deepcopy(before)
    _, _, shape = effect_controls.FullStateProjectionControls().create_states()
    audit = copy.deepcopy(shape['auditlog.LogEntry'][0])
    index = copy.deepcopy(shape['application.AuditLogIndex'][0])
    number = max(row['id'] for row in before['auditlog.LogEntry']) + 1
    if case['effect']['kind'] == 'create':
        identifier = f'00000000-0000-4000-8000-{number:012d}'
        message = dict(case['body'], message_id=identifier,
                       sport_association_id=case['effect']['values']['sport_association_id'], created_at=STAMP)
        after['communications.Message'].append(message)
        after['communications.Message'].sort(key=lambda r: r['message_id'])
        action = 0
    else:
        identifier = case['effect']['message_id']
        message = next(row for row in before['communications.Message'] if row['message_id'] == identifier)
        after['communications.Message'] = [row for row in after['communications.Message'] if row['message_id'] != identifier]
        after['communications.MessageTransaction'] = [row for row in after['communications.MessageTransaction'] if row['message_id'] != identifier]
        action = 2
    actor = next(row for row in before['application.User'] if row['user_id']['value'] == case['audit_actor'])
    values = {'message_id': identifier, 'type': message['type'], 'message': message['message'],
              'subject': message['subject'], 'sport_association': message['sport_association_id'],
              'created_at': '2026-10-26 10:00:00'}
    audit.update(id=number, object_pk=identifier, object_repr=f'Message object ({identifier})', action=action,
        actor_id=case['audit_actor'], actor_email=actor['email'], cid=cid,
        changes={key: ['None', value] if action == 0 else [value, 'None'] for key, value in values.items()})
    index.update(audit_log_index_id=f'00000000-0000-4000-8001-{number:012d}', log_entry_id=number,
                 sport_association_id=message['sport_association_id'],
                 resolution_path='direct' if action == 0 else 'changes_json.sport_association')
    after['auditlog.LogEntry'].append(audit)
    after['application.AuditLogIndex'].append(index)
    after['application.AuditLogIndex'].sort(key=lambda r: r['audit_log_index_id'])
    return after


def traffic_control(method, path, body, *, cid, version='v2', request_body=None, status=200):
    encoded = json.dumps(body).encode()
    proof = {'method': method, 'path': path, 'status': status, 'cid': cid, 'complete': True,
             'headers': {'Content-Type': 'application/json', 'Content-Language': 'en',
                         'Allow': 'POST, OPTIONS' if method == 'POST' else 'DELETE, OPTIONS'},
             'body': encoded, 'request_body': request_body, 'django_events': []}
    proof['proxy'] = {key: proof[key] for key in ('method', 'path', 'status', 'cid')} | {
        'backend': 'django-v1' if version == 'v1' else 'go-v2', 'fallback': False}
    if version == 'v1':
        proof['django_events'] = [{key: proof[key] for key in ('method', 'path', 'status', 'cid')} |
                                  {'seq': 1, 'error_type': None}]
    return proof


def checkpoint_control(actor, action, before, *, version='v2'):
    spec = browser.authored_spec()['actors'][actor]
    models, traffic = copy.deepcopy(before), []
    payload = {'actor': actor, 'action': action, 'requests': []}
    def add(method, path, body, request_body=None, status=200):
        cid = f'message-mutations-{version}-{actor}-{action}-{len(traffic) + 1}'
        row = traffic_control(method, path, body, cid=cid, version=version,
                              request_body=request_body, status=status)
        traffic.append(row)
        payload['requests'].append({key: row[key] for key in ('cid', 'method', 'path', 'status')} |
                                  {'body_sha256': browser.hashlib.sha256(row['body']).hexdigest()})
        return cid
    if action == 'login':
        user = next(row for row in models['application.User'] if row['user_id']['value'] == spec['user_id'])
        user['last_login'] = {'type': 'datetime', 'value': STAMP}
        payload['identity'] = {'user_id': spec['user_id'], 'requires_welcome': False}
        add('POST', browser.LOGIN, {'user_data': payload['identity'], 'access_token': 'OFFLINE-PRIVATE'})
    elif action == 'closed':
        pass
    elif actor == 'restricted_collaborator':
        add('GET', browser.LIST, LIST_DENIAL, status=403)
        payload['view'] = {'response': copy.deepcopy(LIST_DENIAL), 'rows': [],
            'error_text': LIST_ERROR, 'error_visible': True, 'create_controls': 0,
            'delete_controls': 0, 'enabled_delete_controls': 0,
            'sidebar_visible': True, 'hash': '#/communication/messages'}
    else:
        if action in ('create', 'delete'):
            case = spec[action]
            cid = add(case['method'], case['path'], case['response'],
                      json.dumps(case['body']).encode() if action == 'create' else None)
            models = mutate_control(models, case, cid)
        rows = browser.expected_list(models, spec['association_id'])
        rows.sort(key=lambda row: (row['created_at'] is None, row['created_at'] or ''), reverse=True)
        add('GET', browser.LIST, rows)
        payload['view'] = {'response': rows, 'rows': [reads.visible_row(row) for row in rows],
            'absent_ids': sorted(row['message_id'] for row in models['communications.Message']
                                 if row['sport_association_id'] != spec['association_id']),
            'create_control': actor in browser.READERS, 'delete_enabled': actor in browser.READERS,
            'create_controls': 2 if actor in browser.READERS else 0, 'delete_controls': len(rows),
            'sidebar_visible': True, 'hash': '#/communication/messages'}
    return payload, models, traffic


class CheckpointControls(unittest.TestCase):
    def setUp(self):
        self.state = state_control()
        self.cache, self.contrasts = cache_control()
        self.guard = {'running': True, 'pid': 123, 'instance': 'offline-control'}

    def verifier(self, version='v2'):
        return browser.CheckpointVerifier(version=version, contrasts=self.contrasts,
            initial_models=self.state, initial_cache=self.cache)

    def observe(self, verifier, payload, models, traffic, **kwargs):
        return verifier.observe(payload, models=models, cache=kwargs.pop('cache', self.cache),
            traffic=traffic, started=START, finished=END, remote_addr='127.0.0.1',
            process_before=self.guard, process_after=kwargs.pop('process_after', self.guard), **kwargs)

    def through_read(self):
        verifier = self.verifier()
        payload, models, traffic = checkpoint_control('owner', 'login', self.state)
        self.observe(verifier, payload, models, traffic)
        return verifier, models

    def test_all_twenty_four_synthetic_checkpoints_v1_v2_and_provenance(self):
        for version in ('v1', 'v2'):
            verifier, state = self.verifier(version), self.state
            for number, (actor, action) in enumerate(browser.CHECKPOINTS, 1):
                payload, state, traffic = checkpoint_control(actor, action, state, version=version)
                self.assertEqual(self.observe(verifier, payload, state, traffic), {'success': True, 'checkpoint': number})
            receipt, _, _, provenance = read_controls.browser_control(reads.authored_spec())
            provenance['source_sha256'].update({name: 'a' * 64 for name in browser.EXTRA_SOURCES})
            receipt.update(version=version, success=True, contexts_created=4, requests=verifier.requests,
                           checkpoints=verifier.acks, static_verified=['index.html', 'build-assets/main.js'],
                           complete_message_family_parity=False)
            receipt['artifact_sha256'] = provenance['artifact_sha256']
            build = {'invocation': ['offline-synthetic'], 'record_sha256': 'a' * 64,
                     'source_sha256': {k: v for k, v in provenance['source_sha256'].items() if k.startswith('UI/')},
                     'artifact_sha256': provenance['artifact_sha256']}
            self.assertTrue(verifier.finish(receipt, provenance_before=provenance,
                provenance_after=provenance, build_observation=build)['success'])
            self.assertEqual(len(verifier.requests), 26)
            self.assertEqual(len(verifier.acks), 24)
            for bad in ('missing-build', 'artifact', 'static', 'fabricated'):
                changed = copy.deepcopy(receipt)
                if bad == 'artifact': changed['artifact_sha256'] = {}
                if bad == 'static': changed['static_verified'] = ['index.html']
                if bad == 'fabricated': changed['requests'] = []
                with self.subTest(version=version, bad=bad), self.assertRaises(AssertionError):
                    verifier.finish(changed, provenance_before=provenance, provenance_after=provenance,
                                    build_observation=None if bad == 'missing-build' else build)

    def test_duplicate_and_unobserved_checkpoint_poison_ledger(self):
        verifier, state = self.through_read()
        payload, models, traffic = checkpoint_control('owner', 'login', state)
        with self.assertRaises(AssertionError): self.observe(verifier, payload, models, traffic)

        self.assertEqual(verifier.verified, 1)
        payload, models, traffic = checkpoint_control('owner', 'read', state)
        with self.assertRaises(AssertionError): self.observe(verifier, payload, models, traffic)
        self.assertTrue(verifier.failed)
        verifier, state = self.through_read()
        payload, models, traffic = checkpoint_control('owner', 'read', state)
        payload['requests'] = []
        with self.assertRaises(AssertionError): self.observe(verifier, payload, models, traffic)

    def readonly_checkpoint(self, version='v2'):
        verifier, state = self.verifier(version), self.state
        for actor, action in browser.CHECKPOINTS:
            if actor == 'restricted_collaborator': break
            payload, state, traffic = checkpoint_control(actor, action, state, version=version)
            self.observe(verifier, payload, state, traffic)
        payload, state, traffic = checkpoint_control('restricted_collaborator', 'login', state, version=version)
        self.observe(verifier, payload, state, traffic)
        return verifier, state, checkpoint_control('restricted_collaborator', 'read', state, version=version)

    def test_restricted_actual_403_exact_error_dom_and_no_effects(self):
        for version in ('v1', 'v2'):
            verifier, before, (payload, models, traffic) = self.readonly_checkpoint(version)
            self.assertEqual(payload['view']['rows'], [])
            self.assertEqual(payload['view']['response'], LIST_DENIAL)
            self.assertEqual(traffic[0]['status'], 403)
            self.assertEqual(models, before)
            self.assertEqual([(r['method'], r['path']) for r in traffic], [('GET', browser.LIST)])
            self.assertEqual(self.observe(verifier, payload, models, traffic), {'success': True, 'checkpoint': 23})
            self.assertEqual(verifier.acks[-1], {'actor': 'restricted_collaborator', 'action': 'read'})

    def test_restricted_prior_successful_read_assumption_is_rejected(self):
        for version in ('v1', 'v2'):
            verifier, _, (payload, models, traffic) = self.readonly_checkpoint(version)
            association = browser.authored_spec()['actors']['restricted_collaborator']['association_id']
            rows = browser.expected_list(models, association)
            rows.sort(key=lambda row: (row['created_at'] is None, row['created_at'] or ''), reverse=True)
            traffic[0] = traffic_control('GET', browser.LIST, rows,
                                        cid=traffic[0]['cid'], version=version)
            payload['requests'][0] = {key: traffic[0][key] for key in ('cid', 'method', 'path', 'status')} | {
                'body_sha256': browser.hashlib.sha256(traffic[0]['body']).hexdigest()}
            payload['view'] = {'response': rows, 'rows': [reads.visible_row(row) for row in rows],
                'absent_ids': sorted(row['message_id'] for row in models['communications.Message']
                                     if row['sport_association_id'] != association),
                'create_control': False, 'delete_enabled': False, 'create_controls': 0,
                'delete_controls': len(rows), 'sidebar_visible': True, 'hash': '#/communication/messages'}
            with self.subTest(version=version), self.assertRaises(AssertionError):
                self.observe(verifier, payload, models, traffic)

    def test_readonly_rejects_visibility_controls_malformed_extra_events_and_state_changes(self):
        faults = ('sidebar', 'hash', 'error-hidden', 'error-text', 'create-count',
                    'delete', 'delete-count', 'dom', 'false-200', 'wrong-403-body', 'extra-body',
                    'response', 'state', 'cache', 'mutation', 'extra-list', 'extra-payload',
                    'extra-view', 'extra-proof', 'malformed-view', 'old-denied', 'raw-bytes',
                    'incomplete', 'proxy', 'asgi', 'process', 'request-body', 'mime', 'bool-count')
        for version, bad in ((version, bad) for version in ('v1', 'v2') for bad in faults):
            verifier, _, (payload, models, traffic) = self.readonly_checkpoint(version)
            kwargs = {}
            if bad == 'sidebar': payload['view']['sidebar_visible'] = False
            if bad == 'hash': payload['view']['hash'] = '#/404'
            if bad == 'error-hidden': payload['view']['error_visible'] = False
            if bad == 'error-text': payload['view']['error_text'] = 'Unsupported toast'
            if bad == 'create-count': payload['view']['create_controls'] = 1
            if bad == 'delete': payload['view']['enabled_delete_controls'] = 1
            if bad == 'delete-count': payload['view']['delete_controls'] = 1
            if bad == 'bool-count': payload['view']['create_controls'] = False
            if bad == 'dom': payload['view']['rows'] = [{'message': 'FABRICATED'}]
            if bad == 'response': payload['view']['response'] = []
            # Rebind browser, proxy and ASGI too: these reject semantic lies,
            # rather than failing just because one observation was left stale.
            if bad in ('false-200', 'wrong-403-body', 'extra-body'):
                body = ([] if bad == 'false-200' else {'detail': 'Wrong denial'}
                        if bad == 'wrong-403-body' else dict(LIST_DENIAL, extra='PRIVATE'))
                traffic[0] = traffic_control('GET', browser.LIST, body,
                    cid=traffic[0]['cid'], version=version, status=200 if bad == 'false-200' else 403)
                payload['requests'][0] = {key: traffic[0][key] for key in ('cid', 'method', 'path', 'status')} | {
                    'body_sha256': browser.hashlib.sha256(traffic[0]['body']).hexdigest()}
                payload['view']['response'] = body
            if bad == 'state': models['communications.Message'].pop()
            if bad == 'cache': kwargs['cache'] = HistoricalReadCacheSnapshot(MappingProxyType({}))
            if bad in ('mutation', 'extra-list'):
                extra = copy.deepcopy(traffic[0])
                if bad == 'mutation': extra.update(method='POST', path='/communications/messages/add')
                traffic.append(extra)
                payload['requests'].append(copy.deepcopy(payload['requests'][0]))
            if bad == 'extra-payload': payload['denied'] = {'hash': '#/404'}
            if bad == 'extra-view': payload['view']['private'] = 'PRIVATE'
            if bad == 'extra-proof': payload['requests'][0]['private'] = 'PRIVATE'
            if bad == 'malformed-view': payload['view'] = []
            if bad == 'old-denied':
                payload = {'actor': 'restricted_collaborator', 'action': 'denied', 'requests': [],
                           'denied': {'hash': '#/404'}}
                traffic = []
            if bad == 'raw-bytes': traffic[0]['body'] += b'PRIVATE-TAIL'
            if bad == 'incomplete': traffic[0]['complete'] = False
            if bad == 'proxy': traffic[0]['proxy']['fallback'] = True
            if bad == 'asgi':
                traffic[0]['django_events'] = [{key: traffic[0][key] for key in ('cid', 'method', 'path')} |
                    {'seq': 1, 'status': 200, 'error_type': None}]
            if bad == 'process': kwargs['process_after'] = dict(self.guard, running=False)
            if bad == 'request-body': traffic[0]['request_body'] = b'PRIVATE'
            if bad == 'mime': traffic[0]['headers']['Content-Type'] = 'text/html'
            with self.subTest(version=version, bad=bad), self.assertRaises(AssertionError):
                self.observe(verifier, payload, models, traffic, **kwargs)
            self.assertTrue(verifier.failed)

    def test_restricted_closed_keeps_full_state_cache_process_and_zero_traffic_checks(self):
        for version, bad in ((version, bad) for version in ('v1', 'v2')
                             for bad in ('none', 'state', 'cache', 'process', 'list', 'mutation', 'view')):
            verifier, _, (payload, models, traffic) = self.readonly_checkpoint(version)
            self.observe(verifier, payload, models, traffic)
            payload, models, traffic = checkpoint_control('restricted_collaborator', 'closed', models,
                                                         version=version)
            kwargs = {}
            if bad == 'state': models['offline.Model0'][0]['sentinel'] = 'changed'
            if bad == 'cache': kwargs['cache'] = HistoricalReadCacheSnapshot(MappingProxyType({}))
            if bad == 'process': kwargs['process_after'] = dict(self.guard, instance='different')
            if bad in ('list', 'mutation'):
                traffic.append(traffic_control('GET' if bad == 'list' else 'POST',
                    browser.LIST if bad == 'list' else '/communications/messages/add', LIST_DENIAL,
                    cid=f'message-mutations-{version}-restricted_collaborator-closed-1', version=version))
            if bad == 'view': payload['view'] = {}
            with self.subTest(version=version, bad=bad):
                if bad == 'none':
                    self.assertEqual(self.observe(verifier, payload, models, traffic),
                                     {'success': True, 'checkpoint': 24})
                else:
                    with self.assertRaises(AssertionError):
                        self.observe(verifier, payload, models, traffic, **kwargs)
                    self.assertTrue(verifier.failed)

    def test_wrong_proxy_fallback_asgi_body_method_ownership_and_dom(self):
        for bad in ('fallback', 'asgi', 'tail', 'method', 'identity', 'scope', 'dom', 'cache', 'process'):
            verifier, state = self.through_read()
            payload, models, traffic = checkpoint_control('owner', 'read', state)
            kwargs = {}
            if bad == 'fallback': traffic[0]['proxy']['fallback'] = True
            if bad == 'asgi': traffic[0]['django_events'] = [{'unexpected': 'fallback'}]
            if bad == 'tail': traffic[0]['body'] += b'PRIVATE-TAIL'
            if bad == 'method': traffic[0]['method'] = 'POST'
            if bad == 'identity': payload['actor'] = 'foreign_owner'
            if bad == 'scope': payload['view']['response'][0]['sport_association'] = 'foreign'
            if bad == 'dom': payload['view']['rows'][0]['message'] = 'FABRICATED'
            if bad == 'cache': kwargs['cache'] = HistoricalReadCacheSnapshot(MappingProxyType({}))
            if bad == 'process': kwargs['process_after'] = dict(self.guard, pid=999)
            with self.subTest(bad=bad), self.assertRaises(AssertionError):
                self.observe(verifier, payload, models, traffic, **kwargs)

    def test_fabricated_success_wrong_owner_and_deliveries_use_exact_effects(self):
        for bad in ('fabricated', 'owner', 'delivery', 'history'):
            verifier, state = self.through_read()
            payload, state, traffic = checkpoint_control('owner', 'read', state)
            self.observe(verifier, payload, state, traffic)
            payload, after, traffic = checkpoint_control('owner', 'create', state)
            if bad == 'fabricated': after = state
            if bad == 'owner': after['communications.Message'][0]['sport_association_id'] = 'foreign'
            if bad == 'delivery': after['communications.MessageTransaction'].pop()
            if bad == 'history': after['auditlog.LogEntry'][0]['sentinel'] = 'rewritten'
            with self.subTest(bad=bad), self.assertRaises(AssertionError):
                self.observe(verifier, payload, after, traffic)

    def test_login_cannot_hide_prior_mutation_or_change_other_fields(self):
        verifier = self.verifier()
        payload, models, traffic = checkpoint_control('owner', 'login', self.state)
        models['communications.Message'].pop()
        with self.assertRaisesRegex(RuntimeError, 'undeclared-login-model-change'):
            self.observe(verifier, payload, models, traffic)

    def test_delete_history_indexes_delivery_sentinels_and_raw_cache_are_preserved(self):
        for bad in ('history', 'index', 'delivery', 'cache-value', 'cache-expiry'):
            verifier, state = self.verifier(), self.state
            for actor, action in browser.CHECKPOINTS:
                payload, after, traffic = checkpoint_control(actor, action, state)
                kwargs = {}
                if action == 'delete':
                    if bad == 'history': after['auditlog.LogEntry'].pop(0)
                    if bad == 'index': after['application.AuditLogIndex'].pop(0)
                    if bad == 'delivery': after['communications.MessageTransaction'].pop()
                    if bad.startswith('cache'):
                        entries = dict(self.cache.entries)
                        entries[EXPIRING_CONTRAST_KEY] = HistoricalReadCacheEntry(b'string',
                            b'changed' if bad == 'cache-value' else b'expiring-dump',
                            2000000000001 if bad == 'cache-expiry' else 2000000000000)
                        kwargs['cache'] = HistoricalReadCacheSnapshot(MappingProxyType(entries))
                    with self.subTest(bad=bad), self.assertRaises(AssertionError):
                        self.observe(verifier, payload, after, traffic, **kwargs)
                    break
                self.observe(verifier, payload, after, traffic)
                state = after

    def test_parent_http_denials_are_separate_nonvacuous_and_exact(self):
        for version in ('v1', 'v2'):
            for case in browser.authored_spec()['denials']:
                for bad in ('none', 'identity', 'target-absent', 'ownership', 'effect', 'fallback', 'asgi', 'cache'):
                    before, after = copy.deepcopy(self.state), copy.deepcopy(self.state)
                    row = traffic_control(case['method'], case['path'], case['response'], cid=case['name'],
                        version=version, request_body=json.dumps(case['body']).encode() if 'body' in case else None)
                    row['status'] = row['proxy']['status'] = case['status']
                    if row['django_events']: row['django_events'][0]['status'] = case['status']
                    identity, cache = case['audit_actor'], self.cache
                    if bad == 'identity': identity = 'wrong'
                    if bad in ('target-absent', 'ownership'):
                        if case['method'] != 'DELETE': continue
                        identifier = case['path'].split('/')[-2]
                        target = next(r for r in before['communications.Message'] if r['message_id'] == identifier)
                        if bad == 'target-absent': before['communications.Message'].remove(target)
                        else:
                            own = browser.authored_spec()['actors'][case['actor']]['association_id']
                            target['sport_association_id'] = 'foreign' if case['status'] == 403 else own
                        after = copy.deepcopy(before)
                    if bad == 'effect': after['auditlog.LogEntry'].pop()
                    if bad == 'fallback': row['proxy']['fallback'] = True
                    if bad == 'asgi': row['django_events'] = [] if version == 'v1' else [{'unexpected': True}]
                    if bad == 'cache': cache = HistoricalReadCacheSnapshot(MappingProxyType({}))
                    def verify():
                        return browser.verify_denial(case['name'], before=before, after=after,
                            cache_before=self.cache, cache_after=cache, contrasts=self.contrasts,
                            response=row, observed=row['django_events'], version=version,
                            authenticated_user_id=identity, process_before=self.guard, process_after=self.guard,
                            remote_addr='127.0.0.1', started=START, finished=END)
                    with self.subTest(version=version, case=case['name'], bad=bad):
                        if bad == 'none': self.assertEqual(verify()['source'], 'parent-http')
                        else:
                            with self.assertRaises(AssertionError): verify()


class AuthoredControls(unittest.TestCase):
    def test_readonly_input_has_read_scope_but_no_authored_mutation(self):
        spec = browser.authored_spec()
        restricted = spec['actors']['restricted_collaborator']
        self.assertEqual(set(restricted), {'user_id', 'association_id', 'forbidden_ids'})
        self.assertEqual(restricted['forbidden_ids'], spec['actors']['owner']['forbidden_ids'])
        identities = {name: {'username': name, 'password': 'OFFLINE-PRIVATE',
                            'user_id': actor['user_id']} for name, actor in spec['actors'].items()}
        value = browser.browser_input(version='v2', origin='http://127.0.0.1:49123',
            identities=identities, provenance={'artifact_sha256': {'index.html': 'a' * 64}})
        self.assertEqual(set(value['actors']['restricted_collaborator']),
                         {'username', 'password', 'user_id', 'forbidden_ids'})
        self.assertEqual(browser.CHECKPOINTS[-3:], (('restricted_collaborator', 'login'),
                         ('restricted_collaborator', 'read'), ('restricted_collaborator', 'closed')))
        self.assertEqual(browser.RESPONSE_COUNTS, {'login': 4, 'list': 16, 'create': 3, 'delete': 3})

    def test_ui_payloads_and_targets_are_authored_independently(self):
        spec = browser.authored_spec()
        for actor in browser.READERS:
            case = spec['actors'][actor]['create']
            self.assertEqual(case['body']['type'], 'EMAIL')
            self.assertGreaterEqual(len(case['body']['message']), 20)
            self.assertEqual(case['effect']['values']['message'], case['body']['message'])
            self.assertEqual(case['audit_actor'], spec['actors'][actor]['user_id'])
            self.assertEqual(spec['actors'][actor]['delete']['effect']['delivery_count'], 2)
        self.assertEqual(spec['actors']['foreign_owner']['association_id'],
                         spec['ids']['foreign_association'])
        self.assertEqual(len(browser.DENIAL_CASE_NAMES), 4)

    def test_response_controls_wrong_method_path_body_status_and_tail(self):
        case = browser.authored_spec()['actors']['owner']['create']
        row = {'method': case['method'], 'path': case['path'], 'status': 200,
               'body': json.dumps(case['response']).encode(), 'complete': True,
               'headers': {'Content-Type': 'application/json', 'Content-Language': 'en',
                           'Allow': 'POST, OPTIONS'}}
        browser.verify_response(case, row)
        for field, value in [('method', 'GET'), ('path', '/communications/send/email'),
                             ('status', 201), ('complete', False),
                             ('body', b'{"msg":"message added."}PRIVATE-TAIL'),
                             ('body', b'{"msg":"fabricated"}')]:
            with self.subTest(field=field), self.assertRaises(AssertionError):
                browser.verify_response(case, dict(row, **{field: value}))

    def test_existing_exact_effect_oracle_rejects_wrong_ownership_and_related_effects(self):
        controls = effect_controls.FullStateProjectionControls()
        case, before, after = controls.create_states()
        # The production verifier delegates directly to the existing oracle.
        args = dict(started=1793008800, finished=1793008801,
                    remote_addr='127.0.0.1', cid='case')
        self.assertEqual(browser.verify_effect(case, before, after, **args)['kind'], 'create')
        for mutate in ('owner', 'delivery', 'audit', 'index', 'unrelated', 'fabricated'):
            bad = copy.deepcopy(after)
            if mutate == 'owner': bad['communications.Message'][0]['sport_association_id'] = 'foreign'
            elif mutate == 'delivery': bad['communications.MessageTransaction'].append({'id': 'unexpected'})
            elif mutate == 'audit': bad['auditlog.LogEntry'][0]['actor_id'] = 'other'
            elif mutate == 'index': bad['application.AuditLogIndex'][0]['resolution_path'] = 'wrong'
            elif mutate == 'unrelated': bad['unrelated.Sentinel'][0]['value'] = 'changed'
            else: bad = before
            with self.subTest(mutate=mutate), self.assertRaises(AssertionError):
                browser.verify_effect(case, before, bad, **args)

    def test_checkpoint_order_duplicate_unobserved_and_fabricated_finish(self):
        verifier = browser.CheckpointVerifier(version='v2', contrasts=None)
        with self.assertRaises(AssertionError): verifier.finish({'success': True})
        with self.assertRaises(AssertionError):
            verifier.observe({'actor': 'owner', 'action': 'read'}, models={}, cache=None,
                             traffic=[], started=0, finished=1, remote_addr='127.0.0.1')
        self.assertEqual(verifier.verified, 0)

    def test_no_public_private_diagnostics(self):
        self.assertEqual(browser.public_failure(RuntimeError('PRIVATE-PASSWORD PRIVATE-STATE')),
                         {'success': False, 'error': 'checkpoint-verification-failed'})


if __name__ == '__main__':
    unittest.main()
