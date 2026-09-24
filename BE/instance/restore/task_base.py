import logging
import time

from celery import Task
from celery.signals import before_task_publish
from celery.exceptions import Retry


logger = logging.getLogger(__name__)


def defer_for_maintenance(task, exc, countdown):
    """Republish the same command without spending its business retry budget."""
    request = task.request
    if request.called_directly:
        raise Retry(exc=exc, when=countdown)
    signature = task.signature_from_request(
        request, countdown=countdown, retries=request.retries,
    )
    if not request.is_eager:
        delay = 1
        while True:
            try:
                signature.apply_async(retry=False)
                break
            except Exception as publish_error:
                # Ordinary tasks are already acknowledged. Reject/requeue would
                # discard them, so retain this execution until publication works.
                # No operation lease is held here and business retries stay intact.
                logger.warning(
                    'Maintenance publication failed for %s[%s] (%s); retrying in %ss',
                    task.name, request.id, type(publish_error).__name__, delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, 30)
    raise Retry(exc=exc, when=countdown, is_eager=request.is_eager, sig=signature)


class CoordinatedTask(Task):
    """Drain running jobs before replacement and defer new jobs during maintenance."""
    abstract = True

    def __call__(self, *args, **kwargs):
        from django.conf import settings
        from .locking import operation_lock, restore_pending, Busy
        if self.name.startswith('instance.restore.') or not getattr(settings, 'DATA_RESTORE_LOCK_PATH', ''):
            return super().__call__(*args, **kwargs)
        try:
            with operation_lock():
                if restore_pending():
                    raise Busy()
                if not self.request.called_directly:
                    from instance.models import DataRestore
                    completed = DataRestore.objects.filter(state='completed', completed_at__isnull=False).order_by('-completed_at').values_list('completed_at', flat=True).first()
                    published = (self.request.headers or {}).get('assozeta_published_at')
                    if completed and (published is None or published < completed.timestamp()):
                        raise StaleDataTask('Operazione annullata dopo il ripristino dei dati. Ripeti la richiesta.')
                return super().__call__(*args, **kwargs)
        except Busy as exc:
            defer_for_maintenance(self, exc, countdown=15)


class StaleDataTask(RuntimeError):
    """A job was queued against data that a completed restore has replaced."""


def stamp_published_task(sender=None, headers=None, **kwargs):
    import time
    if headers is not None:
        # Celery retries retain the original header, so a retry cannot turn a
        # stale business command into a fresh command after the restore commits.
        headers.setdefault('assozeta_published_at', time.time())


before_task_publish.connect(stamp_published_task, weak=False)
