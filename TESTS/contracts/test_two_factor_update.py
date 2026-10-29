"""Portable 2FA mutation contract; synthetic identities and readback belong to adapters."""
import json
import os
from pathlib import Path
import unittest
from TESTS.http_client import Client
from TESTS.contracts.test_login_http import otp


class TwoFactorUpdateContracts(unittest.TestCase):
    def test_declared_update(self):
        fixture=json.loads(Path(os.environ['TESTS_TWO_FACTOR_UPDATE_FIXTURE']).read_text())
        case=fixture['case']
        client=Client(os.environ['TESTS_BASE_URL'])
        body=case.get('raw_body',case['body'])
        if 'raw_body' in case:body=body.encode()
        if case.get('otp_secret'):
            body=dict(body);value=otp(case['otp_secret'])
            body['otp']=value if not case.get('wrong_otp') else str((int(value)+1)%1000000).zfill(6)
        headers={'Accept-Language':'en','X-Correlation-ID':case['name'],'X-Forwarded-For':'198.51.100.79'}
        if case.get('token'):headers['Authorization']='Bearer '+case['token']
        headers.update(case.get('headers',{}))
        if 'raw_body' in case:headers['Content-Type']=case.get('content_type','application/json')
        status,response,actual=client.request(case.get('method','POST'),'/two-fa/update'+case.get('query',''),body,headers,include_headers=True)
        self.assertEqual(status,case['status'],case['name'])
        if status==500:self.assertIn('Server Error (500)',response)
        else:self.assertEqual(response,case['response'],case['name'])
        self.assertIsNone(actual.get('Set-Cookie'))
        self.assertIn('cookie',actual.get('Vary','').lower())
        if status!=500:self.assertEqual(set(actual['Allow'].split(', ')),{'POST','OPTIONS'})
