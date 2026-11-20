import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import Mock, patch

import requests
from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from application.models import User
from communications.models import WhatsAppDelivery, WhatsAppReceipt
from communications.tasks import deliver_whatsapp
from communications.whatsapp import deliver_one, queue_notifications
from communications.whatsapp_provider import CloudAPI, ProviderError
from notifications.services import NotificationService

CONFIG = dict(
    WHATSAPP_PHONE_NUMBER_ID='phone', WHATSAPP_BUSINESS_ACCOUNT_ID='account',
    WHATSAPP_API_VERSION='v23.0', WHATSAPP_ACCESS_TOKEN='fixture-token',
    WHATSAPP_WEBHOOK_VERIFY_TOKEN='fixture-verify', WHATSAPP_APP_SECRET='fixture-secret',
    WHATSAPP_NOTIFICATION_ROUTES='{"payment":"receipt/en_US/message"}',
)
WEBHOOK = '/communications/whatsapp/webhook'


def api_response(status, data):
    return Mock(status_code=status, json=lambda: data)


@override_settings(**CONFIG)
class ProviderTests(SimpleTestCase):
    @patch('core.celery.app.add_periodic_task')
    def test_configured_app_schedules_the_delivery_worker(self, add_periodic_task):
        communications = apps.get_app_config('communications')
        communications.ready()
        seconds, signature = add_periodic_task.call_args.args
        self.assertEqual((seconds, signature.task), (10.0, 'communications.tasks.deliver_whatsapp'))
        add_periodic_task.reset_mock()
        with self.settings(WHATSAPP_ACCESS_TOKEN=''):
            communications.ready()
        add_periodic_task.assert_not_called()

    @patch('communications.whatsapp_provider.requests.request')
    def test_text_and_provider_errors(self, request):
        provider = CloudAPI()
        request.return_value = api_response(200, {'messages': [{'id': 'wamid.example'}]})
        self.assertEqual(provider.send_text('+393331234567', 'hello', 'ref'), 'wamid.example')
        self.assertEqual(request.call_args.kwargs['json'], {
            'type': 'text', 'text': {'body': 'hello'}, 'messaging_product': 'whatsapp',
            'to': '393331234567', 'biz_opaque_callback_data': 'ref',
        })
        for status, transient in ((400, False), (429, True), (503, True)):
            request.return_value = api_response(status, {'error': {'code': 42, 'message': 'bad fixture-token'}})
            with self.assertRaises(ProviderError) as caught:
                provider.send_text('+1234567', 'hello', 'ref')
            self.assertEqual((caught.exception.code, caught.exception.transient), ('42', transient))
            self.assertNotIn(settings.WHATSAPP_ACCESS_TOKEN, str(caught.exception))
        request.side_effect = requests.ConnectionError()
        with self.assertRaises(ProviderError) as caught:
            provider.send_text('+1234567', 'hello', 'ref')
        self.assertTrue(caught.exception.transient)

    @patch('communications.whatsapp_provider.requests.request')
    def test_registration_and_approved_translations_across_pages(self, request):
        provider = CloudAPI()
        definition = {'name': 'receipt', 'language': 'en_US', 'components': [], 'category': 'UTILITY'}
        approved = dict(definition, id='t', status='APPROVED')
        request.side_effect = [
            api_response(200, {'status': 'PENDING', 'id': 't'}),
            api_response(200, {'data': [dict(approved, status='PENDING')], 'paging': {
                'next': 'https://other.invalid/', 'cursors': {'after': 'cursor'}}}),
            api_response(200, {'data': [approved]}),
        ]
        self.assertEqual(provider.register_template(definition)['status'], 'PENDING')
        self.assertEqual(request.call_args.kwargs['json'], definition)
        self.assertEqual(provider.get_template('receipt', 'en_US'), approved)
        self.assertEqual(request.call_args.kwargs['params']['after'], 'cursor')
        self.assertTrue(request.call_args.args[1].endswith('/account/message_templates'))

    def test_webhook_verification_and_authentication(self):
        query = {'hub.mode': 'subscribe', 'hub.verify_token': 'fixture-verify', 'hub.challenge': '123'}
        self.assertEqual(self.client.get(WEBHOOK, query).content, b'123')
        query['hub.verify_token'] = ''
        self.assertEqual(self.client.get(WEBHOOK, query).status_code, 403)
        self.assertEqual(self.client.post(WEBHOOK, b'{}', content_type='application/json').status_code, 403)


