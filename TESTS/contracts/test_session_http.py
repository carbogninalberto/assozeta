"""Portable session/audit boundary; the adapter supplies a controlled terminal.

Reset seeds documented synthetic Django sessions. The terminal reports the
pre-view audit context and unchanged body digest, not a login or API permission.
"""
import concurrent.futures
import hashlib
import os
import unittest
from TESTS.http_client import Client

OWNER='00000000-0000-4000-8000-000000000001'
DELETED='00000000-0000-4000-8000-000000000004'
INACTIVE='00000000-0000-4000-8000-000000000003'
DELETE_COOKIE='sessionid=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/; SameSite=Lax'


class SessionHTTPContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.client=Client(os.environ['TESTS_BASE_URL'])
    def setUp(self):
        self.assertEqual(self.client.request('POST','/__fixtures/session/reset'),(200,{'reset':True}))
    def request(self,cookie=None,*,path='/oauth2/login',method='POST',headers=None,raw=b'{"user_id":"forged"}'):
        h={'Content-Type':'application/json',**(headers or {})}
        if cookie is not None:h['Cookie']=cookie
        return self.client.request(method,path,raw,h,include_headers=True)
    def check(self,result,*,actor=None,remote='127.0.0.1',port=None,cid=None,status=200,deleted=False,vary='Cookie',raw=b'{"user_id":"forged"}'):
        self.assertEqual(result[:2],(status,{'audit':{'actor_id':actor,'actor_email':'shared@example.test' if actor else None,
            'remote_address':remote,'remote_port':port,'correlation_id':cid},'sha256':hashlib.sha256(raw).hexdigest()}))
        self.assertEqual(result[2].get('Vary'),vary)
        self.assertEqual(result[2].get_all('Set-Cookie',[]),[DELETE_COOKIE] if deleted else [])
    def test_no_cookie_still_varies(self):self.check(self.request())
    def test_active_session(self):self.check(self.request('sessionid=http-session-active'),actor=OWNER)
    def test_inactive_and_deleted_backend_rules(self):
        for key,actor in [('inactive',None),('deleted',DELETED),('social-inactive',INACTIVE)]:
            with self.subTest(key=key):self.check(self.request('sessionid=http-session-'+key),actor=actor)
    def test_missing_short_empty_and_corrupt_sessions_delete_cookie(self):
        for key in ('','short','http-session-missing','http-session-corrupt','http-session-none'):
            with self.subTest(key=key):self.check(self.request('sessionid='+key),deleted=True)
    def test_empty_dictionary_and_oauth_state_keep_cookie(self):
        for key in ('empty','oauth'):self.check(self.request('sessionid=http-session-'+key))
    def test_wrong_hash_deletes_cookie_on_success_and_error_responses(self):
        self.check(self.request('sessionid=http-session-wrong'),deleted=True)
        self.setUp()
        self.check(self.request('sessionid=http-session-wrong',path='/oauth2/status/503'),status=503,deleted=True)
    def test_cookie_is_not_reissued_on_read(self):
        for _ in range(3):self.check(self.request('sessionid=http-session-active'),actor=OWNER)
    def test_last_duplicate_cookie_wins(self):
        self.check(self.request('sessionid=missing; sessionid=http-session-active'),actor=OWNER)
        self.check(self.request('sessionid=http-session-active; sessionid=short'),deleted=True)
    def test_quoted_and_octal_cookie_values(self):
        for cookie in ('sessionid="http-session-active"','sessionid="http-session-\\141ctive"','sessionid="http-session-\\active"',
                       'sessionid="'+r'\350'*8+'"','sessionid='+'è'*8):
            with self.subTest(cookie=cookie):self.check(self.request(cookie),actor=OWNER)
        # Shipped ASGI uses Latin-1 for Cookie too; UTF-8 wire bytes are not
        # recovered into the Unicode key as they would be by WSGIRequest.
        self.check(self.request('sessionid='+('è'*8).encode().decode('latin1')),deleted=True)
        self.check(self.request('sessionid='+(b'\xe2\x82'*8).decode('latin1')),deleted=True)
    def test_cookie_name_whitespace_case_and_unrelated_malformed_fields(self):
        self.check(self.request('broken; other==x; sessionid = http-session-active ; empty='),actor=OWNER)
        self.check(self.request('Sessionid=http-session-active'))
        self.check(self.request('other=http-session-active'))
        self.check(self.request('\xa0sessionid=http-session-active\xa0'),actor=OWNER)
    def test_cookie_percent_encoding_is_literal(self):
        self.check(self.request('sessionid=http-session-%61ctive'),deleted=True)
    def test_vary_preserves_order_deduplicates_case_and_star(self):
        for value,want in [('Accept-Language, Origin','Accept-Language, Origin, Cookie'),('cookie, Origin','cookie, Origin'),
                           ('Origin,cookie','Origin, cookie'),('Origin,  Accept-Language','Origin, Accept-Language, Cookie'),('',', Cookie'),('*','*')]:
            with self.subTest(value=value):self.check(self.request(headers={'X-Fixture-Vary':value}),vary=want)
    def test_other_response_cookies_survive(self):
        result=self.request('sessionid=short',headers={'X-Fixture-Other-Cookie':'yes'})
        self.assertEqual(result[0],200)
        self.assertEqual(sorted(result[2].get_all('Set-Cookie')),sorted([DELETE_COOKIE,'fixture=retained; Path=/']))
        self.assertEqual(result[2]['Vary'],'Cookie')
    def test_handler_session_cookie_is_replaced_on_deletion(self):
        self.check(self.request('sessionid=short',headers={'X-Fixture-Session-Cookie':'yes'}),deleted=True)
    def test_session_headers_precede_streaming_body(self):
        self.check(self.request('sessionid=short',headers={'X-Fixture-Stream':'yes','X-Fixture-Vary':'Origin'}),deleted=True,vary='Origin, Cookie')
    def test_bearer_and_forged_scope_do_not_supply_audit_actor(self):
        headers={'Authorization':'Bearer forged','User-Id':DELETED,'X-Impersonation-Id':'forged','X-Group-Id':'forged'}
        self.check(self.request(headers=headers))
        self.check(self.request('sessionid=http-session-active',headers=headers),actor=OWNER)
    def test_auth_route_prefixes_preserve_session(self):
        for path in ('/oauth2/login','/auth/callback','/accounts/profile','/admin/fixture','/other'):
            with self.subTest(path=path):self.check(self.request('sessionid=http-session-active',path=path,headers={'Authorization':'Bearer forged'}),actor=OWNER)
    def test_raw_api_bearer_skip_preserves_django_authentication_failure(self):
        result=self.request('sessionid=http-session-active',path='/api/fixture',headers={'Authorization':'Bearer forged'})
        self.assertEqual(result[0],500);self.assertIn('Server Error (500)',result[1])
        self.assertIsNone(result[2].get('Set-Cookie'));self.assertIsNone(result[2].get('Vary'))
        self.check(self.request('sessionid=http-session-active',path='/api/fixture',headers={'Authorization':'bearer forged'}),actor=OWNER)
    def session_error(self,key):
        result=self.request('sessionid=http-session-'+key)
        self.assertEqual(result[0],500)
        self.assertEqual(result[1],'\n<!doctype html>\n<html lang="en">\n<head>\n  <title>Server Error (500)</title>\n</head>\n<body>\n  <h1>Server Error (500)</h1><p></p>\n</body>\n</html>\n')
        self.assertEqual(result[2]['Content-Type'],'text/html; charset=utf-8')
        self.assertEqual(result[2]['Vary'],'Cookie')
        self.assertIsNone(result[2].get('Set-Cookie'))
    def test_malformed_cache_types_fail_without_cookie_revocation(self):
        for kind in ('list','tuple','bool','integer','string','bytes','float'):
            with self.subTest(kind=kind):self.session_error('invalid-'+kind)
    def test_malformed_user_ids_fail_without_cookie_revocation(self):
        for kind in ('string','list','float','negative'):
            with self.subTest(kind=kind):self.session_error('invalid-id-'+kind)
    def test_malformed_user_id_is_checked_before_backend_selection(self):
        self.session_error('invalid-id-unknown-backend')
    def test_oversized_hash_integer_fails_without_cookie_revocation(self):
        for kind in ('integer','list','dict','tuple'):
            with self.subTest(kind=kind):self.session_error('invalid-hash-'+kind)
    def test_methods_and_body_are_preserved(self):
        for method in ('GET','POST','PUT','PATCH','DELETE','OPTIONS'):
            self.check(self.request('sessionid=http-session-active',method=method,raw=b'\x00\xffbody'),actor=OWNER,raw=b'\x00\xffbody')
    def test_forwarded_address_rules(self):
        for raw,want in [('203.0.113.4, 192.0.2.3','203.0.113.4'),('203.0.113.4:9000','203.0.113.4'),
                         ('[2001:db8::1]:9000','2001:db8::1'),('2001:db8::1','2001:db8::1'),
                         ('203.0.113.4 , 192.0.2.3','203.0.113.4 '),(',192.0.2.3',''),('','127.0.0.1')]:
            with self.subTest(raw=raw):self.check(self.request(headers={'X-Forwarded-For':raw}),remote=want)
    def test_forwarded_port_integer_rules(self):
        for raw,want in [('443',443),('+00443',443),('4_43',443),('\xa0+00443\xa0',443),('-1',-1),('0',0),('bad',None),('4.0',None),('',None),
                         (str(2**80),2**80),('9'*4300,int('9'*4300)),('9'*4301,None)]:
            with self.subTest(length=len(raw)):self.check(self.request(headers={'X-Forwarded-Port':raw}),port=want)
    def test_correlation_absent_empty_and_supplied(self):
        self.check(self.request())
        for value in ('','fixture-cid','café'):self.check(self.request(headers={'X-Correlation-Id':value}),cid=value)
        self.check(self.request(headers={'X-Correlation-Id':'\xa0café\xa0'}),cid='\xa0café\xa0')
    def test_concurrent_audit_context_is_request_local(self):
        def run(i):
            cid='session-request-'+str(i)
            key,actor=[('active',OWNER),('deleted',DELETED),('inactive',None),('social-inactive',INACTIVE),(None,None)][i%5]
            return cid,actor,self.request('sessionid=http-session-'+key if key else None,headers={'X-Correlation-Id':cid})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for cid,actor,result in pool.map(run,range(32)):self.check(result,actor=actor,cid=cid)


if __name__=='__main__':unittest.main()
