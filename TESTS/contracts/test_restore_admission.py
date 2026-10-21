"""Portable restore exclusion on real application roots; no fixture mutations."""
import concurrent.futures
import os
import unittest
from TESTS.http_client import Client


class RestoreAdmissionContracts(unittest.TestCase):
    def setUp(self):
        self.client=Client(os.environ['TESTS_BASE_URL'])
        self.mode=os.environ['TESTS_RESTORE_MODE']
        assert self.mode in ('open','queued','running','locked','unavailable','disabled')

    def request(self,method,path,normal,*,headers=None,data=None):
        actual={'Origin':'https://allowed.example.test',**(headers or {})}
        return self.client.request(method,path,headers=actual,data=data,include_headers=True)

    def ordinary(self,method,path,normal,*,headers=None):
        blocked=self.mode in ('locked','unavailable') or self.mode in ('queued','running') and method not in ('GET','HEAD')
        status,body,actual=self.request(method,path,normal,headers=headers)
        self.assertEqual(status,503 if blocked else normal)
        self.assertEqual(actual['Access-Control-Allow-Origin'],'https://allowed.example.test')
        if blocked:
            message={'locked':'Un’altra operazione sui dati è in corso. Riprova tra poco.',
                     'unavailable':'Il volume di coordinamento non è disponibile.'}.get(self.mode,
                     'Ripristino dei dati in corso. Le modifiche sono temporaneamente sospese.')
            if method!='HEAD':self.assertEqual(body,{'error':message})
            self.assertEqual(actual['Retry-After'],'10')
            self.assertIsNone(actual.get('Set-Cookie'))
            self.assertIsNone(actual.get('X-Content-Type-Options'))
        else:self.assertIsNone(actual.get('Retry-After'))

    def test_ordinary_reads_block_only_on_exclusive_or_unavailable_lock(self):
        for method,path,normal in [('GET','/oauth2/check/username?username=restore-unused',200),
                                   ('GET','/two-fa/info',401),('HEAD','/two-fa/info',401),('GET','/restore-missing-route',404)]:
            self.ordinary(method,path,normal)

    def test_pending_restore_excludes_mutations_before_authentication_or_csrf(self):
        for method,path,normal in [('POST','/billing/checkout',401),('POST','/two-fa/info',401),
                                   ('PUT','/two-fa/info',401),('POST','/healthz',403)]:
            self.ordinary(method,path,normal)

    def test_health_refresh_and_restore_prefix_remain_usable(self):
        for method,path,normal in [('GET','/healthz',200),('GET','/readyz',200),('HEAD','/healthz',405),
                                   ('GET','/oauth2/refresh-token',405),('GET','/instance/admin/data-restore/unknown',404)]:
            status,body,headers=self.request(method,path,normal)
            self.assertEqual(status,normal)
            self.assertIsNone(headers.get('Retry-After'))
            self.assertEqual(headers['X-Content-Type-Options'],'nosniff')

    def test_normal_options_bypasses_restore_admission(self):
        for path,expected in [('/two-fa/info',401),('/restore-missing-route',404)]:
            status,body,headers=self.request('OPTIONS',path,expected)
            self.assertEqual(status,expected)
            self.assertIsNone(headers.get('Retry-After'))
            self.assertEqual(headers['X-Content-Type-Options'],'nosniff')

    def test_cors_preflight_still_bypasses_all_inner_admission(self):
        status,body,headers=self.request('OPTIONS','/two-fa/info',200,headers={'Access-Control-Request-Method':'POST'})
        self.assertEqual((status,body),(200,''))
        self.assertIsNone(headers.get('Retry-After'))
        self.assertIsNone(headers.get('X-Content-Type-Options'))
        self.assertIsNone(headers.get('Set-Cookie'))

    def test_restore_denial_precedes_malformed_session_loading(self):
        for method in ('GET','POST'):
            self.ordinary(method,'/two-fa/info',500,headers={'Cookie':'sessionid=restore-malformed','Authorization':'Bearer invalid'})

    def test_concurrent_reads_and_writes_keep_independent_admission(self):
        def run(i):
            method='GET' if i%2 else 'POST'
            self.ordinary(method,'/two-fa/info',401)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(run,range(32)))


if __name__=='__main__':unittest.main()