@override_settings(**CONFIG)
class DeliveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # App initialization can cache IDs from the database used before pytest switches databases.
        ContentType.objects.clear_cache()
        cls.user = User.objects.create_user(username='whatsapp-qa', role=User.ATHLETE, phone='0039 3331234567')
        cls.messages = [{'type': 'payment', 'msg': 'Payment received'}]

    def post_status(self, row, state):
        detail = {'id': 'wamid.example', 'status': state, 'timestamp': '1700000000',
                  'biz_opaque_callback_data': str(row.pk)}
        if state == 'failed':
            detail['errors'] = [{'code': 131026, 'title': 'Message undeliverable'}]
        payload = {'object': 'whatsapp_business_account', 'entry': [{'id': 'account', 'changes': [{
            'field': 'messages', 'value': {'metadata': {'phone_number_id': 'phone'}, 'statuses': [detail]},
        }]}]}
        body = json.dumps(payload).encode()
        signature = hmac.new(settings.WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
        response = self.client.post(WEBHOOK, body, content_type='application/json',
                                    HTTP_X_HUB_SIGNATURE_256='sha256=' + signature)
        self.assertEqual(response.status_code, 200)

    def test_notification_to_cloud_api_to_delivery_receipts(self):
        requests_seen = []

        class GraphHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests_seen.append((self.path, payload))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"messages":[{"id":"wamid.example"}]}')

            def log_message(self, *_):
                pass

        with ThreadingHTTPServer(('127.0.0.1', 0), GraphHandler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with self.settings(WHATSAPP_API_BASE_URL=f'http://127.0.0.1:{server.server_port}'):
                    self.assertTrue(NotificationService.send_notification(self.user, self.messages))
                    row = WhatsAppDelivery.objects.get()
                    self.assertEqual((row.status, row.attempts), ('queued', 0))
                    self.assertEqual(requests_seen, [])
                    deliver_whatsapp.run()
            finally:
                server.shutdown()
                thread.join()
        row.refresh_from_db()
        self.assertEqual((row.status, row.attempts, row.provider_message_id), ('sent', 1, 'wamid.example'))
        [(path, payload)] = requests_seen
        self.assertEqual(path, '/v23.0/phone/messages')
        self.assertEqual(payload['to'], '393331234567')
        self.assertEqual(payload['biz_opaque_callback_data'], str(row.pk))
        self.assertEqual(payload['template'], {
            'name': 'receipt', 'language': {'code': 'en_US'},
            'components': [{'type': 'body', 'parameters': [{'type': 'text', 'text': 'Payment received'}]}],
        })
        for state in ('read', 'delivered', 'read', 'sent'):
            self.post_status(row, state)
        row.refresh_from_db()
        self.assertEqual(row.status, 'read')
        self.assertEqual(WhatsAppReceipt.objects.filter(delivery=row).count(), 3)

    @override_settings(WHATSAPP_NOTIFICATION_ROUTES='{"payment":"text"}')
    def test_retry_and_exhaustion(self):
        queue_notifications(self.user, self.messages)
        provider = Mock(spec=CloudAPI)
        provider.send_text.side_effect = ProviderError('42', 'busy', True)
        for attempt in range(1, 4):
            self.assertTrue(deliver_one(provider))
            row = WhatsAppDelivery.objects.get()
            self.assertEqual((row.attempts, row.error_message), (attempt, 'busy'))
            if attempt < 3:
                self.assertEqual(row.status, 'queued')
                self.assertGreater((row.next_attempt_at - timezone.now()).total_seconds(), 30 * attempt - 2)
                self.assertFalse(deliver_one(provider))
                WhatsAppDelivery.objects.update(next_attempt_at=timezone.now())
        self.assertEqual(row.status, 'failed')
        self.assertFalse(deliver_one(provider))

    def test_invalid_recipient_and_permanent_provider_error(self):
        self.user.phone = ''
        queue_notifications(self.user, self.messages)
        row = WhatsAppDelivery.objects.get()
        self.assertEqual((row.status, row.error_code, row.attempts), ('failed', 'invalid_recipient', 0))
        self.user.phone = '+393331234567'
        queue_notifications(self.user, self.messages)
        provider = Mock(spec=CloudAPI)
        provider.send_template.side_effect = ProviderError('100', 'Template rejected')
        self.assertTrue(deliver_one(provider))
        row = WhatsAppDelivery.objects.get(attempts=1)
        self.assertEqual((row.status, row.error_code), ('failed', '100'))

    def test_enqueue_failure_isolated_from_trigger(self):
        with patch.object(WhatsAppDelivery, 'save', side_effect=RuntimeError):
            self.assertTrue(NotificationService.send_notification(self.user, self.messages))
        self.assertFalse(WhatsAppDelivery.objects.exists())

    def test_callback_recovers_uncertain_send_and_retains_provider_failure(self):
        queue_notifications(self.user, self.messages)
        row = WhatsAppDelivery.objects.get()
        # A network timeout may leave the row queued even though Meta accepted it.
        self.post_status(row, 'failed')
        row.refresh_from_db()
        self.assertEqual((row.status, row.provider_message_id), ('failed', 'wamid.example'))
        self.assertEqual((row.error_code, row.error_message), ('131026', 'Message undeliverable'))
        self.assertEqual(WhatsAppReceipt.objects.get(delivery=row).error_code, '131026')
        self.assertFalse(deliver_one(Mock(spec=CloudAPI)))
        self.post_status(row, 'sent')
        row.refresh_from_db()
        self.assertEqual(row.status, 'failed')


@skipUnlessDBFeature('has_select_for_update_skip_locked')
class ConcurrentDeliveryTests(TransactionTestCase):
    def test_other_worker_skips_in_flight_send(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        WhatsAppDelivery.objects.create(event_type='payment', recipient='+393331234567', body='hello')
        sending, finish = Event(), Event()

        def send(*_):
            sending.set()
            if not finish.wait(10):
                raise AssertionError('Timed out waiting for second worker')
            return 'wamid.example'

        provider = Mock(spec=CloudAPI)
        provider.send_text.side_effect = send

        def worker():
            try:
                return deliver_one(provider)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(worker)
            try:
                self.assertTrue(sending.wait(10))
                self.assertFalse(deliver_one(provider))
            finally:
                finish.set()
            self.assertTrue(pending.result(timeout=10))
        self.assertEqual(provider.send_text.call_count, 1)
        self.assertEqual(WhatsAppDelivery.objects.get().attempts, 1)
