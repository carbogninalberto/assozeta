"""Portable CORS protocol over a controlled terminal; no business handler claims."""
import concurrent.futures
import hashlib
import os
import unittest
from TESTS.http_client import Client


ALLOW_HEADERS='accept, authorization, content-type, user-agent, x-csrftoken, x-requested-with, user-id, x-impersonation-id, x-group-id, x-setup-token'
ALLOW_METHODS='DELETE, GET, OPTIONS, PATCH, POST, PUT'


class CORSContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=Client(os.environ['TESTS_BASE_URL'])
        cls.policy=os.environ['TESTS_CORS_POLICY']
        assert cls.policy in ('none','allowlist','null','all')

    def request(self,*,origin=None,method='GET',headers=None,path='/terminal/200',data=None):
        values=dict(headers or {})
        if origin is not None:values['Origin']=origin
        return self.client.request(method,path,data=data,headers=values,include_headers=True)

    def cors(self,headers,origin,allowed):
        expected=('*' if self.policy=='all' else origin) if allowed else None
        self.assertEqual(headers.get('Access-Control-Allow-Origin'),expected)
        self.assertIsNone(headers.get('Access-Control-Allow-Credentials'))
        self.assertIsNone(headers.get('Access-Control-Expose-Headers'))
        self.assertIsNone(headers.get('Access-Control-Allow-Private-Network'))
        self.assertIn('origin',[value.strip().lower() for value in headers['Vary'].split(',')])

    def test_origin_decisions_preserve_scheme_netloc_and_literal_rules(self):
        cases=[('https://allowed.example.test',{'allowlist','all'}),
               ('HTTPS://allowed.example.test',{'allowlist','all'}),
               ('https://allowed.example.test/path?query#fragment',{'allowlist','all'}),
               ('https://ALLOWED.example.test',{'all'}),
               ('https://allowed.example.test:443',{'all'}),
               ('http://allowed.example.test',{'all'}),
               ('https://foreign.example.test',{'all'}),
               ('https://café.example.test',{'allowlist','all'}),
               ('null',{'null','all'}),
               ('relative-origin',{'null','all'}),
               ('//allowed.example.test',{'all'}),
               ('https://[invalid',set()),
               ('https://[127.0.0.1]',set()),
               ('https://[::1]',{'all'}),
               ('https://[vF.future]',{'all'}),
               ('https://[VF.future]',{'all'}),
               ('https://[VX.future]',set()),
               ('https://[vX.future]',set()),
               ('https://[fe80::1%zone]',{'all'}),
               ('https://[fe80::1%]',set()),
               ('https://allowed.example.test:bad-port',{'all'}),
               ('https://allowed.example.test/%broken',{'allowlist','all'}),
               ('https://%broken',{'all'}),
               ('https://]host[::1',set()),
               ('https://prefix[::1]',set()),
               ('https://[::1]suffix',set()),
               ('https://user[::1]@host',set()),
               ('https://[x]@[::1]',{'all'})]
        for origin,policies in cases:
            with self.subTest(origin=origin):
                status,body,headers=self.request(origin=origin)
                self.assertEqual(status,200)
                self.cors(headers,origin,self.policy in policies)

    def test_missing_and_empty_origin_only_add_vary(self):
        for origin in (None,''):
            status,body,headers=self.request(origin=origin)
            self.assertEqual(status,200);self.cors(headers,origin,False)
            self.assertIsNone(headers.get('Access-Control-Allow-Headers'))

    def test_preflight_bypasses_terminal_even_when_origin_is_not_allowed(self):
        for origin in (None,'https://allowed.example.test','https://foreign.example.test','https://[invalid'):
            status,body,headers=self.request(origin=origin,method='OPTIONS',headers={'Access-Control-Request-Method':'POST'},path='/missing')
            self.assertEqual((status,body),(200,''))
            self.assertEqual(headers['Content-Length'],'0')
            self.assertEqual(headers['Content-Type'],'text/html; charset=utf-8')
            allowed=origin=='https://allowed.example.test' and self.policy in ('allowlist','all') or origin=='https://foreign.example.test' and self.policy=='all'
            self.cors(headers,origin,allowed)
            self.assertIsNone(headers.get('Allow'))

    def test_empty_or_unknown_requested_method_is_still_preflight(self):
        for requested in ('','BREW'):
            status,body,headers=self.request(origin='https://allowed.example.test',method='OPTIONS',headers={'Access-Control-Request-Method':requested},path='/missing')
            self.assertEqual((status,body),(200,''))
            self.cors(headers,'https://allowed.example.test',self.policy in ('allowlist','all'))

    def test_allowed_options_headers_use_fixed_server_policy(self):
        origin='null' if self.policy=='null' else 'https://allowed.example.test'
        for headers in ({},{'Access-Control-Request-Method':'PATCH','Access-Control-Request-Headers':'x-not-allowed, authorization'}):
            status,body,actual=self.request(origin=origin,method='OPTIONS',headers=headers)
            allowed=self.policy!='none'
            self.cors(actual,origin,allowed)
            self.assertEqual(actual.get('Access-Control-Allow-Headers'),ALLOW_HEADERS if allowed else None)
            self.assertEqual(actual.get('Access-Control-Allow-Methods'),ALLOW_METHODS if allowed else None)
            self.assertEqual(actual.get('Access-Control-Max-Age'),'86400' if allowed else None)
            if not headers:self.assertEqual(body['method'],'OPTIONS')
            else:self.assertEqual(body,'')

    def test_requested_private_network_does_not_enable_it(self):
        status,body,headers=self.request(origin='https://allowed.example.test',method='OPTIONS',
            headers={'Access-Control-Request-Method':'GET','Access-Control-Request-Private-Network':'true'})
        self.assertEqual((status,body),(200,''))
        self.cors(headers,'https://allowed.example.test',self.policy in ('allowlist','all'))

    def test_simple_requests_do_not_gain_preflight_headers(self):
        for method in ('GET','POST','PUT','PATCH','DELETE','HEAD'):
            status,body,headers=self.request(origin='https://allowed.example.test',method=method)
            self.assertEqual(status,200)
            self.cors(headers,'https://allowed.example.test',self.policy in ('allowlist','all'))
            for key in ('Access-Control-Allow-Methods','Access-Control-Allow-Headers','Access-Control-Max-Age'):self.assertIsNone(headers.get(key))

    def test_existing_vary_is_preserved_and_origin_deduplicated(self):
        for value,expected in [('Cookie','Cookie, origin'),('Origin','Origin'),('Accept-Encoding, Cookie','Accept-Encoding, Cookie, origin'),
                               ('',''+', origin'),('*','*')]:
            status,body,headers=self.request(headers={'X-Fixture-Vary':value})
            self.assertEqual(status,200);self.assertEqual(headers['Vary'],expected)

    def test_error_statuses_retain_cors_headers(self):
        for code in (400,401,403,404,500):
            status,body,headers=self.request(origin='https://allowed.example.test',path=f'/terminal/{code}')
            self.assertEqual(status,code)
            self.cors(headers,'https://allowed.example.test',self.policy in ('allowlist','all'))

    def test_request_body_is_unchanged(self):
        raw=b'\x00\xffcors-body\r\n'*8192
        status,body,headers=self.request(origin='https://allowed.example.test',method='POST',data=raw,headers={'Content-Type':'application/octet-stream'})
        self.assertEqual((status,body),(200,{'method':'POST','sha256':hashlib.sha256(raw).hexdigest()}))
        self.cors(headers,'https://allowed.example.test',self.policy in ('allowlist','all'))

    def test_streaming_response_has_headers_before_first_chunk(self):
        status,body,headers=self.request(origin='https://allowed.example.test',path='/stream')
        self.assertEqual((status,body),(200,'AB'))
        self.cors(headers,'https://allowed.example.test',self.policy in ('allowlist','all'))
        self.assertEqual(headers['Vary'],'Cookie, origin')

    def test_concurrent_origins_keep_response_policy_separate(self):
        def run(i):
            origin='https://allowed.example.test' if i%2 else 'https://foreign.example.test'
            return origin,self.request(origin=origin)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for origin,(status,body,headers) in pool.map(run,range(32)):
                self.assertEqual(status,200)
                self.cors(headers,origin,self.policy=='all' or self.policy=='allowlist' and origin=='https://allowed.example.test')


class CORSInvalidConfigurationContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=Client(os.environ['TESTS_BASE_URL'])

    def count(self):
        status,body=self.client.request('GET','/count')
        self.assertEqual(status,200)
        return body['calls']

    def test_invalid_configuration_replaces_response_after_terminal_executes(self):
        before=self.count()
        status,body,headers=self.client.request('POST','/terminal/201',data=b'payload',headers={'Origin':'https://allowed.example.test'},include_headers=True)
        self.assertEqual(status,500)
        self.assertIn('Server Error (500)',body)
        self.assertEqual(headers['Content-Type'],'text/html; charset=utf-8')
        self.assertIsNone(headers.get('Vary'))
        self.assertIsNone(headers.get('Access-Control-Allow-Origin'))
        self.assertEqual(self.count(),before+1)

    def test_invalid_configuration_preflight_never_executes_terminal(self):
        before=self.count()
        status,body,headers=self.client.request('OPTIONS','/terminal/201',headers={'Origin':'https://allowed.example.test','Access-Control-Request-Method':'POST'},include_headers=True)
        self.assertEqual(status,500)
        self.assertIn('Server Error (500)',body)
        self.assertIsNone(headers.get('Vary'))
        self.assertEqual(self.count(),before)

    def test_missing_invalid_or_literal_null_origin_short_circuits_bad_list(self):
        for origin in (None,'','https://[invalid','null'):
            status,body,headers=self.client.request('GET','/terminal/200',headers={} if origin is None else {'Origin':origin},include_headers=True)
            self.assertEqual(status,200)
            self.assertEqual(headers['Vary'],'origin')
            self.assertEqual(headers.get('Access-Control-Allow-Origin'),'null' if origin=='null' else None)


if __name__=='__main__':unittest.main()
