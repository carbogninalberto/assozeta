"""DELETE /subscription/<uid>/delete-document/<file id>."""
from rest_framework import status

from application.models.subscriptions_models import SubscriptionFile
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_document, create_test_subscription
from docmanager.models import Document


class SubscriptionDeleteDocumentTests(BaseAPITestCase):
    def link(self, subscription, document):
        return SubscriptionFile.objects.create(subscription=subscription, document=document)

    def delete(self, subscription, link):
        return self.client.delete(
            f'/subscription/{subscription.subscription_id}/delete-document/{link.subscription_file_id}',
        )

    def test_document_of_one_subscription_is_removed_with_its_link(self):
        subscription = create_test_subscription(sport_association=self.sport_association)
        document = create_test_document()
        link = self.link(subscription, document)

        response = self.delete(subscription, link)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(SubscriptionFile.objects.filter(pk=link.pk).exists())
        self.assertFalse(Document.objects.filter(pk=document.pk).exists())

    def test_document_used_by_another_subscription_keeps_its_row_and_other_link(self):
        subscription = create_test_subscription(sport_association=self.sport_association)
        other = create_test_subscription(sport_association=self.sport_association)
        document = create_test_document()
        link = self.link(subscription, document)
        other_link = self.link(other, document)

        response = self.delete(subscription, link)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(SubscriptionFile.objects.filter(pk=link.pk).exists())
        self.assertTrue(SubscriptionFile.objects.filter(pk=other_link.pk).exists())
        self.assertTrue(Document.objects.filter(pk=document.pk).exists())
