"""Relay Go realtime outbox events into Django Channels groups.

The Go-owned ``assozeta_go_realtime_events`` table (BE_GO migration 4) is the
cross-runtime protocol; Channels' Redis encoding is never written by Go. This
relay claims pending rows, forwards allowlisted version-1 kinds through the
channel layer and records the outcome. Delivery is at least once: a crash after
sending and before commit resends an idempotent invalidation.
"""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import connection, transaction

from communications.staff_board import staff_board_group

logger = logging.getLogger(__name__)
TABLE = 'public.assozeta_go_realtime_events'
# Each retry waits one relay poll interval; exhaustion keeps the row, marked failed.
MAX_ATTEMPTS = 30
# kind -> (group for target_id, Channels message). Unknown kinds are failed, not dropped.
HANDLERS = {
    'staff_board_changed': lambda target: (staff_board_group(target), {'type': 'staff_board_changed'}),
}


def outbox_present():
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [TABLE])
        return cursor.fetchone()[0]


def relay_once(limit=100, layer=None):
    """Process up to ``limit`` pending events; return counts by outcome."""
    counts = {'delivered': 0, 'retry': 0, 'failed': 0}
    if not outbox_present():
        return counts
    layer = layer or get_channel_layer()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"""SELECT id, envelope_version, kind, target_id, attempts FROM {TABLE}
                WHERE delivered_at IS NULL AND failed_code IS NULL ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED""", [limit])
            rows = cursor.fetchall()
        for identifier, version, kind, target, attempts in rows:
            outcome, code = deliver(layer, version, kind, str(target))
            with connection.cursor() as cursor:
                if outcome == 'delivered':
                    cursor.execute(f"UPDATE {TABLE} SET attempts=attempts+1, delivered_at=clock_timestamp() WHERE id=%s", [identifier])
                elif outcome == 'failed' or attempts + 1 >= MAX_ATTEMPTS:
                    outcome = 'failed'
                    cursor.execute(f"UPDATE {TABLE} SET attempts=attempts+1, failed_code=%s WHERE id=%s",
                                   [code or 'delivery_exhausted', identifier])
                else:
                    cursor.execute(f"UPDATE {TABLE} SET attempts=attempts+1 WHERE id=%s", [identifier])
            counts[outcome] += 1
    return counts


def deliver(layer, version, kind, target):
    handler = HANDLERS.get(kind)
    if version != 1 or handler is None:
        return 'failed', 'unsupported_event'
    if layer is None:
        return 'retry', None
    group, message = handler(target)
    try:
        async_to_sync(layer.group_send)(group, message)
    except Exception:
        logger.exception('Unable to relay Go realtime event')
        return 'retry', None
    return 'delivered', None
