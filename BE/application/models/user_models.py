import json
import uuid
from datetime import date, datetime

from auditlog.registry import auditlog
from django.db.utils import cached_property
from django.utils import timezone
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.validators import MaxValueValidator, RegexValidator
from django.db import models
from google.auth.transport.requests import Request
from mptt.fields import TreeForeignKey
from mptt.models import MPTTModel
from rest_framework.exceptions import PermissionDenied
from google.oauth2.credentials import Credentials
import application.models.utils as utils
from application.mixin import GroupModelMixin
from application.utils.api_utils import BalanceSheetData

from docmanager.models import Document


class ActiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted=False)


class ActiveUserManager(UserManager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted=False)


class User(AbstractUser):
    """
    A user that could be and Association or an Athlete.
    """

    class Meta:
        db_table = 'bakney_user'
        indexes = [
            models.Index(fields=['connected_user'], name='user_connected_idx'),
        ]

    ASSOCIATION = 1
    ATHLETE = 2
    COLLABORATOR = 3

    ROLE_CHOICES = (
        (ASSOCIATION, 'association'),
        (ATHLETE, 'athlete'),
        (COLLABORATOR, 'association')
    )

    FULL = 1
    ONLY_ACCOUNTING = 2
    CUSTOM_COLLABORATOR_ROLE = 3

    COLLABORATOR_ROLE_CHOICES = (
        (FULL, 'full'),
        (ONLY_ACCOUNTING, 'only accounting'),
        (CUSTOM_COLLABORATOR_ROLE, 'custom collaborator role')
    )

    SOLAR_YEAR = 1
    SPORT_YEAR_SEP_AUG = 2
    SPORT_YEAR_JUN_MAY = 3
    OTHER = 4

    FISCAL_YEAR = (
        (SOLAR_YEAR, 'solar year'),
        (SPORT_YEAR_SEP_AUG, 'sport year september to august'),
        (SPORT_YEAR_JUN_MAY, 'sport year june to may'),
        (OTHER, 'other')
    )

    SEASON_YEAR = (
        (SOLAR_YEAR, 'solar year'),
        (SPORT_YEAR_SEP_AUG, 'sport year september to august'),
        (SPORT_YEAR_JUN_MAY, 'sport year june to may'),
        (OTHER, 'other')
    )

    FULL_YEAR = 1
    LIKE_SEASON_YEAR = 2
    LIKE_SEASON_YEAR_FROM_TIME = 3
    LIKE_FISCAL_YEAR = 4
    LIKE_FISCAL_YEAR_FROM_TIME = 5

    SUBSCRIPTION_DURATION = (
        (FULL_YEAR, 'full year'),
        (LIKE_SEASON_YEAR, 'like season year'),
        (LIKE_FISCAL_YEAR, 'like fiscal year'),
        (LIKE_SEASON_YEAR_FROM_TIME, 'like season year from time'),
        (LIKE_FISCAL_YEAR_FROM_TIME, 'like fiscal year from time')
    )

    user_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    role = models.PositiveSmallIntegerField(choices=ROLE_CHOICES, default=2)
    avatar_image = models.TextField(null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    two_fa = models.BooleanField(default=False, blank=True)
    two_fa_secret = models.CharField(max_length=32, default=None, blank=True, null=True)
    stripe_account_id = models.CharField(max_length=255, null=True)
    stripe_on_boarding_completed = models.BooleanField(default=False, blank=True)
    # user settings
    enumerate_invoices = models.BooleanField(default=False, blank=True)
    online_payments = models.BooleanField(default=True, blank=True)
    balance_sheet_year = models.PositiveSmallIntegerField(choices=FISCAL_YEAR, default=1)
    balance_sheet_start_day = models.PositiveSmallIntegerField(validators=[MaxValueValidator(31)], default=1)
    balance_sheet_start_month = models.PositiveSmallIntegerField(validators=[MaxValueValidator(12)], default=1)
    temporary_invoice_deletion = models.BooleanField(default=True, blank=False)
    auto_archive = models.BooleanField(default=False, blank=False)
    auto_mark_attendance = models.BooleanField(default=False, blank=False)
    full_installments_plan = models.BooleanField(default=False, blank=False)
    payment_date_equal_invoice_date = models.BooleanField(default=False, blank=False)
    starting_number_invoices = models.SmallIntegerField(default=0, blank=False)
    connected_user = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True)
    auto_paid_payment = models.BooleanField(default=False, blank=False)
    tables_settings = models.JSONField(default=dict)
    # permissions = models.ManyToManyField('Permission', blank=True)
    deleted = models.BooleanField(null=False, default=False)
    delete_on = models.DateField(null=True, blank=True)
    # requested_delete
    # collaborator settings
    collaborator_role = models.PositiveSmallIntegerField(choices=COLLABORATOR_ROLE_CHOICES, default=1)
    collaborator_permissions = models.JSONField(default=None, null=True, blank=True)
    dark_mode = models.BooleanField(default=False, blank=False)
    integration_google_state = models.CharField(max_length=255, null=True)
    integration_google_credentials = models.JSONField(default=None, null=True, blank=True)
    integration_google_auto_sync = models.BooleanField(default=False, blank=False)
    dashboard_layout = models.JSONField(default=None, null=True, blank=True)
    # subscriptions settings: TODO: remove old subscription settings
    subscription_duration_equal_sport_year = models.BooleanField(default=True)

    # new subscription settings, SEASON settings
    subscription_duration = models.PositiveSmallIntegerField(choices=SUBSCRIPTION_DURATION, default=2)
    membership_duration = models.PositiveSmallIntegerField(choices=SUBSCRIPTION_DURATION, default=2)
    subscription_start_day = models.PositiveSmallIntegerField(validators=[MaxValueValidator(31)], default=1)
    subscription_start_month = models.PositiveSmallIntegerField(validators=[MaxValueValidator(12)], default=1)
    custom_end_date = models.BooleanField(default=False)
    subscription_end_day = models.PositiveSmallIntegerField(validators=[MaxValueValidator(31)], default=31)
    subscription_end_month = models.PositiveSmallIntegerField(validators=[MaxValueValidator(12)], default=12)

    # starting number for membership
    membership_starting_number = models.IntegerField(default=0, blank=False)
    default_membership_type = models.CharField(max_length=255, default="", null=True, blank=True)

    # others
    preview_and_custom_features = models.ManyToManyField('PreviewAndCustomFeatures', blank=True)
    lead_sport_association_role = models.CharField(max_length=150, default=None, null=True)
    lead_sport_association_size = models.CharField(max_length=150, default=None, null=True)
    lead_sport_market_channel = models.CharField(max_length=150, default=None, null=True)
    disable_account_creation = models.BooleanField(default=False)
    force_account_creation = models.BooleanField(default=False)

    # notifications
    medical_certificate_notifications = models.BooleanField(default=True)

    # invoice settings
    hide_category_name = models.BooleanField(default=False)

    show_zero_payments = models.BooleanField(default=False)
    imported_from_associami = models.BooleanField(default=False)

    # other payment settings
    default_payment_category = models.ForeignKey(
        'PaymentCategory',
        related_name='users_default_category',
        default=None,
        null=True,
        on_delete=models.SET_NULL
    )
    default_payment_category_courses = models.ForeignKey(
        'PaymentCategory',
        related_name='users_default_category_courses',
        default=None,
        null=True,
        on_delete=models.SET_NULL
    )

    # override default manager to only execute queries on non-deleted users
    original_objects = UserManager()
    objects = ActiveUserManager()

    def is_sport_association(self, raise_exception=True):
        if self.role != self.ASSOCIATION and self.role != self.COLLABORATOR:
            if raise_exception:
                raise ValueError('User is not a sport association')
            return False
        return True

    def get_google_credentials(self):
        if self.integration_google_credentials is None:
            return None
        credentials = Credentials.from_authorized_user_info(json.loads(self.integration_google_credentials))
        if credentials.expired:
            credentials.refresh(Request())
            self.integration_google_credentials = credentials.to_json()
            self.save()
            return credentials
        return credentials

    def get_preveiw_and_custom_features(self):
        features = self.preview_and_custom_features.all()
        # create a list of dictionaries
        features_list = []
        for feature in features:
            features_list.append({
                'name': feature.name,
                'description': feature.description
            })
        return features_list

    @cached_property
    def google_sync_enabled(self):
        return self.integration_google_credentials is not None

    @cached_property
    def soft_delete(self):
        self.deleted = True
        self.username = self.username + '_deleted_' + str(self.user_id)
        self.email = self.email + '_deleted_' + str(self.user_id)
        self.save()

    @cached_property
    def sport_association(self):
        if self.role == self.ATHLETE:
            raise PermissionDenied('User is not a sport association')
        elif self.role == self.COLLABORATOR:
            sport_association = SportAssociation.objects.get(user=self.connected_user)
            return sport_association
        else:
            sport_association = SportAssociation.objects.get(user=self)
            return sport_association

    @cached_property
    def is_collaborator(self):
        return self.role == self.COLLABORATOR

    def __str__(self):
        return 'name: {} {}'.format(self.first_name, self.last_name)


