from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from auditlog.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from application.models import SportAssociation, User
from application.models.audit_models import AuditLogIndex
from application.tasks import delete_old_audit_logs
from instance.models import InstanceConfiguration


class AuditRetentionTests(TestCase):
    def assert_cutoff(self, now, cutoff):
        owner = User.objects.create_user(username='retention-owner', role=User.ASSOCIATION)
        association = SportAssociation.objects.create(user=owner, denomination='Retention fixture')
        content_type = ContentType.objects.get_for_model(association)
        logs = LogEntry.objects.bulk_create([
            LogEntry(content_type=content_type, object_pk=str(association.pk), object_repr='Retention fixture',
                     action=LogEntry.Action.UPDATE, timestamp=cutoff + timedelta(microseconds=offset))
            for offset in (-1, 0, 1)
        ])
        ids = [entry.pk for entry in logs]
        AuditLogIndex.objects.bulk_create([AuditLogIndex(log_entry=entry, sport_association=association) for entry in logs])
        with patch('application.tasks.timezone.now', return_value=now):
            delete_old_audit_logs.run()
        self.assertCountEqual(LogEntry.objects.filter(pk__in=ids).values_list('pk', flat=True), ids[1:])
        self.assertCountEqual(AuditLogIndex.objects.filter(log_entry_id__in=ids).values_list('log_entry_id', flat=True), ids[1:])

    def test_default_without_configuration(self):
        self.assertFalse(InstanceConfiguration.objects.exists())
        self.assert_cutoff(datetime(2024, 2, 29, 23, 34, tzinfo=timezone.utc),
                           datetime(2023, 2, 28, 23, 34, tzinfo=timezone.utc))

    def test_configured_months_clamp_in_utc(self):
        InstanceConfiguration.objects.create(domain='retention.example.test', audit_log_retention_months=3)
        # This instant is June 1 locally and May 31 in UTC.
        now = datetime(2026, 6, 1, 1, 34, tzinfo=timezone(timedelta(hours=2)))
        self.assert_cutoff(now, datetime(2026, 2, 28, 23, 34, tzinfo=timezone.utc))
