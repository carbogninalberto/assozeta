"""Disposable solo-worker reproduction; memory broker, no application database."""
import sys
import tempfile
import threading
import types
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from celery import Celery, signals
from celery.canvas import Signature
from celery.contrib.testing.worker import start_worker
from celery.worker.request import Request
from django.conf import settings

from instance.restore.locking import operation_lock
from instance.restore.task_base import CoordinatedTask


def verify(directory):
    settings.configure(DATA_RESTORE_LOCK_PATH=directory + '/lifecycle.lock', DATABASES={})
    app = Celery('publication-regression', broker='memory://', backend='cache+memory://',
                 task_cls=CoordinatedTask, fixups=[])
    app.conf.update(worker_hijack_root_logger=False, broker_connection_retry_on_startup=False)
    app.set_current()
    app.set_default()
    completed, outage_observed, publication_allowed = (threading.Event() for _ in range(3))
    acknowledged, rejected, retried, executions, delays = [], [], [], [], []
    publish_attempts = []
    task_id = 'maintenance-publication-fixture'

    @app.task(bind=True, name='fixture.early_ack')
    def job(self):
        executions.append((self.request.id, self.request.retries,
                           self.request.headers['assozeta_published_at']))
        completed.set()
        return 'executed'

    original_publish, original_ack, original_reject = Signature.apply_async, Request.acknowledge, Request.reject

    def publish(signature, *args, **kwargs):
        publish_attempts.append(signature.options.copy())
        if len(publish_attempts) <= 4:
            assert task_id in acknowledged, 'The reproduction must be after the early acknowledgement'
            raise ConnectionError('simulated maintenance publication outage')
        return original_publish(signature, *args, **kwargs)

    def acknowledge(request):
        original_ack(request)
        if request.acknowledged:
            acknowledged.append(request.id)

    def reject(request, requeue=False):
        rejected.append((request.id, request.acknowledged, requeue))
        return original_reject(request, requeue=requeue)

    def pause(delay):
        delays.append(delay)
        if len(delays) == 4:
            outage_observed.set()
            if not publication_allowed.wait(10):
                raise AssertionError('Test did not release the publication outage')

    def on_retry(sender=None, **kwargs):
        retried.append(sender.name)

    models = types.ModuleType('instance.models')
    models.DataRestore = Mock()
    models.DataRestore.objects.filter.return_value.order_by.return_value.values_list.return_value.first.return_value = None
    signals.task_retry.connect(on_retry, weak=False)
    try:
        with patch.dict(sys.modules, {'instance.models': models}), \
                patch('instance.restore.locking.restore_pending', return_value=False), \
                patch.object(Signature, 'apply_async', publish), \
                patch.object(Request, 'acknowledge', acknowledge), \
                patch.object(Request, 'reject', reject), \
                patch('instance.restore.task_base.time', sleep=Mock(side_effect=pause)):
            with start_worker(app, perform_ping_check=False, pool='solo', loglevel='ERROR', shutdown_timeout=10):
                try:
                    with operation_lock(exclusive=True):
                        assert job.acks_late is False
                        result = job.apply_async(task_id=task_id, retries=4, headers={'assozeta_published_at': 123})
                        assert outage_observed.wait(10), 'Worker did not reach publication failures'
                        assert task_id in acknowledged
                        assert not rejected, rejected
                        assert not retried, retried
                        assert not completed.is_set(), 'Business work ran during maintenance'
                    publication_allowed.set()
                    assert completed.wait(25), 'Acknowledged job was lost after publication recovered'
                    assert result.get(timeout=5, disable_sync_subtasks=False) == 'executed'
                    assert result.state == 'SUCCESS'
                    assert executions == [(task_id, 4, 123)], executions
                    assert not rejected, rejected
                    assert retried == ['fixture.early_ack'], retried
                    assert delays == [1, 2, 4, 8], delays
                    assert len(publish_attempts) == 5, publish_attempts
                    assert all(options['retries'] == 4 and options['task_id'] == task_id
                               and options['headers']['assozeta_published_at'] == 123 for options in publish_attempts)
                finally:
                    publication_allowed.set()
    finally:
        signals.task_retry.disconnect(on_retry)
        app.close()
    print('PASS: acknowledged job survives publication failures; SUCCESS, one execution, retry count/header/ID preserved')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        verify(directory)
