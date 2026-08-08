"""
Tests for export_service - owner-password export/import contract.

Ported from SaaS test_export_service.py, adapted for self-host:
- Uses self-host fixtures and models
- Validates self-host EXCLUDED_FIELDS contract: password for owner only,
  two_fa_secret/stripe_account_id always excluded
"""
import base64
import json
import os
import tempfile
import uuid
import zipfile
from unittest.mock import patch

from django.contrib.auth.hashers import get_hasher, make_password
from django.test import TestCase, override_settings

from application.models.user_models import User
from application.services.export_service import AssociationExportService
from application.tests.fixtures.factories import (
    create_test_sport_association,
    create_test_user,
)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AssociationExportPasswordTests(TestCase):
    def setUp(self):
        self.owner = create_test_user(role=User.ATHLETE, password='owner-password')
        self.association = create_test_sport_association(user=self.owner)
        self.service = AssociationExportService(self.association.sport_association_id)

    def test_owner_password_hash_is_exported_exactly(self):
        stored_hash = self.owner.password
        self.owner.two_fa_secret = 'not-exported'
        self.owner.stripe_account_id = 'not-exported'
        self.owner.integration_google_credentials = {'token': 'not-exported'}

        data = self.service.serialize_record(self.owner)

        self.assertEqual(data['password'], stored_hash)
        self.assertIn('password', self.service.EXCLUDED_FIELDS['User'])
        self.assertIn('payment_intent_id', self.service.EXCLUDED_FIELDS['Payment'])
        self.assertIn('smtp_password', self.service.EXCLUDED_FIELDS['CommunicationConfiguration'])
        self.assertNotIn('two_fa_secret', data)
        self.assertNotIn('stripe_account_id', data)
        self.assertNotIn('integration_google_credentials', data)

    def test_non_owner_user_passwords_are_never_exported(self):
        non_owners = [
            create_test_user(role=User.COLLABORATOR),
            create_test_user(role=User.ATHLETE),
            create_test_user(role=User.ATHLETE),
        ]
        for user in non_owners:
            user.connected_user = self.owner
            user.save(update_fields=['connected_user'])

        matching_user = non_owners[0]
        matching_user.role = self.owner.role
        matching_user.email = self.owner.email
        matching_user.username = self.owner.username
        self.assertNotIn('password', self.service.serialize_record(matching_user))

        with tempfile.TemporaryDirectory() as output_dir:
            self.service.export_model_to_json(User, output_dir, '02_users')
            with open(os.path.join(output_dir, 'data', '02_users.json'), encoding='utf-8') as users_file:
                records = json.load(users_file)

        self.assertEqual(len(records), 4)
        owner_id = str(self.association.user_id)
        for record in records:
            if record['user_id'] == owner_id:
                self.assertEqual(record['password'], self.owner.password)
            else:
                self.assertNotIn('password', record)

    def test_invalid_owner_password_hashes_are_omitted(self):
        digest = base64.b64encode(b'\0' * 32).decode('ascii')
        rejected_hashes = {
            'unusable': make_password(None),
            'unsupported': f'pbkdf2_sha256$1$salt${digest}',
        }

        for case, encoded in rejected_hashes.items():
            with self.subTest(case=case):
                self.owner.password = encoded
                self.assertNotIn('password', self.service.serialize_record(self.owner))

    @override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.PBKDF2PasswordHasher'])
    def test_supported_owner_hash_is_parsed_without_verification(self):
        hasher = get_hasher()
        digest = base64.b64encode(b'\0' * hasher.digest().digest_size).decode('ascii')
        self.owner.password = f'{hasher.algorithm}$1$salt${digest}'

        with patch.object(
            type(hasher),
            'verify',
            side_effect=AssertionError('Password verification must not run during export'),
        ):
            data = self.service.serialize_record(self.owner)

        self.assertEqual(data['password'], self.owner.password)

    @override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.PBKDF2PasswordHasher'])
    def test_excessive_cost_owner_password_hash_is_omitted(self):
        hasher = get_hasher()
        digest = base64.b64encode(b'\0' * hasher.digest().digest_size).decode('ascii')
        self.owner.password = (
            f'{hasher.algorithm}${hasher.iterations + 1}$salt${digest}'
        )

        self.assertNotIn('password', self.service.serialize_record(self.owner))

    def test_invalid_algorithm_cost_parameters_are_omitted(self):
        digest = base64.b64encode(b'\0' * 64).decode('ascii')
        invalid_hashes = [
            (
                'django.contrib.auth.hashers.ScryptPasswordHasher',
                f'scrypt$3$salt$8$1${digest}',
            ),
            (
                'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
                f'bcrypt_sha256$$2b$03{"A" * 53}',
            ),
        ]

        for hasher_path, encoded in invalid_hashes:
            with self.subTest(hasher=hasher_path), override_settings(
                PASSWORD_HASHERS=[hasher_path]
            ):
                self.owner.password = encoded
                self.assertNotIn('password', self.service.serialize_record(self.owner))

    def test_password_is_an_optional_addition_to_existing_archive_format(self):
        stored_hash = self.owner.password
        with tempfile.TemporaryDirectory() as output_dir:
            self.service.export_model_to_json(User, output_dir, '02_users')
            manifest = self.service.create_manifest(output_dir)
            zip_path = self.service.create_zip(
                output_dir,
                f'test_export_{uuid.uuid4()}.zip',
            )

            try:
                with zipfile.ZipFile(zip_path) as archive:
                    self.assertEqual(
                        set(archive.namelist()),
                        {'data/02_users.json', 'manifest.json'},
                    )
                    users = json.loads(archive.read('data/02_users.json'))
            finally:
                os.remove(zip_path)

        owner_record = next(
            user for user in users if user['user_id'] == str(self.association.user_id)
        )
        self.assertEqual(owner_record['password'], stored_hash)
        self.assertEqual(manifest['version'], '1.0.0')
        self.assertEqual(manifest['export_format'], 'bakney_sport_export_v1')
        user_manifest = next(
            model for model in manifest['models_exported'] if model['name'] == 'User'
        )
        self.assertEqual(user_manifest['file'], 'data/02_users.json')
