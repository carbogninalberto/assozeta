import uuid
import base64
import binascii
import os
import logging
from datetime import datetime, timedelta


from auditlog.registry import auditlog
from dateutil.relativedelta import relativedelta
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import models
from django.db.utils import cached_property
from django.utils import timezone

from application.mixin import GroupModelMixin, GroupAwareManager
from application.models.payment_models import Payment, SupplierAndCustomers
from application.models.user_models import User, Associate, SportAssociation, Instructor
from application.utils.api_utils import BalanceSheetData, parse_date
from docmanager.models import Document
from django.db.models import Q

logger = logging.getLogger(__name__)


class MedicalCertificate(models.Model):
    """
    This model contains information about a medical certificate
    """

    medical_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    document = models.ForeignKey(Document, null=True, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    creation_date = models.DateTimeField(default=timezone.now)
    expiration_date = models.DateField(null=True)
    competitive_medical_certificate = models.BooleanField(null=False, default=False)
    notes = models.TextField(null=True)

    class Meta:
        indexes = [
            models.Index(fields=['medical_id', 'user']),
            models.Index(fields=['medical_id', 'user', 'creation_date'], name='medical_creation_date_idx'),
        ]


class MedicalAppointments(models.Model):

    AVAILABLE_REGIONS = [
        'agonistica-emilia-romagna',
        'agonistica-lombardia',
        'agonistica-piemonte',
        'agonistica-piemonte-biella'
        'agonistica-sicilia',
        'agonistica-toscana',
        'agonistica',
        'non-agonistica-emilia-romagna',
        'non-agonistica-sicilia',
        'non-agonistica'
    ]

    REGIONS = {
        'agonistica-emilia-romagna': 'Visita Agonistica Emilia Romagna',
        'agonistica-lombardia': 'Visita Agonistica Lombardia',
        'agonistica-piemonte': 'Visita Agonistica Piemonte',
        'agonistica-piemonte-biella': 'Visita Agonistica Piemonte Biella',
        'agonistica-sicilia': 'Visita Agonistica Sicilia',
        'agonistica-toscana': 'Visita Agonistica Toscana',
        'agonistica': 'Visita Agonistica',
        'non-agonistica-emilia-romagna': 'Visita Non Agonistica Emilia Romagna',
        'non-agonistica-sicilia': 'Visita Non Agonistica Sicilia',
        'non-agonistica': 'Visita Non Agonistica'
    }

    MODELS_TEMPLATE_MAP = {
        'agonistica': 'document/medical-appointments/agonistica.html',
        'non-agonistica': 'document/medical-appointments/non-agonistica.html',
        'agonistica-piemonte-biella': 'templates/document/application/pdfs/piemonte-biella.pdf'
    }

    MODELS_TEMPLATE_TYPE_MAP = {
        'agonistica': 'HTML',
        'non-agonistica': 'HTML',
        'agonistica-piemonte-biella': 'PDF'
    }

    medical_appointments_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    subscription = models.ForeignKey('Subscription', null=False, on_delete=models.CASCADE)
    region = models.CharField(max_length=100)
    document = models.ForeignKey(Document, null=True, on_delete=models.CASCADE)
    date = models.DateField(null=True, default=timezone.now)
    notes = models.TextField(null=True)
    sport = models.CharField(max_length=255, blank=True, null=True)
    meta = models.JSONField(default=dict, null=True)
    address = models.CharField(max_length=255, blank=True, null=True)
    creation_date = models.DateTimeField(default=timezone.now)


    def get_template(self):
        return self.MODELS_TEMPLATE_MAP.get(self.region, 'document/medical-appointments/non-agonistica.html')


class SubscriptionManager(GroupAwareManager):
    def create(self, **kwargs):
        # Check for existing subscriptions before creating
        if self.subscription_exists(**kwargs):
            raise ValidationError("Questa iscrizione esiste già.")

        obj = super().create(**kwargs)
        from application.signals import new_subscription
        new_subscription.send(sender=self.__class__, subscription=obj)
        return obj

    def get_subscription_if_it_exists(self, **kwargs):
        # for arg in kwargs:
        #     logger.info(f"Arg: {arg} -> type: {isinstance(kwargs[arg], type)}, value: {kwargs[arg]}")
        # Extract relevant fields for checking
        sport_association = kwargs.get('sport_association')
        associate_data = kwargs.get('associate_data', None)
        associate = kwargs.get('associate', None)
        start_date = kwargs.get('start_date', timezone.now().date() - timedelta(days=1))
        end_date = kwargs.get('end_date', timezone.now().date() + timedelta(days=1))
        custom_data = kwargs.get('custom_data', None)

        # check start_date and end_date if dd/MM/YYYY or YYYY-MM-DD
        if isinstance(start_date, str):
            start_date = parse_date(start_date)
        if isinstance(end_date, str):
            end_date = parse_date(end_date)

        if associate_data is None and associate is not None:
            associate_data = {
                'first_name': associate.first_name,
                'last_name': associate.last_name,
                'tax_code': associate.tax_code,
            }
        elif associate_data is None and associate is None:
            raise ValidationError("associate_data or associate must be provided")

        # Build the query
        query = Q(sport_association=sport_association,
                  associate__first_name__iexact=associate_data.get('first_name'),
                  associate__last_name__iexact=associate_data.get('last_name'),
                  associate__tax_code__iexact=associate_data.get('tax_code'),
                  end_date__gte=start_date,
                  start_date__lte=end_date,
                  archived=False)

        # Add custom data check if applicable
        if custom_data is not None and custom_data.get('type_of_associate'):
            query &= Q(custom_data__icontains=custom_data['type_of_associate'])

        # Check if subscription exists
        if self.filter(query).exists():
            return self.filter(query).first()
        return None

    def subscription_exists(self, **kwargs):
        # for arg in kwargs:
        #     logger.info(f"Arg: {arg} -> type: {isinstance(kwargs[arg], type)}, value: {kwargs[arg]}")
        # Extract relevant fields for checking
        sport_association = kwargs.get('sport_association')
        associate_data = kwargs.get('associate_data', None)
        associate = kwargs.get('associate', None)
        start_date = kwargs.get('start_date', timezone.now().date() - timedelta(days=1))
        end_date = kwargs.get('end_date', timezone.now().date() + timedelta(days=1))
        custom_data = kwargs.get('custom_data', None)

        # check start_date and end_date if dd/MM/YYYY or YYYY-MM-DD
        if isinstance(start_date, str):
            start_date = parse_date(start_date)
        if isinstance(end_date, str):
            end_date = parse_date(end_date)

        if associate_data is None and associate is not None:
            associate_data = {
                'first_name': associate.first_name,
                'last_name': associate.last_name,
                'tax_code': associate.tax_code,
            }
        elif associate_data is None and associate is None:
            raise ValidationError("associate_data or associate must be provided")

        # Build the query
        query = Q(sport_association=sport_association,
                  associate__first_name__iexact=associate_data.get('first_name'),
                  associate__last_name__iexact=associate_data.get('last_name'),
                  associate__tax_code__iexact=associate_data.get('tax_code'),
                  end_date__gte=start_date,
                  start_date__lte=end_date,
                  archived=False)

        # Add custom data check if applicable
        if custom_data is not None and custom_data.get('type_of_associate'):
            query &= Q(custom_data__icontains=custom_data['type_of_associate'])

        # Check if subscription exists
        return self.filter(query).exists()


class Subscription(GroupModelMixin):
    """
    This model contains information about a Subscription
    """

    NOT_SIGNED = 1
    PENDING = 2
    REJECTED = 3
    ACCEPTED = 4
    RESIGNED = 5

    STATUS_FLAG = (
        (NOT_SIGNED, 'non firmata'),
        (PENDING, 'in attesa'),
        (REJECTED, 'rifiutata'),
        (ACCEPTED, 'accettata'),
        (RESIGNED, 'ritirata')
    )

    ASSOCIATE_ONLY = 1  # solo socio
    ASSOCIATE_AND_MEMBER = 2  # socio e tesserato
    MEMBER_ONLY = 3  # solo tesserato

    TYPE = (
        (ASSOCIATE_ONLY, 'associato'),
        (ASSOCIATE_AND_MEMBER, 'associato e tesserato'),
        (MEMBER_ONLY, 'tesserato')
    )

    SOCIO_ORDINARIO = 1
    CONSIGLIERE = 2
    SEGRETARIO = 3
    VICE_PRESIDENTE = 4
    PRESIDENTE = 5
    SOCIO_VOLONTARIO = 6
    SOCIO_SOSTENITORE = 7
    SOCIO_TESORIERE = 8

    ASSOCIATE_ROLE = (
        (SOCIO_ORDINARIO, 'Socio ordinario'),
        (CONSIGLIERE, 'Consigliere'),
        (SEGRETARIO, 'Segretario'),
        (VICE_PRESIDENTE, 'Vice Presidente'),
        (PRESIDENTE, 'Presidente'),
        (SOCIO_VOLONTARIO, 'Socio volontario'),
        (SOCIO_SOSTENITORE, 'Socio sostenitore'),
        (SOCIO_TESORIERE, 'Tesoriere'),
    )

    objects = SubscriptionManager()

    subscription_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    associate = models.ForeignKey(Associate, on_delete=models.SET_NULL, null=True)  # used for associate payments
    supplier = models.ForeignKey(SupplierAndCustomers, on_delete=models.SET_NULL, null=True)  # used for supplier payments
    instructor = models.ForeignKey(Instructor, on_delete=models.SET_NULL, null=True)  # used for instructor payments
    # custom_data
    custom_data = models.JSONField(null=True)
    # additional fields
    additional_fields = models.JSONField(null=True)

    user = models.ForeignKey(User, on_delete=models.CASCADE)

    # associate or member types
    type = models.PositiveSmallIntegerField(choices=TYPE, default=ASSOCIATE_AND_MEMBER)
    # association role
    role = models.PositiveSmallIntegerField(choices=ASSOCIATE_ROLE, default=SOCIO_ORDINARIO)

    # TODO: before reaching the v0.160.0 - Introduce the following fields
    # acceptance date
    acceptance_date = models.DateField(null=True)
    # resignation date
    resignation_date = models.DateField(null=True)

    medical = models.ForeignKey(MedicalCertificate, on_delete=models.SET_NULL, null=True)
    status_flag = models.PositiveSmallIntegerField(choices=STATUS_FLAG, default=1)
    creation_date = models.DateTimeField(default=timezone.now)
    # default to the creation date
    start_date = models.DateField(null=True)
    # default 1 year from the creation date
    end_date = models.DateField(null=True)
    # S3 URL for signature image stored on CDN
    signature_url = models.CharField(max_length=1024, null=True, blank=True, help_text='S3 URL for signature image')
    document_pdf = models.ForeignKey(Document, on_delete=models.SET_NULL, null=True)
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True)
    deleted = models.BooleanField(null=False, default=False)
    archived = models.BooleanField(null=False, default=False)
    draft = models.BooleanField(null=False, default=True)  # for instance, when imported from xls/csv
    trial = models.BooleanField(null=False, default=False)
    meta = models.JSONField(null=True)
    # can have multiple tags for the same subscription
    tags = models.ManyToManyField('Tags', blank=True)
    subscription_number = models.TextField(null=True, blank=False, default=0)
    subscription_type = models.CharField(max_length=255, null=True)
    group = models.ForeignKey('Group', on_delete=models.SET_NULL, null=True)
    deleted = models.BooleanField(default=False)
    competitive = models.BooleanField(default=False)

    notes = models.TextField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['sport_association']),
            models.Index(fields=['sport_association', 'type']),
            models.Index(fields=['sport_association', 'creation_date']),
            models.Index(fields=['start_date', 'end_date']),
            # Keep this one as it covers most query patterns
            models.Index(
                fields=['sport_association_id', 'archived', 'type', 'start_date', 'end_date', 'creation_date'],
                name='sub_full_search_idx'
            ),
            # Add specific index for general search
            models.Index(
                fields=['sport_association_id', 'archived'],
                name='sub_search_idx',
                include=['associate_id']  # Include for JOIN optimization
            ),
            # Add index for text search if used frequently
            models.Index(fields=['custom_data'], name='custom_data_idx'),
            models.Index(
                fields=['sport_association', 'associate', 'start_date', 'end_date', 'archived'],
                name='subscription_overlap_idx'
            ),
            models.Index(
                fields=['medical', 'archived'],
                name='medical_lookup_idx'
            ),
            models.Index(
                fields=['payment', 'archived'],
                name='payment_lookup_idx'
            ),
            models.Index(
                fields=['sport_association', 'status_flag', 'start_date', 'end_date'],
                name='status_period_idx'
            ),
            models.Index(
                fields=['associate', 'archived'],  # Can't index associate__born_date here
                name='associate_filtering_idx'
            ),
            models.Index(
                fields=['deleted', 'archived'],  # For soft delete filtering
                name='deletion_status_idx'
            ),
            # Index for renewal_available check (eliminates N+1 queries)
            models.Index(
                fields=['sport_association', 'start_date', 'archived'],
                name='sub_renewal_idx'
            ),
        ]
    def save(self, *args, **kwargs):
        # Overloading object on save to set default start and end date

        if isinstance(self.creation_date, str):
            self.creation_date = parse_date(self.creation_date)
            # convert to datetime
            self.creation_date = datetime.combine(self.creation_date, datetime.min.time())

        current_date_from, current_date_to = BalanceSheetData.get_range_from_year_and_starting_date(
            date=self.creation_date,
            starting_day=self.sport_association.user.subscription_start_day,
            starting_month=self.sport_association.user.subscription_start_month,
            user=self.sport_association.user
        )

        # set the empty fields based on the settings
        subscription_duration = self.sport_association.user.subscription_duration

        # it's written this way to make it more readable
        if not self.start_date:
            if subscription_duration == User.FULL_YEAR:
                self.start_date = self.creation_date.date()
            elif subscription_duration == User.LIKE_SEASON_YEAR:
                self.start_date = current_date_from.date()
            elif subscription_duration == User.LIKE_SEASON_YEAR_FROM_TIME:
                self.start_date = self.creation_date.date()

        if not self.end_date:
            if subscription_duration == User.FULL_YEAR:
                self.end_date = self.creation_date.date() + relativedelta(years=1)
            elif subscription_duration == User.LIKE_SEASON_YEAR:
                self.end_date = current_date_to.date()
            elif subscription_duration == User.LIKE_SEASON_YEAR_FROM_TIME:
                self.end_date = current_date_to.date()

        super().save(*args, **kwargs)

    def get_age(self):
        return self.associate.calculate_age()

    @cached_property
    def active(self):
        return True if self.current_year() == 'corrente' else False

    @cached_property
    def season(self):
        if self.start_date is None or self.end_date is None:
            return None
        # if the year is the same, return the year
        if self.start_date.year == self.end_date.year:
            return str(self.start_date.year)
        return f"{self.start_date.year}/{self.end_date.year}"

    def current_year(self):
        if self.start_date is None or self.end_date is None:
            return 'corrente'
        today = timezone.now().date()
        # 'corrente' if today is in between of current_date_from and current_date_to
        # 'precedente' if today is before current_date_from
        # 'successivo' if today is after current_date_to
        if self.start_date <= today <= self.end_date:
            return 'corrente'
        elif self.end_date < today:
            return 'precedente'
        else:
            return 'successivo'

    @cached_property
    def is_current(self):
        if self.start_date is None or self.end_date is None:
            return True
        today = timezone.now().date()
        return self.start_date <= today <= self.end_date

    @cached_property
    def is_next_year(self):
        if self.start_date is None or self.end_date is None:
            return False
        today = timezone.now().date()
        return today < self.start_date

    @cached_property
    def full_name_with_year(self):
        base_name = f"{self.associate.first_name} {self.associate.last_name}"

        if self.start_date is None and self.end_date is None:
            return base_name

        if self.start_date is None:
            return f"{base_name} ({self.end_date.year})"
        if self.end_date is None:
            return f"{base_name} ({self.start_date.year})"

        return f"{base_name} ({self.start_date.year}/{self.end_date.year})"

    def get_status_lang(self, lang='it'):
        lang_map = {
            'it': {
                1: 'non firmato',
                2: 'in attesa',
                3: 'rifiutato',
                4: 'accettato',
                5: 'archiviato'
            }
        }

        return lang_map[lang][self.status_flag]

    def get_period(self):
        '''
        If start_date and end_date are in the same year, return the year
        otherwise return the period in the format 'YY/YY'
        if start_date is None or end_date is None return the year of the one that is setted
        if both are None return ''
        '''
        if self.start_date is None and self.end_date is None:
            return ''

        if self.start_date is None and self.end_date is not None:
            return str(self.end_date.year)

        if self.start_date is not None and self.end_date is None:
            return str(self.start_date.year)

        if self.start_date.year == self.end_date.year:
            return str(self.start_date.year)

        if self.start_date.year != self.end_date.year:
            return f"{str(self.start_date.year)[2:]}/{str(self.end_date.year)[2:]}"

        return ''

    def get_courses_list(self):
        # get course subscription associates
        from application.models import CourseSubscription
        course_subscriptions = CourseSubscription.objects.filter(subscription=self, deleted=False).select_related('course')
        return ", ".join([cs.course.title for cs in course_subscriptions])

    def get_plain_medical_label(self):
        if self.get_age() < 6:
            remaining_days = self.associate.calculate_days_to_age()
            return f"Esente per {remaining_days} giorni"
        elif self.medical is None or self.medical.expiration_date is None:
            return 'Scadenza mancante'
        elif self.medical.expiration_date < timezone.now().date():
            return f"Scaduto da {(timezone.now().date() - self.medical.expiration_date).days} giorni"
        else:
            return f"{(self.medical.expiration_date - timezone.now().date()).days} giorni rimanenti"

    def get_medical_label(self):
        ag_tag = ''
        if self.medical is not None and self.medical.competitive_medical_certificate:
            ag_tag = '<span class="badge badge-info ml-1" title="Certificato Medico Agonistico">AG</span>'

        if self.get_age() < 6:
            remaining_days = self.associate.calculate_days_to_age()
            return f"<span class=\"text-success font-weight-boldest\">Esente per {remaining_days} giorni</span>{ag_tag}"
        elif self.medical is None or self.medical.expiration_date is None:
            return f'<span class="text-warning font-weight-boldest"></i>Scadenza mancante</span>{ag_tag}'
        elif self.medical.expiration_date < timezone.now().date():
            return f"<span class=\"text-danger font-weight-boldest\"><i class=\"ph-bold ph-first-aid-kit\"></i>Scaduto da {(timezone.now().date() - self.medical.expiration_date).days} giorni</span>{ag_tag}"
        else:
            return f"<span class=\"text-success font-weight-boldest\"><i class=\"ph-bold ph-first-aid-kit\"></i>{(self.medical.expiration_date - timezone.now().date()).days} giorni rimanenti</span>{ag_tag}"

    @property
    def get_signature(self):
        """
        Returns the S3 CDN URL for the signature image.
        For backward compatibility and quick checks.

        Returns:
            str: S3 CDN URL or None if no signature exists
        """
        return self.signature_url

    def get_signature_base64(self):
        """
        Fetches signature from S3 and returns as base64 data URI for PDF embedding.
        WARNING: This makes an HTTP request - only call when actually needed for PDF generation!

        Returns:
            str: Base64 data URI (data:image/png;base64,...) or None if no signature exists
        """
        if not self.signature_url:
            return None

        try:
            import requests

            # Fetch image from S3 CDN
            response = requests.get(self.signature_url, timeout=10)
            response.raise_for_status()

            # Convert to base64
            signature_base64 = base64.b64encode(response.content).decode('utf-8')

            # Return as data URI for embedding in HTML/PDF
            return f"data:image/png;base64,{signature_base64}"

        except Exception as e:
            logger.error(
                "Failed to fetch signature from S3 for PDF rendering",
                extra={
                    'subscription_id': str(self.subscription_id),
                    'signature_url': self.signature_url,
                    'error': str(e)
                },
                exc_info=True
            )
            return None

    def set_signature_from_base64(self, signature_data):
        """
        Upload base64 signature to S3 and set signature_url.

        Args:
            signature_data: Base64 encoded signature (with or without data URI prefix)

        Returns:
            str: The CDN URL of the uploaded signature

        Raises:
            ValueError: If signature_data is invalid or cannot be decoded
        """
        from core.settings import STORAGE_DIR, AWS_STORAGE_BUCKET_NAME, AWS_LOCATION

        if not signature_data:
            raise ValueError("signature_data cannot be empty")

        # Check if it's already a URL
        if signature_data.startswith('http://') or signature_data.startswith('https://'):
            logger.warning(
                "Signature data is already a URL, skipping S3 upload",
                extra={'subscription_id': str(self.subscription_id)}
            )
            self.signature_url = signature_data
            return signature_data

        # Extract base64 data (handle data URI format)
        if ',' in signature_data:
            # Format: "data:image/png;base64,iVBORw0KG..."
            _, sig_data = signature_data.split(',', 1)
        else:
            sig_data = signature_data

        # Decode base64
        try:
            decoded_signature = base64.b64decode(sig_data)
        except (binascii.Error, ValueError) as e:
            logger.error(
                "Failed to decode base64 signature",
                extra={'subscription_id': str(self.subscription_id), 'error': str(e)}
            )
            raise ValueError(f"Failed to decode base64 signature: {e}")

        # Generate storage path with timestamp for uniqueness
        timestamp = datetime.now().timestamp()
        storage_path = os.path.join(
            STORAGE_DIR,
            f'subscriptions/{self.subscription_id}/signature_{timestamp}.png'
        )

        # Upload to S3
        try:
            saved_path = default_storage.save(
                storage_path,
                ContentFile(decoded_signature)
            )

            # Set ACL to public-read
            s3_client = default_storage.connection.meta.client
            s3_client.put_object_acl(
                Bucket=AWS_STORAGE_BUCKET_NAME,
                Key=f"{AWS_LOCATION}/{saved_path}",
                ACL='public-read'
            )

            # Generate CDN URL
            cdn_url = os.path.join(
                'https://bakney-object-spaces.fra1.cdn.digitaloceanspaces.com',
                AWS_LOCATION,
                storage_path
            )

            # Set signature_url
            self.signature_url = cdn_url

            logger.info(
                "Signature uploaded to S3 successfully",
                extra={
                    'subscription_id': str(self.subscription_id),
                    'cdn_url': cdn_url
                }
            )

            return cdn_url

        except Exception as e:
            logger.error(
                "Failed to upload signature to S3",
                extra={'subscription_id': str(self.subscription_id), 'error': str(e)},
                exc_info=True
            )
            raise


