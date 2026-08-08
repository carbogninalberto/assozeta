import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from application.models.user_models import SportAssociationDocumentsArchive, User
from application.services.export_service import AssociationExportService
from application.tasks import cleanup_old_exports
from application.tests.fixtures.factories import (
    create_test_sport_association,
    create_test_user,
)
from docmanager.download_tokens import is_valid_document_download_token
from docmanager.models import Document


class ExportSizeTests(SimpleTestCase):
    @patch('application.services.export_service.SportAssociationDocumentsArchive.objects.create')
    @patch('application.services.export_service.Document.objects.create')
    @patch('application.services.export_service.default_storage.save')
    def test_save_to_storage_persists_file_size(
        self,
        storage_save,
        document_create,
        archive_create,
    ):
        document = MagicMock()
        storage_save.return_value = 'exports/backup.zip'
        document_create.return_value = document
        service = object.__new__(AssociationExportService)
        service.sport_association = SimpleNamespace()

        with tempfile.NamedTemporaryFile() as backup:
            backup.write(b'backup-content')
            backup.flush()
            result = service.save_to_storage(backup.name, 'backup.zip')

        self.assertIs(result, document)
        self.assertEqual(document.file_size_bytes, len(b'backup-content'))
        document.save.assert_called_once_with(update_fields=['filepath', 'file_size_bytes'])
        archive_create.assert_called_once()


class ExportDeliveryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = create_test_user(role=User.ASSOCIATION)
        self.association = create_test_sport_association(user=self.owner)
        self.document = Document.objects.create(
            filename='export_backup.zip',
            filepath='exports/backup.zip',
        )
        self.archive = SportAssociationDocumentsArchive.objects.create(
            sport_association=self.association,
            document=self.document,
        )
        self.client.force_authenticate(user=self.owner)

    def tearDown(self):
        cache.clear()

    @patch('application.views.export_views.default_storage.size', return_value=2048)
    def test_list_returns_size_and_association_scoped_token(self, storage_size):
        response = self.client.get('/association/export/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        export = response.data['exports'][0]
        self.assertEqual(export['file_size_bytes'], 2048)
        self.assertTrue(is_valid_document_download_token(
            export['download_token'],
            self.document.document_id,
            self.association.sport_association_id,
        ))
        self.document.refresh_from_db()
        self.assertEqual(self.document.file_size_bytes, 2048)
        storage_size.assert_called_once_with('exports/backup.zip')

    def test_collaborator_cannot_list_exports(self):
        collaborator = create_test_user(role=User.COLLABORATOR)
        collaborator.connected_user = self.owner
        collaborator.save(update_fields=['connected_user'])
        self.client.force_authenticate(user=collaborator)

        response = self.client.get('/association/export/list')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch('application.views.export_views.default_storage.delete')
    def test_delete_removes_storage_object_and_database_rows(self, storage_delete):
        response = self.client.delete(
            '/association/export/delete',
            {'document_id': str(self.document.document_id)},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        storage_delete.assert_called_once_with('exports/backup.zip')
        self.assertFalse(Document.objects.filter(pk=self.document.pk).exists())

    @patch('application.views.export_views.AsyncResult')
    @patch('application.views.export_views.export_association_data.delay')
    def test_export_status_is_scoped_to_requesting_association(self, task_delay, async_result):
        task_delay.return_value = SimpleNamespace(id='task-123')
        async_result.return_value.status = 'PENDING'
        async_result.return_value.ready.return_value = False
        start_response = self.client.post('/association/export/start', {}, format='json')

        self.assertEqual(start_response.status_code, status.HTTP_202_ACCEPTED)
        owner_response = self.client.get('/association/export/status?task_id=task-123')
        self.assertEqual(owner_response.status_code, status.HTTP_200_OK)

        foreign_owner = create_test_user(role=User.ASSOCIATION)
        create_test_sport_association(user=foreign_owner)
        self.client.force_authenticate(user=foreign_owner)
        foreign_response = self.client.get('/association/export/status?task_id=task-123')
        self.assertEqual(foreign_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_export_queryset_excludes_previous_backups(self):
        regular_document = Document.objects.create(filename='receipt.pdf')
        regular_archive = SportAssociationDocumentsArchive.objects.create(
            sport_association=self.association,
            document=regular_document,
        )
        service = AssociationExportService(self.association.sport_association_id)

        queryset = service.get_queryset_for_model(SportAssociationDocumentsArchive)

        self.assertEqual(list(queryset), [regular_archive])

    @patch('application.tasks.default_storage.delete')
    def test_cleanup_removes_excess_export_from_storage(self, storage_delete):
        self.archive.date = timezone.now().date() - timedelta(days=3)
        self.archive.save(update_fields=['date'])
        for days_ago in (2, 1, 0):
            document = Document.objects.create(
                filename=f'export_{days_ago}.zip',
                filepath=f'exports/{days_ago}.zip',
            )
            SportAssociationDocumentsArchive.objects.create(
                sport_association=self.association,
                document=document,
                date=timezone.now().date() - timedelta(days=days_ago),
            )

        deleted = cleanup_old_exports(
            str(self.association.sport_association_id),
            max_count=3,
        )

        self.assertEqual(deleted, 1)
        storage_delete.assert_called_once_with('exports/backup.zip')
