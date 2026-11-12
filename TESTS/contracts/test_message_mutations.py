"""Independently authored saved-message mutation expectations, shared by all HTTP transports.

No Django imports; actual response/persistence never defines expected behavior.
Fault cases require the bounded atomic reference correction authorized after parent red proof.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import unittest
from uuid import UUID

from TESTS.http_client import Client

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/message_mutations.json'
MESSAGE = 'communications.Message'
DELIVERY = 'communications.MessageTransaction'
AUDIT = 'auditlog.LogEntry'
INDEX = 'application.AuditLogIndex'
# Authored DEBUG=False default document. Compare all bytes, including the tail.
SERVER_ERROR_DOCUMENT = (b'\n<!doctype html>\n<html lang="en">\n<head>\n'
    b'  <title>Server Error (500)</title>\n</head>\n<body>\n'
    b'  <h1>Server Error (500)</h1><p></p>\n</body>\n</html>\n')


def require(condition, code):
    if not condition:
        raise AssertionError(code)


def source_digest(source):
    # ASCII escaping also binds explicitly rejected surrogate inputs.
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= 90, 'manifest.version-count')
    require(len({case['name'] for case in source['cases']}) == len(source['cases']), 'manifest.names')
    for case in source['cases']:
        require(case['name'].startswith('message-mutations-') and case['actor'] in source['actors'], 'manifest.identity')
        require(case['path'].startswith('/communications/messages/') and case['method'] in ('POST', 'DELETE', 'PUT'), 'manifest.route')
        require(case['effect']['kind'] in ('none', 'create', 'delete'), 'manifest.effect')
        require(not ('body' in case and 'raw_body' in case), 'manifest.body')
        if case.get('fault'):
            require(case['status'] == 500 and case['effect'] == {'kind': 'none'}
                    and bool(case['reference_correction']), 'manifest.atomic-invariant')
    return source


def substitute(value, sessions):
    if isinstance(value, str) and value.startswith('$session:'):
        return sessions[value[9:]]
    if isinstance(value, dict):
        return {key: substitute(item, sessions) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, sessions) for item in value]
    return value


def validate_runtime(runtime, source=None):
    source = source or load_source()
    require(set(runtime) == set(source) | {'source_sha256'}, 'runtime.fields')
    require(runtime['source_sha256'] == source_digest(source), 'runtime.digest')
    require(len(runtime['cases']) == len(source['cases']), 'runtime.count')
    sessions, tokens = {}, {}
    for authored, actual in zip(source['cases'], runtime['cases'], strict=True):
        for key, value in authored['headers'].items():
            if value.startswith('$session:'):
                label = value[9:]
                identifier = actual['headers'][key]
                require(str(UUID(identifier)) == identifier, 'runtime.session-uuid')
                require(sessions.setdefault(label, identifier) == identifier, 'runtime.session-binding')
        actor = authored['actor']
        token = actual.get('token')
        require(token is None if actor == 'anonymous' else isinstance(token, str) and bool(token), 'runtime.token')
        require(tokens.setdefault(actor, token) == token, 'runtime.actor-binding')
    require(len(set(sessions.values())) == len(sessions), 'runtime.session-alias')
    expected = copy.deepcopy(source)
    expected['source_sha256'] = source_digest(source)
    for case in expected['cases']:
        actor = case.pop('actor')
        case['token'] = tokens[actor]
        case['headers'] = substitute(case['headers'], sessions)
        case['audit_additional'] = substitute(case['audit_additional'], sessions)
    require(expected == runtime, 'runtime.authored-projection')
    return runtime


class ResponseFailure(AssertionError):
    def __init__(self, code, status):
        self.code, self.actual_status = code, status
        super().__init__(code)


def assert_case(case, base_url):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json',
               'X-Correlation-ID': case['name'], **case['headers']}
    body = case['raw_body'].encode('utf-8') if 'raw_body' in case else case.get('body')
    status, actual, received = Client(base_url, case['token']).request(
        case['method'], case['path'], body, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(actual == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
    else:
        try:
            actual = json.loads(actual)
        except (ValueError, UnicodeDecodeError):
            raise ResponseFailure('response.json', status) from None
        check(type(actual) is type(case['response']) and actual == case['response'], 'response.body')
        check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
        check(received.get('Content-Language') == 'en', 'response.language')
        if status != 503:
            allowed = {'POST', 'OPTIONS'} if case['path'].endswith('/add') else {'DELETE', 'OPTIONS'}
            check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed, 'response.allow')
        else:
            check(received.get('Retry-After') == '10' and received.get('Allow') is None, 'response.admission')
    check(received.get('Set-Cookie') is None, 'response.cookie')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    return status


def scalar(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'} and value['type'] in ('uuid', 'datetime'):
        return value['value']
    return value


def row_values(row):
    return {key: scalar(value) for key, value in row.items()}


def uuid4(value):
    require(isinstance(value, str) and str(UUID(value)) == value and UUID(value).version == 4, 'state.uuid4')


def timestamp(value, started, finished):
    value = scalar(value)
    require(isinstance(value, str), 'state.timestamp-type')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0, 'state.timestamp-utc')
    require(started <= parsed.timestamp() <= finished, 'state.timestamp-window')
    return value


def keyed(rows, key):
    return {str(scalar(row[key])): row for row in rows}


def assert_effect(case, before, after, *, started, finished, remote_addr, cid):
    """Exact expected full-state projection, including all audit and index fields.

    Only generated IDs/times are taken from observations, after validation; field
    values, relation effects, actor, diff, index path and unrelated state are expected.
    """
    require(set(before) == set(after) and bool(before), 'state.model-set')
    kind = case['effect']['kind']
    if kind == 'none':
        require(before == after, 'state.no-effects')
        return {'kind': 'none'}
    expected = copy.deepcopy(before)
    old_messages = keyed(before[MESSAGE], 'message_id')
    new_messages = keyed(after[MESSAGE], 'message_id')
    if kind == 'create':
        added = set(new_messages) - set(old_messages)
        require(len(added) == 1 and set(old_messages) <= set(new_messages), 'state.one-created-message')
        identifier = added.pop()
        uuid4(identifier)
        require(identifier not in json.dumps(before), 'state.new-message-uuid')
        observed = row_values(new_messages[identifier])
        created_at = timestamp(observed['created_at'], started, finished)
        values = dict(case['effect']['values'], message_id=identifier, created_at=created_at)
        require(observed == values, 'state.created-fields')
        expected[MESSAGE] = sorted([*before[MESSAGE], new_messages[identifier]], key=lambda row: scalar(row['message_id']))
        action, index_path = 0, 'direct'
    else:
        identifier = case['effect']['message_id']
        require(identifier in old_messages, 'state.delete-target-seeded')
        require(identifier not in new_messages, 'state.target-removed')
        values = row_values(old_messages[identifier])
        message_types = {row['id'] for row in before['contenttypes.ContentType']
                         if row['app_label'] == 'communications' and row['model'] == 'message'}
        history = [row_values(row) for row in before[AUDIT]
                   if scalar(row['object_pk']) == identifier and row['content_type_id'] in message_types
                   and row['action'] in (0, 1)]
        require({row['action'] for row in history} == {0, 1}, 'state.target-history')
        for event in history:
            require(any(row['log_entry_id'] == event['id']
                        and scalar(row['sport_association_id']) == values['sport_association_id']
                        for row in before[INDEX]), 'state.target-history-index')
        expected[MESSAGE] = [row for row in before[MESSAGE] if scalar(row['message_id']) != identifier]
        expected[DELIVERY] = [row for row in before[DELIVERY] if scalar(row['message_id']) != identifier]
        count = sum(scalar(row['message_id']) == identifier for row in before[DELIVERY])
        if 'delivery_count' in case['effect']:
            require(type(case['effect']['delivery_count']) is int and count == case['effect']['delivery_count'], 'state.authored-delivery-count')
        else:
            require(count >= 1, 'state.real-cascade-seed')
        if count:
            require(any(scalar(row['message_id']) != identifier for row in before[DELIVERY]), 'state.delivery-sentinel')
        action, index_path = 2, 'changes_json.sport_association'
    require(after[MESSAGE] == expected[MESSAGE] and after[DELIVERY] == expected[DELIVERY], 'state.message-delivery-projection')
    old_audit, current_audit = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    added_audit = set(current_audit) - set(old_audit)
    require(len(added_audit) == 1 and set(old_audit) <= set(current_audit), 'audit.one-event')
    audit_key = added_audit.pop()
    audit_row = current_audit[audit_key]
    event = row_values(audit_row)
    require(type(event['id']) is int and event['id'] > 0, 'audit.generated-id')
    audit_time = timestamp(event['timestamp'], started, finished)
    content_types = [row for row in before['contenttypes.ContentType']
                     if row['app_label'] == 'communications' and row['model'] == 'message']
    require(len(content_types) == 1, 'audit.message-content-type')
    users = [row_values(row) for row in before['application.User']
             if scalar(row['user_id']) == case['audit_actor']]
    require(len(users) == 1, 'audit.initiating-user')
    changes = {}
    for field, value in values.items():
        field = 'sport_association' if field == 'sport_association_id' else field
        if field == 'created_at':
            if value is None:
                # Actual auditlog DateTimeField compares None with absent-side "None".
                changes[field] = ['None', 'None']
                continue
            value = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None)
        text = str(value)
        # auditlog default string diffs omit literal "None" and null CharFields.
        if text != 'None':
            changes[field] = ['None', text] if action == 0 else [text, 'None']
    expected_event = {'id': event['id'], 'content_type_id': content_types[0]['id'],
        'object_pk': identifier, 'object_id': None, 'object_repr': f'Message object ({identifier})',
        'serialized_data': None, 'action': action, 'changes_text': '', 'changes': changes,
        'actor_id': case['audit_actor'], 'actor_email': users[0]['email'], 'cid': cid,
        'remote_addr': remote_addr, 'remote_port': None, 'timestamp': audit_time,
        'additional_data': case['audit_additional']}
    require(event == expected_event, 'audit.exact-fields')
    expected[AUDIT] = sorted([*before[AUDIT], audit_row], key=lambda row: row['id'])
    old_indexes, current_indexes = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    added_indexes = set(current_indexes) - set(old_indexes)
    require(len(added_indexes) == 1 and set(old_indexes) <= set(current_indexes), 'index.one-row')
    index_key = added_indexes.pop()
    uuid4(index_key)
    index_row = current_indexes[index_key]
    index = row_values(index_row)
    index_time = timestamp(index['created_at'], started, finished)
    require(index == {'audit_log_index_id': index_key, 'log_entry_id': event['id'],
            'sport_association_id': values['sport_association_id'], 'resolution_path': index_path,
            'created_at': index_time}, 'index.exact-fields')
    expected[INDEX] = sorted([*before[INDEX], index_row], key=lambda row: scalar(row['audit_log_index_id']))
    require(expected == after, 'state.complete-managed-projection')
    # Comparison projection is only emitted after exact raw-state assertions.
    normalized = {'kind': kind, 'message': copy.deepcopy(values), 'audit': copy.deepcopy(expected_event),
                  'index': copy.deepcopy(index)}
    normalized['audit']['id'] = '$audit-id'
    normalized['audit']['timestamp'] = '$audit-time'
    normalized['index']['audit_log_index_id'] = '$index-uuid'
    normalized['index']['log_entry_id'] = '$audit-id'
    normalized['index']['created_at'] = '$index-time'
    if kind == 'create':
        normalized['message']['message_id'] = '$message-uuid'
        normalized['message']['created_at'] = '$message-time'
        normalized['audit']['object_pk'] = '$message-uuid'
        normalized['audit']['object_repr'] = 'Message object ($message-uuid)'
        normalized['audit']['changes']['message_id'] = ['None', '$message-uuid']
        normalized['audit']['changes']['created_at'] = ['None', '$message-time']
    # Sessions differ between fresh fixtures, but their actor/target/scope remain exact.
    if normalized['audit']['additional_data'] is not None:
        normalized['audit']['additional_data']['impersonation_session'] = '$verified-session'
    return normalized


class MessageMutationsContract(unittest.TestCase):
    def test_authored_http_sequence(self):
        # Portable HTTP-only entry point cannot claim state/fault/admission acceptance.
        path, base = os.environ.get('TESTS_FIXTURE'), os.environ.get('TESTS_BASE_URL')
        require(path and base, 'runtime.explicit-fixture-and-base-required')
        runtime = validate_runtime(json.loads(Path(path).read_text()))
        require(all(not case.get('fault') and not case.get('admission') for case in runtime['cases']),
                'runtime.use-observed-runner-for-controls')
        for case in runtime['cases']:
            assert_case(case, base)
