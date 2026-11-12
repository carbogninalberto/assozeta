"""Portable reads after importing the pinned historical archive overlay.

Expected HTTP values are authored in the fixture. This module imports neither
Django nor Go and never derives an expected response from an observed response.
"""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
import zipfile

from TESTS.contracts.test_impersonation_management import assert_json_equal
from TESTS.fixtures.historical_read_routes import (
    BASE_BYTES,
    BASE_REVISION,
    BASE_SHA256,
    OVERLAY_ALGORITHM,
    SOURCE_SHA256,
    FixtureError,
    _apply_overlay,
    _validate_overlay,
    archive_bytes,
    historical,
    load_base,
    load_contract,
    overlay_digest,
)
from TESTS.http_client import Client


def _record_key(record):
    return record.get('user_id') or record.get('collaboration_invite_id')


def assert_collaborator_records(test, actual, expected):
    """Reject dropped rows, duplicate IDs, list-length drift and JSON aliases."""
    test.assertIs(type(actual), list)
    test.assertEqual(len(actual), len(expected))
    actual_by_id = {_record_key(row): row for row in actual}
    expected_by_id = {_record_key(row): row for row in expected}
    test.assertEqual(set(actual_by_id), set(expected_by_id),
                     'list dropped, duplicated, or exposed an unarchived/deleted row')
    for identifier in expected_by_id:
        with test.subTest(record=identifier):
            assert_json_equal(test, actual_by_id[identifier], expected_by_id[identifier], identifier)
    return actual_by_id


