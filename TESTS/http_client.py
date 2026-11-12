"""Standard-library contract client; redirects are returned, never followed.

Callers observe the original status/body/headers and credentials stay on the
requested backend. Contracts requiring a redirect must assert that response.
"""
import json
import time
import urllib.error
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, token=None, timeout=15):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.timeout = timeout
        self._opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, data=None, headers=None, raw=False, include_headers=False):
        headers = dict(headers or {})
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        if data is not None and not isinstance(data, bytes):
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            response = self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            response_headers = response.headers
            content = response.read()
            if raw:
                result = (response.status, content)
                return (*result, response_headers) if include_headers else result
            try:
                content = json.loads(content)
            except (ValueError, UnicodeDecodeError):
                content = content.decode(errors='replace')[:1000]
            result = (response.status, content)
            return (*result, response_headers) if include_headers else result

    def login(self, account):
        status, response = self.request('POST', '/oauth2/login', account)
        if status != 200 or not response.get('access_token'):
            raise AssertionError(f'Login failed: HTTP {status}: {response}')
        return Client(self.base_url, response['access_token'], self.timeout), response

    def upload(self, path, filename, content, headers=None):
        boundary = 'contract-' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                'Content-Type: application/zip\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
        return self.request('POST', path, body, {**(headers or {}), 'Content-Type': f'multipart/form-data; boundary={boundary}'})


def poll(read, ready, timeout=30, interval=0.1):
    deadline = time.monotonic() + timeout
    while True:
        result = read()
        if ready(result):
            return result
        if time.monotonic() >= deadline:
            raise AssertionError(f'Timed out waiting for operation: {result}')
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
