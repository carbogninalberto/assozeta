"""
Copyright: Bakney S.r.l.
"""
import uuid

from auditlog.registry import auditlog
from django.db import models
from django.utils import timezone

from application.models.user_models import SportAssociation

def current_year():
    return timezone.now().year

class BalanceSheet(models.Model):
    """
    This model contains information about the balance sheet of a Sport Association
    """

    DRAFT = 1
    APPROVED = 2

    STATUS_FLAG = (
        (DRAFT, 'draft'),
        (APPROVED, 'approved')
    )

    balance_sheet_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, null=True)
    status_flag = models.PositiveSmallIntegerField(choices=STATUS_FLAG, default=1)
    creation_date = models.DateTimeField(default=timezone.now)
    year = models.PositiveSmallIntegerField(default=current_year)
    data = models.JSONField(null=True)
    archived = models.BooleanField(null=False, default=False)

    class Meta:
        indexes = [
            models.Index(fields=['balance_sheet_id', 'sport_association']),
        ]


class CustomAccounts(models.Model):

    CASH = 1
    BANK = 2
    OTHER = 3

    ACCOUNT_TYPE = (
        (CASH, 'contanti'),
        (BANK, 'banca'),
        (OTHER, 'altro')
    )

    custom_account_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    enabled = models.BooleanField(null=False, default=True)
    name = models.CharField(max_length=100, null=False)
    initial_balance = models.DecimalField(max_digits=10, decimal_places=2, null=False)
    account_type = models.PositiveSmallIntegerField(choices=ACCOUNT_TYPE, default=1)
    account_code = models.CharField(max_length=100, null=False)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, null=True)
    editable = models.BooleanField(null=False, default=True)

    class Meta:
        indexes = [
            models.Index(fields=['sport_association']),
        ]


class CustomAccountsTransfer(models.Model):
    custom_account_transfer_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    custom_account_from = models.ForeignKey(CustomAccounts, on_delete=models.CASCADE, null=True,
                                            related_name='custom_account_from')
    custom_account_to = models.ForeignKey(CustomAccounts, on_delete=models.CASCADE, null=True,
                                          related_name='custom_account_to')
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=False)
    date = models.DateTimeField(default=timezone.now)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['custom_account_transfer_id', 'sport_association']),
        ]


auditlog.register(BalanceSheet)
auditlog.register(CustomAccounts)
auditlog.register(CustomAccountsTransfer)
