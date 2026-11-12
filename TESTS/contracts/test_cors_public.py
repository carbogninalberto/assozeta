"""Portable CORS contracts against actual application roots with an allowlist."""
import os
import unittest
from TESTS.http_client import Client
from TESTS.contracts.test_cors import ALLOW_HEADERS, ALLOW_METHODS


class PublicCORSContracts(unittest.TestCase):
    def setUp(self):
        self.client=Client(os.environ['TESTS_BASE_URL'])
        self.origin='https://allowed.example.test'

    def test_preflight_precedes_route_authentication_and_session(self):
        for path in ('/healthz','/readyz','/oauth2/refresh-token','/two-fa/info','/billing/checkout','/missing-cors-route'):
            status,body,headers=self.client.request('OPTIONS',path,headers={
                'Origin':self.origin,'Access-Control-Request-Method':'POST',
                'Authorization':'Bearer invalid','Cookie':'sessionid=refresh-session-malformed',
                'X-Impersonation-Id':'invalid','X-Group-Id':'invalid'},include_headers=True)
            self.assertEqual((status,body),(200,''))
            self.assertEqual(headers['Access-Control-Allow-Origin'],self.origin)
            self.assertEqual(headers['Access-Control-Allow-Headers'],ALLOW_HEADERS)
            self.assertEqual(headers['Access-Control-Allow-Methods'],ALLOW_METHODS)
            self.assertEqual(headers['Access-Control-Max-Age'],'86400')
            vary={v.strip().lower() for v in headers['Vary'].split(',')}
            self.assertIn('origin',vary)
            self.assertNotIn('cookie',vary)
            self.assertIsNone(headers.get('Set-Cookie'))
            self.assertIsNone(headers.get('Allow'))

    def test_health_response_has_cors_and_preserves_body(self):
        status,body,headers=self.client.request('GET','/healthz',headers={'Origin':self.origin},include_headers=True)
        self.assertEqual(status,200)
        self.assertEqual(body['status'],'ok')
        self.assertEqual(headers['Access-Control-Allow-Origin'],self.origin)
        self.assertIn('origin',headers['Vary'].lower())

    def test_authentication_failure_has_cors_and_session_vary(self):
        status,body,headers=self.client.request('GET','/two-fa/info',headers={'Origin':self.origin},include_headers=True)
        self.assertEqual(status,401)
        self.assertEqual(headers['Access-Control-Allow-Origin'],self.origin)
        self.assertTrue({'cookie','origin'} <= {v.strip().lower() for v in headers['Vary'].split(',')})

    def test_disallowed_and_malformed_origins_do_not_change_preflight_status(self):
        for origin in ('https://foreign.example.test','https://[invalid',''):
            status,body,headers=self.client.request('OPTIONS','/missing-cors-route',headers={
                'Origin':origin,'Access-Control-Request-Method':''},include_headers=True)
            self.assertEqual((status,body),(200,''))
            self.assertIsNone(headers.get('Access-Control-Allow-Origin'))
            self.assertIn('origin',{v.strip().lower() for v in headers['Vary'].split(',')})


if __name__=='__main__':unittest.main()
