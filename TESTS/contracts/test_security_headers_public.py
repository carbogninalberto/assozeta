"""Current security response headers on actual application roots."""
import os
import unittest
from TESTS.http_client import Client
from TESTS.contracts.test_security_headers import SECURITY_HEADERS


class PublicSecurityHeadersContracts(unittest.TestCase):
    def setUp(self):
        self.client=Client(os.environ['TESTS_BASE_URL'])

    def check(self,method,path,expected,headers=None):
        status,body,actual=self.client.request(method,path,headers=headers,include_headers=True)
        self.assertEqual(status,expected)
        for key,value in SECURITY_HEADERS.items():self.assertEqual(actual.get(key),value)
        self.assertIsNone(actual.get('Strict-Transport-Security'))
        self.assertIsNone(actual.get('X-Frame-Options'))

    def test_liveness_preserves_security_headers(self):
        self.check('GET','/healthz',200)

    def test_authentication_failure_preserves_security_headers(self):
        self.check('GET','/two-fa/info',401)

    def test_csrf_and_method_denials_preserve_security_headers(self):
        self.check('POST','/healthz',403)
        self.check('HEAD','/healthz',405)

    def test_not_found_preserves_security_headers(self):
        self.check('GET','/missing-security-route',404)

    def test_preflight_still_short_circuits_security(self):
        status,body,headers=self.client.request('OPTIONS','/two-fa/info',headers={
            'Origin':'https://allowed.example.test','Access-Control-Request-Method':'GET'},include_headers=True)
        self.assertEqual((status,body),(200,''))
        for key in SECURITY_HEADERS:self.assertIsNone(headers.get(key))


if __name__=='__main__':unittest.main()
