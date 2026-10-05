"""The fixture date must not invalidate real SDK network signatures."""
from datetime import datetime, timezone
import time

import botocore.auth
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from django.test import SimpleTestCase, TransactionTestCase
from django.core.signals import request_finished
from django.db import connections
from django.utils import timezone as django_timezone
from freezegun.api import real_datetime

from application.management.commands.run_manuale_instance import manual_capture_clock, manual_capture_connections


class ManualCaptureClockTests(SimpleTestCase):
    def test_actual_sigv4_signing_uses_wall_clock_while_application_date_stays_fixed(self):
        original_namespace = botocore.auth.datetime
        before = real_datetime.now(timezone.utc).strftime('%Y%m%d')
        with manual_capture_clock('2000-01-01 12:00:00+00:00'):
            self.assertEqual(django_timezone.now(), datetime(2000, 1, 1, 12, tzinfo=timezone.utc))
            request = AWSRequest(method='GET', url='https://storage.example.test/bucket/receipt.pdf')
            SigV4Auth(Credentials('fixture-key', 'fixture-secret'), 's3', 'us-east-1').add_auth(request)
            after = real_datetime.now(timezone.utc).strftime('%Y%m%d')
            self.assertIn(request.headers['X-Amz-Date'][:8], (before, after))
            self.assertNotEqual(request.headers['X-Amz-Date'][:8], '20000101')
            self.assertIn('/s3/aws4_request', request.headers['Authorization'])
            self.assertEqual(django_timezone.now().year, 2000)
        self.assertIs(botocore.auth.datetime, original_namespace)

    def test_transport_clock_is_restored_when_application_raises(self):
        original_namespace = botocore.auth.datetime
        with self.assertRaisesRegex(RuntimeError, 'fixture stopped'):
            with manual_capture_clock('2000-01-01 12:00:00+00:00'):
                raise RuntimeError('fixture stopped')
        self.assertIs(botocore.auth.datetime, original_namespace)


class ManualCaptureConnectionTests(TransactionTestCase):
    def test_real_postgres_connections_close_at_each_frozen_clock_request_end(self):
        connection = connections['default']
        if connection.vendor != 'postgresql':
            self.skipTest('Requires actual PostgreSQL connection lifecycle.')
        original_max_age = connection.settings_dict['CONN_MAX_AGE']
        connection.ensure_connection()
        backend_ids = []
        with manual_capture_connections(), manual_capture_clock('2000-01-01 12:00:00+00:00'):
            self.assertIsNone(connection.connection)  # startup connection closed
            self.assertEqual(connection.settings_dict['CONN_MAX_AGE'], 0)
            for _ in range(4):
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    backend_ids.append(cursor.fetchone()[0])
                self.assertEqual(connection.close_at, time.monotonic())
                self.assertIsNotNone(connection.connection)
                # This is Django's real ASGI response-completion signal. The
                # test client suppresses its connection cleanup, so send it.
                request_finished.send(sender=self.__class__)
                self.assertIsNone(connection.connection)
                self.assertEqual(django_timezone.now().year, 2000)
        self.assertEqual(len(set(backend_ids)), 4)
        self.assertEqual(connection.settings_dict['CONN_MAX_AGE'], original_max_age)

    def test_scoped_connection_setting_restores_after_runtime_exception(self):
        previous = {alias: config['CONN_MAX_AGE'] for alias, config in connections.settings.items()}
        with self.assertRaisesRegex(RuntimeError, 'capture stopped'):
            with manual_capture_connections():
                self.assertTrue(all(config['CONN_MAX_AGE'] == 0 for config in connections.settings.values()))
                raise RuntimeError('capture stopped')
        self.assertEqual({alias: config['CONN_MAX_AGE'] for alias, config in connections.settings.items()}, previous)
