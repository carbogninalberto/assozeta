"""Regression coverage for acknowledged jobs whose maintenance publish fails."""
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch

from celery.exceptions import Retry
from django.test import SimpleTestCase

from instance.restore.task_base import defer_for_maintenance


class MaintenancePublicationTests(SimpleTestCase):
    def test_publication_backoff_is_bounded_and_does_not_end_the_execution(self):
        task = Mock()
        task.name = 'fixture.backoff'
        task.request.called_directly = False
        task.request.is_eager = False
        task.request.retries = 4
        task.request.id = 'fixture-command'
        signature = task.signature_from_request.return_value
        signature.apply_async.side_effect = [ConnectionError('broker offline')] * 7 + [None]
        # Patch the module task_base sees, not time.sleep itself: a global patch also captures every other
        # thread of the test process (drf_api_logger's daemon calls time.sleep(DRF_LOGGER_INTERVAL=1) in a loop).
        clock = Mock(wraps=time)
        sleep = clock.sleep = Mock()
        with patch('instance.restore.task_base.time', clock):
            with self.assertRaises(Retry) as caught:
                defer_for_maintenance(task, RuntimeError('maintenance'), countdown=15)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4, 8, 16, 30, 30])
        self.assertEqual(signature.apply_async.call_count, 8)
        signature.apply_async.assert_called_with(retry=False)
        task.signature_from_request.assert_called_once_with(task.request, countdown=15, retries=4)
        self.assertIs(caught.exception.sig, signature)

    def test_real_worker_retains_early_acknowledged_job_until_publication_recovers(self):
        # A subprocess isolates the actual Celery worker/app and its memory broker
        # from Django's application app, broker and database used by other tests.
        probe = Path(__file__).with_name('maintenance_worker_fixture.py')
        result = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS: acknowledged job survives publication failures', result.stdout)