class Group(models.Model):
    # this is the "location" of the tenants
    group_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=150)
    description = models.CharField(max_length=250, blank=True, null=True)
    sport_association = models.ForeignKey('SportAssociation', on_delete=models.CASCADE)



class PreviewAndCustomFeatures(models.Model):

    preview_and_custom_features_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=150)
    description = models.TextField()


class UsersOnboarding(models.Model):

    users_onboarding_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    show = models.BooleanField(default=True)
    create_membership = models.BooleanField(default=False)
    view_membership = models.BooleanField(default=False)
    approve_payment = models.BooleanField(default=False)
    download_invoice = models.BooleanField(default=False)
    view_collaborators = models.BooleanField(default=False)
    view_settings = models.BooleanField(default=False)


class CollaborationInvites(models.Model):

    collaboration_invite_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    email = models.EmailField()
    creation_date = models.DateTimeField(default=timezone.now)
    accepted = models.BooleanField(default=False)
    expiration_date = models.DateTimeField(default=None, null=True, blank=True)
    token = models.CharField(max_length=255, default=None, null=True, blank=True)
    collaborator_role = models.PositiveSmallIntegerField(choices=User.COLLABORATOR_ROLE_CHOICES, default=1)
    collaborator_permissions = models.JSONField(default=None, null=True, blank=True)


