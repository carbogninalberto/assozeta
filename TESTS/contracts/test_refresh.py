"""Portable refresh HTTP contract. Fixtures contain synthetic credentials only.

No application/ORM/Go imports. Signature verification is additional adapter work;
this contract asserts protocol, claims, cookies and denial precedence explicitly.
"""
import base64
import concurrent.futures
import json
import os
from pathlib import Path
from http.cookies import SimpleCookie
import time
import unittest

from TESTS.http_client import Client


def claims(token):
    part = token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))


class RefreshContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = Client(os.environ['TESTS_BASE_URL'])
        cls.fixture = json.loads(Path(os.environ['TESTS_REFRESH_FIXTURE']).read_text())
        documents=cls.fixture['jwt_documents']
        if len(documents)!=56 or any(sum(c['kind']==kind and c['valid']==valid for c in documents)!=count
                                    for kind in ('access','refresh') for valid,count in ((True,11),(False,17))):
            raise AssertionError('JWT document fixture must contain all declared scenarios')

    def request(self, data=None, headers=None, method='POST'):
        return self.client.request(method, '/oauth2/refresh-token', data, headers, include_headers=True)

    def test_refresh_preserves_original_token_and_claims_and_sets_cookie(self):
        status, body, headers = self.request({'refresh_token': self.fixture['refresh']})
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {'access_token', 'refresh_token', 'expires_in', 'token_type'})
        self.assertEqual(body['refresh_token'], self.fixture['refresh'])
        self.assertEqual((body['expires_in'], body['token_type']), (14400, 'Bearer'))
        before, after = claims(self.fixture['refresh']), claims(body['access_token'])
        self.assertEqual(after['token_type'], 'access')
        self.assertNotEqual(after['jti'], before['jti'])
        self.assertAlmostEqual(after['exp'], time.time() + 14400, delta=5)
        for key in ('user_id', 'role', 'email', 'iat', 'iss', 'aud', 'custom'):
            self.assertEqual(after[key], before[key])
        cookie = SimpleCookie(); cookie.load(headers.get('Set-Cookie', ''))
        self.assertEqual(set(cookie), {'BKN_AUTH'})
        value = cookie['BKN_AUTH']
        self.assertEqual(value.value, body['access_token'])
        self.assertTrue(value['httponly']); self.assertTrue(value['secure'])
        self.assertEqual((value['samesite'], value['path'], value['max-age']), ('Strict', '/', '14400'))

    def session_headers(self,kind):
        return {'Cookie':'sessionid='+self.fixture['sessions'][kind]}

    def varies_cookie(self,headers):
        self.assertIn('cookie',[value.strip().lower() for value in headers.get('Vary','').split(',')])

    def test_refresh_responses_vary_on_session_cookie(self):
        for data,status in [({'refresh_token':self.fixture['refresh']},200),({},400),({'refresh_token':'invalid'},401)]:
            result=self.request(data)
            self.assertEqual(result[0],status);self.varies_cookie(result[2])

    def test_existing_session_never_changes_refreshed_identity(self):
        for kind in ('active','inactive','deleted','social-inactive'):
            with self.subTest(kind=kind):
                status,body,headers=self.request({'refresh_token':self.fixture['refresh']},self.session_headers(kind))
                self.assertEqual(status,200);self.varies_cookie(headers)
                self.assertEqual(claims(body['access_token'])['user_id'],self.fixture['target'])
                cookie=SimpleCookie();cookie.load('; '.join(headers.get_all('Set-Cookie',[])))
                self.assertEqual(set(cookie),{'BKN_AUTH'})
                self.assertEqual(cookie['BKN_AUTH'].value,body['access_token'])

    def test_missing_and_revoked_sessions_delete_cookie_without_losing_auth_cookie(self):
        deletion='sessionid=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/; SameSite=Lax'
        for kind in ('missing','wrong'):
            with self.subTest(kind=kind):
                status,body,headers=self.request({'refresh_token':self.fixture['refresh']},self.session_headers(kind))
                self.assertEqual(status,200);self.varies_cookie(headers)
                cookies=headers.get_all('Set-Cookie',[])
                self.assertEqual(len(cookies),2);self.assertIn(deletion,cookies)
                parsed=SimpleCookie();parsed.load('; '.join(cookies))
                self.assertEqual(set(parsed),{'BKN_AUTH','sessionid'})
                self.assertEqual(parsed['BKN_AUTH'].value,body['access_token'])
        status,body,headers=self.request({'refresh_token':'invalid'},self.session_headers('missing'))
        self.assertEqual(status,401);self.varies_cookie(headers)
        self.assertEqual(headers.get_all('Set-Cookie'),[deletion])

    def test_invalid_session_precedes_refresh_method_bearer_negotiation_and_body(self):
        for kind in ('malformed','invalid-id','invalid-hash'):
            for method in ('POST','GET','OPTIONS'):
                with self.subTest(kind=kind,method=method):
                    status,body,headers=self.request(b'broken-json',{**self.session_headers(kind),'Authorization':'Bearer invalid','Accept':'text/plain','Content-Type':'application/json'},method)
                    self.assertEqual(status,500)
                    self.assertIn('Server Error (500)',body)
                    self.assertEqual(headers['Content-Type'],'text/html; charset=utf-8')
                    self.varies_cookie(headers)
                    self.assertIsNone(headers.get('Set-Cookie'))
                    self.assertIsNone(headers.get('Allow'))
                    self.assertIsNone(headers.get('WWW-Authenticate'))

    def test_session_cannot_replace_bearer_or_supply_scope(self):
        status,body,headers=self.request({'refresh_token':self.fixture['refresh']},{**self.session_headers('active'),'Authorization':'Bearer invalid'})
        self.assertEqual(status,401);self.assertEqual(body['code'],'token_not_valid')
        self.assertIsNone(headers.get('Set-Cookie'));self.varies_cookie(headers)
        status,body,headers=self.request({'refresh_token':self.fixture['refresh']},{**self.session_headers('active'),'User-Id':'00000000-0000-4000-8000-000000000008','X-Impersonation-Id':'forged','X-Group-Id':'forged'})
        self.assertEqual(status,200);self.assertEqual(claims(body['access_token'])['user_id'],self.fixture['target'])

    def test_concurrent_refresh_sessions_remain_separate(self):
        kinds=('active','inactive','deleted','social-inactive','malformed')
        def run(i):
            kind=kinds[i%len(kinds)]
            return kind,self.request({'refresh_token':self.fixture['refresh']},self.session_headers(kind))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for kind,(status,body,headers) in pool.map(run,range(32)):
                self.varies_cookie(headers)
                if kind=='malformed':
                    self.assertEqual(status,500);self.assertIn('Server Error (500)',body)
                    self.assertIsNone(headers.get('Set-Cookie'))
                else:
                    self.assertEqual(status,200)
                    self.assertEqual(claims(body['access_token'])['user_id'],self.fixture['target'])

    def test_missing_refresh_field(self):
        for data in ({}, {'irrelevant': 1}, []):
            with self.subTest(shape=type(data).__name__):
                status, body, headers = self.request(data)
                self.assertEqual((status, body), (400, {'msg': 'refresh_token required'}))
                self.assertIsNone(headers.get('Set-Cookie'))

    def test_invalid_refresh_values(self):
        for token in ('', 'not.a.token', self.fixture['access'], self.fixture['expired'], 0, False, {}, []):
            with self.subTest(value_type=type(token).__name__):
                status, body, headers = self.request({'refresh_token': token})
                self.assertEqual((status, body), (401, {'msg': 'Invalid or expired refresh token'}))
                self.assertIsNone(headers.get('WWW-Authenticate'))
                self.assertIsNone(headers.get('Set-Cookie'))

    def test_refresh_is_not_account_lookup_without_bearer(self):
        for label in ('inactive_refresh', 'deleted_refresh', 'missing_refresh'):
            with self.subTest(label=label):
                status, body, _ = self.request({'refresh_token': self.fixture[label]})
                self.assertEqual(status, 200)
                self.assertEqual(body['refresh_token'], self.fixture[label])

    def test_null_refresh_retains_simplejwt_unbound_token_behavior(self):
        status, body, _ = self.request({'refresh_token': None})
        self.assertEqual(status, 200)
        self.assertEqual(set(claims(body['refresh_token'])), {'token_type', 'exp', 'iat', 'jti'})
        self.assertNotIn('user_id', claims(body['access_token']))
        status, denied, _ = self.request({}, {'Authorization':'Bearer '+body['access_token']})
        self.assertEqual((status,denied),(401,{'detail':'Token contained no recognizable user identification','code':'token_not_valid'}))

    def test_malformed_bearer_precedes_missing_refresh_field(self):
        status, body, headers = self.request({}, {'Authorization': 'Bearer'})
        self.assertEqual(status, 401)
        self.assertEqual(body, {'detail': 'Authorization header must contain two space-delimited values', 'code': 'bad_authorization_header'})
        self.assertEqual(headers.get('WWW-Authenticate'), 'Bearer realm="api"')

    def test_inactive_bearer_precedes_valid_refresh(self):
        status, body, _ = self.request({'refresh_token': self.fixture['refresh']}, {'Authorization': 'Bearer ' + self.fixture['inactive_access']})
        self.assertEqual((status, body), (401, {'detail':'User is inactive', 'code':'user_inactive'}))

    def test_malformed_signed_account_claim_retains_server_error(self):
        status, body, headers = self.request({}, {'Authorization':'Bearer '+self.fixture['malformed_access']})
        self.assertEqual(status,500)
        self.assertIn('Server Error (500)',body)
        self.assertIsNone(headers.get('Set-Cookie'))

    def test_anonymous_ignores_impersonation_headers(self):
        status, body, _ = self.request({'refresh_token':self.fixture['refresh']}, {'X-Impersonation-Id':'forged', 'User-Id':self.fixture['target']})
        self.assertEqual(status, 200)
        self.assertEqual(body['refresh_token'], self.fixture['refresh'])

    def test_bound_athlete_operation_is_denied_before_refresh(self):
        status, body, _ = self.request({}, {'Authorization':'Bearer ' + self.fixture['owner_access'], 'X-Impersonation-Id':self.fixture['owner_session']})
        self.assertEqual((status, body), (403, {'detail':'Operazione non disponibile nel contesto dell’associazione.'}))

    def test_valid_actor_does_not_bind_refresh_to_that_actor(self):
        status, body, _ = self.request({'refresh_token':self.fixture['refresh']}, {'Authorization':'Bearer ' + self.fixture['owner_access']})
        self.assertEqual(status, 200)
        self.assertEqual(claims(body['access_token'])['user_id'], self.fixture['target'])

    def test_unsupported_method(self):
        for method in ('GET', 'HEAD', 'PUT', 'DELETE'):
            with self.subTest(method=method):
                status, body, headers = self.request(method=method)
                self.assertEqual(status, 405)
                self.assertEqual(set(headers['Allow'].split(', ')), {'POST', 'OPTIONS'})
                if method != 'HEAD':
                    self.assertEqual(body, {'detail':f'Metodo "{method}" non consentito'})

    def test_content_negotiation_precedes_authentication(self):
        status, body, _ = self.request({}, {'Accept':'text/html', 'Authorization':'Bearer'})
        self.assertEqual((status, body), (406, {'detail':"Impossibile soddisfare l'header \"Accept\" presente nella richiesta."}))

    def test_urlencoded_form_and_duplicate_last_value(self):
        from urllib.parse import urlencode
        encoded = urlencode([('refresh_token','bad'), ('refresh_token',self.fixture['refresh'])]).encode()
        status, body, _ = self.request(encoded, {'Content-Type':'application/x-www-form-urlencoded'})
        self.assertEqual(status, 200)
        self.assertEqual(body['refresh_token'], self.fixture['refresh'])

    def test_unknown_content_type(self):
        status, body, _ = self.request(b'payload', {'Content-Type':'text/plain'})
        self.assertEqual((status, body), (415, {'detail':'Tipo di media "text/plain"non supportato.'}))

    def test_empty_body_does_not_require_a_parser(self):
        status, body, _ = self.request(b'', {'Content-Type':'text/plain'})
        self.assertEqual((status, body), (400, {'msg':'refresh_token required'}))

    def test_malformed_json_reports_python312_positions(self):
        for raw, message in [
            (b'{', 'Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'),
            (b'invalid', 'Expecting value: line 1 column 1 (char 0)'),
            (b'{"a":}', 'Expecting value: line 1 column 6 (char 5)'),
            (b'[1,]', 'Expecting value: line 1 column 4 (char 3)'),
            (b'{"a":1,}', 'Expecting property name enclosed in double quotes: line 1 column 8 (char 7)'),
            (b'{"a" 1}', "Expecting ':' delimiter: line 1 column 6 (char 5)"),
            (b'1 2', 'Extra data: line 1 column 3 (char 2)'),
            (b'{} {}', 'Extra data: line 1 column 4 (char 3)'),
            (b'"unterminated', 'Unterminated string starting at: line 1 column 1 (char 0)'),
            (b'NaN', "Out of range float values are not JSON compliant: 'NaN'"),
            (b'\xff', "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"),
            (b'\xef\xbb\xbf{}', 'Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)'),
        ]:
            with self.subTest(raw=raw):
                status, body, _ = self.request(raw, {'Content-Type':'application/json'})
                self.assertEqual((status, body), (400, {'detail':'JSON parse error - ' + message}))

    def test_scope_operation_precedes_body_parse_failure(self):
        status, body, _ = self.request(b'{', {'Content-Type':'application/json', 'Authorization':'Bearer '+self.fixture['owner_access'], 'X-Impersonation-Id':self.fixture['owner_session']})
        self.assertEqual((status, body), (403, {'detail':'Operazione non disponibile nel contesto dell’associazione.'}))

    def test_options_metadata(self):
        status, body, headers = self.request(method='OPTIONS')
        self.assertEqual(status, 200)
        self.assertEqual(body, {'name':'Oauth2 Refresh Token',
            'description':'API endpoint to refresh access token using JWT.\n\nRequest body:\n    {refresh_token: string}\n\nReturns:\n    {access_token, refresh_token, expires_in, token_type}',
            'renders':['application/json'], 'parses':['application/json','application/x-www-form-urlencoded','multipart/form-data']})
        self.assertEqual(set(headers['Allow'].split(', ')), {'POST','OPTIONS'})

    def test_multipart_fields(self):
        boundary='refresh-contract-boundary'
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="refresh_token"\r\n\r\n'
              f'{self.fixture["refresh"]}\r\n--{boundary}--\r\n').encode()
        status, data, _ = self.request(body, {'Content-Type':f'multipart/form-data; boundary={boundary}'})
        self.assertEqual(status, 200)
        self.assertTrue(data['refresh_token']==self.fixture['refresh'], 'refresh credential changed')

    def test_bkn_cookie_does_not_authenticate_rest(self):
        status, body, _ = self.request({}, {'Cookie':'BKN_AUTH=invalid'})
        self.assertEqual((status, body), (400, {'msg':'refresh_token required'}))

    def test_invalid_bearer_body(self):
        status, body, headers = self.request({}, {'Authorization':'Bearer invalid'})
        self.assertEqual(status,401)
        self.assertEqual(body, {'detail':'Given token not valid for any token type','code':'token_not_valid',
            'messages':[{'token_class':'AccessToken','token_type':'access','message':'Token is invalid or expired'}]})
        self.assertEqual(headers['WWW-Authenticate'],'Bearer realm="api"')

    def test_invalid_impersonation_has_legacy_string_flag(self):
        status, body, _ = self.request({}, {'Authorization':'Bearer '+self.fixture['owner_access'],'X-Impersonation-Id':'forged'})
        self.assertEqual((status,body), (403,{'detail':'Sessione di impersonificazione non valida. Torna al tuo account.','impersonation_invalid':'True'}))

    def test_english_method_error(self):
        status, body, headers = self.request(method='GET', headers={'Accept-Language':'en'})
        self.assertEqual((status,body), (405, {'detail':'Method "GET" not allowed.'}))
        self.assertEqual(headers['Content-Language'],'en')

    def test_latin1_json_charset(self):
        raw=json.dumps({'extra':'café','refresh_token':self.fixture['refresh']},ensure_ascii=False).encode('latin-1')
        status, body, _ = self.request(raw, {'Content-Type':'application/json; charset=iso-8859-1'})
        self.assertEqual(status,200)
        self.assertTrue(body['refresh_token']==self.fixture['refresh'], 'refresh credential changed')

    def test_file_field_is_not_a_textual_refresh_token(self):
        boundary='refresh-file-contract'
        raw=(f'--{boundary}\r\nContent-Disposition: form-data; name="refresh_token"; filename="token.txt"\r\n'
            f'Content-Type: text/plain\r\n\r\n{self.fixture["refresh"]}\r\n--{boundary}--\r\n').encode()
        status,body,_=self.request(raw,{'Content-Type':f'multipart/form-data; boundary={boundary}'})
        self.assertEqual((status,body),(401,{'msg':'Invalid or expired refresh token'}))

    def test_signed_invalid_document_bearer_precedes_refresh(self):
        for case in self.fixture['jwt_documents']:
            if case['kind']!='access' or case['valid']:continue
            with self.subTest(case=case['name']):
                status,body,headers=self.request({'refresh_token':self.fixture['refresh']},{'Authorization':'Bearer '+case['token']})
                self.assertEqual((status,body),(401,{'detail':'Given token not valid for any token type','code':'token_not_valid',
                    'messages':[{'token_class':'AccessToken','token_type':'access','message':'Token is invalid or expired'}]}))
                self.assertEqual(headers.get('WWW-Authenticate'),'Bearer realm="api"')
                self.assertIsNone(headers.get('Set-Cookie'))

    def test_signed_valid_document_bearer_reaches_body_validation(self):
        for case in self.fixture['jwt_documents']:
            if case['kind']!='access' or not case['valid']:continue
            with self.subTest(case=case['name']):
                status,body,headers=self.request({}, {'Authorization':'Bearer '+case['token']})
                self.assertEqual((status,body),(400,{'msg':'refresh_token required'}))
                self.assertIsNone(headers.get('Set-Cookie'))

    def test_signed_invalid_refresh_documents_never_issue_cookie(self):
        for case in self.fixture['jwt_documents']:
            if case['kind']!='refresh' or case['valid']:continue
            with self.subTest(case=case['name']):
                status,body,headers=self.request({'refresh_token':case['token']})
                self.assertEqual((status,body),(401,{'msg':'Invalid or expired refresh token'}))
                self.assertIsNone(headers.get('Set-Cookie'))

    def test_valid_refresh_documents_are_normalized_without_claim_rotation(self):
        for case in self.fixture['jwt_documents']:
            if case['kind']!='refresh' or not case['valid']:continue
            with self.subTest(case=case['name']):
                status,body,headers=self.request({'refresh_token':case['token']})
                self.assertEqual(status,200)
                self.assertTrue(body['refresh_token']==case['normalized'],'refresh document serialization differs')
                before,after=claims(case['token']),claims(body['refresh_token'])
                self.assertEqual(after,before)
                access=claims(body['access_token'])
                for name,value in before.items():
                    if name not in ('token_type','exp','jti'):self.assertEqual(access[name],value)
                self.assertNotEqual(access['jti'],before['jti'])
                self.assertAlmostEqual(access['exp'],time.time()+14400,delta=5)
                cookie=SimpleCookie();cookie.load(headers.get('Set-Cookie',''))
                self.assertEqual(set(cookie),{'BKN_AUTH'})
                self.assertTrue(cookie['BKN_AUTH'].value==body['access_token'],'cookie does not contain issued access token')
