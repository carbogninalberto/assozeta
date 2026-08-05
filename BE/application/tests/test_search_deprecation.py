from django.test import TestCase
from rest_framework.test import APIClient

from application.models import SportAssociation, User


class SearchDeprecationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        owner = User.objects.create_user(
            username='primary-association',
            email='primary-association@example.com',
            password='StrongPass!1',
            role=User.ASSOCIATION,
        )
        self.association = SportAssociation.objects.create(
            user=owner,
            denomination='Primary Association',
            tax_code='12345678901',
        )

    def test_discovery_endpoint_is_removed(self):
        response = self.client.get('/search/all?q=primary')

        self.assertEqual(response.status_code, 404)

    def test_association_profile_endpoint_remains_available(self):
        response = self.client.get('/search/profile/primary-association')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            str(response.data['data']['sport_association_id']),
            str(self.association.sport_association_id),
        )