class Instructor(models.Model):

    instructor_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.EmailField(blank=True, null=True)
    born_date = models.DateField(null=True, blank=True)
    born_city = models.CharField(max_length=150, null=True, blank=True)
    born_province = models.CharField(max_length=150, null=True, blank=True)
    tax_code = models.CharField(max_length=25, null=True, blank=True)
    address_city = models.CharField(max_length=150, null=True, blank=True)
    address = models.CharField(max_length=150, null=True, blank=True)
    address_province = models.CharField(max_length=150, null=True, blank=True)
    civic_number = models.CharField(max_length=10, null=True, blank=True)
    study_title = models.CharField(max_length=150, null=True, blank=True)
    role = models.CharField(max_length=150, default='Istruttore', null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    phone_2 = models.CharField(max_length=20, blank=True, null=True)
    phone_3 = models.CharField(max_length=20, blank=True, null=True)
    phone_4 = models.CharField(max_length=20, blank=True, null=True)
    draft = models.BooleanField(null=False, default=False)  # for instance, when imported from xls/csv
    associated_user_id = models.UUIDField(default=None, null=True, blank=True)
    is_volunteer = models.BooleanField(default=False)
    stipulated_contract_in = models.DateField(null=True, blank=True)
    default_hourly_billing = models.DecimalField(max_digits=9, decimal_places=2, default=0.00)
    default_percentage_billing = models.DecimalField(max_digits=4, decimal_places=2, default=0.00, null=True)
    documents = models.ManyToManyField(Document, related_name='instructor_documents', blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'draft'], name='instructor_user_draft_idx'),
        ]

    def get_full_name(self):
        """
        Return the first_name plus the last_name, with a space in between.
        """
        full_name = '%s %s' % (self.first_name, self.last_name)
        return full_name.strip()