class HistoricalReadFixtureControls(unittest.TestCase):
    """Checks that the independent archive description is complete and fixed."""

    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract()
        cls.base = load_base()
        cls.derived = historical()

    def test_exact_base_overlay_source_and_archive_hashes(self):
        base_raw = (Path(__file__).resolve().parents[1] / 'fixtures/bakney-v1.json').read_bytes()
        self.assertEqual(len(base_raw), BASE_BYTES)
        self.assertEqual(hashlib.sha256(base_raw).hexdigest(), BASE_SHA256)
        self.assertEqual(self.contract['overlay_algorithm'], OVERLAY_ALGORITHM)
        self.assertEqual(
            self.contract['overlay_sha256'],
            overlay_digest(self.contract['archive_overlay']),
        )
        self.assertEqual(self.contract['historical_source']['revision'], BASE_REVISION)
        self.assertEqual(self.contract['historical_source']['files_sha256'], SOURCE_SHA256)

        first = archive_bytes()
        self.assertEqual(hashlib.sha256(first).hexdigest(),
                         self.contract['derived_archive_sha256'])
        self.assertEqual(first, archive_bytes(), 'archive packaging must be deterministic')
        self.assertEqual(self.contract['base_fixture']['sha256'], BASE_SHA256)

    def test_archive_keeps_frozen_sections_baseline_records_and_counts(self):
        baseline_specs = self.base['entries']['manifest.json']['models_exported']
        derived_specs = self.derived['entries']['manifest.json']['models_exported']
        self.assertEqual([(item['name'], item['file']) for item in derived_specs],
                         [(item['name'], item['file']) for item in baseline_specs])
        self.assertEqual(len(derived_specs), 67)
        self.assertEqual(set(self.derived['entries']), set(self.base['entries']))
        self.assertEqual(self.derived['text_files'], self.base['text_files'])

        changed = {'data/02_users.json', 'data/05_collaboration_invites.json'}
        for section, records in self.base['entries'].items():
            if section == 'manifest.json':
                continue
            if section in changed:
                self.assertEqual(self.derived['entries'][section][:len(records)], records)
            else:
                self.assertEqual(self.derived['entries'][section], records, section)

        for specification in derived_specs:
            self.assertIs(type(specification['count']), int)
            self.assertEqual(specification['count'],
                             len(self.derived['entries'][specification['file']]),
                             specification['name'])
        counts = {item['name']: item['count'] for item in derived_specs}
        self.assertEqual(counts['User'], 4)
        self.assertEqual(counts['CollaborationInvites'], 3)
        self.assertEqual(counts['BillingPlan'], 1)
        self.assertEqual(counts['PreviewAndCustomFeatures'], 1)
        for baseline_id, value in self.base['ids'].items():
            self.assertEqual(self.contract['ids'][baseline_id], value)

        payments = self.derived['entries']['data/43_payments.json']
        subscriptions = self.derived['entries']['data/25_subscriptions.json']
        associates = self.derived['entries']['data/07_associates.json']
        documents = self.derived['entries']['data/99_documents.json']
        self.assertEqual([row['payment_id'] for row in payments], [self.base['ids']['payment']])
        self.assertEqual([row['associate_id'] for row in associates], [self.base['ids']['person']])
        self.assertEqual([row['document_id'] for row in documents], [self.base['ids']['document']])
        self.assertEqual([row['subscription_id'] for row in subscriptions],
                         [self.base['ids']['subscription']])
        self.assertEqual(subscriptions[0]['signature_url'],
                         'https://legacy.example.test/signature.png')
        self.assertEqual(self.derived['entries']['data/42_signatures.json'], [])

        with zipfile.ZipFile(io.BytesIO(archive_bytes())) as packaged:
            self.assertEqual(set(packaged.namelist()), set(self.derived['entries']) |
                             set(self.derived['text_files']))
            manifest = json.loads(packaged.read('manifest.json'))
            users = json.loads(packaged.read('data/02_users.json'))
            invites = json.loads(packaged.read('data/05_collaboration_invites.json'))
            self.assertEqual((manifest['models_exported'][2]['count'],
                              manifest['models_exported'][5]['count']), (4, 3))
            self.assertEqual(len(users), 4)
            self.assertEqual(len(invites), 3)

    def test_overlay_is_append_only_source_typed_and_relationship_safe(self):
        self.assertEqual(self.contract['retired_policy']['explicit_skip_records'],
                         {'BillingPlan': 1, 'PreviewAndCustomFeatures': 1})
        users = self.derived['entries']['data/02_users.json'][1:]
        invites = self.derived['entries']['data/05_collaboration_invites.json']
        self.assertEqual(
            {row['user_id'] for row in users},
            {
                self.contract['ids']['active_collaborator'],
                self.contract['ids']['inactive_collaborator'],
                self.contract['ids']['deleted_collaborator'],
            },
        )
        self.assertTrue(all(row['connected_user_id'] == self.base['ids']['owner'] for row in users))
        self.assertTrue(all('password' not in row for row in users))
        self.assertTrue(any(type(row['is_active']) is bool and not row['is_active']
                            for row in users))
        self.assertTrue(any(type(row['deleted']) is bool and row['deleted'] for row in users))
        permissions = next(row['collaborator_permissions'] for row in users
                           if not row['is_active'])
        self.assertIs(permissions['calendar'], True)
        self.assertIs(permissions['optional'], None)
        self.assertIs(type(permissions['large_integer']), int)
        self.assertEqual(permissions['large_integer'], 9007199254740993)
        self.assertIs(type(permissions['nested'][0]), bool)
        self.assertIs(type(permissions['nested'][1]), int)
        self.assertIs(type(permissions['nested'][2]), float)

        self.assertEqual(
            {row['collaboration_invite_id'] for row in invites},
            {
                self.contract['ids']['accepted_invite'],
                self.contract['ids']['pending_invite'],
                self.contract['ids']['expired_invite'],
            },
        )
        self.assertTrue(all(row['user_id'] == self.base['ids']['owner'] for row in invites))
        self.assertEqual({row['accepted'] for row in invites}, {False, True})
        self.assertTrue(any(type(row['accepted']) is bool and row['accepted']
                            for row in invites))

    def test_overlay_controls_reject_unknown_sections_fields_counts_and_aliases(self):
        base = load_base()
        sections = base['entries']['manifest.json']['models_exported']
        for defect in (
            'section',
            'field',
            'accepted-alias',
            'duplicate-user-id',
            'expected-count',
        ):
            malformed = deepcopy(self.contract)
            if defect == 'section':
                malformed['archive_overlay']['operations'][0]['section'] = \
                    'data/99_unknown.json'
            elif defect == 'field':
                malformed['archive_overlay']['operations'][0]['records'][0]['unknown'] = True
            elif defect == 'accepted-alias':
                malformed['archive_overlay']['operations'][1]['records'][0]['accepted'] = 1
            elif defect == 'duplicate-user-id':
                users = malformed['archive_overlay']['operations'][0]['records']
                users[1]['user_id'] = users[0]['user_id']
            else:
                malformed['expected_counts']['User'] = 3
            with self.subTest(defect=defect):
                with self.assertRaises(FixtureError):
                    _validate_overlay(malformed, base, sections)
                    _apply_overlay(base, malformed)

    def test_http_controls_reject_length_ids_and_boolean_number_aliases(self):
        expected = self.contract['http']['collaborators']['list_records']
        for name, malformed in (
            ('dropped-row', expected[:-1]),
            ('duplicate-row', expected + [expected[0]]),
            ('duplicate-id-same-length', expected[:-1] + [expected[0]]),
        ):
            with self.subTest(defect=name):
                with self.assertRaises(AssertionError):
                    assert_collaborator_records(self, malformed, expected)

        inactive = next(
            deepcopy(row) for row in expected
            if _record_key(row) == self.contract['ids']['inactive_collaborator']
        )
        aliases = []
        boolean_alias = deepcopy(inactive)
        boolean_alias['collaborator_permissions']['calendar'] = 1
        aliases.append(('boolean-number', boolean_alias))
        number_alias = deepcopy(inactive)
        number_alias['collaborator_permissions']['nested'][2] = 2
        aliases.append(('integer-float', number_alias))
        for name, alias in aliases:
            with self.subTest(defect=name):
                with self.assertRaises(AssertionError):
                    assert_json_equal(self, alias, inactive, name)


