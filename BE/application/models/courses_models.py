"""
Copyright: Bakney S.r.l.s.
"""
import uuid

from auditlog.registry import auditlog
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from application.mixin import GroupModelMixin, GroupAwareManager
from application.models.payment_models import Payment
from application.models.subscriptions_models import Subscription
from application.models.user_models import SportAssociation, Group
from docmanager.models import Document


def random_color():
    import random
    return "#{:06x}".format(random.randint(0, 0xFFFFFF))


class CourseTags(models.Model):
    """
    This model contains information about a Tag
    """

    tag_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    tag_name = models.CharField(max_length=255)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    creation_date = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['tag_id', 'sport_association']),
        ]

class CourseManager(GroupAwareManager):

    # create a method to get also the soft deleted
    def all_objects(self):
        return super().get_queryset()

    # get list excluding soft deleted
    def get_queryset(self):
        return super().get_queryset().filter(deleted=False)


class Course(GroupModelMixin):
    """
    This model contains information about a Course
    """

    DRAFT = 1
    ACTIVE = 2
    INTERNAL = 3

    STATUS_FLAG = (
        (DRAFT, 'archived'),
        (ACTIVE, 'active'),
        (INTERNAL, 'internal')
    )

    DEFAULT_TYPE = 1
    MULTIPLE_QUOTES_TYPE = 2
    MEMBERSHIP_TYPE = 3

    COURSE_TYPE = (
        (DEFAULT_TYPE, 'default'),
        (MULTIPLE_QUOTES_TYPE, 'multiple_quotes'),
        (MEMBERSHIP_TYPE, 'membership')
    )

    course_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, null=True)
    status_flag = models.PositiveSmallIntegerField(choices=STATUS_FLAG, default=ACTIVE)
    course_type = models.PositiveSmallIntegerField(choices=COURSE_TYPE, default=1)
    creation_date = models.DateTimeField(default=timezone.now)
    title = models.CharField(max_length=255, null=True)
    description = models.TextField(null=True)
    multi_payments = models.BooleanField(default=False)
    fee = models.DecimalField(max_digits=9, decimal_places=2, default=0.00, null=True)
    events = models.JSONField(null=True)
    # support the one fee
    one_fee_payment = models.BooleanField(default=False)
    one_fee = models.DecimalField(max_digits=9, decimal_places=2, default=0.00)  # this value is not mandatory
    google_calendar_id = models.CharField(max_length=255, null=True)
    google_background_color = models.CharField(max_length=7, null=True, default=random_color)
    tags = models.ManyToManyField('CourseTags', blank=True)
    multiple_quotes = models.JSONField(null=True, blank=True)
    locations = models.ManyToManyField('CourseLocation', blank=True)
    start_date = models.DateTimeField(null=True)
    end_date = models.DateTimeField(null=True)

    pinned = models.BooleanField(default=False)

    # support the membership
    billed_duration_is_sport_season = models.BooleanField(default=False)  # if true, blank billed_frequency
    # if billed_duration_is_sport_season do not set: billed_frequency, billed_from_subscription_date
    # billed_from_day_of_month, auto_renewal
    billed_frequency = models.PositiveSmallIntegerField(default=1, null=True, blank=True)  # number of months between each bill if not billed_duration_is_sport_season
    billed_from_subscription_date = models.BooleanField(default=False)  # if true the subscription will be billed from the subscription date
    billed_from_day_of_month = models.IntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(28)
        ],
        default=1,
    )  # used only if billed_from_subscription_date is false
    auto_renewal = models.BooleanField(default=False) #  set only if not billed_duration_is_sport_season

    deleted = models.BooleanField(default=False)
    group = models.ForeignKey('Group', on_delete=models.CASCADE, null=True)

    objects = CourseManager()

    def get_status_label(self):
        if self.status_flag == self.DRAFT:
            return 'Non visibile'
        elif self.status_flag == self.ACTIVE:
            return 'Visibile'
        return 'Sconosciuto'

    def get_course_type_label(self):
        if self.course_type == self.DEFAULT_TYPE:
            return 'Default'
        elif self.course_type == self.MULTIPLE_QUOTES_TYPE:
            return 'Quote multiple'
        elif self.course_type == self.MEMBERSHIP_TYPE:
            return 'Abbonamento'
        return 'Sconosciuto'


    class Meta:
        indexes = [
            models.Index(fields=['sport_association', '-creation_date']),
            models.Index(fields=['status_flag'])
        ]

    def save(self, *args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)
        logger.info('Course save method called')
        # If this is an existing course being updated
        if self.pk:
            logger.info('Course save method called - existing course')
            try:
                # Get the old instance from database
                old_instance = Course.objects.get(course_id=self.course_id)
                old_title = old_instance.title

                # Save the new changes
                super().save(*args, **kwargs)

                # If the title changed, dispatch the signal
                if old_title != self.title:
                    from application.signals import update_metadata_courses
                    update_metadata_courses.send(
                        sender=self.__class__,
                        instance=self,
                        title=old_title
                    )
            except Course.DoesNotExist:
                # If somehow the course doesn't exist, just do a normal save
                super().save(*args, **kwargs)
        else:
            # This is a new course being created
            super().save(*args, **kwargs)