class InstructorHours(models.Model):

    instructor_hours_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    instructor = models.ForeignKey(Instructor, on_delete=models.CASCADE)
    creation_date = models.DateTimeField(default=timezone.now)
    date = models.DateField(default=timezone.now)
    compensation_type = models.CharField(max_length=15, default='hourly')
    # hours
    hours = models.DecimalField(max_digits=4, decimal_places=2)
    hourly_billing = models.DecimalField(max_digits=9, decimal_places=2, default=0.00)
    # percentage
    percentage_billing = models.DecimalField(max_digits=4, decimal_places=2, default=0.00, null=True)
    courses = models.JSONField(default=None, blank=True, null=True)
    period = models.CharField(max_length=50, blank=True, null=True)
    # payment
    amount = models.DecimalField(max_digits=9, decimal_places=2, default=0.00)
    paid = models.BooleanField(default=False)
    payment = models.ForeignKey('Payment', on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    calculation_data = models.JSONField(default=list, null=True)
    document = models.ForeignKey(Document, on_delete=models.SET_NULL, null=True, blank=True)


class UserPartial(models.Model):
    email = models.EmailField(unique=True)
    creation_date = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'bakney_user_partial'


class Family(models.Model):
    FAMILY = 'family'
    GROUPED_SUBSCRIPTIONS = 'grouped_subscriptions'

    ALL_TYPES = [FAMILY, GROUPED_SUBSCRIPTIONS]

    family_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    type = models.CharField(
        max_length=50,
        choices=[
            (FAMILY, 'Family'),
            (GROUPED_SUBSCRIPTIONS, 'Grouped Subscriptions')
        ],
        default=FAMILY
    )


class Associate(GroupModelMixin):
    """
    This model contains information about an associate
    """

    OTHER = 'A'
    MALE = 'M'
    FEMALE = 'F'

    SEX_CHOICES = (
        (OTHER, 'other'),
        (MALE, 'male'),
        (FEMALE, 'female')
    )

    associate_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.DO_NOTHING, null=True)
    sport_association = models.ForeignKey('SportAssociation', on_delete=models.SET_NULL, null=True)
    picture_path = models.CharField(max_length=512, null=True, blank=True)
    first_name = models.CharField(max_length=150, null=True, blank=True)
    last_name = models.CharField(max_length=150, null=True, blank=True)
    sex = models.CharField(choices=SEX_CHOICES, default=OTHER, max_length=1, blank=True)
    tax_code = models.CharField(max_length=25, null=True, blank=True)
    born_date = models.DateField(null=True, blank=True)
    born_city = models.CharField(max_length=150, null=True, blank=True)
    address_city = models.CharField(max_length=150, null=True, blank=True)
    address = models.CharField(max_length=150, null=True, blank=True)
    address_cap = models.CharField(
        max_length=5,
        validators=[
            RegexValidator(
                regex=r'^\d{5}$',
                message='Enter exactly 5 digits',
            )
        ],
        null=True,
        blank=True
    )  # max length is 5
    nationality = models.CharField(max_length=150, null=True, default='Italiana', blank=True)
    nationality_residence = models.CharField(max_length=150, null=True, default='Italia', blank=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    phone_2 = models.CharField(max_length=30, blank=True, null=True)
    phone_3 = models.CharField(max_length=30, blank=True, null=True)
    phone_4 = models.CharField(max_length=30, blank=True, null=True)
    phone_label = models.CharField(max_length=100, blank=True, null=True)
    phone_2_label = models.CharField(max_length=100, blank=True, null=True)
    phone_3_label = models.CharField(max_length=100, blank=True, null=True)
    phone_4_label = models.CharField(max_length=100, blank=True, null=True)
    is_minor = models.BooleanField(default=False)
    disabled = models.BooleanField(default=False)
    draft = models.BooleanField(null=False, default=False)  # for instance, when imported from xls/csv
    group = models.ForeignKey('Group', on_delete=models.CASCADE, null=True, blank=True)
    deleted = models.BooleanField(default=False)
    family = models.ForeignKey(Family, on_delete=models.CASCADE, null=True, blank=True)
    family_role = models.CharField(max_length=100, blank=True, null=True)
    id_number = models.CharField(max_length=100, blank=True, null=True)
    # tutor relations
    tutors = models.ManyToManyField(
        'self',
        through='AssociateTutorRelation',
        symmetrical=False,
        related_name='tutored_associates'
    )

    creation_date = models.DateTimeField(default=timezone.now)
    migrated = models.BooleanField(default=False)

    notes = models.TextField(null=True, blank=True)

    class Meta:
        # Note: Case-insensitive unique constraint is defined in migration 0410
        # CREATE UNIQUE INDEX unique_tax_code_sport_assoc_upper_active
        # ON application_associate (UPPER(tax_code), sport_association_id)
        # WHERE deleted = FALSE AND tax_code IS NOT NULL
        indexes = [
            models.Index(fields=['associate_id', 'user']),
            # For search optimization
            models.Index(fields=['first_name']),
            models.Index(fields=['last_name']),
            # For filtering
            models.Index(fields=['sport_association', 'deleted'], name='associate_deleted_idx'),
            # For sorting
            models.Index(fields=['born_date']),
            # For case-insensitive tax_code lookups
            models.Index(fields=['sport_association'], name='associate_sport_assoc_id_idx')
        ]

    @property
    def address_number(self):
        if self.address is None:
            return ''
        # extract the number from the address (CAN also be something like 23A or 23 A)
        # use regex
        import re
        number = re.findall(r'\d+\s*[A-Za-z]?', self.address)
        if len(number) > 0:
            return number[0]
        return ''

    @property
    def address_no_number(self):
        if self.address is None:
            return self.address
        # remove the number from the address
        import re
        return re.sub(r'\d+\s*[A-Za-z]?', '', self.address).replace(',', '').strip()

    @property
    def is_minor_now(self):
        return self.calculate_age() < 18

    @property
    def full_name(self):
        return self.get_full_name()

    @property
    def full_name_lower(self):
        return self.get_full_name().lower()

    @property
    def family_members(self):
        if self.family is None:
            return []
        return Associate.objects.filter(family=self.family).exclude(associate_id=self.associate_id)

    @property
    def main_tutor(self):
        return self.get_main_tutor()

    @property
    def is_tutor(self):
        return self.get_is_tutor()

    @property
    def age(self):
        return self.calculate_age()

    def get_is_primary_tutor(self, associate):
        # if there is a relation where this is a primary tutor
        return AssociateTutorRelation.objects.filter(tutor=self, is_primary=True, associate=associate).exists()

    def get_is_tutor(self):
        # if there is a relation where this is a tutor
        return AssociateTutorRelation.objects.filter(tutor=self).exists()

    def get_full_name(self):
        """
        Return the first_name plus the last_name, with a space in between.
        """
        full_name = '%s %s' % (self.first_name, self.last_name)
        return full_name.strip()

    def get_main_tutor(self):
        tutor_relation = self.tutor_relations.filter(is_primary=True).first()
        if tutor_relation:
            return tutor_relation.tutor
        else:
            # get the first available tutor and set it primary
            tutor_relation = self.tutor_relations.first()
            if tutor_relation:
                tutor_relation.is_primary = True
                tutor_relation.save()
                return tutor_relation.tutor
        return None

    def add_main_tutor(self, tutor):
        tutor_relation = AssociateTutorRelation.objects.create(
            associate=self,
            tutor=tutor,
            is_primary=True
        )
        return tutor_relation

    def calculate_age(self):
        now = date.today()

        # Check if born_date is None
        if not self.born_date:
            return 0

        # Convert born_date to date object if it's a string
        if isinstance(self.born_date, str):
            try:
                # Assuming the date format is 'YYYY-MM-DD'
                year, month, day = map(int, self.born_date.split('-'))
                born_date = date(year, month, day)
            except (ValueError, AttributeError):
                return 0
        else:
            # If it's already a date object, use it directly
            born_date = self.born_date

        delta = now - born_date

        return int(delta.days / 365.25)

    def calculate_days_to_age(self):
        now = date.today()

        if not self.born_date:
            return 0

        if isinstance(self.born_date, str):
            try:
                year, month, day = map(int, self.born_date.split('-'))
                born_date = date(year, month, day)
            except (ValueError, AttributeError):
                return 0
        else:
            born_date = self.born_date

        delta = now - born_date

        return int((6 * 365.25) - delta.days)


class AssociateTutorRelation(models.Model):
    """Represents the relationship between an associate and their tutor"""
    associate = models.ForeignKey(
        Associate,
        on_delete=models.CASCADE,
        related_name='tutor_relations'
    )
    tutor = models.ForeignKey(
        Associate,
        on_delete=models.CASCADE,
        related_name='tutored_relations'
    )
    is_primary = models.BooleanField(default=False)

    class Meta:
        constraints = [
            # Ensure only one primary tutor per associate
            models.UniqueConstraint(
                fields=['associate'],
                condition=models.Q(is_primary=True),
                name='unique_primary_tutor'
            )
        ]
        indexes = [
            models.Index(fields=['tutor', 'associate']),
            models.Index(fields=['is_primary'])
        ]

class Institution(models.Model):

    institution_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    short_name = models.CharField(max_length=50)
    full_name = models.CharField(max_length=200)


class SportAssociation(models.Model):
    """
    This model contains information about a sport association
    """

    AVAILABLE_INVOICES_TEMPLATES = [
        {'name': 'invoice.html', 'description': 'Modello ricevuta standard'},
        {'name': 'invoice_classic.html', 'description': 'Modello ricevuta semplice'}
    ]

    AVAILABLE_SUBSCRIPTIONS_TEMPLATES = [
        {'name': 'subscription.html', 'description': 'Modello modulo iscrizione standard'},
        {'name': 'subscription_classic.html', 'description': 'Modello modulo iscrizione semplice'}
    ]

    sport_association_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.OneToOneField(User, unique=True, on_delete=models.CASCADE)
    denomination = models.CharField(max_length=150)
    address_city = models.CharField(max_length=150, null=True)
    address = models.CharField(max_length=150, null=True)
    address_cap = models.CharField(
        max_length=5,
        validators=[
            RegexValidator(
                regex=r'^\d{5}$',
                message='Enter exactly 5 digits',
            )
        ],
        null=True
    )
    tax_code = models.CharField(max_length=11)
    email = models.EmailField(null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    regulation = models.TextField(null=True)
    demand = models.TextField(null=True)
    additional_sections = models.JSONField(default=utils.get_default_additional_sections)
    logo = models.TextField(null=True)
    subscription_fee = models.DecimalField(max_digits=9, decimal_places=2, default=0.00)
    multiple_subscription_fee = models.BooleanField(default=False)
    subscription_fee_plans = models.JSONField(default=list)
    membership_fee = models.DecimalField(max_digits=9, decimal_places=2, default=None, null=True)
    multiple_membership_fee = models.BooleanField(default=False)
    membership_fee_plans = models.JSONField(default=list)
    custom_subscription_form_data = models.JSONField(default=None, null=True)
    document_header = models.TextField(null=True)
    invoice_footer = models.TextField(null=True)
    enable_quotes_management = models.BooleanField(default=True)
    configuration = models.JSONField(default=utils.get_default_configuration)
    reviewed = models.BooleanField(default=False)
    affiliate_code = models.CharField(max_length=8, default=None, null=True)
    affiliate_code_stripe = models.CharField(max_length=255, null=True)
    deleted = models.BooleanField(null=False, default=False)
    review_url = models.CharField(max_length=255, null=True)
    review_url_enabled = models.BooleanField(default=False)
    crm_user_id = models.CharField(max_length=255, null=True)
    crm_contact_id = models.CharField(max_length=255, null=True)
    president_signature = models.TextField(null=True)
    stamp = models.TextField(null=True)
    president_first_name = models.CharField(max_length=255, null=True)
    president_last_name = models.CharField(max_length=255, null=True)
    enroll_number = models.CharField(max_length=100, blank=True, null=True)
    federation = models.CharField(max_length=100, blank=True, null=True)
    sport = models.CharField(max_length=100, blank=True, null=True)
    # array enabled for ["associate", "associate-membership", "membership"]
    enabled_for = models.JSONField(default=utils.get_default_enabled_for, null=True)
    checkout_info = models.TextField(null=True)
    additional_fields = models.JSONField(default=None, null=True)
    stripe_available_methods = models.JSONField(default=utils.get_default_stripe_methods, null=True)
    # additional fields
    iban = models.CharField(max_length=34, null=True)
    website = models.CharField(max_length=1024, null=True)
    abbreviated = models.CharField(max_length=100, null=True)
    vat_number = models.CharField(max_length=20, null=True)
    whatsapp = models.CharField(max_length=20, null=True)
    imported_from_associami = models.BooleanField(default=False)
    extra_text_invoices = models.TextField(null=True)

    show_regulation_to_members = models.BooleanField(default=True)
    show_regulation_to_both = models.BooleanField(default=True)
    show_regulation_to_athletes = models.BooleanField(default=True)
    show_demand_to_members = models.BooleanField(default=True)
    show_demand_to_both = models.BooleanField(default=True)
    show_demand_to_athletes = models.BooleanField(default=True)

    # invoice_template
    invoice_template = models.TextField(default='invoice.html', null=True, blank=True)
    # subscription_template
    subscription_template = models.TextField(default='subscription.html', null=True, blank=True)

    # notes used for internal notes on the sport association for bakney internal use
    notes = models.TextField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['sport_association_id', 'user']),
        ]

    # override default manager to only execute queries on non-deleted users
    original_objects = models.Manager()
    objects = ActiveManager()

    @property
    def soft_delete(self):
        self.deleted = True
        self.save()

    def get_current_year(self):
        date_from, date_to = BalanceSheetData.get_range_from_year_and_starting_date(
            date=datetime.now(),
            starting_day=self.user.balance_sheet_start_day,
            starting_month=self.user.balance_sheet_start_month
        )
        return date_from, date_to

    def get_invoice_template(self):
        if self.invoice_template is None:
            return 'invoice.html'
        elif self.invoice_template not in [template['name'] for template in self.AVAILABLE_INVOICES_TEMPLATES]:
            return 'invoice.html'
        else:
            return self.invoice_template

    def get_subscription_template(self):
        if self.subscription_template is None:
            return 'subscription.html'
        elif self.subscription_template not in [template['name'] for template in self.AVAILABLE_SUBSCRIPTIONS_TEMPLATES]:
            return 'subscription.html'
        else:
            return self.subscription_template

