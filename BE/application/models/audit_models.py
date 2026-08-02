"""
Copyright: Bakney S.r.l.
Audit Log Index Model for multi-tenant audit log filtering.
"""
import uuid

from auditlog.models import LogEntry
from django.db import models
from django.utils import timezone

from application.models.user_models import SportAssociation


class AuditLogIndex(models.Model):
    """
    Denormalized index table linking LogEntry records to SportAssociation.
    This enables efficient multi-tenant filtering of audit logs.

    Design based on Slack's event indexing pattern:
    - One-to-one relationship with LogEntry
    - Direct FK to SportAssociation for fast joins
    - Stores resolution_path for debugging
    """

    audit_log_index_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    log_entry = models.OneToOneField(
        LogEntry,
        on_delete=models.CASCADE,
        related_name='sport_association_index'
    )
    sport_association = models.ForeignKey(
        SportAssociation,
        on_delete=models.CASCADE,
        db_index=True
    )
    resolution_path = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Path used to resolve sport_association (e.g., 'direct', 'course.sport_association')"
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['sport_association', 'created_at']),
            models.Index(fields=['sport_association', 'log_entry']),
        ]
        verbose_name = 'Audit Log Index'
        verbose_name_plural = 'Audit Log Indexes'

    def __str__(self):
        return f"AuditLogIndex: {self.log_entry_id} -> {self.sport_association_id}"
