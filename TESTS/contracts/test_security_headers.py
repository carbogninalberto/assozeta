"""Portable current security-header policy over controlled HTTP terminals."""
import concurrent.futures
import os
import unittest
from TESTS.http_client import Client


SECURITY_HEADERS={'X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','Cross-Origin-Opener-Policy':'same-origin'}


class SecurityHeadersContracts(unittest.TestCase):
    def setUp(self):
        self.client=Client(os.environ['TESTS_BASE_URL'])

    def defaults(self,headers):
        for key,value in SECURITY_HEADERS.items():self.assertEqual(headers.get(key),value)
        self.assertIsNone(headers.get('Strict-Transport-Security'))
        self.assertIsNone(headers.get('X-Frame-Options'))

    def test_defaults_preserve_terminal_status_for_all_methods(self):
        for method in ('GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'):
            for code in (200,400,401,403,404,500):
                status,body,headers=self.client.request(method,f'/terminal/{code}',include_headers=True)
                self.assertEqual(status,code)
                self.defaults(headers)

    def test_explicit_terminal_values_including_empty_are_not_replaced(self):
        for value in ('fixture-policy',''):
            status,body,headers=self.client.request('GET','/terminal/200',headers={'X-Fixture-Security':value},include_headers=True)
            self.assertEqual(status,200)
            for key in SECURITY_HEADERS:self.assertEqual(headers.get(key),value)

    def test_forwarded_https_does_not_add_hsts_or_redirect(self):
        for value in ('https','http','https,http'):
            status,body,headers=self.client.request('GET','/terminal/200',headers={'X-Forwarded-Proto':value},include_headers=True)
            self.assertEqual(status,200)
            self.assertIsNone(headers.get('Location'))
            self.defaults(headers)

    def test_streaming_response_has_defaults_before_first_chunk(self):
        status,body,headers=self.client.request('GET','/stream',include_headers=True)
        self.assertEqual((status,body),(200,'AB'))
        self.defaults(headers)

    def test_cors_preflight_bypasses_security_middleware(self):
        status,body,headers=self.client.request('OPTIONS','/missing',headers={
            'Origin':'https://allowed.example.test','Access-Control-Request-Method':'POST'},include_headers=True)
        self.assertEqual((status,body),(200,''))
        for key in SECURITY_HEADERS:self.assertIsNone(headers.get(key))
        self.assertEqual(headers['Access-Control-Allow-Origin'],'*')

    def test_concurrent_custom_and_default_policies_do_not_leak(self):
        def run(i):
            custom=i%2==1
            return custom,self.client.request('GET','/terminal/200',headers={'X-Fixture-Security':'fixture-policy'} if custom else {},include_headers=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for custom,(status,body,headers) in pool.map(run,range(32)):
                self.assertEqual(status,200)
                for key,value in SECURITY_HEADERS.items():self.assertEqual(headers[key],'fixture-policy' if custom else value)


if __name__=='__main__':unittest.main()