class SportAssociationMembershipCardConfiguration(models.Model):
    sport_association_membership_card_configuration_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    emit_only_on_approval = models.BooleanField(default=False)
    customized_template = models.JSONField(null=True)

class SportAssociationHeadquarter(models.Model):

    sport_association_headquarter_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    name = models.CharField(max_length=150)
    description = models.TextField(null=True)
    address_city = models.CharField(max_length=150, null=True)
    address = models.CharField(max_length=150, null=True)
    address_cap = models.CharField(
        max_length=5,
        validators=[
            RegexValidator(
                regex=r'^\d{5}$',
                message='Enter exactly 5 digits',
            )
        ],
        null=True
    )
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(null=True)


class SportAssociationModuleTemplates(models.Model):

    sport_association_module_templates_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    name = models.CharField(max_length=150)
    template = models.TextField(null=True)
    footer = models.BooleanField(default=True)
    header = models.BooleanField(default=True)
    custom_footer_header = models.BooleanField(default=False)
    custom_footer = models.TextField(null=True)
    custom_header = models.TextField(null=True)


class SportAssociationMaterial(models.Model):

    sport_association_material_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    name = models.CharField(max_length=150)
    description = models.TextField(null=True)


class Folder(MPTTModel):
    name = models.CharField(max_length=255)
    parent = TreeForeignKey('self', null=True, blank=True, on_delete=models.CASCADE)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ('name', 'parent', 'sport_association') # Prevents duplicate names in same folder


