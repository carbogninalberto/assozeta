import tempfile
import uuid

from django.test import TestCase

from application.models import Associate
from application.services.import_service import AssociationImportService, ImportOptions
from application.tests import test_import_identity_media as identity


class SetupImportTaxCodeConflictTests(TestCase):
    """Legacy backups can hold an unassigned anagrafica sharing a member's tax code.

    The setup import moves every anagrafica into the imported association, so it
    must clear the legacy copy's tax code (as the data restore does) instead of
    failing on unique_tax_code_sport_assoc_upper_active.
    """

    _user_record = identity.AssociationImportIdentityTests._user_record
    _association_record = identity.AssociationImportIdentityTests._association_record
    _write_archive = identity.AssociationImportIdentityTests._write_archive

    def setUp(self):
        self.owner_id = uuid.uuid4()
        self.association_id = uuid.uuid4()
        self.association = self._association_record(self.association_id, self.owner_id)
        self.users = [self._user_record(self.owner_id, 'owner', 'owner@example.com', role='ASSOCIATION')]

    def _associate(self, associate_id, tax_code, association=True, deleted=False, **extra):
        return {
            'associate_id': str(associate_id),
            'sport_association_id': str(self.association_id) if association is True else association,
            'first_name': extra.pop('first_name', 'Ada'),
            'last_name': extra.pop('last_name', 'Lovelace'),
            'tax_code': tax_code,
            'deleted': deleted,
            'creation_date': '2024-12-25T03:57:24.162568+00:00',
            **extra,
        }

    def _import(self, associates):
        with tempfile.NamedTemporaryFile(suffix='.zip') as archive:
            self._write_archive(archive.name, self.association, self.users,
                                extra_data={'data/07_associates.json': associates})
            service = AssociationImportService(archive.name, ImportOptions(owner_password='Recovery!123'))
            service.import_all()
        return service

    def _tax_codes(self):
        return dict(Associate._base_manager.values_list('associate_id', 'tax_code'))

    def test_unassigned_legacy_copy_of_a_member_loses_its_tax_code(self):
        member, legacy = uuid.uuid4(), uuid.uuid4()
        member_b, legacy_b = uuid.uuid4(), uuid.uuid4()
        self._import([
            self._associate(member, 'BNCGNN85M01H501Z'),
            self._associate(legacy, 'BNCGNN85M01H501Z', association=None),
            # The legacy copy can also come first, and differ in case.
            self._associate(legacy_b, 'vrdmrc70c15f205k', association=None),
            self._associate(member_b, 'VRDMRC70C15F205K'),
        ])
        self.assertEqual(self._tax_codes(), {
            member: 'BNCGNN85M01H501Z', legacy: None,
            member_b: 'VRDMRC70C15F205K', legacy_b: None,
        })

    def test_legacy_copies_without_a_member_keep_the_code_on_the_lowest_uuid(self):
        low = uuid.UUID('00000000-0000-4000-8000-000000000001')
        high = uuid.UUID('ffffffff-ffff-4fff-8fff-ffffffffffff')
        self._import([
            self._associate(high, 'RSSMRA80A01H501U', association=None),
            self._associate(low, 'RSSMRA80A01H501U', association=None),
        ])
        self.assertEqual(self._tax_codes(), {low: 'RSSMRA80A01H501U', high: None})

    def test_deleted_rows_neither_claim_nor_lose_a_tax_code(self):
        deleted_member, legacy, deleted_legacy, member = (uuid.uuid4() for _ in range(4))
        self._import([
            self._associate(deleted_member, 'RSSMRA80A01H501U', deleted=True),
            self._associate(legacy, 'RSSMRA80A01H501U', association=None),
            self._associate(deleted_legacy, 'VRDLGU80A01H501X', association=None, deleted=True),
            self._associate(member, 'VRDLGU80A01H501X'),
        ])
        self.assertEqual(self._tax_codes(), {
            deleted_member: 'RSSMRA80A01H501U', legacy: 'RSSMRA80A01H501U',
            deleted_legacy: 'VRDLGU80A01H501X', member: 'VRDLGU80A01H501X',
        })

    def test_duplicate_active_members_fail_with_a_readable_error(self):
        first, second = uuid.uuid4(), uuid.uuid4()
        with self.assertRaisesMessage(ValueError, f'Codice fiscale duplicato tra le anagrafiche attive: {second}.'):
            self._import([
                self._associate(first, 'RSSMRA80A01H501U'),
                self._associate(second, 'rssmra80a01h501u'),
            ])
        self.assertFalse(Associate._base_manager.exists())