class HistoricalReadRoutes(unittest.TestCase):
    """Actual HTTP reads after the parent's supported import/configuration."""

    @classmethod
    def setUpClass(cls):
        base_url = os.environ.get('TESTS_BASE_URL')
        if not base_url:
            raise unittest.SkipTest('TESTS_BASE_URL is required for imported read-route acceptance')
        cls.contract = load_contract()
        cls.base = load_base()
        cls.public = Client(base_url, timeout=60)
        cls.owner, login = cls.public.login(cls.base['account'])
        cls.login = login

    def test_manifest_exact_configured_payload(self):
        expected = self.contract['http']['manifest']
        status, body = self.public.request(
            'GET', expected['path'], headers={'Accept-Language': 'en'})
        self.assertEqual(status, expected['status'], body)
        assert_json_equal(self, body, expected['body'], 'manifest')

    def test_owner_list_has_exact_accepted_pending_inactive_and_owned_rows(self):
        expected = self.contract['http']['collaborators']
        self.assertEqual(self.login['user_data']['user_id'], self.base['ids']['owner'])
        status, body = self.owner.request(
            'GET', expected['list_path'], headers={'Accept-Language': 'en'})
        self.assertEqual(status, expected['list_status'], body)
        self.assertIs(type(body), list)
        self.assertEqual(len(body), len(expected['list_records']))
        actual_by_id = assert_collaborator_records(self, body, expected['list_records'])
        for identifier in expected['expected_absent_ids']:
            self.assertNotIn(identifier, actual_by_id)

    def test_active_inactive_deleted_and_unknown_details(self):
        expected = self.contract['http']['collaborators']
        for name in ('active_collaborator', 'inactive_collaborator'):
            identifier = self.contract['ids'][name]
            path = expected['detail_path_template'].format(user_id=identifier)
            status, body = self.owner.request(
                'GET', path, headers={'Accept-Language': 'en'})
            self.assertEqual(status, expected['detail_status'], body)
            assert_json_equal(self, body, expected['details'][name], name)

        empty = expected['details'][expected['deleted_detail']]
        for name in ('deleted_collaborator', 'unknown_user'):
            identifier = self.contract['ids'][name]
            path = expected['detail_path_template'].format(user_id=identifier)
            status, body = self.owner.request(
                'GET', path, headers={'Accept-Language': 'en'})
            self.assertEqual(status, expected['detail_status'], body)
            assert_json_equal(self, body, empty, name)

    def test_anonymous_read_is_denied(self):
        expected = self.contract['http']['collaborators']['anonymous']
        status, body = self.public.request(
            'GET',
            self.contract['http']['collaborators']['list_path'],
            headers={'Accept-Language': 'en'},
        )
        self.assertEqual(status, expected['status'], body)
        assert_json_equal(self, body, expected['body'], 'anonymous')


if __name__ == '__main__':
    unittest.main()
