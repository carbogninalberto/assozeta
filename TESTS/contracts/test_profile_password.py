"""Portable profile password response contracts; durable checks live in adapters."""
import json
import os
from pathlib import Path
import unittest
from TESTS.http_client import Client


class ProfilePasswordContracts(unittest.TestCase):
    def test_declared_password_changes(self):
        fixture=json.loads(Path(os.environ['TESTS_PROFILE_PASSWORD_FIXTURE']).read_text())
        for case in fixture['cases']:
            with self.subTest(case=case['name']):
                body=case.get('body')
                headers={'Accept-Language':'en',**case.get('headers',{})}
                if 'raw_body' in case:
                    body=case['raw_body'].encode();headers['Content-Type']=case.get('content_type','application/json')
                status,response,received=Client(os.environ['TESTS_BASE_URL'],case.get('token')).request(case.get('method','PATCH'),'/profile/update/password'+case.get('query',''),body,headers,include_headers=True)
                self.assertEqual(status,case['status'],case['name'])
                self.assertIsNone(received.get('Set-Cookie'))
                if status==500:self.assertIn('Server Error (500)',response)
                else:
                    self.assertEqual(set(received['Allow'].split(', ')),{'PATCH','OPTIONS'})
                    self.assertEqual(response,case['response'],case['name'])


class ProfilePasswordLoginFlows(unittest.TestCase):
    def test_existing_and_new_credentials_across_backends(self):
        case=json.loads(Path(os.environ['TESTS_PROFILE_PASSWORD_FIXTURE']).read_text())['cases'][0]
        producer=os.environ['TESTS_PASSWORD_PRODUCER_URL'];consumer=os.environ['TESTS_PASSWORD_CONSUMER_URL']
        username=case['response']['user_data']['username']
        old_client,before=Client(producer).login({'username':username,'password':case['old']})
        status,body=old_client.request('PATCH','/profile/update/password',case['body'])
        self.assertEqual((status,body),(200,case['response']))
        self.assertEqual(Client(consumer).request('POST','/oauth2/login',{'username':username,'password':case['old']}),(401,{'error':'invalid credentials'}))
        _,after=Client(consumer).login({'username':username,'password':case['new']})
        self.assertEqual(after['role'],'athlete');self.assertEqual(after['user_data']['user_id'],case['target'])
        expected={'info':{'role':'athlete'},'user_data':case['response']['user_data']}
        for base in (producer,consumer):
            for credential in (before['access_token'],after['access_token']):
                self.assertEqual(Client(base,credential).request('GET','/profile/info'),(200,expected))
