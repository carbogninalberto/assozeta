"""Portable rate-admission contracts using a documented fixture terminal view.

The adapter supplies isolated Redis and /__fixtures/login-rate/reset plus a
terminal view that echoes the SHA-256 of unchanged bytes. This is not credential
success or public-login composition evidence.
"""
import hashlib
import os
import unittest
from TESTS.http_client import Client


class LoginRateContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.client=Client(os.environ['TESTS_BASE_URL'])
    def setUp(self):
        self.assertEqual(self.client.request('POST','/__fixtures/login-rate/reset'),(200,{'reset':True}))
    def request(self,raw=b'{"username":"fixture"}',*,ip='203.0.113.7',method='POST',path='/oauth2/login',headers=None):
        h={'Content-Type':'application/json','X-Forwarded-For':ip,**(headers or {})}
        return self.client.request(method,path,raw,h,include_headers=True)
    def admitted(self,result,raw):
        self.assertEqual(result[:2],(200,{'sha256':hashlib.sha256(raw).hexdigest()}))
        self.assertIsNone(result[2].get('Set-Cookie'))
    def denied(self,result):
        self.assertEqual(result[:2],(429,'Too many requests, blocked.'))
        self.assertEqual(result[2]['Content-Type'],'text/html; charset=utf-8')
        for name in ('Set-Cookie','Retry-After','WWW-Authenticate','Allow'):self.assertIsNone(result[2].get(name))
    def test_account_casefold_whitespace_and_distinct_ips(self):
        for i,name in enumerate(('STRASSE','straße',' Straße ','strasse','STRASSE')):
            raw=('{"username":"'+name+'"}').encode()
            self.admitted(self.request(raw,ip=f'198.51.100.{i+1}'),raw)
        self.denied(self.request(b'{"username":"strasse"}',ip='198.51.100.99'))
    def test_ip_first_forwarded_address_and_threshold(self):
        for i in range(20):
            raw=('{"username":"member-'+str(i)+'"}').encode()
            self.admitted(self.request(raw,ip=' 203.0.113.8 , 192.0.2.1'),raw)
        # Invalid non-object JSON must never reach the account-key function here.
        self.denied(self.request(b'null',ip='203.0.113.8, 192.0.2.99',headers={'Authorization':'Bearer','Accept':'text/html'}))
    def test_invalid_bodies_share_bucket_independent_of_media(self):
        values=[(b'{','application/json'),(b'username=alice','application/x-www-form-urlencoded'),
                (b'\xff','application/json; charset=latin1'),(b'','text/plain'),(b'--fixture\r\n','multipart/form-data; boundary=fixture')]
        for i,(raw,media) in enumerate(values):self.admitted(self.request(raw,ip=f'192.0.2.{i+1}',headers={'Content-Type':media}),raw)
        self.denied(self.request(b'not JSON',ip='192.0.2.99'))
    def test_json_bytes_ignore_content_type_and_are_preserved(self):
        raw=b'{ "username" : "same" , "password" : "synthetic" }\n'
        for media in ('text/plain','application/json','application/x-www-form-urlencoded','multipart/form-data; boundary=x','application/json; charset=latin1'):
            self.admitted(self.request(raw,headers={'Content-Type':media}),raw)
        self.denied(self.request(raw))
    def test_missing_empty_and_whitespace_share_bucket(self):
        for raw in (b'{}',b'{"username":""}',b'{"username":" "}',b'{"username":"\\u001c\\u00a0"}',b'{"Username":"x"}'):
            self.admitted(self.request(raw),raw)
        self.denied(self.request(b'{}'))
    def test_raw_containers_use_python_repr_then_casefold(self):
        raw=b'{"username":["Alice",null,true,3.0]}'
        for _ in range(5):self.admitted(self.request(raw),raw)
        self.denied(self.request(b"{\"username\":\"['alice', none, true, 3.0]\"}"))
    def test_object_order_and_duplicate_last_value(self):
        raw=b'{"username":{"z":true,"a":null,"z":false}}'
        for _ in range(5):self.admitted(self.request(raw),raw)
        self.denied(self.request(b"{\"username\":\"{'z': false, 'a': none}\"}"))
    def test_nonfinite_values_retain_python_json_rules(self):
        for raw in (b'{"username":NaN}',b'{"username":"nan"}',b'{"username":NaN}',b'{"username":"NAN"}',b'{"username":NaN}'):
            self.admitted(self.request(raw),raw)
        self.denied(self.request(b'{"username":"nan"}'))
    def test_infinities_and_overflow_share_repr_bucket(self):
        for raw in (b'{"username":[Infinity,-Infinity]}',b'{"username":[1e400,-1e400]}',
                    b'{"username":"[inf, -inf]"}',b'{"username":[Infinity,-Infinity]}',b'{"username":"[INF, -INF]"}'):
            self.admitted(self.request(raw),raw)
        self.denied(self.request(b'{"username":[Infinity,-Infinity]}'))
    def test_integer_limit_applies_even_outside_username(self):
        raw=b'{"other":'+b'1'*4300+b'}'
        self.admitted(self.request(raw),raw)
        status,body,_=self.request(b'{"other":'+b'1'*4301+b'}')
        self.assertEqual(status,500);self.assertIn('Server Error (500)',body)
    def test_nonobjects_fail_before_account_but_consume_ip(self):
        for raw in (b'null',b'[]',b'42',b'true',b'"text"'):
            status,body,_=self.request(raw)
            self.assertEqual(status,500);self.assertIn('Server Error (500)',body)
        for i in range(15):
            raw=('{"username":"later-'+str(i)+'"}').encode()
            self.admitted(self.request(raw),raw)
        self.denied(self.request(b'{}'))
    def test_other_methods_do_not_consume_counters(self):
        for method in ('GET','PUT','PATCH','DELETE','OPTIONS'):
            for _ in range(5):self.admitted(self.request(b'null',method=method),b'null')
        raw=b'{"username":"first"}';self.admitted(self.request(raw),raw)
    def test_disabled_gate_does_not_parse_or_consume(self):
        for _ in range(25):self.admitted(self.request(b'null',path='/__fixtures/login-rate/disabled'),b'null')
        raw=b'{"username":"first"}';self.admitted(self.request(raw),raw)
    def test_empty_first_forwarded_address_falls_back_to_peer(self):
        for i in range(20):
            raw=('{"username":"peer-'+str(i)+'"}').encode()
            self.admitted(self.request(raw,ip=' , 192.0.2.9'),raw)
        self.denied(self.request(b'{}',ip=''))
    def test_denied_accounts_still_consume_ip_attempts(self):
        raw=b'{"username":"shared"}'
        for i in range(20):
            result=self.request(raw)
            if i<5:self.admitted(result,raw)
            else:self.denied(result)
        self.denied(self.request(b'{"username":"new-account"}'))


if __name__=='__main__':unittest.main()
