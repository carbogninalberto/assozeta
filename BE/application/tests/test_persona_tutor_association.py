"""A tutor id from another association is treated exactly like an unknown id."""
from django.test import TestCase
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from application.models.user_models import Associate, AssociateTutorRelation


class PersonaTutorAssociationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='OWNER', email='owner@example.com', password='StrongPass!1',
            role=User.ASSOCIATION,
        )
        self.association = SportAssociation.objects.create(
            user=self.owner, denomination='Primary Association', tax_code='12345678901',
        )
        self.foreign_owner = User.objects.create_user(
            username='FOREIGN', email='foreign@example.com', password='StrongPass!1',
            role=User.ASSOCIATION,
        )
        self.foreign_association = SportAssociation.objects.create(
            user=self.foreign_owner, denomination='Other Association', tax_code='10987654321',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.persona = Associate.objects.create(
            user=self.owner, sport_association=self.association, first_name='Anna',
        )
        self.foreign_tutor = Associate.objects.create(
            user=self.foreign_owner, sport_association=self.foreign_association,
            first_name='Bruno', last_name='Neri',
        )

    def assert_foreign_tutor_untouched_and_not_linked(self, persona):
        self.foreign_tutor.refresh_from_db()
        self.assertEqual(
            (self.foreign_tutor.first_name, self.foreign_tutor.last_name),
            ('Bruno', 'Neri'),
        )
        self.assertFalse(
            AssociateTutorRelation.objects.filter(tutor=self.foreign_tutor).exists(),
        )
        relations = AssociateTutorRelation.objects.filter(associate=persona)
        self.assertEqual(relations.count(), 1)
        return relations.first().tutor

    def test_update_with_another_association_associate_creates_a_local_tutor(self):
        response = self.client.patch(
            f'/personas/{self.persona.pk}/update',
            {
                'tutors_data': [{
                    'tutor': {'associate_id': str(self.foreign_tutor.pk), 'first_name': 'Cambiato'},
                    'is_primary': True,
                }],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        tutor = self.assert_foreign_tutor_untouched_and_not_linked(self.persona)
        self.assertEqual(tutor.first_name, 'Cambiato')
        self.assertNotEqual(tutor.pk, self.foreign_tutor.pk)

    def test_add_with_another_association_associate_creates_a_local_tutor(self):
        response = self.client.post(
            '/personas/add',
            {
                'first_name': 'Nuovo',
                'tutors_data': [{
                    'tutor': {'associate_id': str(self.foreign_tutor.pk), 'last_name': 'Altro'},
                }],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.content)
        persona = Associate.objects.get(first_name='Nuovo')
        tutor = self.assert_foreign_tutor_untouched_and_not_linked(persona)
        self.assertEqual(tutor.last_name, 'Altro')
        self.assertNotEqual(tutor.pk, self.foreign_tutor.pk)

    def test_update_with_an_own_associate_updates_and_links_it(self):
        own_tutor = Associate.objects.create(
            user=self.owner, sport_association=self.association,
            first_name='Carla', last_name='Bianchi',
        )

        response = self.client.patch(
            f'/personas/{self.persona.pk}/update',
            {
                'tutors_data': [{
                    'tutor': {'associate_id': str(own_tutor.pk), 'first_name': 'Aggiornata'},
                    'is_primary': True,
                }],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        own_tutor.refresh_from_db()
        self.assertEqual(own_tutor.first_name, 'Aggiornata')
        self.assertTrue(
            AssociateTutorRelation.objects.filter(
                associate=self.persona, tutor=own_tutor,
            ).exists(),
        )
