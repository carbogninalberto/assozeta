from celery import shared_task


@shared_task(bind=True, name='instance.restore.execute', acks_late=True, reject_on_worker_lost=True,
             soft_time_limit=3300, time_limit=3600, max_retries=None)
def restore_instance_data(self, operation_id):
    from .restore.execution import execute
    from .restore.locking import Busy
    try:
        execute(operation_id)
    except Busy as exc:
        from .restore.task_base import defer_for_maintenance
        defer_for_maintenance(self, exc, countdown=10)


@shared_task(name='instance.restore.cleanup')
def cleanup_data_restores():
    from datetime import timedelta
    from django.utils import timezone
    from .models import DataRestore
    from .restore.execution import cleanup, save_operation
    from .restore.locking import operation_lock, Busy
    try:
        with operation_lock(exclusive=True, required=True):
            for op in DataRestore.objects.exclude(state__in=DataRestore.ACTIVE).iterator():
                if op.state == 'review' and op.created_at < timezone.now() - timedelta(hours=24):
                    save_operation(op, state='cancelled', stage='expired')
                if op.state in ('failed', 'cancelled', 'completed'):
                    cleanup(op)
    except Busy:
        return
