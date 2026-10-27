from application.models import SportAssociationDocumentsArchive, User
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_sport_association, create_test_user
from docmanager.models import Document


class DocumentDeleteScopeTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.owner = create_test_user(role=User.ASSOCIATION)
        self.own = create_test_sport_association(user=self.owner)
        self.other_owner = create_test_user(role=User.ASSOCIATION)
        self.other = create_test_sport_association(user=self.other_owner)

    def archived(self, association):
        document = Document.objects.create(filename='f.pdf')
        SportAssociationDocumentsArchive.objects.create(sport_association=association, document=document)
        return document

    def delete(self, user, document_id):
        self.client.force_authenticate(user)
        return self.client.delete(f'/document/{document_id}/delete')

    def test_owner_deletes_own_document(self):
        document = self.archived(self.own)
        response = self.delete(self.owner, document.pk)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Document.objects.filter(pk=document.pk).exists())

    def test_foreign_document_is_not_found_and_kept(self):
        document = self.archived(self.other)
        response = self.delete(self.owner, document.pk)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data, {'error': 'Document not found.'})
        self.assertTrue(Document.objects.filter(pk=document.pk).exists())
        self.assertTrue(SportAssociationDocumentsArchive.objects.filter(document=document).exists())

    def test_shared_document_requires_every_reference_to_be_own(self):
        document = self.archived(self.own)
        SportAssociationDocumentsArchive.objects.create(sport_association=self.other, document=document)
        self.assertEqual(self.delete(self.owner, document.pk).status_code, 404)
        self.assertTrue(Document.objects.filter(pk=document.pk).exists())

    def test_unreferenced_document_follows_its_creation_audit(self):
        from auditlog.context import set_actor
        with set_actor(self.owner):
            own = Document.objects.create(filename='own-orphan.pdf')
        with set_actor(self.other_owner):
            foreign = Document.objects.create(filename='foreign-orphan.pdf')
        self.assertEqual(self.delete(self.owner, foreign.pk).status_code, 404)
        self.assertTrue(Document.objects.filter(pk=foreign.pk).exists())
        self.assertEqual(self.delete(self.owner, own.pk).status_code, 200)
        self.assertFalse(Document.objects.filter(pk=own.pk).exists())

    def test_unattributed_orphan_and_athlete_are_not_found(self):
        orphan = Document.objects.create(filename='orphan.pdf')
        self.assertEqual(self.delete(self.owner, orphan.pk).status_code, 404)
        athlete = create_test_user(role=User.ATHLETE)
        document = self.archived(self.own)
        self.assertEqual(self.delete(athlete, document.pk).status_code, 404)
        self.assertTrue(Document.objects.filter(pk__in=[orphan.pk, document.pk]).count() == 2)

    def test_route_uses_scoped_view(self):
        from django.urls import resolve
        from application import document_scope
        self.assertIs(resolve('/document/00000000-0000-0000-0000-000000000000/delete').func, document_scope.delete_document)
