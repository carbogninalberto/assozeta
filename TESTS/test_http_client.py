"""Socket-free checks through urllib's real opener/error/redirect handler chain."""
from contextlib import contextmanager
import http.client
import io
import os
import unittest
from unittest.mock import patch

from TESTS.http_client import Client


@contextmanager
def fake_http_transport(status, location=None, body=b'{"redirect":true}', headers=None, followed_body=b'[]'):
    """Replace only HTTP connections; parse real HTTP wire responses in urllib."""
    calls = []
    response_headers = dict(headers or {})
    if location is not None:
        response_headers['Location'] = location

    class Wire:
        def __init__(self, content):
            self.content = content

        def makefile(self, mode):
            return io.BytesIO(self.content)

    class Connection(http.client.HTTPConnection):
        sock = None
        debuglevel = 0

        def __init__(self, host, timeout=None, **kwargs):
            self.host = host

        def set_debuglevel(self, level):
            pass

        def request(self, method, url, body, headers, **kwargs):
            calls.append({'host': self.host, 'method': method, 'url': url,
                          'body': body, 'headers': dict(headers)})

        def getresponse(self):
            code, content, received = (status, body, response_headers) if len(calls) == 1 else (200, followed_body, response_headers)
            wire = (f'HTTP/1.1 {code} Synthetic\r\nContent-Length: {len(content)}\r\n'
                    + ''.join(f'{name}: {value}\r\n' for name, value in received.items())
                    + '\r\n').encode('ascii') + content
            response = http.client.HTTPResponse(Wire(wire))
            response.begin()
            return response

        def close(self):
            pass

    with patch.dict(os.environ, {}, clear=True), \
         patch('urllib.request._opener', None), \
         patch('http.client.HTTPConnection', Connection), \
         patch('http.client.HTTPSConnection', Connection), \
         patch('socket.socket', side_effect=AssertionError('socket forbidden')):
        yield calls


class ClientTransportControls(unittest.TestCase):
    def test_redirect_status_body_headers_and_credentials_never_follow(self):
        credentials = {'User-Id': 'synthetic-user', 'X-Impersonation-Id': 'synthetic-session'}
        for status in (301, 302, 303, 307, 308):
            for location in ('/next', 'http://owned.invalid/next',
                             'http://outside.invalid/next', 'https://outside.invalid/next'):
                for method in ('GET', 'POST'):
                    for raw in (False, True):
                        with self.subTest(status=status, location=location, method=method, raw=raw):
                            with fake_http_transport(status, location, headers={'X-Original': 'preserved'}) as calls:
                                result = Client('http://owned.invalid', 'synthetic-token').request(
                                    method, '/original', data=b'synthetic-body' if method == 'POST' else None,
                                    headers=credentials, raw=raw, include_headers=True)
                            self.assertEqual(len(calls), 1, 'redirect must not issue a second request')
                            self.assertEqual(calls[0]['host'], 'owned.invalid')
                            self.assertEqual(calls[0]['url'], '/original')
                            self.assertEqual(calls[0]['method'], method)
                            self.assertEqual(calls[0]['body'], b'synthetic-body' if method == 'POST' else None)
                            self.assertEqual(calls[0]['headers']['Authorization'], 'Bearer synthetic-token')
                            for name, value in credentials.items():
                                self.assertEqual(calls[0]['headers'][name.title()], value)
                            self.assertEqual(result[:2], (status, b'{"redirect":true}' if raw else {'redirect': True}))
                            self.assertEqual(result[2]['Location'], location)
                            self.assertEqual(result[2]['X-Original'], 'preserved')

    def test_success_error_login_and_upload_keep_existing_shapes(self):
        for status in (200, 403):
            with self.subTest(status=status), fake_http_transport(status, body=b'{"ok":true}') as calls:
                self.assertEqual(Client('http://owned.invalid').request('GET', '/read'), (status, {'ok': True}))
                self.assertEqual(len(calls), 1)
        with fake_http_transport(200, body=b'{"access_token":"synthetic-login"}'):
            logged_in, payload = Client('http://owned.invalid', timeout=7).login({'email': 'synthetic'})
        self.assertEqual(logged_in.token, payload['access_token'])
        self.assertEqual(logged_in.timeout, 7)
        with fake_http_transport(200, body=b'"uploaded"') as calls:
            self.assertEqual(logged_in.upload('/upload', 'synthetic.zip', b'zip-content'), (200, 'uploaded'))
        self.assertEqual(calls[0]['method'], 'POST')
        self.assertIn(b'zip-content', calls[0]['body'])
        self.assertTrue(calls[0]['headers']['Content-Type'].startswith('multipart/form-data; boundary='))


if __name__ == '__main__':
    unittest.main()