class SportAssociationDocumentsArchive(models.Model):

    sport_association_documents_archive_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    document = models.ForeignKey(Document, default=None, on_delete=models.CASCADE)
    date = models.DateField(default=timezone.now)
    folder = models.ForeignKey(Folder, null=True, on_delete=models.SET_NULL)  # null=True allows root documents


class Reminder(models.Model):
    EMAIL = 1
    PUSH = 3

    REMINDER_TYPES = (
        (EMAIL, 'email'),
        (PUSH, 'push')
    )

    ANNUAL = 1
    BIANNUAL = 2
    QUARTERLY_SOLAR = 3
    QUARTERLY = 4
    MONTHLY_SOLAR = 5
    MONTHLY = 6
    WEEKLY = 7

    FREQUENCY_TYPES = (
        (ANNUAL, 'annual'),
        (BIANNUAL, 'biannual'),
        (QUARTERLY_SOLAR, 'quarterly solar'),
        (QUARTERLY, 'quarterly'),
        (MONTHLY_SOLAR, 'monthly solar'),
        (MONTHLY, 'monthly'),
        (WEEKLY, 'weekly')
    )

    reminder_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    reminder_type = models.PositiveSmallIntegerField(choices=REMINDER_TYPES, default=EMAIL)
    frequency = models.PositiveSmallIntegerField(choices=FREQUENCY_TYPES, default=ANNUAL)
    name = models.CharField(max_length=150)
    description = models.TextField(null=True)
    next_occurrence = models.DateField(null=True)
    time_occurrence = models.TimeField(null=True)
    last_occurrence = models.DateField(null=True)
    enabled = models.BooleanField(default=True)
    recipients = models.JSONField(default=list, null=True)
    # TOOD: add template field
    template_text = models.TextField(null=True)
    template_html = models.TextField(null=True)