class CourseLocation(models.Model):

    course_location_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    title = models.CharField(max_length=255, null=True)
    description = models.CharField(max_length=2048, null=True, blank=True)
    address = models.CharField(max_length=255, null=True, blank=True)
    documents = models.ManyToManyField(Document, blank=True)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, null=True)


class CourseSubscriptionManager(models.Manager):

    # create a method to get also the soft deleted
    def all_objects(self):
        return super().get_queryset()

    # get list excluding soft deleted
    def get_queryset(self):
        return super().get_queryset().filter(deleted=False)


class CourseSubscription(models.Model):

    DEFAULT_TYPE = 1
    MULTIPLE_QUOTES_TYPE = 2
    MEMBERSHIP_TYPE = 3

    COURSE_TYPE = (
        (DEFAULT_TYPE, 'default'),
        (MULTIPLE_QUOTES_TYPE, 'multiple_quotes'),
        (MEMBERSHIP_TYPE, 'membership')
    )

    course_subscription_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    paid = models.BooleanField(default=False)
    creation_date = models.DateTimeField(default=timezone.now)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, null=True)
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True)
    multi_payments = models.BooleanField(default=False)
    one_fee_payment = models.BooleanField(default=False)
    archived = models.BooleanField(null=False, default=False)
    type = models.PositiveSmallIntegerField(choices=COURSE_TYPE, default=1)
    multiple_quote = models.JSONField(null=True)

    # support the membership
    billed_frequency = models.PositiveSmallIntegerField(default=1)  # number of months between each bill
    billed_from = models.DateTimeField(null=True, blank=True)  # if not null the subscription will be billed from this date
    billed_until = models.DateTimeField(null=True, blank=True)  # if not null the subscription will be billed until this date
    billed_from_day_of_month = models.IntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(28)
        ],
        default=1,
    )
    membership_active = models.BooleanField(default=True)
    membership_fee = models.DecimalField(max_digits=9, decimal_places=2, default=0.00, null=True, blank=True)
    auto_renewal = models.BooleanField(default=False)
    membership_payments = models.ManyToManyField('Payment', related_name='membership_course_subscriptions', blank=True)

    deleted = models.BooleanField(default=False)

    objects = CourseSubscriptionManager()

    class Meta:
        indexes = [
            models.Index(fields=['course', 'subscription']),
        ]
        # constraints = [
        #     models.UniqueConstraint(
        #         fields=['course', 'subscription', 'deleted'],
        #         name='unique_course_subscription'
        #     )
        # ]

    def delete(self, *args, **kwargs):
        """
        Override the delete method to implement soft delete
        """
        CourseSubscriptionInstallment.objects.filter(course_subscription=self).delete()
        self.deleted = True
        self.save()

    def hard_delete(self, *args, **kwargs):
        """
        Hard delete the course subscription
        """
        super().delete(*args, **kwargs)

    def is_paid(self):
        if self.multi_payments:
            return CourseSubscriptionInstallment.objects.filter(course_subscription=self).filter(paid=True).count() \
                   == CourseSubscriptionInstallment.objects.filter(course_subscription=self).count()
        else:
            return self.paid

    # print to string
    def __str__(self):
        # output all the fields
        return str(self.__dict__)


