import json
import os
import tempfile
import uuid
import zipfile

from django.contrib.auth.hashers import make_password
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from application.models import Associate, Family, SportAssociation, Subscription, User
from application.services.export_service import AssociationExportService
from application.services.import_service import AssociationImportService, ImportOptions
from application.services.validators import ImportValidator, is_restorable_password_hash


class AssociationImportIdentityTests(TestCase):
    def _user_record(self, user_id, username, email, role=User.ATHLETE, **extra):
        record = {
            'user_id': str(user_id),
            'username': username,
            'email': email,
            'role': role,
            'first_name': extra.pop('first_name', ''),
            'last_name': extra.pop('last_name', ''),
            'is_active': extra.pop('is_active', True),
            'is_staff': extra.pop('is_staff', False),
            'is_superuser': extra.pop('is_superuser', False),
            'two_fa': extra.pop('two_fa', False),
        }
        record.update(extra)
        return record

    def _association_record(self, association_id, owner_id, **extra):
        record = {
            'sport_association_id': str(association_id),
            'user_id': str(owner_id),
            'denomination': extra.pop('denomination', 'Archived ASD'),
            'tax_code': extra.pop('tax_code', '12345678901'),
            'multiple_subscription_fee': extra.pop('multiple_subscription_fee', True),
            'subscription_fee_plans': extra.pop('subscription_fee_plans', [{'amount': '10.00'}]),
            'enable_quotes_management': extra.pop('enable_quotes_management', False),
            'president_first_name': extra.pop('president_first_name', 'Ada'),
        }
        record.update(extra)
        return record

    def _write_archive(self, path, association, users, extra_data=None, files=None):
        models = [
            ('SportAssociation', 'data/01_sport_association.json', 1),
            ('User', 'data/02_users.json', len(users)),
        ]
        extra_data = extra_data or {}
        for data_path, records in extra_data.items():
            if data_path == 'data/03_groups.json':
                models.append(('Group', data_path, len(records)))
            if data_path == 'data/06_families.json':
                models.append(('Family', data_path, len(records)))

        manifest = {
            'version': '1.0.0',
            'export_format': 'bakney_sport_export_v1',
            'export_date': '2026-08-05T00:00:00Z',
            'association': {
                'sport_association_id': association['sport_association_id'],
                'denomination': association['denomination'],
                'tax_code': association['tax_code'],
            },
            'models_exported': [
                {'name': name, 'file': file_path, 'count': count}
                for name, file_path, count in models
            ],
        }

        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('manifest.json', json.dumps(manifest))
            zf.writestr('data/01_sport_association.json', json.dumps([association]))
            zf.writestr('data/02_users.json', json.dumps(users))
            for data_path, records in extra_data.items():
                zf.writestr(data_path, json.dumps(records))
            for archive_name, content in (files or {}).items():
                zf.writestr(archive_name, content)

    def _import_archive(self, association, users, extra_data=None, owner_password='Recovery!123'):
        with tempfile.NamedTemporaryFile(suffix='.zip') as archive:
            self._write_archive(archive.name, association, users, extra_data=extra_data)
            service = AssociationImportService(
                archive.name,
                ImportOptions(owner_password=owner_password),
            )
            imported = service.import_all()
        return imported, service

    def test_owner_is_selected_from_association_user_id_when_multiple_role_association_users_exist(self):
        owner_id = uuid.uuid4()
        other_role_one_id = uuid.uuid4()
        association_id = uuid.uuid4()
        association = self._association_record(association_id, owner_id)
        users = [
            self._user_record(
                other_role_one_id,
                'wrong-owner',
                'wrong-owner@example.com',
                role=User.ASSOCIATION,
                is_staff=True,
                is_superuser=True,
                two_fa=True,
            ),
            self._user_record(
                owner_id,
                'archived-owner',
                'archived-owner@example.com',
                role=User.ATHLETE,
            ),
        ]

        imported, _service = self._import_archive(association, users)

        self.assertEqual(imported.sport_association_id, association_id)
        self.assertEqual(imported.user_id, owner_id)
        self.assertEqual(imported.user.username, 'archived-owner')
        self.assertEqual(imported.user.email, 'archived-owner@example.com')
        self.assertTrue(imported.user.check_password('Recovery!123'))

        other_user = User.original_objects.get(user_id=other_role_one_id)
        self.assertEqual(other_user.role, User.ASSOCIATION)
        self.assertFalse(other_user.has_usable_password())
        self.assertFalse(other_user.is_staff)
        self.assertFalse(other_user.is_superuser)
        self.assertFalse(other_user.two_fa)

    def test_archived_owner_password_is_reported_and_preserved_without_recovery_password(self):
        owner_id = uuid.uuid4()
        association = self._association_record(uuid.uuid4(), owner_id)
        archived_password = make_password('ArchivedOwner!123')
        users = [
            self._user_record(
                owner_id,
                'owner',
                'owner@example.com',
                role=User.ASSOCIATION,
                password=archived_password,
            ),
        ]

        with tempfile.NamedTemporaryFile(suffix='.zip') as archive:
            self._write_archive(archive.name, association, users)

            validation = ImportValidator(archive.name).validate_all()
            self.assertTrue(validation.is_valid, validation.errors)
            self.assertFalse(
                validation.info['owner_user']['requires_recovery_password']
            )

            service = AssociationImportService(archive.name, ImportOptions())
            self.assertTrue(service.validate().is_valid)
            imported = service.import_all()

        self.assertEqual(imported.user.password, archived_password)
        self.assertTrue(imported.user.check_password('ArchivedOwner!123'))

    def test_backup_without_supported_owner_password_requires_recovery_password(self):
        owner_id = uuid.uuid4()
        association = self._association_record(uuid.uuid4(), owner_id)
        users = [
            self._user_record(
                owner_id,
                'owner',
                'owner@example.com',
                role=User.ASSOCIATION,
                password='pbkdf2_sha256$1000000$salt$AAAA',
            ),
        ]

        with tempfile.NamedTemporaryFile(suffix='.zip') as archive:
            self._write_archive(archive.name, association, users)

            validation = ImportValidator(archive.name).validate_all()
            self.assertTrue(validation.is_valid, validation.errors)
            self.assertTrue(
                validation.info['owner_user']['requires_recovery_password']
            )

            service = AssociationImportService(archive.name, ImportOptions())
            service_validation = service.validate()

        self.assertFalse(service_validation.is_valid)
        self.assertIn(
            'Owner recovery password is required',
            service_validation.errors[0],
        )
        self.assertFalse(
            is_restorable_password_hash(
                'pbkdf2_sha256$999999999$salt$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
            )
        )
        self.assertFalse(is_restorable_password_hash('scrypt$3$salt$8$5$digest'))
        self.assertFalse(
            is_restorable_password_hash(
                'bcrypt_sha256$$2b$03$' + ('A' * 53)
            )
        )

    def test_existing_owner_password_is_preserved_without_archive_or_recovery_password(self):
        owner_id = uuid.uuid4()
        existing_owner = User.original_objects.create_user(
            user_id=owner_id,
            username='existing-owner',
            email='existing-owner@example.com',
            password='ExistingOwner!123',
            role=User.ASSOCIATION,
        )
        existing_password = existing_owner.password
        association = self._association_record(uuid.uuid4(), owner_id)
        users = [
            self._user_record(
                owner_id,
                'archived-owner',
                'archived-owner@example.com',
                role=User.ASSOCIATION,
            ),
        ]

        with tempfile.NamedTemporaryFile(suffix='.zip') as archive:
            self._write_archive(archive.name, association, users)

            validation = ImportValidator(archive.name).validate_all()
            self.assertTrue(validation.is_valid, validation.errors)
            self.assertFalse(
                validation.info['owner_user']['requires_recovery_password']
            )

            service = AssociationImportService(archive.name, ImportOptions())
            self.assertTrue(service.validate().is_valid)
            imported = service.import_all()

        existing_owner.refresh_from_db()
        self.assertEqual(imported.user, existing_owner)
        self.assertEqual(existing_owner.password, existing_password)
        self.assertTrue(existing_owner.check_password('ExistingOwner!123'))

    def test_export_includes_only_owner_password_hash(self):
        owner = User.objects.create_user(
            username='owner@example.com',
            email='owner@example.com',
            password='Owner!123',
            role=User.ASSOCIATION,
        )
        member = User.objects.create_user(
            username='member@example.com',
            email='member@example.com',
            password='Member!123',
            role=User.ATHLETE,
        )
        association = SportAssociation.objects.create(
            user=owner,
            denomination='Password Export ASD',
            tax_code='12345678901',
        )
        service = AssociationExportService(association.sport_association_id)

        owner_data = service.serialize_record(owner)
        member_data = service.serialize_record(member)

        self.assertEqual(owner_data['password'], owner.password)
        self.assertNotIn('password', member_data)

    def test_source_uuids_are_preserved_for_imported_uuid_models(self):
        owner_id = uuid.uuid4()
        association_id = uuid.uuid4()
        family_id = uuid.uuid4()
        association = self._association_record(
            association_id,
            owner_id,
            denomination='Complete ASD',
            president_last_name='Lovelace',
        )
        users = [
            self._user_record(owner_id, 'owner', 'owner@example.com', role=User.ASSOCIATION),
        ]
        families = [
            {
                'family_id': str(family_id),
                'type': Family.FAMILY,
            }
        ]

        imported, _service = self._import_archive(
            association,
            users,
            extra_data={'data/06_families.json': families},
        )

        self.assertEqual(imported.sport_association_id, association_id)
        self.assertEqual(imported.user_id, owner_id)
        self.assertEqual(Family.objects.get(family_id=family_id).type, Family.FAMILY)
        self.assertTrue(imported.multiple_subscription_fee)
        self.assertEqual(imported.subscription_fee_plans, [{'amount': '10.00'}])
        self.assertFalse(imported.enable_quotes_management)
        self.assertEqual(imported.president_last_name, 'Lovelace')

    def test_shared_user_reuse_by_exact_uuid_does_not_overwrite_credentials_or_profile(self):
        owner_id = uuid.uuid4()
        shared_id = uuid.uuid4()
        association_id = uuid.uuid4()
        existing_shared = User.original_objects.create_user(
            user_id=shared_id,
            username='existing-shared',
            email='existing-shared@example.com',
            password='KeepMe!123',
            role=User.COLLABORATOR,
            first_name='Existing',
            two_fa=True,
            is_staff=True,
        )
        association = self._association_record(association_id, owner_id)
        users = [
            self._user_record(owner_id, 'owner', 'owner@example.com', role=User.ASSOCIATION),
            self._user_record(
                shared_id,
                'archived-shared',
                'archived-shared@example.com',
                role=User.ATHLETE,
                first_name='Archived',
                two_fa=False,
                is_staff=False,
            ),
        ]

        self._import_archive(association, users)

        existing_shared.refresh_from_db()
        self.assertEqual(existing_shared.username, 'existing-shared')
        self.assertEqual(existing_shared.email, 'existing-shared@example.com')
        self.assertEqual(existing_shared.first_name, 'Existing')
        self.assertEqual(existing_shared.role, User.COLLABORATOR)
        self.assertTrue(existing_shared.two_fa)
        self.assertTrue(existing_shared.is_staff)
        self.assertTrue(existing_shared.check_password('KeepMe!123'))

    def test_import_fails_when_source_owner_already_owns_a_different_association(self):
        owner_id = uuid.uuid4()
        existing_owner = User.original_objects.create_user(
            user_id=owner_id,
            username='owner',
            email='owner@example.com',
            password='Owner!123',
            role=User.ASSOCIATION,
        )
        SportAssociation.original_objects.create(
            user=existing_owner,
            denomination='Existing ASD',
            tax_code='11111111111',
        )
        association = self._association_record(uuid.uuid4(), owner_id)
        users = [
            self._user_record(owner_id, 'owner', 'owner@example.com', role=User.ASSOCIATION),
        ]

        with tempfile.NamedTemporaryFile(suffix='.zip') as archive:
            self._write_archive(archive.name, association, users)
            service = AssociationImportService(
                archive.name,
                ImportOptions(owner_password='Recovery!123'),
            )
            with self.assertRaisesMessage(ValueError, 'already owns another SportAssociation'):
                service.import_all()

    def test_role_association_shared_user_can_become_second_import_owner_once(self):
        owner_a_id = uuid.uuid4()
        owner_b_id = uuid.uuid4()
        association_a_id = uuid.uuid4()
        association_b_id = uuid.uuid4()
        association_c_id = uuid.uuid4()

        association_a = self._association_record(
            association_a_id,
            owner_a_id,
            denomination='Association A',
        )
        users_a = [
            self._user_record(
                owner_a_id,
                'owner-a',
                'owner-a@example.com',
                role=User.ASSOCIATION,
            ),
            self._user_record(
                owner_b_id,
                'owner-b',
                'owner-b@example.com',
                role=User.ASSOCIATION,
                first_name='Owner',
                last_name='Bee',
            ),
        ]

        self._import_archive(association_a, users_a, owner_password='RecoveryA!123')

        shared_user = User.original_objects.get(user_id=owner_b_id)
        self.assertEqual(shared_user.username, 'owner-b')
        self.assertEqual(shared_user.email, 'owner-b@example.com')
        self.assertFalse(shared_user.has_usable_password())
        self.assertFalse(SportAssociation.original_objects.filter(user=shared_user).exists())

        association_b = self._association_record(
            association_b_id,
            owner_b_id,
            denomination='Association B',
        )
        users_b = [
            self._user_record(
                owner_b_id,
                'owner-b',
                'owner-b@example.com',
                role=User.ASSOCIATION,
                first_name='Different archived name',
            ),
        ]

        imported_b, _service_b = self._import_archive(
            association_b,
            users_b,
            owner_password='RecoveryB!123',
        )

        shared_user.refresh_from_db()
        self.assertEqual(imported_b.user_id, owner_b_id)
        self.assertEqual(imported_b.user, shared_user)
        self.assertEqual(User.original_objects.filter(user_id=owner_b_id).count(), 1)
        self.assertTrue(shared_user.check_password('RecoveryB!123'))
        self.assertEqual(shared_user.username, 'owner-b')
        self.assertEqual(shared_user.email, 'owner-b@example.com')
        self.assertEqual(shared_user.first_name, 'Owner')
        self.assertEqual(shared_user.last_name, 'Bee')

        encoded_password_after_b = shared_user.password
        association_c = self._association_record(
            association_c_id,
            owner_b_id,
            denomination='Association C',
        )
        users_c = [
            self._user_record(
                owner_b_id,
                'owner-b-overwrite',
                'owner-b-overwrite@example.com',
                role=User.ASSOCIATION,
            ),
        ]

        with tempfile.NamedTemporaryFile(suffix='.zip') as archive:
            self._write_archive(archive.name, association_c, users_c)
            service = AssociationImportService(
                archive.name,
                ImportOptions(owner_password='RecoveryC!123'),
            )
            with self.assertRaisesMessage(ValueError, 'already owns another SportAssociation'):
                service.import_all()

        shared_user.refresh_from_db()
        self.assertEqual(shared_user.password, encoded_password_after_b)
        self.assertTrue(shared_user.check_password('RecoveryB!123'))
        self.assertFalse(shared_user.check_password('RecoveryC!123'))
        self.assertEqual(SportAssociation.original_objects.filter(user=shared_user).count(), 1)

    def test_old_backup_without_media_entries_imports_silently(self):
        owner_id = uuid.uuid4()
        association = self._association_record(uuid.uuid4(), owner_id)
        users = [
            self._user_record(owner_id, 'owner', 'owner@example.com', role=User.ASSOCIATION),
        ]

        with tempfile.NamedTemporaryFile(suffix='.zip') as archive:
            self._write_archive(archive.name, association, users)
            validation = ImportValidator(archive.name).validate_all()
            self.assertTrue(validation.is_valid, validation.errors)
            self.assertEqual(validation.warnings, [])
            self.assertEqual(validation.info['owner_user']['email'], 'owner@example.com')

            service = AssociationImportService(
                archive.name,
                ImportOptions(owner_password='Recovery!123'),
            )
            service.import_all()

        self.assertEqual(service.stats.get('files_imported'), 0)
        self.assertEqual(service.errors, [])