class SubscriptionMembership(models.Model):

    subscription_membership_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, null=True)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    creation_date = models.DateTimeField(default=timezone.now)
    start_date = models.DateField(null=True)
    end_date = models.DateField(null=True)
    membership_type = models.CharField(max_length=255, null=True)
    membership_number = models.CharField(max_length=50, null=True, blank=True, default=0)
    description = models.CharField(max_length=255, null=True, blank=True)
    price = models.DecimalField(max_digits=9, decimal_places=2, blank=True, null=True)
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True)
    attached_membership_documents = models.ManyToManyField(Document, related_name='attached_membership_documents', blank=True)
    associate = models.ForeignKey(Associate, on_delete=models.SET_NULL, null=True)

    @property
    def paid(self):
        if self.payment is not None:
            return self.payment.paid

        return None

    def get_period(self):
        '''
        If start_date and end_date are in the same year, return the year
        otherwise return the period in the format 'YY/YY'
        if start_date is None or end_date is None return the year of the one that is setted
        if both are None return ''
        '''
        if self.start_date is None and self.end_date is None:
            return ''

        if self.start_date is None and self.end_date is not None:
            return str(self.end_date.year)

        if self.start_date is not None and self.end_date is None:
            return str(self.start_date.year)

        if self.start_date.year == self.end_date.year:
            return str(self.start_date.year)

        if self.start_date.year != self.end_date.year:
            return f"{str(self.start_date.year)[2:]}/{str(self.end_date.year)[2:]}"

        return ''


