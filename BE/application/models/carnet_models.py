"""
Copyright: Bakney srl
"""
import uuid

from auditlog.registry import auditlog
from django.db import models

from application.models.courses_models import CourseSubscription
from application.models.payment_models import Payment
from application.models.subscriptions_models import Subscription
from application.models.user_models import SportAssociation, User


class Carnet(models.Model):
    """
    This model contains information about a Carnet
    """

    carnet_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    title = models.CharField(max_length=255, null=True)
    description = models.CharField(max_length=1024, null=True)
    user_id = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, null=True)
    lessons_number = models.PositiveSmallIntegerField(default=0)
    fee = models.DecimalField(max_digits=9, decimal_places=2, default=0.00)
    creation_date = models.DateTimeField(auto_now_add=True)
    public = models.BooleanField(null=False, default=True)
    meta = models.JSONField(null=True)

    class Meta:
        indexes = [
            models.Index(fields=['carnet_id', 'sport_association']),
        ]


class CarnetSubscription(models.Model):
    """
    This model contains information about a CarnetSubscription
    """

    carnet_subscription_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user_id = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, null=True)
    course_subscription = models.ManyToManyField(CourseSubscription)
    carnet_id = models.ForeignKey(Carnet, on_delete=models.CASCADE, null=True)
    creation_date = models.DateTimeField(auto_now_add=True)
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True)
    disabled = models.BooleanField(null=False, default=False)
    meta = models.JSONField(null=True)  # the meta field is used to store lessons that the user has already used


auditlog.register(Carnet)
auditlog.register(CarnetSubscription)
