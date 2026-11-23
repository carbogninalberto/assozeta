"""Capabilities of the shared outbox ownership migration."""
from django.db import connection


def installed():
    if connection.vendor != 'postgresql':
        return False
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.assozeta_outbox_effects') IS NOT NULL")
        return cursor.fetchone()[0]
