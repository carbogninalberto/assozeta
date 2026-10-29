"""Offline controls for installed manifest assertions; no runtime acceptance claims."""
import copy
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from urllib.error import HTTPError

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('application_update_manifest', HERE / 'application-update-smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class Response(io.BytesIO):
    def __init__(self, body, *, status=200, changed_headers=None):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        for name, value in {
            'Allow': 'GET, HEAD, OPTIONS', 'Content-Type': 'application/json',
            'Content-Language': 'en', 'Vary': 'Origin, Cookie, Accept-Language',
        }.items():
            self.headers[name] = value
        self.headers['Vary'] = 'Accept-Encoding'
        for name, value in (changed_headers or {}).items():
            del self.headers[name]
            if value is not None:
                self.headers[name] = value


class InstalledManifestTests(unittest.TestCase):
    def exercise(self, *, mutate=None, state_change=None, prefixes=('/api', '/api/v1', '/api/v2')):
        calls = []
        snapshots = []
        def snapshot():
            value = {'database': {'configuration': 'preserved', 'audit': 'preserved'},
                     'cache': {'session': 'preserved'}}
            if calls and state_change == 'audit':
                value['database']['audit'] = 'written'
            if calls and state_change == 'cache':
                value['cache']['session'] = 'changed'
            snapshots.append(copy.deepcopy(value))
            return value
        def opener(request, timeout):
            calls.append(request)
            method = request.get_method()
            body = {
                'name': 'Preserved Club', 'short_name': 'PC',
                'description': 'Gestionale per associazioni sportive',
                'icons': [{'src': '/api/instance/logo.png', 'sizes': size, 'type': 'image/png'}
                          for size in ('192x192', '512x512')],
                'start_url': '/', 'display': 'standalone',
                'background_color': '#ffffff', 'theme_color': '#351DC2',
            } if method == 'GET' else {
                'name': 'Instance Manifest', 'description': 'GET /instance/manifest.json\nDynamic PWA manifest.',
                'renders': ['application/json'],
                'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data'],
            }
            status, headers = 200, {}
            if mutate:
                body, status, headers = mutate(method, body)
            wire = b'' if method == 'HEAD' else json.dumps(body).encode()
            if method == 'HEAD' and headers.pop('head_body', False):
                wire = b'forbidden head body'
            return Response(wire, status=status, changed_headers=headers)
        result = smoke.assert_installed_manifest('http://127.0.0.1:1234', 'fixture.invalid:1234',
            '/api/instance/logo.png', snapshot, prefixes=prefixes, opener=opener)
        return result, calls, snapshots

    def test_executes_all_methods_and_routes_with_state_observation(self):
        result, calls, snapshots = self.exercise()
        self.assertEqual([(r.full_url.removeprefix('http://127.0.0.1:1234'), r.get_method()) for r in calls],
            [(prefix + '/instance/manifest.json', method)
             for prefix in ('/api', '/api/v1', '/api/v2') for method in ('GET', 'HEAD', 'OPTIONS')])
        self.assertEqual(len(result), 9)
        self.assertEqual(len(snapshots), 18)
        self.assertTrue(all(r.get_header('Accept-language') == 'en' for r in calls))

    def test_old_controller_only_checks_supported_alias(self):
        result, calls, _ = self.exercise(prefixes=('/api',))
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(result), 3)

    def test_wrong_branding_default_logo_extra_secret_and_metadata_fail(self):
        mutations = [
            lambda method, body: ({**body, 'name': 'Bakney Sport'}, 200, {}),
            lambda method, body: ({**body, 'secret': 'must never be accepted'}, 200, {}),
            lambda method, body: ({**body, 'theme_color': '#000000'}, 200, {}),
            lambda method, body: ({**body, 'icons': []}, 200, {}),
            lambda method, body: ({**body, 'icons': [
                {'src': '/api/instance/logo.png?v=1', 'sizes': size, 'type': 'image/png'}
                for size in ('192x192', '512x512')]}, 200, {}),
            lambda method, body: ({**body, 'start_url': '/other'}, 200, {}),
            lambda method, body: ({} if method == 'OPTIONS' else body, 200, {}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                self.exercise(mutate=mutation)

    def test_wrong_status_headers_and_head_body_fail(self):
        for headers in ({'Allow': 'GET'}, {'Content-Language': 'it'}, {'Content-Type': 'text/html'},
                        {'WWW-Authenticate': 'Bearer'}, {'Retry-After': '60'},
                        {'Set-Cookie': 'sessionid=changed'}, {'Vary': None}, {'head_body': True}):
            with self.subTest(headers=headers), self.assertRaises(AssertionError):
                self.exercise(mutate=lambda method, body: (body, 200, dict(headers)))
        with self.assertRaises(AssertionError):
            self.exercise(mutate=lambda method, body: (body, 404, {}))

    def test_real_snapshot_difference_is_rejected(self):
        for changed in ('audit', 'cache'):
            with self.subTest(changed=changed), self.assertRaisesRegex(AssertionError, 'state'):
                self.exercise(state_change=changed)

    def test_maintenance_probes_are_real_and_reject_application_success(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            headers = Message()
            headers['Cache-Control'] = 'no-store'
            headers['Retry-After'] = '60'
            body = b'Torniamo tra poco' if request.get_method() == 'GET' else b''
            raise HTTPError(request.full_url, 503, 'maintenance', headers, io.BytesIO(body))
        observed = smoke.assert_manifest_maintenance('http://fixture.invalid', 'fixture.invalid', opener=opener)
        self.assertEqual(len(calls), 9)
        self.assertEqual([item['status'] for item in observed], [503] * 9)
        with self.assertRaisesRegex(AssertionError, 'maintenance'):
            smoke.assert_manifest_maintenance('http://fixture.invalid', 'fixture.invalid',
                opener=lambda request, timeout: Response(b'{}'))

    def test_lifecycle_retains_source_database_and_requires_restore_cache_flush(self):
        initial = {'database': {'config': 'source', 'audit': 'source'}, 'sentinels': [None, None]}
        seeded = {'database': initial['database'], 'sentinels': [
            {'retained': 'manifest-cache'}, {'manifest_fixture': 'retained-session'}]}
        seeds = []
        def prepare(states, source=None, restored=False):
            values = iter(states)
            return smoke.prepare_manifest_checkpoint(lambda: next(values), lambda: seeds.append(True),
                                                       source_database=source, restored=restored)
        self.assertEqual(prepare([initial, seeded]), initial['database'])
        self.assertEqual(len(seeds), 1)
        self.assertEqual(prepare([seeded, seeded], initial['database']), initial['database'])
        self.assertEqual(len(seeds), 1)
        self.assertEqual(prepare([initial, seeded], initial['database'], restored=True), initial['database'])
        self.assertEqual(len(seeds), 2)
        for before, restored in ((seeded, True), (initial, False),
                                  ({**seeded, 'database': {'config': 'changed', 'audit': 'source'}}, False),
                                  ({**seeded, 'database': {'config': 'source', 'audit': 'changed'}}, False)):
            with self.subTest(before=before, restored=restored), self.assertRaises(AssertionError):
                prepare([before, before], initial['database'], restored=restored)


if __name__ == '__main__':
    unittest.main()
