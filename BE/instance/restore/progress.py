"""Best-effort live telemetry, separate from the atomic restore receipt."""
import logging
import time
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


def progress_key(operation_id):
    return f'data-restore-progress:{operation_id}'


def snapshot(op):
    if op.state not in op.ACTIVE:
        return None
    try:
        value = cache.get(progress_key(op.id))
        return value if value and value['attempt'] == op.attempts else None
    except Exception:
        return None


class RestoreProgress:
    def __init__(self, op):
        self.op = op
        self.phase = None
        self.started = time.monotonic()
        self.last_sent = 0
        self.sequence = 0

    def publish(self, phase, *, completed=None, total=None, unit=None, detail=None, percent=None, force=False):
        now = time.monotonic()
        changed = phase != self.phase
        if changed:
            self.phase, self.started = phase, now
        if not changed and not force and now - self.last_sent < 1:
            return
        elapsed = max(0, now - self.started)
        eta = None
        if total and completed is not None:
            percent = min(100, max(0, completed * 100 / total))
            if elapsed >= 5 and completed > 0 and completed < total:
                eta = round(elapsed * (total - completed) / completed)
        self.sequence += 1
        payload = {
            'operation_id': str(self.op.id), 'attempt': self.op.attempts,
            'sequence': self.sequence, 'phase': phase, 'updated_at': timezone.now().isoformat(),
            'completed': completed, 'total': total, 'unit': unit, 'detail': detail,
            'percent': round(percent) if percent is not None else None,
            'elapsed_seconds': round(elapsed), 'eta_seconds': eta,
        }
        self.last_sent = now
        # No database writes here: counts are visible while replacement is still
        # uncommitted. Only the durable receipt can authorize a success screen.
        try:
            cache.set(progress_key(self.op.id), payload, timeout=86400)
        except Exception:
            logger.warning('Restore progress cache unavailable', exc_info=True)
        try:
            layer = get_channel_layer()
            if layer:
                async_to_sync(layer.group_send)(f'notifications_user_{self.op.owner_id}',
                    {'type': 'restore_progress', 'payload': payload})
        except Exception:
            logger.warning('Restore progress WebSocket unavailable', exc_info=True)