class NotificationTemplates(models.Model):

    EMAIL = 1
    SMS = 2

    NOTIFICATION_TYPE = (
        (EMAIL, 'email'),
        (SMS, 'sms')
    )

    notification_template_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    template_name = models.CharField(max_length=255)
    template = models.TextField()
    type = models.PositiveSmallIntegerField(choices=NOTIFICATION_TYPE, default=1)
    creation_date = models.DateTimeField(default=timezone.now)


class ProductsAndServices(GroupModelMixin):

    PRODUCT = 1
    SERVICE = 2

    PRODUCT_TYPE = (
        (PRODUCT, 'product'),
        (SERVICE, 'service')
    )

    product_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    product_sku = models.CharField(max_length=255)
    product_name = models.CharField(max_length=255)
    product_description = models.TextField(default='', null=True, blank=True)
    product_price = models.DecimalField(max_digits=9, decimal_places=2, default=0.00, null=True, blank=True)
    type = models.PositiveSmallIntegerField(choices=PRODUCT_TYPE, default=1)
    creation_date = models.DateTimeField(default=timezone.now)
    sku_in_receipt = models.BooleanField(default=False)
    name_in_receipt = models.BooleanField(default=False)
    description_in_receipt = models.BooleanField(default=False)
    stock_quantity = models.PositiveIntegerField(default=0)
    group = models.ForeignKey('Group', on_delete=models.SET_NULL, null=True)
    deleted = models.BooleanField(default=False)


