import uuid

from auditlog.registry import auditlog
from django.db import models

from application.models.user_models import User


class NotificationsBroadcast(models.Model):

    broadcast_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    broadcast_name = models.CharField(max_length=255, unique=True, null=True)


class UserNotificationsBroadcast(models.Model):

    user_broadcast_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    broadcast = models.ForeignKey(NotificationsBroadcast, on_delete=models.CASCADE)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'broadcast'], name='user_broadcast_idx'),
            models.Index(fields=['user'], name='user_idx'),
        ]


auditlog.register(NotificationsBroadcast)
auditlog.register(UserNotificationsBroadcast)
