"""Test-only Celery task of the Phase 3 handoff stack (CELERY_IMPORTS in p3_django_settings; never in production).

``fixture.early_ack`` keeps Celery's default early acknowledgement: the broker copy is gone before the body
runs, so a queue can be empty while this process still owns the work (task-coverage.md, "Legacy names and
test-only coverage"). Its business effect is one UPDATE statement, counted by the stack's effect trigger.
"""
from celery import shared_task
from django.db import connection


@shared_task(name='fixture.early_ack')
def early_ack(marker=None):
    with connection.cursor() as cursor:
        cursor.execute('UPDATE communications_communicationconfiguration SET daily_email_limit = daily_email_limit')
    return marker