class CourseSubscriptionInstallmentManager(GroupAwareManager):
        # create a method to get also the soft deleted
        def all_objects(self):
            return super().get_queryset()

        # get list excluding soft deleted
        def get_queryset(self):
            return super().get_queryset().filter(deleted=False)


class CourseSubscriptionInstallment(GroupModelMixin):

    course_subscription_installment_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    course_subscription = models.ForeignKey(CourseSubscription, on_delete=models.CASCADE)
    id = models.PositiveSmallIntegerField(validators=[MaxValueValidator(11), MinValueValidator(0)], default=0)
    amount = models.DecimalField(max_digits=9, decimal_places=2, default=0.00)
    paid = models.BooleanField(default=False)
    payment_date = models.DateTimeField(null=False)
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True)
    archived = models.BooleanField(null=False, default=False)
    group = models.ForeignKey(Group, default=None, on_delete=models.SET_NULL, null=True)
    deleted = models.BooleanField(default=False)

    objects = CourseSubscriptionInstallmentManager()



class CampsAndRetreats(models.Model):

    camps_and_retreats_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    title = models.CharField(max_length=255, null=True)
    description = models.CharField(max_length=2048, null=True)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, default=None)


class CampsAndRetreatsPeriod(models.Model):

    camps_and_retreats_period_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    camps_and_retreats = models.ForeignKey(CampsAndRetreats, on_delete=models.CASCADE)
    start_date = models.DateTimeField(null=False)
    end_date = models.DateTimeField(null=False)
    title = models.CharField(max_length=255, null=True)
    description = models.CharField(max_length=2048, null=True)
    fee = models.DecimalField(max_digits=9, decimal_places=2, default=0.00)
    max_participants = models.PositiveIntegerField(default=0, null=True, blank=True)
    camps_and_Retreats_period_services = models.ManyToManyField('CampsAndRetreatsPeriodsService', blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class CampsAndRetreatsPeriodsService(models.Model):

    camps_and_retreats_period_service_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    camps_and_retreats_period = models.ForeignKey(CampsAndRetreatsPeriod, on_delete=models.CASCADE)
    title = models.CharField(max_length=255, null=True)
    description = models.CharField(max_length=2048, null=True)
    fee = models.DecimalField(max_digits=9, decimal_places=2, default=0.00)
    payment_category = models.ForeignKey('PaymentCategory', on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(default=timezone.now)


class CampsAndRetreatsSubscriptionPeriod(models.Model):

    camps_and_retreats_subscription_period_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    camps_and_retreats_period = models.ForeignKey(CampsAndRetreatsPeriod, on_delete=models.CASCADE)
    camps_and_retreats_period_services = models.ManyToManyField('CampsAndRetreatsPeriodsService', blank=True)
    camps_and_retreats_subscription = models.ForeignKey('CampsAndRetreatsSubscription', on_delete=models.CASCADE)
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True)


class CampsAndRetreatsSubscription(models.Model):

    PENDING = 1
    APPROVED = 2
    REJECTED = 3

    STATUS_FLAG = (
        (PENDING, 'pending'),
        (APPROVED, 'approved'),
        (REJECTED, 'rejected')
    )

    camps_and_retreats_subscription_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    camps_and_retreats = models.ForeignKey(CampsAndRetreats, on_delete=models.CASCADE)
    status_flag = models.PositiveSmallIntegerField(choices=STATUS_FLAG, default=2)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, null=True)
    created_at = models.DateTimeField(default=timezone.now)


auditlog.register(CourseTags)
auditlog.register(CampsAndRetreats)
auditlog.register(CampsAndRetreatsPeriod)
auditlog.register(CampsAndRetreatsPeriodsService)
auditlog.register(CampsAndRetreatsSubscriptionPeriod)
auditlog.register(CampsAndRetreatsSubscription)
auditlog.register(Course)
auditlog.register(CourseSubscription)
auditlog.register(CourseSubscriptionInstallment)
auditlog.register(CourseLocation)
