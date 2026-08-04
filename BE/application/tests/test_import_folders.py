import json
import tempfile
import zipfile

from django.test import TestCase

from application.models import Folder, Invoice, MedicalCertificate, SportAssociation, User
from application.services.import_service import AssociationImportService, ImportOptions


class FolderImportTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(
            username='owner@example.com',
            email='owner@example.com',
            role=User.ASSOCIATION,
        )
        self.association = SportAssociation.objects.create(
            user=owner,
            denomination='Test association',
            tax_code='12345678901',
        )

    def test_import_regenerates_mptt_fields_and_preserves_hierarchy(self):
        records = [
            {
                'id': 2,
                'name': 'Child',
                'parent_id': '1',
                'sport_association_id': str(self.association.pk),
                'lft': 2,
                'rght': 3,
                'tree_id': 1,
                'level': 1,
            },
            {
                'id': 1,
                'name': 'Root',
                'parent_id': None,
                'sport_association_id': str(self.association.pk),
                'lft': 1,
                'rght': 4,
                'tree_id': 1,
                'level': 0,
            },
        ]

        with tempfile.NamedTemporaryFile(suffix='.zip') as archive:
            with zipfile.ZipFile(archive.name, 'w') as zf:
                zf.writestr('data/14_folders.json', json.dumps(records))

            service = AssociationImportService(
                archive.name,
                ImportOptions(
                    owner_email='owner@example.com',
                    owner_password='unused',
                    preserve_uuids=True,
                ),
            )
            service.association = self.association

            with zipfile.ZipFile(archive.name) as zf:
                imported_count = service._import_model_data(zf, '14_folders.json', Folder)

        root = Folder.objects.get(name='Root')
        child = Folder.objects.get(name='Child')

        self.assertEqual(imported_count, 2)
        self.assertIsNone(root.parent_id)
        self.assertEqual(child.parent_id, root.id)
        self.assertEqual(child.tree_id, root.tree_id)
        self.assertGreater(child.lft, root.lft)
        self.assertLess(child.rght, root.rght)
        self.assertEqual(service._resolve_fk('1', 'Folder'), root.id)

    def test_preserved_uuids_are_available_for_deferred_relationships(self):
        service = AssociationImportService(
            'unused.zip',
            ImportOptions(
                owner_email='owner@example.com',
                owner_password='unused',
                preserve_uuids=True,
            ),
        )
        old_uuid = '57af2f03-3d3d-4679-a77d-411cc886b2db'

        generated_uuid = service._generate_uuid(old_uuid, 'Associate')

        self.assertEqual(generated_uuid, old_uuid)
        self.assertEqual(service.uuid_mapping[old_uuid], old_uuid)

    def test_missing_preserved_medical_certificate_user_is_cleared(self):
        service = AssociationImportService(
            'unused.zip',
            ImportOptions(
                owner_email='owner@example.com',
                owner_password='unused',
                preserve_uuids=True,
            ),
        )
        service.imported_models.update({'User', 'Document'})

        certificate = service._create_model_instance(
            MedicalCertificate,
            {
                'medical_id': '338d435c-38a8-41e5-9d21-04f5af9a1561',
                'document_id': None,
                'user_id': '0735c549-b112-4059-8ece-067b1d2bde49',
                'expiration_date': '2024-01-30',
                'competitive_medical_certificate': False,
                'notes': None,
            },
        )

        self.assertIsNone(certificate.user_id)
        self.assertIn('MedicalCertificate references missing User', service.errors[0])

    def test_unlisted_nullable_foreign_key_is_discovered_and_cleared(self):
        service = AssociationImportService(
            'unused.zip',
            ImportOptions(
                owner_email='owner@example.com',
                owner_password='unused',
                preserve_uuids=True,
            ),
        )
        service.imported_models.add('Associate')

        invoice = service._create_model_instance(
            Invoice,
            {
                'invoice_id': 'c8938bdd-2372-42cc-9c4c-c5caa8a001ba',
                'selected_tutor_id': '95943b02-43b2-473b-8b79-393497fe5bf7',
            },
        )

        self.assertIsNone(invoice.selected_tutor_id)
        self.assertIn('Invoice references missing Associate', service.errors[0])