class SoldProducts(models.Model):

    sold_product_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, null=True)
    supplier = models.ForeignKey(SupplierAndCustomers, on_delete=models.SET_NULL, null=True)
    product = models.ForeignKey(ProductsAndServices, on_delete=models.CASCADE, blank=True, default=None)
    quantity = models.PositiveIntegerField(default=1)
    creation_date = models.DateTimeField(default=timezone.now)
    selling_date = models.DateTimeField(default=timezone.now)
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True)


class Material(models.Model):

    material_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    info = models.CharField(max_length=255, null=True)


class SubscriptionToken(models.Model):
    """
    This model contains information about a Subscription Token
    """

    subscription_token_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE)
    token = models.UUIDField(default=uuid.uuid4)
    creation_date = models.DateTimeField(default=timezone.now)
    expiration_date = models.DateTimeField(default=timezone.now)


class SubscriptionFile(models.Model):

    subscription_file_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE)
    document = models.ForeignKey(Document, on_delete=models.CASCADE)
    creation_date = models.DateTimeField(default=timezone.now)


class Tags(models.Model):
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


class Signature(models.Model):
    signature_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    signature = models.TextField()


class AssociateTrial(models.Model):

    associate_trial_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    data = models.JSONField(null=True)
    valid = models.BooleanField(null=True, default=False)

    class Meta:
        indexes = [
            models.Index(fields=['associate_trial_id', 'sport_association']),
        ]


