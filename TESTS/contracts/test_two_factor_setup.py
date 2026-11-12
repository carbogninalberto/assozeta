"""Independent public 2FA provisioning contracts against either backend."""
import json
import os
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote,urlencode
from TESTS.http_client import Client
from TESTS.contracts.qr import decode_svg_qr


class TwoFactorSetupContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=json.loads(Path(os.environ['TESTS_TWO_FACTOR_SETUP_FIXTURE']).read_text())
        cls.base=os.environ['TESTS_BASE_URL']

    def verify(self,case):
        headers={'Accept-Language':'en',**case.get('headers',{})}
        client=Client(self.base,case.get('token'))
        status,body,actual=client.request(case.get('method','GET'),'/two-fa/setup'+case.get('query',''),headers=headers,include_headers=True)
        self.assertEqual(status,case['status'],case['name'])
        self.assertIsNone(actual.get('Set-Cookie'))
        self.assertIn('cookie',actual.get('Vary','').lower())
        if status!=500:self.assertEqual(set(actual['Allow'].split(', ')),{'GET','OPTIONS'})
        if case.get('response') is not None:
            self.assertEqual(body,case['response']);return None
        if status==500:self.assertIn('Server Error (500)',body);return None
        self.assertEqual(set(body),{'data'});self.assertEqual(set(body['data']),{'qrcode_uri','otp_secret'})
        secret=body['data']['otp_secret'];self.assertRegex(secret,r'^[A-Z2-7]{32}$')
        decoded,_=decode_svg_qr(body['data']['qrcode_uri'])
        name=self.fixture['account_name'] or 'Secret';issuer=self.fixture['issuer']
        label=(quote(issuer)+':' if issuer else '')+quote(name)
        query={'secret':secret}
        if issuer:query['issuer']=issuer
        expected='otpauth://totp/'+label+'?'+urlencode(query).replace('+','%20')
        self.assertEqual(decoded,expected,'QR provisioned a different account, issuer or secret')
        return secret

    def test_declared_setup(self):
        for case in self.fixture['cases']:
            with self.subTest(case=case['name']):self.verify(case)

    def test_fresh_concurrent_secrets(self):
        case=next(case for case in self.fixture['cases'] if case['name']=='owner')
        count=self.fixture.get('concurrent_count',32)
        with ThreadPoolExecutor(max_workers=8) as pool:secrets=list(pool.map(lambda _:self.verify(case),range(count)))
        if case['status']==500:self.assertEqual(secrets,[None]*count,'failed setup returned a secret')
        else:self.assertEqual(len(set(secrets)),count,'setup reused a secret across requests')


class TwoFactorLoginFlowContracts(unittest.TestCase):
    result=None

    def test_provision_then_login_and_disable_across_backends(self):
        from TESTS.contracts.test_login_http import otp
        fixture=json.loads(Path(os.environ['TESTS_TWO_FACTOR_SETUP_FIXTURE']).read_text())
        account=fixture['login']
        producer=Client(os.environ['TESTS_SETUP_PRODUCER_URL'],account['token'])
        consumer=Client(os.environ['TESTS_SETUP_CONSUMER_URL'],account['token'])
        for client in (producer,consumer):self.assertEqual(client.request('GET','/two-fa/info'),(200,{'data':{'enabled':False}}))
        status,body=producer.request('GET','/two-fa/setup');self.assertEqual(status,200)
        secret=body['data']['otp_secret'];self.assertRegex(secret,r'^[A-Z2-7]{32}$')
        decoded,_=decode_svg_qr(body['data']['qrcode_uri'])
        self.assertIn('secret='+secret,decoded)
        for client in (producer,consumer):self.assertEqual(client.request('GET','/two-fa/info'),(200,{'data':{'enabled':False}}),'setup persisted before confirmation')
        self.assertEqual(consumer.request('POST','/two-fa/update',{'enable':True,'secret':secret,'otp':otp(secret)}),(200,{'data':{'msg':'Two factor authentication enabled.'}}))
        login=Client(consumer.base_url)
        credentials={'username':account['username'],'password':account['password']}
        self.assertEqual(login.request('POST','/oauth2/login',credentials),(401,{'msg':'OTP code required.'}))
        bad=str((int(otp(secret))+1)%1000000).zfill(6)
        self.assertEqual(login.request('POST','/oauth2/login',{**credentials,'otp':bad}),(401,{'msg':'OTP code not valid.'}))
        status,body,headers=login.request('POST','/oauth2/login',{**credentials,'otp':otp(secret)},include_headers=True)
        self.assertEqual(status,200);self.assertEqual(body['role'],'athlete');self.assertEqual(body['user_data']['user_id'],account['id'])
        self.assertEqual(body['token_type'],'Bearer');self.assertTrue(body['access_token']);self.assertTrue(body['refresh_token'])
        self.assertIn('BKN_AUTH=',headers.get('Set-Cookie',''))
        for client in (producer,consumer):
            client.token=body['access_token']
            self.assertEqual(client.request('GET','/two-fa/info'),(200,{'data':{'enabled':True}}),'issued credential failed after runtime switch')
        self.assertEqual(producer.request('POST','/two-fa/update',{'enable':False,'secret':None,'otp':None}),(200,{'data':{'msg':'Disabled two factor authentication'}}))
        for client in (producer,consumer):self.assertEqual(client.request('GET','/two-fa/info'),(200,{'data':{'enabled':False}}))
        type(self).result={'secret':secret}
