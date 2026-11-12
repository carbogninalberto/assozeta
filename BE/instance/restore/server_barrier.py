"""Drain cooperating Go transactions before an application restore snapshot.

The caller must retain the exclusive filesystem operation lease throughout this
barrier and all subsequent restore work. This is not a fence for every Django
writer. PostgreSQL transaction locks also work through transaction-mode pooling.
"""
from django.db import DatabaseError, connection, transaction

from .locking import Busy


# ASZG / RSTR. Shared with the Go transaction boundary, not a schema identifier.
RESTORE_LOCK_KEY = (1095981639, 1381192786)
LOCK_WAIT_MS = 5000


def drain_go_transactions():
    # SQLite remains supported by the Django application. Go only supports
    # PostgreSQL, so there are no cooperating Go transactions on this backend.
    if connection.vendor == 'sqlite':
        return
    if connection.vendor != 'postgresql':
        raise Busy('Il database non supporta il coordinamento del ripristino.')
    # Keep this a short, acknowledged transaction before any export reads.
    # A caller-owned enclosing transaction would retain the exclusive lock.
    try:
        if connection.in_atomic_block or not connection.get_autocommit():
            raise Busy('Il coordinamento del ripristino richiede una transazione separata.')
        with transaction.atomic(durable=True):
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, true)", [f'{LOCK_WAIT_MS}ms'])
                cursor.execute('SELECT pg_advisory_xact_lock(%s, %s)', RESTORE_LOCK_KEY)
    except DatabaseError as exc:
        # No operation has been read or attempted yet. The existing Celery Busy
        # path defers without spending the restore's interruption budget.
        raise Busy('Il database sta terminando operazioni precedenti. Riprova tra poco.') from exc
