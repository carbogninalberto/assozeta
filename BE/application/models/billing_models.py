"""
Copyright: Bakney S.r.l.s.
"""
import uuid

from auditlog.registry import auditlog
from django.db import models
from django.utils import timezone

from application.models.user_models import User


class BillingPlan(models.Model):
    """
    This model contains the billing plans
    """

    class Meta:
        db_table = 'billing_plan'

    BASE_PLAN = 1
    PRO_PLAN = 2
    TEAMS_PLAN = 3

    BILLING_TYPE = (
        (BASE_PLAN, 'base plan'),
        (PRO_PLAN, 'pro plan'),
        (TEAMS_PLAN, 'teams plan'),
    )

    billing_plan_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=255, null=True)
    description = models.CharField(max_length=255, null=True)
    monthly_fee = models.IntegerField(default=0)
    annually_fee = models.IntegerField(default=0)
    billing_type = models.PositiveSmallIntegerField(choices=BILLING_TYPE, default=1)


class BillingSubscription(models.Model):
    """
    This model contains the billing subscriptions for a sport association user
    """

    class Meta:
        db_table = 'billing_subscription'

    MONTHLY = 1
    ANNUALLY = 2

    STATUS_FLAG = (
        (MONTHLY, 'paid monthly'),
        (ANNUALLY, 'paid annually'),
    )

    billing_subscription_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.PROTECT, null=True)
    auto_renewal = models.BooleanField(default=True)
    renewal_type = models.PositiveSmallIntegerField(choices=STATUS_FLAG, default=1)
    ends_on = models.DateTimeField(default=None, null=True)
    billing_plan = models.ForeignKey(BillingPlan, on_delete=models.PROTECT, null=True)

    def is_active(self):
        """
        This function returns true if the bill subscription plan is enabled
        """
        if self.billing_plan is None or self.ends_on is None:
            return False
        return (self.ends_on - timezone.now()).days > 0


class BillingPayment(models.Model):
    """
    This model contains the billing payments
    """

    class Meta:
        db_table = 'billing_payment'

    billing_payment_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.PROTECT, null=True)
    amount = models.DecimalField(max_digits=9, decimal_places=2, default=0.00)
    payment_date = models.DateTimeField(default=timezone.now)
    subscription_id = models.CharField(max_length=125, null=True)


class NurturingEmailsPlan(models.Model):
    """
    This model contains the nurturing plans
    """

    class Meta:
        db_table = 'nurturing_emails_plan'

    nurturing_plan_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=255, null=True)
    description = models.CharField(max_length=255, null=True)
    email_template = models.TextField(null=True)
    email_subject = models.CharField(max_length=255, null=True)
    email_from = models.CharField(max_length=255, null=True)
    email_reply_to = models.CharField(max_length=255, null=True)
    # ---- Preconditions ----
    # only free plan
    only_free_plan = models.BooleanField(default=False)
    # check date_conditions
    check_date_conditions = models.BooleanField(default=False)
    # required nurturing plan
    required_nurturing_plan = models.ForeignKey('self', on_delete=models.DO_NOTHING, null=True)
    # ---- Preconditions ----

    # ---- Date conditions (are mutually exclusive) ----
    # days after registration
    at_least_these_days_after_registration = models.IntegerField(default=0)
    at_most_these_days_after_registration = models.IntegerField(default=0)
    # days before expiration
    at_least_these_days_before_expiration = models.IntegerField(default=0)
    at_most_these_days_before_expiration = models.IntegerField(default=0)
    # days after expiration
    at_least_these_days_after_expiration = models.IntegerField(default=0)
    at_most_these_days_after_expiration = models.IntegerField(default=0)
    # ---- Date conditions ----

    # specific date of the year
    active_specific_date_of_the_year = models.DateField(default=None, null=True)
    # specify excluding date from of the year
    exclude_date_from_of_the_year = models.DateField(default=None, null=True)
    # specify excluding date to of the year
    exclude_date_to_of_the_year = models.DateField(default=None, null=True)


class NurturingEmails(models.Model):
    """
    This model contains the nurturing emails:
    an email is sent based on the nurturing plan emails.
    """

    class Meta:
        db_table = 'nurturing_emails'

    nurturing_email_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    nurturing_plan = models.ForeignKey(NurturingEmailsPlan, on_delete=models.PROTECT, null=True)
    sent_date = models.DateTimeField(default=timezone.now)
    meta = models.JSONField(null=True)


auditlog.register(NurturingEmailsPlan)
auditlog.register(NurturingEmails)
auditlog.register(BillingPlan)
auditlog.register(BillingSubscription)
auditlog.register(BillingPayment)
