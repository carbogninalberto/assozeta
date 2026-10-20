"""Portable HTTP contracts for an installed release manual; no backend imports.

The harness supplies committed release expectations and a disposable account.
These checks consume the same public endpoints a future Go backend must expose.
"""
import hashlib
import json
import os
from pathlib import Path
import unittest
from urllib.parse import quote

from TESTS.http_client import Client


class InstalledManual(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(Path(os.environ['TESTS_FIXTURE']).read_text())
        cls.expected = cls.fixture['manual']
        cls.public = Client(os.environ['TESTS_BASE_URL'])
        cls.owner, _ = cls.public.login(cls.fixture['accounts']['missing'])
        status, response = cls.owner.request('GET', '/manuale/sections')
        if status != 200 or response.get('status') != 'verified' or not response.get('results'):
            raise AssertionError(f'Installed manual unavailable: HTTP {status}, {response.get("status")}')
        cls.sections = response['results']

    def test_exact_release_available_without_ai_or_software_subscription(self):
        status, value = self.public.request('GET', '/instance/status')
        self.assertEqual(status, 200)
        self.assertTrue(value['manual_enabled'])
        self.assertFalse(value['ai_enabled'])
        self.assertEqual(self.owner.request('GET', '/billing/active-plan')[0], 410)
        self.assertEqual(self.owner.request('GET', '/collaborators/list')[0], 200)
        self.assertEqual(len(self.sections), self.expected['public_sections'])
        for section in self.sections:
            self.assertEqual(section['version'], self.expected['release'])
            self.assertEqual(section['application_revision'], self.expected['application_revision'])
            self.assertEqual(section['corpus_identity'], self.expected['corpus_identity'])
            self.assertEqual(section['publication_status'], 'release-bundled')
            self.assertTrue(section['url'].startswith('/#/manuale?section='))
            self.assertNotIn('evidence', section)

    def test_every_image_is_local_authenticated_and_matches_its_verified_bytes(self):
        images = {image['url']: image for section in self.sections for image in section['screenshots']}
        self.assertTrue(images, 'Release manual must contain verified screenshots')
        for url, image in images.items():
            self.assertTrue(url.startswith('/api/manuale/assets/'))
            status, contents = self.owner.request('GET', url.removeprefix('/api'), raw=True)
            self.assertEqual(status, 200, image['path'])
            self.assertTrue(contents.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertEqual(hashlib.sha256(contents).hexdigest(), image['sha256'])
        first_url = next(iter(images)).removeprefix('/api')
        self.assertIn(self.public.request('GET', first_url, raw=True)[0], (401, 403))
        self.assertEqual(self.owner.request('GET', '/manuale/assets/images/not-indexed.png')[0], 404)

    def test_reader_requires_authentication_and_search_uses_verified_content(self):
        self.assertIn(self.public.request('GET', '/manuale/sections')[0], (401, 403))
        status, result = self.owner.request('GET', '/manuale/sections?query=' + quote(self.expected['query']))
        self.assertEqual(status, 200)
        self.assertEqual(result['status'], 'verified')
        self.assertIn(self.expected['section_id'], [section['id'] for section in result['results']])
        status, result = self.owner.request('GET', '/manuale/sections?query=teletrasporto%20satellitare')
        self.assertEqual(status, 200)
        self.assertEqual(result['results'], [])
