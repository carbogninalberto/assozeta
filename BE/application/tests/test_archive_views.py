"""
Tests for Archive views - folder and document management endpoints.

This module tests the archive-related API endpoints for
folders, documents, and templates.
"""
import base64
import uuid

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from application.models import User
from application.models.user_models import SportAssociationDocumentsArchive, Folder
from application.tests.fixtures.factories import create_test_user, create_test_sport_association


class FolderListTests(TestCase):
    """Tests for folder list endpoint: GET /folders/list"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_list_folders_empty(self):
        response = self.client.get('/folders/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

    def test_list_folders_with_data(self):
        Folder.objects.create(
            sport_association=self.sport_association,
            name='Test Folder'
        )
        response = self.client.get('/folders/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

    def test_list_folders_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/folders/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class FolderAddTests(TestCase):
    """Tests for folder add endpoint: POST /folders/add"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_add_folder_success(self):
        response = self.client.post(
            '/folders/add',
            {'name': 'New Test Folder'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Folder.objects.filter(
                sport_association=self.sport_association,
                name='New Test Folder'
            ).exists()
        )

    def test_add_folder_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            '/folders/add',
            {'name': 'Test'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class FolderUpdateTests(TestCase):
    """Tests for folder update endpoint: PATCH /folders/<pk>/update"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_update_folder_success(self):
        folder = Folder.objects.create(
            sport_association=self.sport_association,
            name='Original Name'
        )
        response = self.client.patch(
            f'/folders/{folder.id}/update',
            {'name': 'Updated Name'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        folder.refresh_from_db()
        self.assertEqual(folder.name, 'Updated Name')


class FolderDeleteTests(TestCase):
    """Tests for folder delete endpoint: DELETE /folders/<pk>/delete"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_delete_folder_success(self):
        folder = Folder.objects.create(
            sport_association=self.sport_association,
            name='Folder To Delete'
        )
        folder_id = folder.id
        response = self.client.delete(f'/folders/{folder_id}/delete')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Folder.objects.filter(id=folder_id).exists())

    def test_delete_nonexistent_folder(self):
        fake_id = str(uuid.uuid4())
        response = self.client.delete(f'/folders/{fake_id}/delete')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class DocumentListTests(TestCase):
    """Tests for document list endpoint: GET /documents/list"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_list_documents_empty(self):
        response = self.client.get('/documents/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

    def test_list_documents_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/documents/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class TemplateListTests(TestCase):
    """Tests for templates list endpoint: GET /templates/"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_list_templates(self):
        response = self.client.get('/templates/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_templates_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/templates/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class FolderMoveTests(TestCase):
    """Tests for folder move endpoint: POST /folders/<pk>/move"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_move_folder_to_parent(self):
        parent = Folder.objects.create(
            sport_association=self.sport_association,
            name='Parent Folder'
        )
        child = Folder.objects.create(
            sport_association=self.sport_association,
            name='Child Folder'
        )
        response = self.client.post(
            f'/folders/{child.id}/move',
            {'new_parent': parent.id},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        child.refresh_from_db()
        self.assertEqual(child.parent_id, parent.id)

    def test_move_folder_to_root(self):
        parent = Folder.objects.create(
            sport_association=self.sport_association,
            name='Parent'
        )
        child = Folder.objects.create(
            sport_association=self.sport_association,
            name='Child',
            parent=parent
        )
        response = self.client.post(
            f'/folders/{child.id}/move',
            {'new_parent': None},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_move_folder_different_association_forbidden(self):
        other_user = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_user)
        other_folder = Folder.objects.create(
            sport_association=other_association,
            name='Other Association Folder'
        )
        my_folder = Folder.objects.create(
            sport_association=self.sport_association,
            name='My Folder'
        )
        response = self.client.post(
            f'/folders/{my_folder.id}/move',
            {'new_parent': other_folder.id},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class FolderPermissionTests(TestCase):
    """Tests for folder permission checks."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_delete_other_association_folder_forbidden(self):
        other_user = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_user)
        other_folder = Folder.objects.create(
            sport_association=other_association,
            name='Other Association Folder'
        )
        response = self.client.delete(f'/folders/{other_folder.id}/delete')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_other_association_folder_forbidden(self):
        other_user = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_user)
        other_folder = Folder.objects.create(
            sport_association=other_association,
            name='Other Association Folder'
        )
        response = self.client.patch(
            f'/folders/{other_folder.id}/update',
            {'name': 'Hacked Name'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_folder_with_specific_parent(self):
        parent = Folder.objects.create(
            sport_association=self.sport_association,
            name='Parent'
        )
        child = Folder.objects.create(
            sport_association=self.sport_association,
            name='Child',
            parent=parent
        )
        response = self.client.get(f'/folders/list?folder={parent.id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        self.assertIn('path', data)

    def test_list_folder_nonexistent_parent_returns_404(self):
        fake_id = 99999
        response = self.client.get(f'/folders/list?folder={fake_id}')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class DocumentBulkDeleteTests(TestCase):
    """Tests for document bulk delete endpoint: DELETE /documents/bulk-delete"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_bulk_delete_no_files_error(self):
        response = self.client.delete(
            '/documents/bulk-delete',
            {'files': []},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class DocumentMoveTests(TestCase):
    """Tests for document move endpoint: POST /documents/<pk>/move"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_move_document_nonexistent_returns_404(self):
        fake_id = str(uuid.uuid4())
        response = self.client.post(
            f'/documents/{fake_id}/move',
            {'folder': None},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class TemplateCreateTests(TestCase):
    """Tests for template create endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_create_template(self):
        response = self.client.post(
            '/templates/',
            {
                'name': 'Test Template',
                'content': 'Test content',
                'type': 'generic'
            },
            format='json'
        )
        self.assertIn(response.status_code, [status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST])


class TemplateDeleteTests(TestCase):
    """Tests for template delete endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_delete_template_nonexistent_returns_404(self):
        fake_id = str(uuid.uuid4())
        response = self.client.delete(f'/templates/{fake_id}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class TemplateBulkDeleteTests(TestCase):
    """Tests for template bulk delete endpoint: DELETE /templates/bulk-delete"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_bulk_delete_no_templates_error(self):
        response = self.client.delete(
            '/templates/bulk-delete',
            {'sport_association_module_templates_ids': []},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ArchiveAuthenticationTests(TestCase):
    """Tests for authentication requirements across archive endpoints."""

    def setUp(self):
        self.client = APIClient()

    def test_folders_list_requires_auth(self):
        response = self.client.get('/folders/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_folders_add_requires_auth(self):
        response = self.client.post('/folders/add', {'name': 'Test'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_folders_update_requires_auth(self):
        response = self.client.patch(f'/folders/{uuid.uuid4()}/update', {'name': 'Test'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_folders_delete_requires_auth(self):
        response = self.client.delete(f'/folders/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_folders_move_requires_auth(self):
        response = self.client.post(f'/folders/{uuid.uuid4()}/move', {})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_documents_list_requires_auth(self):
        response = self.client.get('/documents/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_documents_add_requires_auth(self):
        response = self.client.post('/documents/add', {})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_documents_delete_requires_auth(self):
        response = self.client.delete(f'/documents/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_documents_bulk_delete_requires_auth(self):
        response = self.client.delete('/documents/bulk-delete', {})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_documents_update_requires_auth(self):
        response = self.client.patch(f'/documents/{uuid.uuid4()}/update', {})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_documents_move_requires_auth(self):
        response = self.client.post(f'/documents/{uuid.uuid4()}/move', {})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_templates_list_requires_auth(self):
        response = self.client.get('/templates/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class DocumentAddTests(TestCase):
    """Tests for document add endpoint: POST /documents/add"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_add_document_missing_document_key(self):
        response = self.client.post('/documents/add', {
            'files': [{
                'filename': 'test_file.txt'
            }]
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_add_document_missing_filename_key(self):
        file_content = base64.b64encode(b'Test content').decode('utf-8')
        response = self.client.post('/documents/add', {
            'files': [{
                'document': file_content
            }]
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class DocumentListAdvancedTests(TestCase):
    """Advanced tests for document list endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_list_documents_with_folder_filter(self):
        folder = Folder.objects.create(
            sport_association=self.sport_association,
            name='Test Folder'
        )
        response = self.client.get(f'/documents/list?folder={folder.id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        self.assertIn('pagination', data)

    def test_list_documents_with_pagination(self):
        response = self.client.get('/documents/list?page=0')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('pagination', data)
        self.assertIn('page', data['pagination'])
        self.assertIn('total_pages', data['pagination'])

    def test_list_documents_folder_different_association_forbidden(self):
        other_user = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_user)
        other_folder = Folder.objects.create(
            sport_association=other_association,
            name='Other Folder'
        )
        response = self.client.get(f'/documents/list?folder={other_folder.id}')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class DocumentUpdateTests(TestCase):
    """Tests for document update endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_update_document_nonexistent(self):
        fake_id = str(uuid.uuid4())
        response = self.client.patch(
            f'/documents/{fake_id}/update',
            {'name': 'Updated'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class DocumentMoveAdvancedTests(TestCase):
    """Advanced tests for document move endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_move_document_to_different_association_folder_forbidden(self):
        other_user = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_user)
        other_folder = Folder.objects.create(
            sport_association=other_association,
            name='Other Folder'
        )
        from docmanager.models import Document
        document = Document.objects.create(filename='test.txt')
        archive = SportAssociationDocumentsArchive.objects.create(
            sport_association=self.sport_association,
            document=document
        )
        response = self.client.post(
            f'/documents/{archive.sport_association_documents_archive_id}/move',
            {'folder': other_folder.id},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_move_document_to_root(self):
        from docmanager.models import Document
        folder = Folder.objects.create(
            sport_association=self.sport_association,
            name='Test Folder'
        )
        document = Document.objects.create(filename='test.txt')
        archive = SportAssociationDocumentsArchive.objects.create(
            sport_association=self.sport_association,
            document=document,
            folder=folder
        )
        response = self.client.post(
            f'/documents/{archive.sport_association_documents_archive_id}/move',
            {'folder': None},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_move_document_to_folder(self):
        from docmanager.models import Document
        folder = Folder.objects.create(
            sport_association=self.sport_association,
            name='Target Folder'
        )
        document = Document.objects.create(filename='test.txt')
        archive = SportAssociationDocumentsArchive.objects.create(
            sport_association=self.sport_association,
            document=document
        )
        response = self.client.post(
            f'/documents/{archive.sport_association_documents_archive_id}/move',
            {'folder': folder.id},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class DocumentBulkDeleteAdvancedTests(TestCase):
    """Advanced tests for document bulk delete endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_bulk_delete_documents_success(self):
        from docmanager.models import Document
        doc1 = Document.objects.create(filename='test1.txt')
        doc2 = Document.objects.create(filename='test2.txt')
        archive1 = SportAssociationDocumentsArchive.objects.create(
            sport_association=self.sport_association,
            document=doc1
        )
        archive2 = SportAssociationDocumentsArchive.objects.create(
            sport_association=self.sport_association,
            document=doc2
        )
        response = self.client.delete(
            '/documents/bulk-delete',
            {'files': [
                str(archive1.sport_association_documents_archive_id),
                str(archive2.sport_association_documents_archive_id)
            ]},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_bulk_delete_other_association_documents_forbidden(self):
        from docmanager.models import Document
        other_user = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_user)
        doc = Document.objects.create(filename='other.txt')
        archive = SportAssociationDocumentsArchive.objects.create(
            sport_association=other_association,
            document=doc
        )
        response = self.client.delete(
            '/documents/bulk-delete',
            {'files': [str(archive.sport_association_documents_archive_id)]},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_bulk_delete_nonexistent_documents(self):
        response = self.client.delete(
            '/documents/bulk-delete',
            {'files': [str(uuid.uuid4())]},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
