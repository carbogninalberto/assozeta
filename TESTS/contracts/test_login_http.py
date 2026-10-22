"""Portable public-login contracts; fixture preparation and durable readback are adapters."""
import concurrent.futures
import base64
import hashlib
import hmac
from http.cookies import SimpleCookie
import json
import os
import struct
import time
import unittest

from TESTS.http_client import Client


def claims(token):
    encoded=token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))


def otp(secret):
    remaining=30-time.time()%30
    if remaining<5:time.sleep(remaining+.01)
    digest=hmac.new(base64.b32decode(secret),struct.pack('>Q',int(time.time())//30),hashlib.sha1).digest()
    offset=digest[-1]&15
    return f'{(int.from_bytes(digest[offset:offset+4],"big")&0x7fffffff)%1000000:06}'


class LoginHTTPContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.environ['TESTS_LOGIN_HTTP_FIXTURE']) as stream:cls.fixture=json.load(stream)
        cls.client=Client(os.environ['TESTS_BASE_URL'],timeout=30)
        cls.issued=[]

    def test_credential_responses_and_cookie_delivery(self):
        for case in self.fixture['cases']:
            with self.subTest(case=case['name']):
                payload=dict(case['payload'])
                if case.get('totp_secret'):payload['otp']=otp(case['totp_secret'])
                headers={'X-Correlation-ID':case['name'],'X-Forwarded-For':'198.51.100.73'}
                if case.get('cookie'):headers['Cookie']=case['cookie']
                started=time.time()
                status,body,response=self.client.request('POST','/oauth2/login',payload,headers,include_headers=True)
                finished=time.time()
                self.assertEqual(status,case['status'])
                self.assertIn('cookie',response.get('Vary','').lower())
                if status==500:self.assertIsNone(response.get('Allow'))
                else:self.assertEqual(set(response['Allow'].split(', ')),{'POST','OPTIONS'})
                cookies=SimpleCookie()
                for value in response.get_all('Set-Cookie',[]):cookies.load(value)
                if status!=200:
                    self.assertNotIn('BKN_AUTH',cookies)
                    if status==500:self.assertIn('Server Error (500)',body)
                    else:self.assertEqual(body,case['expected'])
                    continue
                access,refresh=body.pop('access_token'),body.pop('refresh_token')
                self.assertEqual(body,case['expected'])
                self.assertEqual(set(cookies),{'BKN_AUTH'})
                cookie=cookies['BKN_AUTH']
                self.assertTrue(cookie.value==access,'cookie must contain issued access token')
                self.assertTrue(cookie['secure']);self.assertTrue(cookie['httponly'])
                self.assertEqual((cookie['path'],cookie['samesite'],cookie['max-age']),('/','Strict','14400'))
                pair=[]
                for encoded,kind,lifetime in [(access,'access',14400),(refresh,'refresh',2592000)]:
                    value=claims(encoded)
                    self.assertEqual({k:value[k] for k in ('user_id','role','email','token_type','iss','aud')},
                        {'user_id':case['id'],'role':case['role'],'email':case['email'],'token_type':kind,'iss':'https://bakney.com','aud':'bakney'})
                    self.assertLessEqual(int(started)-1,value['iat']);self.assertLessEqual(value['iat'],int(finished)+1)
                    self.assertEqual(value['exp']-value['iat'],lifetime)
                    self.assertRegex(value['jti'],r'^[0-9a-f]{32}$')
                    pair.append(value)
                self.assertNotEqual(pair[0]['jti'],pair[1]['jti'])
                self.issued.append((case['id'],access,refresh))

    def test_concurrent_sessions_keep_responses_separate(self):
        case=self.fixture['concurrent']
        def request(_):
            return self.client.request('POST','/oauth2/login',case['payload'],include_headers=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(request,range(16)))
        access_tokens=set();refresh_tokens=set()
        for status,body,headers in results:
            self.assertEqual(status,200)
            access,refresh=body.pop('access_token'),body.pop('refresh_token')
            self.assertEqual(body,case['expected'])
            cookie=SimpleCookie();cookie.load(headers.get('Set-Cookie',''))
            self.assertTrue(cookie['BKN_AUTH'].value==access,'concurrent response cookie belongs to another request')
            self.assertEqual(claims(access)['user_id'],case['id'])
            access_tokens.add(access);refresh_tokens.add(refresh)
            self.issued.append((case['id'],access,refresh))
        self.assertEqual(len(access_tokens),16);self.assertEqual(len(refresh_tokens),16)

    def test_preflight_bypasses_invalid_credentials_and_session(self):
        status,body,headers=self.client.request('OPTIONS','/oauth2/login',b'{',{
            'Origin':'https://allowed.example.test','Access-Control-Request-Method':'POST',
            'Authorization':'Bearer invalid','Cookie':'sessionid=login-http-malformed'},include_headers=True)
        self.assertEqual((status,body),(200,''))
        self.assertEqual(headers.get('Access-Control-Allow-Origin'),'https://allowed.example.test')
        self.assertNotIn('cookie',headers.get('Vary','').lower())
        self.assertIsNone(headers.get('Set-Cookie'))


class LoginRateHTTPContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.environ['TESTS_LOGIN_HTTP_FIXTURE']) as stream:cls.fixture=json.load(stream)
        cls.client=Client(os.environ['TESTS_BASE_URL'])

    def test_ip_denial_precedes_bearer_and_body(self):
        status,body,headers=self.client.request('POST','/oauth2/login',b'{',{
            'X-Forwarded-For':self.fixture['rate_ip'],'Authorization':'Bearer invalid','Content-Type':'application/json'},include_headers=True)
        self.assertEqual((status,body),(429,'Too many requests, blocked.'))
        self.assertIsNone(headers.get('Set-Cookie'));self.assertIsNone(headers.get('Allow'))
        self.assertIn('cookie',headers.get('Vary','').lower())

    def test_account_denial_precedes_credential_selection(self):
        status,body,headers=self.client.request('POST','/oauth2/login',{'username':self.fixture['rate_identifier'],'password':'wrong'},
            {'X-Forwarded-For':'198.51.100.74'},include_headers=True)
        self.assertEqual((status,body),(429,'Too many requests, blocked.'))
        self.assertIsNone(headers.get('Set-Cookie'))

    def test_session_error_precedes_rate_denial(self):
        status,body,headers=self.client.request('POST','/oauth2/login',{},
            {'X-Forwarded-For':self.fixture['rate_ip'],'Cookie':'sessionid=login-http-malformed'},include_headers=True)
        self.assertEqual(status,500);self.assertIn('Server Error (500)',body)
        self.assertIsNone(headers.get('Set-Cookie'));self.assertIn('cookie',headers.get('Vary','').lower())

    def test_get_bypasses_post_rate_limit(self):
        status,body,_=self.client.request('GET','/oauth2/login',headers={'X-Forwarded-For':self.fixture['rate_ip'],'Accept-Language':'en'},include_headers=True)
        self.assertEqual((status,body),(405,{'detail':'Method "GET" not allowed.'}))

    def test_cors_preflight_bypasses_rate_and_session(self):
        status,body,headers=self.client.request('OPTIONS','/oauth2/login',b'{',{
            'X-Forwarded-For':self.fixture['rate_ip'],'Cookie':'sessionid=login-http-malformed','Authorization':'Bearer invalid',
            'Origin':'https://allowed.example.test','Access-Control-Request-Method':'POST'},include_headers=True)
        self.assertEqual((status,body),(200,''));self.assertIsNone(headers.get('Set-Cookie'))
        self.assertNotIn('cookie',headers.get('Vary','').lower())