class AssociateImportDraft(models.Model):
    """
    This model contains the imported a draft of the associate imported from file
    """

    associate_import_draft_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, null=True)
    data = models.JSONField(null=True)
    valid = models.BooleanField(null=True, default=False)

    class Meta:
        indexes = [
            models.Index(fields=['associate_import_draft_id', 'sport_association']),
        ]


class AssociateImportDraftStatus(models.Model):
    IMPORTING = 1
    APPROVING = 2

    STATUS_TYPE = (
        (IMPORTING, 'importing'),
        (APPROVING, 'approving'),
    )

    associate_import_draft_status_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, null=True)
    doing = models.BooleanField(null=True, default=True)
    status_type = models.PositiveSmallIntegerField(choices=STATUS_TYPE, default=1)
    creation_date = models.DateTimeField(default=timezone.now)


class SignatureRequest(object):
    def __init__(self, there_is_signature, data):
        self.there_is_signature = there_is_signature  # pragma: no cover
        self.data = data  # pragma: no cover


def default_expires_at():
    # Set the expiration delta as per your requirement (e.g., 30 days).
    return timezone.now() + timedelta(days=30)


class SubscriptionTransfer(models.Model):
    PENDING = 1
    ACCEPTED = 2
    REJECTED = 3
    EXPIRED = 4

    STATUS_CHOICES = (
        (PENDING, 'Pending'),
        (ACCEPTED, 'Accepted'),
        (REJECTED, 'Rejected'),
        (EXPIRED, 'Expired'),
    )

    subscription_transfer_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    subscription = models.ForeignKey('Subscription', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_expires_at)
    requester = models.ForeignKey('User', related_name='requester', on_delete=models.CASCADE)
    recipient = models.ForeignKey('User', related_name='recipient', on_delete=models.SET_NULL, null=True, blank=True)
    token = models.UUIDField(default=uuid.uuid4, unique=True)
    status = models.PositiveSmallIntegerField(choices=STATUS_CHOICES, default=1)

    class Meta:
        indexes = [
            models.Index(fields=['subscription_transfer_id', 'subscription']),
        ]

    def is_expired(self):
        return self.expires_at <= timezone.now()


class CumulativeSubscriptionGymLinks(models.Model):
    cumulative_subscription_gym_links_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    token = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    creation_date = models.DateTimeField(default=timezone.now)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    gym_name = models.CharField(max_length=255, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['cumulative_subscription_gym_links_id', 'sport_association']),
        ]


auditlog.register(MedicalCertificate)
auditlog.register(MedicalAppointments)
auditlog.register(Subscription)
auditlog.register(Signature)
auditlog.register(AssociateImportDraft)
auditlog.register(SubscriptionTransfer)
auditlog.register(SubscriptionMembership)
auditlog.register(NotificationTemplates)
auditlog.register(SoldProducts)
auditlog.register(Material)
auditlog.register(SubscriptionToken)
auditlog.register(SubscriptionFile)
auditlog.register(Tags)
auditlog.register(AssociateTrial)
auditlog.register(AssociateImportDraftStatus)
auditlog.register(CumulativeSubscriptionGymLinks)
