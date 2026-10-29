"""Portable profile HTTP contracts; expectations are declared by fixture adapters."""
import json
import os
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor
from TESTS.http_client import Client


class ProfileInfoContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=json.loads(Path(os.environ['TESTS_PROFILE_INFO_FIXTURE']).read_text())
        cls.base=os.environ['TESTS_BASE_URL']

    def verify(self,case):
        status,body,headers=Client(self.base,case.get('token')).request(case.get('method','GET'),'/profile/info'+case.get('query',''),headers={'Accept-Language':'en',**case.get('headers',{})},include_headers=True)
        self.assertEqual(status,case['status'],case['name'])
        self.assertIsNone(headers.get('Set-Cookie'))
        self.assertIn('cookie',headers.get('Vary','').lower())
        if status==500:self.assertIn('Server Error (500)',body)
        else:
            self.assertEqual(set(headers['Allow'].split(', ')),{'GET','OPTIONS'})
            self.assertEqual(body,case['response'],case['name'])
        return body

    def test_declared_profiles(self):
        for case in self.fixture['cases']:
            with self.subTest(case=case['name']):self.verify(case)

    def test_mixed_concurrent_profiles(self):
        cases=[case for case in self.fixture['cases'] if case['name'] in ('owner','athlete','collaborator','administrator')]
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda i:self.verify(cases[i%len(cases)]),range(self.fixture.get('concurrent_count',32))))
