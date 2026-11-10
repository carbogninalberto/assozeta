import base64
import re
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from application.models.user_models import Associate

PICTURE = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG picture').decode()
STORAGE = 'application.serializers.personas_serializers.default_storage'


@override_settings(AWS_S3_PUBLIC_BASE_URL='https://media.example.test/public/', AWS_S3_USE_OBJECT_ACL=True)
class PersonaPictureUploadTests(TestCase):
    """A picture is stored under the persona's own id once the request is valid; its URL comes from the storage settings."""

    def setUp(self):
        self.owner = User.objects.create_user(username='OWNER', email='owner@example.com', password='StrongPass!1',
                                              role=User.ASSOCIATION)
        self.association = SportAssociation.objects.create(user=self.owner, denomination='Primary Association',
                                                           tax_code='12345678901')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.storage = MagicMock()
        self.storage.save.side_effect = lambda name, content: name
        self.storage.location = 'tenant-files'
        self.storage.bucket_name = 'media-bucket'
        self.storage.custom_domain = None
        self.storage.endpoint_url = 'https://objects.example.test/'
        patcher = patch(STORAGE, self.storage)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _associate(self, **values):
        return Associate.objects.create(user=self.owner, sport_association=self.association, first_name='Anna', **values)

    def _saved_name(self, associate_id):
        (name, content), _ = self.storage.save.call_args
        self.assertRegex(name, r'^associates/%s/picture_\d+\.\d+\.jpg$' % re.escape(str(associate_id)))
        self.assertEqual(content.read(), b'\x89PNG picture')
        return name

    def test_add_stores_the_picture_under_the_new_persona(self):
        response = self.client.post('/personas/add', {'first_name': 'Fiamma', 'associate_id': '../../elsewhere',
                                                      'picture_path': PICTURE}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        associate = Associate.objects.get(first_name='Fiamma')
        self.assertEqual(response.data['associate_id'], str(associate.pk))
        name = self._saved_name(associate.pk)
        self.storage.save.assert_called_once()
        self.storage.connection.meta.client.put_object_acl.assert_called_once_with(
            Bucket='media-bucket', Key='tenant-files/' + name, ACL='public-read')
        self.assertEqual(associate.picture_path, 'https://media.example.test/public/tenant-files/' + name)
        self.assertEqual(response.data['picture_path'], associate.picture_path)

    def test_add_with_invalid_fields_stores_nothing(self):
        response = self.client.post('/personas/add', {'first_name': 'Falco', 'email': 'nope', 'picture_path': PICTURE},
                                    format='json')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(set(response.data), {'email'})
        self.storage.save.assert_not_called()
        self.assertFalse(Associate.objects.filter(first_name='Falco').exists())

    @override_settings(AWS_S3_PUBLIC_BASE_URL='', AWS_S3_USE_OBJECT_ACL=False)
    def test_update_uses_the_persona_id_and_the_storage_endpoint(self):
        associate, other = self._associate(), self._associate()

        response = self.client.patch(f'/personas/{associate.pk}/update',
                                     {'associate_id': str(other.pk), 'picture_path': PICTURE, 'notes': 'foto'}, format='json')

        self.assertEqual(response.status_code, 200, response.content)
        name = self._saved_name(associate.pk)
        self.storage.connection.meta.client.put_object_acl.assert_not_called()
        associate.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(associate.picture_path, 'https://objects.example.test/media-bucket/tenant-files/' + name)
        self.assertEqual((associate.notes, other.picture_path), ('foto', None))

    @override_settings(AWS_S3_PUBLIC_BASE_URL='')
    def test_update_uses_the_storage_custom_domain(self):
        associate = self._associate()
        self.storage.custom_domain = 'cdn.example.test'
        self.storage.url_protocol = 'https:'

        response = self.client.patch(f'/personas/{associate.pk}/update', {'picture_path': PICTURE}, format='json')

        self.assertEqual(response.status_code, 200, response.content)
        associate.refresh_from_db()
        self.assertEqual(associate.picture_path, 'https://cdn.example.test/tenant-files/' + self._saved_name(associate.pk))

    def test_update_with_invalid_fields_stores_nothing(self):
        associate = self._associate(picture_path='https://media.example.test/old.jpg')

        response = self.client.patch(f'/personas/{associate.pk}/update', {'sex': 'X', 'picture_path': PICTURE}, format='json')

        self.assertEqual(response.status_code, 400, response.content)
        self.storage.save.assert_not_called()
        associate.refresh_from_db()
        self.assertEqual(associate.picture_path, 'https://media.example.test/old.jpg')

    def test_invalid_picture_text_is_refused_before_storage(self):
        response = self.client.post('/personas/add', {'first_name': 'Fausto', 'picture_path': 'data:image/png;base64,QUJDQ'},
                                    format='json')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('Invalid base64 string', str(response.data['picture_path']))
        self.storage.save.assert_not_called()

    def test_storage_refusal_saves_no_row(self):
        self.storage.connection.meta.client.put_object_acl.side_effect = RuntimeError('refused')

        response = self.client.post('/personas/add', {'first_name': 'Fermo', 'picture_path': PICTURE}, format='json')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data, {'picture_path': 'Error processing image: refused'})
        self.assertFalse(Associate.objects.filter(first_name='Fermo').exists())
