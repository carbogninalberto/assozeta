"""Offline negative controls; no HTTP/DB acceptance is inferred from these tests."""
import ast
import copy
from contextlib import contextmanager
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def deletion_handler():
    """Compile the real handler body; decorators/auth/routing are outside this probe."""
    tree = ast.parse((ROOT / 'BE/communications/views.py').read_text())
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == 'communication_messages_delete')
    node.decorator_list = []
    events = []
    active = False

    @contextmanager
    def atomic_mutation():
        nonlocal active
        active = True
        events.append('enter')
        try:
            yield
        finally:
            active = False
            events.append('exit')

    def inside_transaction(event, result=None):
        if not active:
            raise AssertionError(f'{event} outside mutation transaction')
        events.append(event)
        return result

    message = Mock(spec=['delete'])
    message.delete.side_effect = lambda: inside_transaction('delete')
    queryset = Mock(spec=['filter', 'first'])
    queryset.filter.side_effect = lambda **kwargs: inside_transaction('filter', queryset)
    queryset.first.side_effect = lambda: inside_transaction('first', queryset.first.return_value)
    queryset.first.return_value = message
    manager = Mock(spec=['select_for_update'])
    manager.select_for_update.side_effect = lambda: inside_transaction('lock', queryset)
    namespace = {'Message': SimpleNamespace(objects=manager), 'is_valid_uuid': Mock(),
                 'atomic_audit_mutation': atomic_mutation,
                 'queryset': queryset, 'message': message, 'events': events,
                 'Response': lambda data, status: (status, data),
                 'status': SimpleNamespace(HTTP_200_OK=200, HTTP_404_NOT_FOUND=404)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'actual-delete-handler', 'exec'), namespace)
    return namespace


class DeletionHandlerRegression(unittest.TestCase):
    def test_missing_scoped_message_is_404(self):
        namespace = deletion_handler()
        namespace['queryset'].first.return_value = None
        request = SimpleNamespace(user=Mock(sport_association='owner-association'))
        result = namespace['communication_messages_delete'](request, 'absent-uuid')
        self.assertEqual(result, (404, {'msg': 'message not found.'}))
        namespace['Message'].objects.select_for_update.assert_called_once_with()
        namespace['queryset'].filter.assert_called_once_with(
            sport_association='owner-association', message_id='absent-uuid')
        namespace['message'].delete.assert_not_called()
        self.assertEqual(namespace['events'], ['enter', 'lock', 'filter', 'first', 'exit'])

    def test_existing_deletes_once(self):
        namespace = deletion_handler()
        message = namespace['message']
        request = SimpleNamespace(user=Mock(sport_association='owner-association'))
        self.assertEqual(namespace['communication_messages_delete'](request, 'present-uuid'),
                         (200, {'msg': 'message deleted.'}))
        message.delete.assert_called_once_with()
        namespace['Message'].objects.select_for_update.assert_called_once_with()
        namespace['queryset'].filter.assert_called_once_with(
            sport_association='owner-association', message_id='present-uuid')
        self.assertEqual(namespace['events'], ['enter', 'lock', 'filter', 'first', 'delete', 'exit'])

    def test_uuid_failure_precedes_query(self):
        namespace = deletion_handler()
        namespace['is_valid_uuid'].side_effect = ValueError('invalid UUID')
        with self.assertRaises(ValueError):
            namespace['communication_messages_delete'](SimpleNamespace(user=Mock()), 'bad')
        namespace['Message'].objects.select_for_update.assert_not_called()
        self.assertEqual(namespace['events'], [])


