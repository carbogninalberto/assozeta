import base64
import copy
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.db import transaction
from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from application.exceptions import DuplicateSubscriptionError, InvalidSignatureError, StorageUnavailableError
from application.models.payment_models import Payment
from application.models.subscriptions_models import Signature, Subscription, SubscriptionMembership
from application.models.user_models import Associate, Family, SportAssociation, User
from application.serializers.subscriptions_serializers import SubscriptionInfoSerializer
from application.utils.subscriptions_utils import create_subscription
from core.settings import _is_digitalocean_spaces_endpoint


SIGNATURE_DATA_URI = 'data:image/png;base64,' + base64.b64encode(b'png-bytes').decode('ascii')


class FakeS3Client:
    def __init__(self, raise_acl=False):
        self.raise_acl = raise_acl
        self.acl_calls = []

    def put_object_acl(self, **kwargs):
        self.acl_calls.append(kwargs)
        if self.raise_acl:
            raise RuntimeError('acl not supported')


class FakeStorage:
    location = ''

    def __init__(self, read_content=b'private-signature', raise_save=False, raise_acl=False):
        self.read_content = read_content
        self.raise_save = raise_save
        self.saved = []
        self.deleted = []
        self.client = FakeS3Client(raise_acl=raise_acl)
        self.connection = SimpleNamespace(meta=SimpleNamespace(client=self.client))

    def save(self, name, content):
        if self.raise_save:
            raise RuntimeError('storage down')
        self.saved.append(name)
        content.read()
        return name

    def open(self, name, mode='rb'):
        return BytesIO(self.read_content)

    def delete(self, name):
        self.deleted.append(name)


class SubscriptionStorageTests(SimpleTestCase):
    def test_digitalocean_endpoint_detection(self):
        self.assertTrue(_is_digitalocean_spaces_endpoint('https://fra1.digitaloceanspaces.com'))
        self.assertFalse(_is_digitalocean_spaces_endpoint('http://minio:9000'))

    @override_settings(
        AWS_S3_USE_OBJECT_ACL=False,
        AWS_S3_PUBLIC_BASE_URL='',
        AWS_STORAGE_BUCKET_NAME='bucket',
        AWS_LOCATION='storage',
        STORAGE_DIR='',
    )
    def test_minio_private_path_skips_acl_and_public_url(self):
        storage = FakeStorage()
        subscription = Subscription()

        with patch('application.models.subscriptions_models.default_storage', storage):
            storage_key = subscription.set_signature_from_base64(SIGNATURE_DATA_URI)

        self.assertEqual(subscription.signature_storage_key, storage_key)
        self.assertIsNone(subscription.signature_url)
        self.assertEqual(storage.client.acl_calls, [])
        self.assertIn('/subscriptions/', f'/{storage_key}')
        self.assertNotIn('\\', storage_key)

    @override_settings(
        AWS_S3_USE_OBJECT_ACL=True,
        AWS_S3_PUBLIC_BASE_URL='https://cdn.example.com',
        AWS_STORAGE_BUCKET_NAME='bucket',
        AWS_LOCATION='storage',
        STORAGE_DIR='',
    )
    def test_acl_true_sets_acl_and_public_url(self):
        storage = FakeStorage()
        subscription = Subscription()

        with patch('application.models.subscriptions_models.default_storage', storage):
            storage_key = subscription.set_signature_from_base64(SIGNATURE_DATA_URI)

        self.assertEqual(storage.client.acl_calls, [{
            'Bucket': 'bucket',
            'Key': f'storage/{storage_key}',
            'ACL': 'public-read',
        }])
        self.assertEqual(subscription.signature_url, f'https://cdn.example.com/storage/{storage_key}')

    @override_settings(
        AWS_S3_USE_OBJECT_ACL=True,
        AWS_S3_PUBLIC_BASE_URL='https://cdn.example.com',
        AWS_STORAGE_BUCKET_NAME='bucket',
        AWS_LOCATION='storage',
        STORAGE_DIR='',
    )
    def test_acl_failure_deletes_uploaded_object(self):
        storage = FakeStorage(raise_acl=True)
        subscription = Subscription()

        with patch('application.models.subscriptions_models.default_storage', storage):
            with self.assertRaises(StorageUnavailableError):
                subscription.set_signature_from_base64(SIGNATURE_DATA_URI)

        self.assertEqual(storage.deleted, storage.saved)

    def test_private_signature_reads_from_storage(self):
        storage = FakeStorage(read_content=b'private')
        subscription = Subscription(signature_storage_key='subscriptions/sub/signature.png')

        with patch('application.models.subscriptions_models.default_storage', storage):
            value = subscription.get_signature_base64()

        self.assertEqual(value, 'data:image/png;base64,' + base64.b64encode(b'private').decode('ascii'))

    def test_legacy_url_signature_fallback(self):
        subscription = Subscription(signature_url='https://cdn.example.com/signature.png')
        response = SimpleNamespace(content=b'legacy', raise_for_status=lambda: None)

        with patch('requests.get', return_value=response) as mocked_get:
            value = subscription.get_signature_base64()

        mocked_get.assert_called_once_with('https://cdn.example.com/signature.png', timeout=10)
        self.assertEqual(value, 'data:image/png;base64,' + base64.b64encode(b'legacy').decode('ascii'))

    def test_invalid_signature_is_a_bad_request_error(self):
        subscription = Subscription()

        with self.assertRaises(InvalidSignatureError) as ctx:
            subscription.set_signature_from_base64('data:image/png;base64,not-valid!')

        self.assertEqual(ctx.exception.status_code, 400)

    def test_pdf_templates_use_provider_independent_signature_presence(self):
        for template_name in (
            'document/application/subscription.html',
            'document/application/subscription_classic.html',
        ):
            source = get_template(template_name).template.source
            self.assertIn('{% if subscription.has_signature %}', source)
            self.assertNotIn('{% if subscription.signature_url %}', source)