class SentEmails(models.Model):
    date = models.DateField(auto_now_add=True)
    number_of_emails = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.date} - {self.number_of_emails}"


class EmailLog(models.Model):

    email_log_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    recipient = models.EmailField(null=True)
    subject = models.CharField(max_length=255)
    result = models.CharField(max_length=255)
    sent_at = models.DateTimeField(auto_now_add=True)
    sport_association = models.ForeignKey('application.SportAssociation', on_delete=models.CASCADE, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['email_log_id', 'sport_association']),
        ]


class Testimonial(models.Model):

    testimonial_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    text = models.TextField()
    score = models.FloatField()


# REQUEST / RESPONSE OBJECTS

class UserAccount(object):
    def __init__(self, new_member):
        self.new_member = new_member


auditlog.register(User, exclude_fields=['last_login', 'password'])  # Exclude login timestamps and password hashes
auditlog.register(Instructor)
auditlog.register(Associate)
auditlog.register(SportAssociation)
auditlog.register(Group)
auditlog.register(PreviewAndCustomFeatures)
auditlog.register(UsersOnboarding)
auditlog.register(CollaborationInvites)
auditlog.register(InstructorHours)
auditlog.register(UserPartial)
auditlog.register(Family)
auditlog.register(AssociateTutorRelation)
auditlog.register(Institution)
auditlog.register(SportAssociationMembershipCardConfiguration)
auditlog.register(SportAssociationHeadquarter)
auditlog.register(SportAssociationModuleTemplates)
auditlog.register(SportAssociationMaterial)
auditlog.register(SportAssociationDocumentsArchive)
auditlog.register(Reminder)
auditlog.register(SentEmails)