class AssociationImportMediaTests(TestCase):
    def _copy_storage_file_to_temp_zip(self, storage_key):
        temp_file = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
        try:
            with default_storage.open(storage_key, 'rb') as src:
                temp_file.write(src.read())
            temp_file.close()
            return temp_file.name
        except Exception:
            temp_file.close()
            os.remove(temp_file.name)
            raise

    def test_private_subscription_signature_media_round_trips_and_is_rekeyed(self):
        with tempfile.TemporaryDirectory() as storage_dir:
            with override_settings(
                STORAGES={
                    'default': {
                        'BACKEND': 'django.core.files.storage.FileSystemStorage',
                        'OPTIONS': {'location': storage_dir},
                    },
                    'staticfiles': {
                        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
                    },
                },
                AWS_S3_PUBLIC_BASE_URL='',
            ):
                owner = User.objects.create_user(
                    username='owner@example.com',
                    email='owner@example.com',
                    password='Owner!123',
                    role=User.ASSOCIATION,
                )
                member = User.objects.create_user(
                    username='member@example.com',
                    email='member@example.com',
                    role=User.ATHLETE,
                )
                association = SportAssociation.objects.create(
                    user=owner,
                    denomination='Signature ASD',
                    tax_code='12345678901',
                )
                associate = Associate.objects.create(
                    user=member,
                    sport_association=association,
                    first_name='Signed',
                    last_name='Member',
                    tax_code='SMBRSG00A01H501Z',
                )
                signature_bytes = b'private-signature-bytes'
                source_signature_key = default_storage.save(
                    'source/signature.png',
                    ContentFile(signature_bytes),
                )
                subscription = Subscription.objects.create(
                    sport_association=association,
                    associate=associate,
                    user=member,
                    custom_data={},
                    additional_fields={},
                    signature_storage_key=source_signature_key,
                )
                subscription_id = subscription.subscription_id

                export_document = AssociationExportService(association.sport_association_id).export()
                zip_path = self._copy_storage_file_to_temp_zip(export_document.filepath)

                try:
                    with zipfile.ZipFile(zip_path) as zf:
                        signature_entries = [
                            name for name in zf.namelist()
                            if name.startswith(f'files/subscription_signatures/{subscription_id}/')
                        ]
                        self.assertEqual(len(signature_entries), 1)

                    Subscription.objects.all().delete()
                    Associate.objects.all().delete()
                    SportAssociation.original_objects.all().delete()
                    User.original_objects.all().delete()

                    service = AssociationImportService(
                        zip_path,
                        ImportOptions(owner_password='Recovery!123'),
                    )
                    service.import_all()

                    imported_subscription = Subscription.objects.get(subscription_id=subscription_id)
                    self.assertNotEqual(imported_subscription.signature_storage_key, source_signature_key)
                    self.assertTrue(default_storage.exists(imported_subscription.signature_storage_key))
                    with default_storage.open(imported_subscription.signature_storage_key, 'rb') as imported_file:
                        self.assertEqual(imported_file.read(), signature_bytes)
                finally:
                    os.remove(zip_path)
