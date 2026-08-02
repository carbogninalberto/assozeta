import uuid

from django.db import models
from django.utils import timezone

from application.models.user_models import SportAssociation, User


class SavedReport(models.Model):
    """
    Stores a reusable report configuration (tool name + parameters + UI metadata).
    Users can save, edit, and replay reports without the AI agent.
    """

    saved_report_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(
        SportAssociation,
        on_delete=models.CASCADE,
        db_index=True,
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='saved_reports',
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    tool_name = models.CharField(max_length=100)
    params = models.JSONField(default=dict)
    ui_config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['sport_association', 'created_by']),
        ]

    def __str__(self):
        return f"{self.name} ({self.tool_name})"