class SubscriptionCreationMixin:
    def create_association(self):
        user = User.objects.create_user(
            username='assoc',
            email='assoc@example.com',
            password='password',
            role=User.ASSOCIATION,
        )
        sport_association = SportAssociation.objects.create(
            user=user,
            denomination='Associazione Test',
            tax_code='12345678901',
            email='assoc@example.com',
            enable_quotes_management=True,
        )
        return user, sport_association

    def payload(self, tax_code='RSSMRA00A01H501U', with_signature=False):
        return {
            'new_user_account': {'new_member': False},
            'associate_data': {
                'first_name': 'Mario',
                'last_name': 'Rossi',
                'born_date': '01/01/2000',
                'born_city': 'Roma',
                'sex': 'M',
                'tax_code': tax_code,
                'address_city': 'Roma',
                'address_cap': '00100',
                'email': 'mario.rossi@example.com',
                'type': Subscription.ASSOCIATE_AND_MEMBER,
            },
            'associate_tutor_data': None,
            'signature': {
                'there_is_signature': with_signature,
                'data': SIGNATURE_DATA_URI if with_signature else None,
            },
        }


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'api'])
class SubscriptionCreationRollbackTests(SubscriptionCreationMixin, TestCase):
    def test_duplicate_api_returns_http_409(self):
        user, _ = self.create_association()
        create_subscription(copy.deepcopy(self.payload()), user, None)

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post('/subscription/add', self.payload(), format='json')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {
            'msg': 'Questa iscrizione esiste già.',
            'code': '409',
            'ref': 'subscription_duplicate',
        })

    def test_duplicate_exception_uses_409_status(self):
        user, _ = self.create_association()
        create_subscription(copy.deepcopy(self.payload()), user, None)

        with self.assertRaises(DuplicateSubscriptionError) as ctx:
            create_subscription(copy.deepcopy(self.payload()), user, None)

        self.assertEqual(ctx.exception.status_code, 409)

    def test_storage_failure_rolls_back_and_is_not_duplicate(self):
        user, _ = self.create_association()
        storage = FakeStorage(raise_save=True)

        with patch('application.models.subscriptions_models.default_storage', storage):
            with self.assertRaises(StorageUnavailableError) as ctx:
                create_subscription(copy.deepcopy(self.payload(with_signature=True)), user, None)

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(Subscription.objects.count(), 0)
        self.assertEqual(Associate.objects.count(), 0)
        self.assertEqual(Signature.objects.count(), 0)

    def test_membership_failure_rolls_back_subscription_graph(self):
        user, _ = self.create_association()
        storage = FakeStorage()

        with patch(
            'application.models.subscriptions_models.default_storage', storage
        ), patch(
            'application.utils.subscriptions_utils.default_storage', storage
        ), patch(
            'application.utils.subscriptions_utils.SubscriptionMembership.objects.create',
            side_effect=RuntimeError('membership failure')
        ):
            with self.assertRaises(RuntimeError):
                create_subscription(copy.deepcopy(self.payload(with_signature=True)), user, None)

        self.assertEqual(Subscription.objects.count(), 0)
        self.assertEqual(SubscriptionMembership.objects.count(), 0)
        self.assertEqual(Associate.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(storage.deleted, storage.saved)

    def test_manual_sign_save_failure_deletes_uploaded_object(self):
        user, _ = self.create_association()
        _, subscription = create_subscription(copy.deepcopy(self.payload()), user, None)
        storage = FakeStorage()
        client = APIClient()
        client.force_authenticate(user=user)

        with patch(
            'application.models.subscriptions_models.default_storage', storage
        ), patch(
            'application.utils.subscriptions_utils.default_storage', storage
        ), patch.object(
            Subscription,
            'save',
            side_effect=RuntimeError('database save failed')
        ):
            with self.assertRaises(RuntimeError):
                client.post('/subscription/sign', {
                    'subscription_id': str(subscription.subscription_id),
                    'signature': {
                        'there_is_signature': True,
                        'data': SIGNATURE_DATA_URI,
                    },
                }, format='json')

        self.assertEqual(storage.deleted, storage.saved)
        self.assertEqual(Signature.objects.count(), 0)

    def test_private_signature_presence_is_exposed_without_storage_key(self):
        user, _ = self.create_association()
        _, subscription = create_subscription(copy.deepcopy(self.payload()), user, None)
        subscription.signature_storage_key = 'subscriptions/private/signature.png'

        serialized = SubscriptionInfoSerializer(subscription).data

        self.assertTrue(serialized['signature_present'])
        self.assertNotIn('signature_storage_key', serialized)

    def test_family_request_is_all_or_nothing(self):
        from application.views.subscriptions_views import _handle_family_subscription

        user, _ = self.create_association()
        request = SimpleNamespace(user=user, headers={})
        first_subscription = SimpleNamespace(payment=None, _created_storage_keys=['subscriptions/first/signature.png'])
        data = {
            'type': Family.FAMILY,
            'sport_association': 'assoc',
            'multiple_entry_form_data': [
                {'valid': True, 'associate_data': {}, 'custom_data': {}},
                {'valid': True, 'associate_data': {}, 'custom_data': {}},
            ]
        }

        with patch(
            'application.views.subscriptions_views.create_subscription',
            side_effect=[(True, first_subscription), RuntimeError('second entry failed')]
        ), patch('application.views.subscriptions_views.cleanup_storage_keys') as cleanup:
            with self.assertRaises(RuntimeError):
                _handle_family_subscription(data, request, False)

        cleanup.assert_called_once_with(['subscriptions/first/signature.png'])
        self.assertEqual(Family.objects.count(), 0)


class SubscriptionOnCommitTests(SubscriptionCreationMixin, TransactionTestCase):
    def test_core_side_effects_run_after_commit_and_are_robust(self):
        user, _ = self.create_association()

        with patch(
            'application.utils.subscriptions_utils.print_document_subscription.delay',
            side_effect=RuntimeError('broker down')
        ) as print_delay, patch(
            'application.utils.subscriptions_utils.NotificationService.send_notification',
            side_effect=RuntimeError('redis down')
        ) as send_notification, patch(
            'application.signals.check_workflows_trigger.delay',
            side_effect=RuntimeError('workflow broker down')
        ) as workflow_delay:
            with transaction.atomic():
                create_subscription(copy.deepcopy(self.payload()), user, None)
                self.assertFalse(print_delay.called)
                self.assertFalse(send_notification.called)
                self.assertFalse(workflow_delay.called)

        self.assertTrue(print_delay.called)
        self.assertTrue(send_notification.called)
        self.assertTrue(workflow_delay.called)

    def test_core_side_effects_do_not_run_after_rollback(self):
        user, _ = self.create_association()

        with patch(
            'application.utils.subscriptions_utils.print_document_subscription.delay'
        ) as print_delay, patch(
            'application.utils.subscriptions_utils.NotificationService.send_notification'
        ) as send_notification, patch(
            'application.signals.check_workflows_trigger.delay'
        ) as workflow_delay:
            with transaction.atomic():
                create_subscription(copy.deepcopy(self.payload()), user, None)
                transaction.set_rollback(True)

        self.assertFalse(print_delay.called)
        self.assertFalse(send_notification.called)
        self.assertFalse(workflow_delay.called)
        self.assertEqual(Subscription.objects.count(), 0)
