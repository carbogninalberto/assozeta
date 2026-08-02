import uuid

from auditlog.registry import auditlog
from django.db import models
from django.utils import timezone


class Document(models.Model):
    """
    This model contains information about a medical certificate
    """

    document_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    creation_date = models.DateTimeField(default=timezone.now)
    filepath = models.CharField(max_length=1000, default=None, null=True, blank=True)
    filename = models.CharField(max_length=255)
    # this token is used for external sharing
    token = models.UUIDField(default=uuid.uuid4)

    class Meta:
        indexes = [
            models.Index(fields=['document_id']),
        ]


auditlog.register(Document)
