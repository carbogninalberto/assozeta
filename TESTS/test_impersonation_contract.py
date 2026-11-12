"""Real HTTP and mutation controls for the impersonation oracle assertions."""
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest

from TESTS.adapters.django.impersonation_effects import (
    assert_cache_transition, assert_retained_rows,
)
from TESTS.contracts.test_impersonation_management import assert_case


@contextmanager
def json_server(payload):
    content = json.dumps(payload).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Language', 'en')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if thread.is_alive():
            raise AssertionError('Owned HTTP server did not stop')


class ImpersonationContractControls(unittest.TestCase):
    expected = {'results': [{'active': True, 'count': 0}], 'meta': {'page': 1, 'size': 25}}

    def check_payload(self, payload):
        with json_server(payload) as base:
            return assert_case(self, base, {
                'name': 'real-local-json-control', 'method': 'GET', 'path': '/control',
                'status': 200, 'response': self.expected,
            }, {})

    def test_correct_payload_passes_real_http(self):
        self.assertEqual(self.check_payload(self.expected), self.expected)

    def test_boolean_integer_substitutions_fail_real_http(self):
        for path, value in ((('meta', 'page'), True), (('results', 0, 'count'), False),
                            (('results', 0, 'active'), 1)):
            with self.subTest(path=path):
                payload = deepcopy(self.expected)
                target = payload
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = value
                with self.assertRaisesRegex(AssertionError, 'JSON type'):
                    self.check_payload(payload)

    def test_integer_float_substitutions_fail_real_http(self):
        for field in ('page', 'size'):
            with self.subTest(field=field):
                payload = deepcopy(self.expected)
                payload['meta'][field] = float(payload['meta'][field])
                with self.assertRaisesRegex(AssertionError, 'JSON type'):
                    self.check_payload(payload)


class RetainedAuditControls(unittest.TestCase):
    def rows(self, key):
        return [{key: 1, 'actor_id': 7, 'changes': {'items': [1, {'active': True}]}},
                {key: 2, 'actor_id': 8, 'changes': None}]

    def test_exact_append_passes_for_audit_and_index(self):
        for key in ('id', 'audit_log_index_id'):
            with self.subTest(key=key):
                before = self.rows(key)
                assert_retained_rows(before, deepcopy(before) + [{key: 3}], key=key, created=[3])

    def test_deleted_or_modified_old_rows_fail(self):
        for key in ('id', 'audit_log_index_id'):
            before = self.rows(key)
            modified = deepcopy(before)
            modified[0]['actor_id'] = 9
            for after in (before[1:], modified):
                with self.subTest(key=key, after=after):
                    with self.assertRaises(AssertionError):
                        assert_retained_rows(before, after + [{key: 3}], key=key, created=[3])

    def test_nested_json_type_changes_fail(self):
        for key in ('id', 'audit_log_index_id'):
            for value in (True, 1.0):
                with self.subTest(key=key, value=value):
                    before = self.rows(key)
                    after = deepcopy(before)
                    after[0]['changes']['items'][0] = value
                    with self.assertRaisesRegex(AssertionError, 'Preexisting row changed'):
                        assert_retained_rows(before, after, key=key)

    def test_unapproved_new_duplicate_or_reclassified_rows_fail(self):
        before = self.rows('id')
        for after, created in ((before + [{'id': 3}], ()), (before + [before[0]], ()),
                               (before, (1,)), (before + [{'id': 3}], (3, 3))):
            with self.subTest(created=created, count=len(after)):
                with self.assertRaises(AssertionError):
                    assert_retained_rows(before, after, key='id', created=created)


class RetainedCacheControls(unittest.TestCase):
    before = {b'session:old': b'original serialized session', b'unrelated': b'cache bytes'}

    def test_approved_create_and_delete_pass(self):
        assert_cache_transition(self.before, {**self.before, b'session:new': b'new'},
                                created=[b'session:new'])
        assert_cache_transition(self.before, {b'unrelated': b'cache bytes'}, deleted=[b'session:old'])
        assert_cache_transition(self.before, {b'unrelated': b'cache bytes', b'session:new': b'new'},
                                created=[b'session:new'], deleted=[b'session:old'])

    def test_retained_session_or_unrelated_bytes_changes_fail(self):
        for key in self.before:
            with self.subTest(key=key):
                after = {**self.before, key: b'changed', b'session:new': b'new'}
                with self.assertRaisesRegex(AssertionError, 'Retained cache bytes changed'):
                    assert_cache_transition(self.before, after, created=[b'session:new'])

    def test_unrelated_key_addition_or_removal_fails(self):
        for after in ({**self.before, b'unexpected': b'new'}, {b'session:old': self.before[b'session:old']}):
            with self.subTest(keys=list(after)):
                with self.assertRaisesRegex(AssertionError, 'Unexpected cache key transition'):
                    assert_cache_transition(self.before, after)

    def test_existing_key_cannot_be_exempted_as_created(self):
        with self.assertRaisesRegex(AssertionError, 'not new'):
            assert_cache_transition(self.before, {**self.before, b'session:old': b'changed'},
                                    created=[b'session:old'])

    def test_nonbyte_snapshots_fail(self):
        with self.assertRaisesRegex(AssertionError, 'raw bytes'):
            assert_cache_transition({b'key': 'decoded'}, {b'key': 'decoded'})


if __name__ == '__main__':
    unittest.main()