class PortableMutationControls(unittest.TestCase):
    def sequence_connection(self, state):
        cursor = Mock()

        def execute(sql, parameters):
            self.assertEqual(sql, 'SELECT setval(%s::regclass, %s, %s)')
            name, value, called = parameters
            # Model PostgreSQL identifier resolution: unquoted input folds case.
            resolved = name[1:-1].replace('""', '"') if name.startswith('"') and name.endswith('"') else name.lower()
            if resolved not in state:
                raise LookupError('regclass.identifier-resolution')
            state[resolved] = [value, called]

        cursor.execute.side_effect = execute
        context = Mock(__enter__=Mock(return_value=cursor), __exit__=Mock(return_value=False))
        return SimpleNamespace(cursor=lambda: context,
            ops=SimpleNamespace(quote_name=lambda name: '"' + name.replace('"', '""') + '"'))

    def test_sequence_restore_resolves_mixed_case_and_preserves_is_called(self):
        from TESTS.adapters.django import message_mutations as adapter
        mixed = 'application_campsandretreatsperiod_camps_and_Retreats_pe_id_seq'
        for called in (False, True):
            original = {mixed: [37, called], 'plain_sequence': [12, not called]}
            state = {name: [99, True] for name in original}
            with patch('django.db.connection', self.sequence_connection(state)), \
                    patch.object(adapter, 'sequence_snapshot', side_effect=lambda: copy.deepcopy(state)):
                self.assertTrue(adapter.restore_sequences(original))
            self.assertEqual(state, original)

    def test_sequence_restore_keeps_exact_set_and_value_checks(self):
        from TESTS.adapters.django import message_mutations as adapter
        original = {'MixedCase_sequence': [37, False]}
        for snapshots, code in [([{'unexpected': [1, True]}], 'cleanup.sequence-set'),
                                ([original, {'MixedCase_sequence': [37, True]}], 'cleanup.sequences')]:
            with patch('django.db.connection', self.sequence_connection(copy.deepcopy(original))), \
                    patch.object(adapter, 'sequence_snapshot', side_effect=snapshots):
                with self.assertRaisesRegex(AssertionError, code):
                    adapter.restore_sequences(original)

    def test_restore_denies_authorized_owner_create_and_delete(self):
        from TESTS.contracts import test_message_mutations as contract
        cases = contract.load_source()['cases']
        for mode in ('pending', 'locked'):
            for method in ('POST', 'DELETE'):
                selected = [case for case in cases if case.get('admission') == mode
                            and case['actor'] == 'owner' and case['method'] == method]
                self.assertEqual(len(selected), 1, (mode, method))
                self.assertEqual(selected[0]['status'], 503)
                self.assertEqual(selected[0]['effect'], {'kind': 'none'})

    def test_second_tenant_mutations_and_reciprocal_denials(self):
        from TESTS.contracts import test_message_mutations as contract
        source = contract.load_source()
        cases, ids = source['cases'], source['ids']
        for actor in ('foreign_owner', 'admin'):
            creates = [case for case in cases if case['actor'] == actor
                       and case['effect'].get('values', {}).get('sport_association_id') == ids['foreign_association']]
            deletes = [case for case in cases if case['actor'] == actor and case['effect']['kind'] == 'delete'
                       and case['effect']['message_id'] in (ids.get('delete_foreign_owner'), ids.get('delete_foreign_admin'))]
            self.assertEqual(len(creates), 1, actor)
            self.assertEqual(len(deletes), 1, actor)
            if actor == 'admin':
                for case in creates + deletes:
                    self.assertEqual(case['headers']['X-Impersonation-Id'], '$session:admin_foreign')
                    self.assertEqual(case['audit_actor'], ids['admin'])
                    self.assertEqual(case['audit_additional']['target_user_id'], ids['foreign_owner'])
        for actor, target in [('owner', ids.get('delete_foreign_owner')), ('foreign_owner', ids['delete_email'])]:
            denied = [case for case in cases if case['actor'] == actor
                      and case['path'] == f'/communications/messages/{target}/delete' and case['status'] == 404]
            self.assertEqual(len(denied), 1, actor)
            self.assertEqual(denied[0]['effect'], {'kind': 'none'})
            success_position = next(index for index, case in enumerate(cases)
                                    if case['effect'].get('message_id') == target)
            self.assertLess(cases.index(denied[0]), success_position, 'denial must target a still-existing row')

    def test_scoped_session_delete_malformed_body_precedes_lookup(self):
        from TESTS.contracts import test_message_mutations as contract
        source = contract.load_source()
        expected_paths = {f'/communications/messages/{target}/delete' for target in
                          (source['ids']['delete_owner_session'], source['ids']['foreign_email'],
                           source['ids']['missing'], 'not-a-uuid')}
        cases = [case for case in source['cases'] if case['method'] == 'DELETE'
                 and case['headers'].get('X-Impersonation-Id') == '$session:owner_full'
                 and case.get('raw_body') == '{']
        self.assertEqual({case['path'] for case in cases}, expected_paths)
        for case in cases:
            self.assertEqual(case['status'], 400)
            self.assertEqual(case['response'], {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'})
            self.assertEqual(case['effect'], {'kind': 'none'})

    def response_probe(self, case, content, *, content_type='text/html'):
        from TESTS.contracts import test_message_mutations as contract
        response = io.BytesIO(content)
        response.status = case['status']
        response.headers = {'Content-Type': content_type, 'Content-Language': 'en', 'Allow': 'POST, OPTIONS'}
        opener = SimpleNamespace(open=lambda *args, **kwargs: response)
        with patch('TESTS.http_client.urllib.request.build_opener', return_value=opener):
            return contract.assert_case(dict(case, token=None), 'http://offline.invalid')

    def test_complete_500_document_rejects_traceback_after_shared_client_boundary(self):
        from TESTS.contracts import test_message_mutations as contract
        case = next(case for case in contract.load_source()['cases'] if case['status'] == 500)
        page = (b'\n<!doctype html>\n<html lang="en">\n<head>\n  <title>Server Error (500)</title>\n'
                b'</head>\n<body>\n  <h1>Server Error (500)</h1><p></p>\n</body>\n</html>\n')
        self.assertEqual(self.response_probe(case, page), 500)
        for tail in (b'Traceback: private data', b'unexpected trailing data', b'\xff'):
            with self.subTest(tail=tail):
                with self.assertRaises(contract.ResponseFailure) as raised:
                    self.response_probe(case, page + b' ' * 1100 + tail)
                self.assertEqual(raised.exception.code, 'response.error-document')

    def test_full_json_response_decodes_locally(self):
        from TESTS.contracts import test_message_mutations as contract
        case = copy.deepcopy(contract.load_source()['cases'][0])
        case['response'] = {'msg': 'Unicode response é ' * 100}
        body = json.dumps(case['response']).encode()
        self.assertGreater(len(body), 1000)
        self.assertEqual(self.response_probe(case, body, content_type='application/json'), 200)
        with self.assertRaises(contract.ResponseFailure) as raised:
            self.response_probe(case, body + b'trailing data', content_type='application/json')
        self.assertEqual(raised.exception.code, 'response.json')

    def test_delete_cases_really_issue_delete(self):
        from TESTS.contracts import test_message_mutations as contract
        for case in contract.load_source()['cases']:
            if case['path'].endswith('/delete') and case['status'] != 405 and '-auth-before-method-' not in case['name']:
                self.assertEqual(case['method'], 'DELETE', case['name'])

    def test_contract_is_authored_and_bound(self):
        from TESTS.contracts import test_message_mutations as contract
        from TESTS.adapters.django import message_mutations as adapter
        source = contract.load_source()
        tokens = {actor: 'synthetic-' + actor for actor in source['actors'] if actor != 'anonymous'}
        sessions = {label: f'00000000-0000-4000-8000-{index:012d}'
                    for index, label in enumerate(source['sessions'], 1)}
        runtime = adapter.runtime_manifest(source, tokens, sessions)
        contract.validate_runtime(runtime)
        changed = copy.deepcopy(runtime)
        changed['cases'][0]['status'] = 201
        with self.assertRaises(AssertionError):
            contract.validate_runtime(changed)

    def test_create_expected_values_do_not_come_from_observed_rows(self):
        from TESTS.contracts import test_message_mutations as contract
        before = {'communications.Message': [], 'communications.MessageTransaction': [], 'auditlog.LogEntry': []}
        case = {'effect': {'kind': 'create', 'values': {'type': 'EMAIL', 'message': 'intended',
                'subject': None, 'sport_association_id': 'own'}}, 'audit_actor': 'actor', 'audit_additional': None}
        after = copy.deepcopy(before)
        after['communications.Message'] = [{'message_id': '00000000-0000-4000-8000-000000000001',
            'type': 'EMAIL', 'message': 'silently discarded', 'subject': None,
            'sport_association_id': 'own', 'created_at': '2026-10-26T10:00:00Z'}]
        with self.assertRaises(AssertionError):
            contract.assert_effect(case, before, after, started=0, finished=2000000000, remote_addr='127.0.0.1', cid='case')

    def test_misroute_is_rejected(self):
        from TESTS.adapters.django.message_reads import assert_attribution, AttributionError
        event = {'seq': 0, 'method': 'DELETE', 'path': '/communications/messages/x/delete',
                 'cid': 'case', 'status': 200, 'error_type': None}
        with self.assertRaises(AttributionError):
            assert_attribution('go', 'DELETE', event['path'], 200, [event], 'case')

    def test_runtime_requires_explicit_services(self):
        from TESTS.adapters.django import message_mutations_runtime as runtime
        with self.assertRaises((TypeError, ValueError, AssertionError)):
            runtime.run()

    def test_actual_serializer_and_message_diff_with_database_and_network_blocked(self):
        from TESTS.adapters.django.production_runtime import require_production_python
        require_production_python()
        result = subprocess.run([sys.executable, '-m', 'TESTS.test_message_mutations', '--django-metadata-probe'],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, 'Offline production Django metadata probe failed; no service evidence')
        self.assertEqual(result.stdout.strip(), 'PASS: 42 serializer cases; 10 parser cases; 4 scoped DELETE parses; '
                         '22 historical events/indexes with mocked writes; actual registry API and unsaved Message None diff; DB/network blocked')


class FullStateProjectionControls(unittest.TestCase):
    def add_history(self, before, after, identifier):
        """Real target history is preserved, separately from the new DELETE event."""
        for number, action in enumerate((0, 1), 101):
            event = copy.deepcopy(after['auditlog.LogEntry'][0])
            event.update(id=number, action=action, cid='historical-' + str(action),
                         timestamp='2026-10-23T09:00:00Z')
            index = copy.deepcopy(after['application.AuditLogIndex'][0])
            index.update(audit_log_index_id=f'00000000-0000-4000-8000-{number:012d}',
                         log_entry_id=number, resolution_path='direct', created_at='2026-10-23T09:00:00Z')
            self.assertEqual(event['object_pk'], identifier)
            for target in (before, after):
                target['auditlog.LogEntry'].append(copy.deepcopy(event))
                target['application.AuditLogIndex'].append(copy.deepcopy(index))
                target['auditlog.LogEntry'].sort(key=lambda row: row['id'])
                target['application.AuditLogIndex'].sort(key=lambda row: row['audit_log_index_id'])

    def test_delete_requires_target_historical_create_update_and_indexes(self):
        case, original, state = self.create_states()
        identifier = state['communications.Message'][0]['message_id']
        before = copy.deepcopy(original)
        before['communications.Message'] = copy.deepcopy(state['communications.Message'])
        after = copy.deepcopy(state)
        after['communications.Message'] = []
        after['auditlog.LogEntry'][0]['action'] = 2
        after['auditlog.LogEntry'][0]['changes'] = {key: list(reversed(value))
            for key, value in after['auditlog.LogEntry'][0]['changes'].items()}
        after['application.AuditLogIndex'][0]['resolution_path'] = 'changes_json.sport_association'
        case['effect'] = {'kind': 'delete', 'message_id': identifier, 'delivery_count': 0}
        with self.assertRaisesRegex(AssertionError, 'state.target-history'):
            self.check(case, before, after)
        self.add_history(before, after, identifier)
        self.assertEqual(self.check(case, before, after)['kind'], 'delete')
        for model in ('auditlog.LogEntry', 'application.AuditLogIndex'):
            for operation in ('delete', 'rewrite'):
                changed = copy.deepcopy(after)
                historical = next(row for row in changed[model]
                                  if row.get('id', row.get('log_entry_id')) == 101)
                if operation == 'delete':
                    changed[model].remove(historical)
                else:
                    historical['cid' if model == 'auditlog.LogEntry' else 'resolution_path'] = 'rewritten'
                with self.subTest(model=model, operation=operation), self.assertRaises(AssertionError):
                    self.check(case, before, changed)
        for action in (0, 1):
            missing = copy.deepcopy(before)
            missing['auditlog.LogEntry'] = [row for row in missing['auditlog.LogEntry'] if row['action'] != action]
            with self.assertRaisesRegex(AssertionError, 'state.target-history'):
                self.check(case, missing, after)
        missing = copy.deepcopy(before)
        missing['application.AuditLogIndex'] = []
        with self.assertRaisesRegex(AssertionError, 'state.target-history-index'):
            self.check(case, missing, after)

    def create_states(self, *, literal_none=False):
        message_id = '00000000-0000-4000-8000-000000000001'
        text = 'None' if literal_none else 'Avviso'
        subject = 'None' if literal_none else 'Oggetto'
        before = {'communications.Message': [], 'communications.MessageTransaction': [],
                  'auditlog.LogEntry': [], 'application.AuditLogIndex': [],
                  'contenttypes.ContentType': [{'id': 30, 'app_label': 'communications', 'model': 'message'}],
                  'application.User': [{'user_id': 'actor', 'email': 'actor@example.test'}],
                  'unrelated.Sentinel': [{'id': 1, 'value': 'preserve'}]}
        after = copy.deepcopy(before)
        after['communications.Message'] = [{'message_id': message_id, 'type': 'EMAIL', 'message': text,
            'subject': subject, 'sport_association_id': 'own', 'created_at': '2026-10-26T10:00:00Z'}]
        changes = {'message_id': ['None', message_id], 'type': ['None', 'EMAIL'],
                   'sport_association': ['None', 'own'], 'created_at': ['None', '2026-10-26 10:00:00']}
        if not literal_none:
            changes.update(message=['None', 'Avviso'], subject=['None', 'Oggetto'])
        after['auditlog.LogEntry'] = [{'id': 1, 'content_type_id': 30, 'object_pk': message_id,
            'object_id': None, 'object_repr': f'Message object ({message_id})', 'serialized_data': None,
            'action': 0, 'changes_text': '', 'changes': changes, 'actor_id': 'actor',
            'actor_email': 'actor@example.test', 'cid': 'case', 'remote_addr': '127.0.0.1',
            'remote_port': None, 'timestamp': '2026-10-26T10:00:00Z', 'additional_data': None}]
        after['application.AuditLogIndex'] = [{'audit_log_index_id': '00000000-0000-4000-8000-000000000002',
            'log_entry_id': 1, 'sport_association_id': 'own', 'resolution_path': 'direct',
            'created_at': '2026-10-26T10:00:00Z'}]
        case = {'effect': {'kind': 'create', 'values': {'type': 'EMAIL', 'message': text,
                'subject': subject, 'sport_association_id': 'own'}}, 'audit_actor': 'actor', 'audit_additional': None}
        return case, before, after

    def check(self, case, before, after):
        from TESTS.contracts import test_message_mutations as contract
        return contract.assert_effect(case, before, after, started=1793008800, finished=1793008801,
                                      remote_addr='127.0.0.1', cid='case')

    def test_complete_authored_create_projection(self):
        self.assertEqual(self.check(*self.create_states())['kind'], 'create')

    def test_second_tenant_projection_rejects_fixed_first_tenant(self):
        case, before, after = self.create_states()
        case['effect']['values']['sport_association_id'] = 'second'
        after['communications.Message'][0]['sport_association_id'] = 'second'
        after['auditlog.LogEntry'][0]['changes']['sport_association'][1] = 'second'
        after['application.AuditLogIndex'][0]['sport_association_id'] = 'second'
        self.assertEqual(self.check(case, before, after)['kind'], 'create')
        # A consistent but wrong tenant across all observed domains still fails.
        after['communications.Message'][0]['sport_association_id'] = 'own'
        after['auditlog.LogEntry'][0]['changes']['sport_association'][1] = 'own'
        after['application.AuditLogIndex'][0]['sport_association_id'] = 'own'
        with self.assertRaisesRegex(AssertionError, 'state.created-fields'):
            self.check(case, before, after)

    def test_literal_none_text_is_omitted_from_audit_diff(self):
        result = self.check(*self.create_states(literal_none=True))
        self.assertNotIn('message', result['audit']['changes'])
        self.assertNotIn('subject', result['audit']['changes'])

    def test_unrelated_and_each_audit_domain_cannot_change(self):
        for model, field, value in [
            ('unrelated.Sentinel', 'value', 'changed'),
            ('communications.Message', 'message', 'dropped'),
            ('auditlog.LogEntry', 'actor_id', 'owner-instead-of-initiator'),
            ('auditlog.LogEntry', 'content_type_id', 31),
            ('auditlog.LogEntry', 'remote_addr', '198.51.100.1'),
            ('auditlog.LogEntry', 'changes', {}),
            ('auditlog.LogEntry', 'serialized_data', {}),
            ('application.AuditLogIndex', 'sport_association_id', 'foreign'),
            ('application.AuditLogIndex', 'resolution_path', 'actor'),
        ]:
            with self.subTest(model=model, field=field):
                case, before, after = self.create_states()
                after[model][0][field] = value
                with self.assertRaises(AssertionError):
                    self.check(case, before, after)

    def test_missing_audit_or_index_is_rejected(self):
        for label in ('auditlog.LogEntry', 'application.AuditLogIndex'):
            case, before, after = self.create_states()
            after[label] = []
            with self.assertRaises(AssertionError):
                self.check(case, before, after)

    def test_generated_times_are_not_blanket_exclusions(self):
        for value in ('2000-01-01T00:00:00Z', None, '2026-10-26T10:00:00'):
            case, before, after = self.create_states()
            after['communications.Message'][0]['created_at'] = value
            with self.assertRaises(AssertionError):
                self.check(case, before, after)

    def test_delete_requires_real_deliveries_and_preserves_foreign_reference(self):
        case, original, state = self.create_states()
        message_id = state['communications.Message'][0]['message_id']
        before = copy.deepcopy(original)
        before['communications.Message'] = state['communications.Message']
        before['communications.MessageTransaction'] = [
            {'message_transaction_id': 'own-delivery', 'message_id': message_id, 'recipient': 'same@example.test'},
            {'message_transaction_id': 'foreign-delivery', 'message_id': 'foreign', 'recipient': 'same@example.test'}]
        after = copy.deepcopy(state)
        after['communications.Message'] = []
        after['communications.MessageTransaction'] = [before['communications.MessageTransaction'][1]]
        after['auditlog.LogEntry'][0]['action'] = 2
        after['auditlog.LogEntry'][0]['changes'] = {key: list(reversed(value))
                                                   for key, value in state['auditlog.LogEntry'][0]['changes'].items()}
        after['application.AuditLogIndex'][0]['resolution_path'] = 'changes_json.sport_association'
        case['effect'] = {'kind': 'delete', 'message_id': message_id}
        self.add_history(before, after, message_id)
        self.assertEqual(self.check(case, before, after)['kind'], 'delete')
        after['communications.MessageTransaction'] = []
        with self.assertRaises(AssertionError):
            self.check(case, before, after)
        before['communications.MessageTransaction'] = []
        with self.assertRaises(AssertionError):
            self.check(case, before, after)

    def test_cache_serialization_preserves_dump_type_and_absolute_expiry(self):
        from TESTS.adapters.django import message_mutations as adapter
        snapshot = SimpleNamespace(entries={b'private-key': SimpleNamespace(
            redis_type=b'string', serialized_value=b'\x00binary-dump', expires_at_ms=1793008899000)})
        self.assertEqual(adapter.private_cache(snapshot), [{'key_hex': b'private-key'.hex(),
            'type_hex': b'string'.hex(), 'dump_hex': b'\x00binary-dump'.hex(), 'expires_at_ms': 1793008899000}])

    def test_unsent_null_date_delete_uses_explicit_zero_delivery_expectation(self):
        case, original, state = self.create_states()
        identifier = state['communications.Message'][0]['message_id']
        before = copy.deepcopy(original)
        before['communications.Message'] = copy.deepcopy(state['communications.Message'])
        before['communications.Message'][0]['created_at'] = None
        after = copy.deepcopy(state)
        after['communications.Message'] = []
        audit = after['auditlog.LogEntry'][0]
        audit['action'] = 2
        audit['changes'] = {key: list(reversed(value)) for key, value in audit['changes'].items()}
        audit['changes']['created_at'] = ['None', 'None']
        after['application.AuditLogIndex'][0]['resolution_path'] = 'changes_json.sport_association'
        case['effect'] = {'kind': 'delete', 'message_id': identifier, 'delivery_count': 0}
        self.add_history(before, after, identifier)
        self.assertEqual(self.check(case, before, after)['kind'], 'delete')


def offline_django_probe():
    """Actual application classes, no services; guards installed before AppConfig.ready."""
    import contextlib
    import tempfile
    from unittest.mock import patch
    from django.db.backends.base.base import BaseDatabaseWrapper
    from TESTS.adapters.django import message_reads_runtime as reuse
    from TESTS.adapters.django.production_runtime import require_production_python
    from TESTS.contracts import test_message_mutations as contract
    require_production_python()
    with tempfile.TemporaryDirectory(prefix='message-mutations-offline-', dir='/tmp') as temp:
        environment, _ = reuse.private_environment(Path(temp), 9, reuse.sanitized_tool_environment())
        reuse.apply_private_environment(environment)
        with (Path(temp) / 'private.log').open('w') as private_log, \
                contextlib.redirect_stdout(private_log), contextlib.redirect_stderr(private_log), \
                patch.object(BaseDatabaseWrapper, 'ensure_connection', side_effect=AssertionError('offline-database-forbidden')), \
                patch('psycopg2.connect', side_effect=AssertionError('offline-driver-forbidden')), \
                patch('socket.socket.connect', side_effect=AssertionError('offline-network-forbidden')):
            reuse.initialize_django_apps()
            from communications.serializers import MessageInputSerializer
            from communications.models import Message
            from auditlog.diff import model_instance_diff
            from rest_framework.test import APIRequestFactory
            from rest_framework.request import Request
            from rest_framework.parsers import JSONParser, FormParser, MultiPartParser
            from rest_framework.exceptions import ParseError, UnsupportedMediaType
            from django.utils.translation import activate
            activate('en')  # Match each authored HTTP request's Accept-Language.
            count = 0
            for case in contract.load_source()['cases']:
                if ('body' not in case or case.get('fault') or not
                        (case['effect']['kind'] == 'create' or case['response'] == {'msg': 'message not valid.'})):
                    continue
                serializer = MessageInputSerializer(data=case['body'])
                valid = serializer.is_valid()
                assert valid == (case['effect']['kind'] == 'create'), case['name']
                if valid:
                    authored = case['effect']['values'].copy()
                    authored.pop('sport_association_id')
                    observed = dict(serializer.validated_data)
                    observed.setdefault('subject', None)
                    assert observed == authored, case['name']
                count += 1
            parser_count = 0
            for case in contract.load_source()['cases']:
                if 'raw_body' not in case or case['actor'] != 'owner' or case['method'] != 'POST':
                    continue
                request = APIRequestFactory().generic('POST', '/communications/messages/add',
                    data=case['raw_body'].encode(), content_type=case['headers']['Content-Type'])
                parsed = Request(request, parsers=[JSONParser(), FormParser(), MultiPartParser()])
                try:
                    data = parsed.data
                except (ParseError, UnsupportedMediaType) as error:
                    assert error.status_code == case['status'] and {'detail': str(error.detail)} == case['response'], case['name']
                else:
                    serializer = MessageInputSerializer(data=data)
                    valid = serializer.is_valid()
                    assert valid == (case['effect']['kind'] == 'create'), case['name']
                    if valid:
                        authored = case['effect']['values'].copy()
                        authored.pop('sport_association_id')
                        observed = dict(serializer.validated_data)
                        observed.setdefault('subject', None)
                        assert observed == authored, case['name']
                parser_count += 1
            row = Message(message_id='00000000-0000-4000-8000-000000000001',
                sport_association_id='00000000-0000-4000-8000-000000000002', type='EMAIL',
                message='None', subject='None', created_at=None)
            difference = model_instance_diff(None, row)
            assert 'message' not in difference and 'subject' not in difference
            assert difference['created_at'] == ('None', 'None')
            assert model_instance_diff(row, None)['created_at'] == ('None', 'None')
            # Exercise the real scope guard and DRF parser with no resolver or DB I/O.
            from application.impersonation_scope import enforce_scoped_request
            from application.models import User, AuditLogIndex
            source = contract.load_source()
            scoped_count = 0
            for case in source['cases']:
                if not case['name'].startswith('message-mutations-delete-scoped-malformed-body-'):
                    continue
                request = APIRequestFactory().generic('DELETE', case['path'], data=b'{', content_type='application/json')
                parsed = Request(request, parsers=[JSONParser(), FormParser(), MultiPartParser()])
                parsed.impersonation_association = SimpleNamespace(pk=source['ids']['own_association'])
                parsed.effective_user = SimpleNamespace(role=User.ASSOCIATION)
                parsed.resolver_match = SimpleNamespace(func=SimpleNamespace(__name__='communication_messages_delete'),
                    kwargs={'message_id': case['path'].split('/')[-2]})
                try:
                    enforce_scoped_request(parsed)
                except ParseError as error:
                    assert error.status_code == case['status'] and {'detail': str(error.detail)} == case['response'], case['name']
                else:
                    raise AssertionError('offline.scope-failed-to-parse')
                scoped_count += 1
            # Actual model construction/diff and registry; writes/cursor below are
            # mocked and cannot certify PostgreSQL seed, constraints or HTTP.
            from TESTS.adapters.django import message_mutations as adapter
            from auditlog.models import LogEntry
            from django.contrib.contenttypes.models import ContentType
            from django.db import connection
            target_ids = {case['effect']['message_id'] for case in source['cases'] if case['effect']['kind'] == 'delete'}
            target_ids.add(source['ids']['fault_delete'])
            records = {identifier: Message(message_id=identifier, type='EMAIL',
                sport_association_id=source['ids']['foreign_association' if identifier in
                    (source['ids']['delete_foreign_owner'], source['ids']['delete_foreign_admin']) else 'own_association'],
                message='Preserved current body', subject=None, created_at=None) for identifier in target_ids}
            events, indexes = [], []

            def capture_events(rows):
                for event in rows:
                    event.pk = len(events) + 1
                    events.append(event)
                return rows

            with patch.object(Message._base_manager, 'get', side_effect=lambda *, pk: records[pk]), \
                    patch.object(ContentType.objects, 'get_for_model', return_value=ContentType(pk=30)), \
                    patch.object(LogEntry.objects, 'bulk_create', side_effect=capture_events), \
                    patch.object(AuditLogIndex.objects, 'bulk_create', side_effect=lambda rows: indexes.extend(rows)):
                adapter._seed_target_history(source)
            assert len(events) == len(indexes) == 22
            assert {event.object_pk for event in events} == target_ids
            for identifier in target_ids:
                prior = [event for event in events if event.object_pk == identifier]
                assert {event.action for event in prior} == {0, 1}
                assert next(event for event in prior if event.action == 1).changes == {
                    'message': ('Historical original ' + identifier, 'Preserved current body')}
                for event in prior:
                    index = next(index for index in indexes if index.log_entry_id == event.pk)
                    assert index.sport_association_id == records[identifier].sport_association_id
                    assert index.resolution_path == 'direct'
            cursor = Mock()
            cursor.fetchall.side_effect = [
                [('fake_fk', 'communications_messagetransaction', 'a', True, True, 'mocked constraint')],
                [('communications_messagetransaction', 'fake_index', '(message_id)')]]
            cursor_context = Mock(__enter__=Mock(return_value=cursor), __exit__=Mock(return_value=False))
            with patch.object(adapter, 'require_private_database'), patch.object(connection, 'cursor', return_value=cursor_context):
                assert adapter.schema_evidence()['message_audited'] is True
    print(f'PASS: {count} serializer cases; {parser_count} parser cases; {scoped_count} scoped DELETE parses; '
          f'{len(events)} historical events/indexes with mocked writes; actual registry API and unsaved Message None diff; DB/network blocked')


if __name__ == '__main__':
    if sys.argv[1:] == ['--django-metadata-probe']:
        offline_django_probe()
    else:
        unittest.main()
