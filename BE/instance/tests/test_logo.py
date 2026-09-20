from io import BytesIO
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from instance.models import InstanceConfiguration
from instance.serializers import CANONICAL_LOGO_URL


class InstanceLogoTests(TestCase):
    def setUp(self):
        storage = TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        override = self.settings(
            MEDIA_ROOT=storage.name,
            STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}},
        )
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()
        self.owner = User.objects.create_user(
            username='logo-owner', email='logo-owner@example.test', role=User.ASSOCIATION,
        )
        self.association = SportAssociation.objects.create(
            user=self.owner, denomination='Logo ASD', tax_code='12345678901',
        )
        self.config = InstanceConfiguration.objects.create(
            domain='logo.example.test', name='Logo ASD', primary_association=self.association,
        )

    def image_file(self, color='red', image_format='PNG'):
        output = BytesIO()
        Image.new('RGB', (200, 40), color).save(output, format=image_format)
        return SimpleUploadedFile(
            f'logo.{image_format.lower()}', output.getvalue(),
            content_type=f'image/{image_format.lower()}',
        )

    def test_owner_can_replace_logo_and_public_config_uses_new_image(self):
        self.client.force_authenticate(user=self.owner)
        settings_response = self.client.get('/instance/access')
        self.assertEqual(settings_response.status_code, 200)
        self.assertTrue(settings_response.data['is_owner'])

        first = self.client.post('/instance/logo', {'file': self.image_file()}, format='multipart')
        self.assertEqual(first.status_code, 200)
        second_file = self.image_file('blue', 'JPEG')
        expected = second_file.read()
        second_file.seek(0)
        second = self.client.post('/instance/logo', {'file': second_file}, format='multipart')
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(first.data['logo_url'], second.data['logo_url'])
        self.config.refresh_from_db()
        self.assertEqual(self.config.logo_path, CANONICAL_LOGO_URL)

        self.client.force_authenticate(user=None)
        config = self.client.get('/instance/config')
        self.assertEqual(config.data['oem']['logo'], second.data['logo_url'])
        response = self.client.get(second.data['logo_url'].removeprefix('/api'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertEqual(b''.join(response.streaming_content), expected)

    def test_other_association_and_collaborator_cannot_change_instance_logo(self):
        other_owner = User.objects.create_user(
            username='other-owner', email='other@example.test', role=User.ASSOCIATION,
        )
        SportAssociation.objects.create(user=other_owner, denomination='Other ASD', tax_code='98765432101')
        collaborator = User.objects.create_user(
            username='collaborator', email='collaborator@example.test',
            role=User.COLLABORATOR, connected_user=self.owner,
        )
        for user in (other_owner, collaborator):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user=user)
                response = self.client.get('/instance/access')
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.data['is_owner'])
                # Reset forced authentication after the profile collaborator context swap.
                self.client.force_authenticate(user=user)
                response = self.client.post('/instance/logo', {'file': self.image_file()}, format='multipart')
                self.assertEqual(response.status_code, 403)

    def test_invalid_upload_preserves_existing_logo(self):
        self.client.force_authenticate(user=self.owner)
        first = self.client.post('/instance/logo', {'file': self.image_file()}, format='multipart')
        response = self.client.post('/instance/logo', {
            'file': SimpleUploadedFile('invalid.txt', b'not an image', content_type='text/plain'),
        }, format='multipart')
        self.assertEqual(response.status_code, 400)
        config = self.client.get('/instance/config')
        self.assertEqual(config.data['oem']['logo'], first.data['logo_url'])
