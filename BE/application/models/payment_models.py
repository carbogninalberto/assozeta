import uuid

from auditlog.registry import auditlog
from django.db import models
from django.db.utils import cached_property
from django.utils import timezone
from django.db.models import Q

from application.mixin import GroupModelMixin, GroupAwareManager
from application.models.balance_sheet_models import CustomAccounts
from application.models.invoices_models import Invoice
from application.models.user_models import User, SportAssociation, Associate, Instructor
from docmanager.models import Document


class SupplierAndCustomers(GroupModelMixin):

    # types are:
    # - supplier
    # - customer
    # - pa
    # - other
    # - vip
    # - product

    supplier_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=255, null=False)
    address = models.CharField(max_length=255, null=True, blank=True)
    tax_code = models.CharField(max_length=30, null=True, blank=True)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, null=True)
    vat_number = models.CharField(max_length=30, null=True, blank=True)
    type = models.CharField(max_length=30, null=True, default='supplier')
    email = models.EmailField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    cap = models.CharField(max_length=10, null=True, blank=True)
    state = models.CharField(max_length=50, null=True, blank=True)
    country = models.CharField(max_length=50, null=True, blank=True, default='Italia')
    nationality = models.CharField(max_length=100, null=True, blank=True, default='Italiana')
    note = models.TextField(null=True, blank=True)
    group = models.ForeignKey('Group', on_delete=models.SET_NULL, null=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['supplier_id', 'sport_association']),
        ]


class PaymentManager(GroupAwareManager):
    # exclude the soft deleted payments
    def get_queryset(self):
        return super().get_queryset().filter(deleted=False)

    def create(self, **kwargs):
        user = kwargs.get('user')
        if user:
            kwargs['custom_accounts'] = Payment.get_default_bank_account(kwargs['sport_association'])
        return super().create(**kwargs)


class Payment(GroupModelMixin):

    OTHER = 0
    SUBSCRIPTION = 1
    COURSE = 2
    ACCOUNT_TRANSFER = 3


    # PAYMENT_TYPES
    DEFAULT = 'default'
    CASH = 'cash'
    TRANSFER = 'transfer'
    ONLINE = 'online'
    SEPA_TRANSFER = 'sepa-transfer'
    STRIPE = 'stripe'
    POS = 'pos'

    TYPE_CHOICES = (
        ('default', 'DEFAULT'),
        ('cash', 'CONTANTI'),
        ('transfer', 'BONIFICO'),
        ('online', 'ONLINE'),
        ('sepa-transfer', 'BONIFICO SEPA'),
        ('stripe', 'STRIPE'),
        ('pos', 'POS')
    )

    SUBJECT = (
        (OTHER, 'Altro'),
        (SUBSCRIPTION, 'Iscrizione'),
        (COURSE, 'Corso'),
        (ACCOUNT_TRANSFER, 'Giroconto')
    )

    payment_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    type = models.CharField(choices=TYPE_CHOICES, max_length=13, default=TYPE_CHOICES[0][0])
    subject = models.PositiveSmallIntegerField(choices=SUBJECT, default=0)
    amount = models.DecimalField(max_digits=9, decimal_places=2, default=0.00)
    creation_date = models.DateTimeField(default=timezone.now)
    payment_date = models.DateTimeField(null=True)
    paid = models.BooleanField(default=False)
    # True if it is an expense, False if it is an income
    expense = models.BooleanField(default=False)
    description = models.CharField(max_length=5120, null=True)
    # payment category
    payment_category = models.ForeignKey('PaymentCategory', on_delete=models.SET_NULL, null=True)
    # used to add more payment categories and their amount and subject
    # example: [{'payment_category_id': 1, 'amount': 10.00, 'subject': 0}]
    meta_payment_categories = models.JSONField(null=True, blank=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True)

    # payment owner (one of the following)
    associate = models.ForeignKey(Associate, on_delete=models.CASCADE, null=True)  # used for associate payments
    supplier = models.ForeignKey(SupplierAndCustomers, on_delete=models.SET_NULL, null=True)  # used for supplier payments
    instructor = models.ForeignKey(Instructor, on_delete=models.SET_NULL, null=True)  # used for instructor payments
    # TODO: write migration to migrate current data to the new field
    # subscription = models.ForeignKey('Subscription', on_delete=models.SET_NULL, null=True)

    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, null=True)
    payment_intent_id = models.CharField(max_length=125, null=True)
    archived = models.BooleanField(null=False, default=False)
    invoice_fiscal_code = models.CharField(max_length=125, null=True)
    meta = models.JSONField(null=True)
    notes = models.CharField(max_length=5120, null=True, blank=True)
    # bank/wallet account
    custom_accounts = models.ForeignKey(CustomAccounts, on_delete=models.CASCADE, null=True)
    signature = models.ForeignKey('Signature', on_delete=models.SET_NULL, null=True)
    deleted = models.BooleanField(default=False)
    group = models.ForeignKey('Group', on_delete=models.SET_NULL, null=True)
    # course = models.ForeignKey('Course', on_delete=models.SET_NULL, null=True)

    course = models.JSONField(null=True, blank=True)

    attachments = models.ManyToManyField(Document, related_name='attachments', blank=True)


    objects = PaymentManager()

    class Meta:
        indexes = [
            models.Index(fields=['payment_id', 'sport_association']),
            models.Index(fields=['payment_id', 'sport_association', 'creation_date'], name='payment_creation_date_idx'),
            models.Index(fields=['payment_id', 'sport_association', 'payment_date'], name='payment_payment_date_idx'),
            models.Index(fields=['payment_id', 'sport_association', 'paid'], name='payment_paid_idx'),
            models.Index(fields=['payment_id', 'sport_association', 'expense'], name='payment_expense_idx'),
            # New optimized composite indexes for payment_stats endpoint
            models.Index(fields=['sport_association', 'archived', 'payment_date'], name='payment_stats_pay_date_idx'),
            models.Index(fields=['sport_association', 'archived', 'creation_date'], name='payment_stats_cre_date_idx'),
            models.Index(fields=['sport_association', 'archived', 'paid', 'expense'], name='payment_stats_filter_idx'),
            models.Index(fields=['custom_accounts', 'paid'], name='payment_account_paid_idx'),
            # Partial index for active payments
            models.Index(
                fields=['sport_association', 'payment_date'],
                name='active_payments_idx',
                condition=Q(deleted=False, archived=False)
            ),

            # Covering index (avoids table lookups)
            models.Index(
                fields=['sport_association', 'paid', 'amount'],
                name='payment_summary_idx',
                include=['payment_date', 'description']  # PostgreSQL 11+
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name='payment_amount_positive'
            ),
        ]

    def create(self, *args, **kwargs):
        if self.user is not None and self.custom_accounts is None:
            self.custom_accounts = self.get_default_bank_account(self.sport_association)
        super().create(*args, **kwargs)

    def save(self, *args, **kwargs):

        # payment category, set default to PaymentCategory.objects.filter(
        #             name__iexact='entrate e proventi da attività tipiche').first(),
        # NOTE: in order to make default_payment_categories customizable we can duplicate the
        # defaults one and add the sport_association
        if self.payment_category is None:
            try:
                self.payment_category = PaymentCategory.objects.filter(
                    name__iexact='entrate e proventi da attività tipiche').first()
            except PaymentCategory.DoesNotExist:
                pass
        if self.description is None:
            description = self.get_course_carnet_name()

            if description is None and self.subject == 2:  # it's a course
                description = "Quota Corso"
            self.description = description

        if self.description and self.description.lower() == 'quota corso':
            # check if meta contains description and set it to it
            if self.meta and 'description' in self.meta:
                self.description = self.meta['description']

        if self.pk is None:  # This is a new object
            if self.user is not None and self.custom_accounts is None:
                self.custom_accounts = self.get_default_bank_account(self.sport_association)
        super().save(*args, **kwargs)

    @cached_property
    def get_subscription(self):
        # reverse engineer the subscription from the payment
        from application.models import CourseSubscriptionInstallment, Subscription, CourseSubscription, \
            CampsAndRetreatsSubscriptionPeriod
        from application.models.carnet_models import CarnetSubscription
        installment = CourseSubscriptionInstallment.objects.filter(payment=self).select_related(
            'course_subscription',
            'course_subscription__subscription',
        ).first()
        if installment is not None:
            return installment.course_subscription.subscription

        subscription = Subscription.objects.filter(payment=self).first()
        if subscription is not None:
            return subscription

        carnet = CarnetSubscription.objects.filter(payment=self).select_related('subscription').first()
        if carnet is not None:
            return carnet.subscription

        course = CourseSubscription.objects.filter(payment=self).select_related('subscription').first()
        if course is not None:
            return course.subscription

        camp_and_retreats = CampsAndRetreatsSubscriptionPeriod.objects.filter(payment=self).select_related(
            'camps_and_retreats_subscription__subscription',
        ).first()
        if camp_and_retreats is not None:
            return camp_and_retreats.camps_and_retreats_subscription.subscription
        return None

    @staticmethod
    def get_default_bank_account(sport_association):
        # get user sport association
        custom_account = CustomAccounts.objects.filter(
            sport_association=sport_association,
            editable=False,
            enabled=True,
            account_type=CustomAccounts.BANK).first()
        return custom_account

    def get_type_in_lang(self, lang='it'):
        lang_map = {
            'it': {
                'default': 'Non specificato',
                'cash': 'Contanti',
                'transfer': 'Bonifico',
                'online': 'Online',
                'sepa-transfer': 'Bonifico SEPA',
                'stripe': 'Stripe',
                'pos': 'POS'
            }
        }

        return lang_map[lang][self.type]

    def get_course_carnet_name(self):
        if self.subject == Payment.COURSE:
            # Local import to avoid circular dependency
            from application.models.courses_models import CourseSubscription, CourseSubscriptionInstallment
            from application.models.carnet_models import CarnetSubscription

            # look for payment in CourseSubscriptions
            course_subscription = CourseSubscription.objects.all_objects().filter(payment=self).first()
            if course_subscription:
                if course_subscription.payment and \
                    course_subscription.payment.meta and \
                    'description' in course_subscription.payment.meta:
                    return course_subscription.payment.meta['description']
                return course_subscription.course.title
            else:
                # look for payment in CourseSubscriptionInstallment
                course_subscription_installment = CourseSubscriptionInstallment.objects.all_objects().filter(payment=self).first()
                if course_subscription_installment:
                    if course_subscription_installment.payment and \
                        course_subscription_installment.payment.meta and \
                        'description' in course_subscription_installment.payment.meta:
                        return course_subscription_installment.payment.meta['description']
                    return course_subscription_installment.course_subscription.course.title
                else:
                    # look for payment in CarnetSubscription
                    carnet_subscription = CarnetSubscription.objects.filter(payment=self).first()
                    if carnet_subscription:
                        if carnet_subscription.payment and \
                            carnet_subscription.payment.meta and \
                            'description' in carnet_subscription.payment.meta:
                            return carnet_subscription.payment.meta['description']
                        return carnet_subscription.carnet_id.title

            if self.course and 'label' in self.course:
                return ", ".join([self.course['label'], self.description if self.description else ''])
            return self.description
        elif self.subject == Payment.SUBSCRIPTION:
            return 'Iscrizione'
        elif self.subject == Payment.OTHER:
            # use the payment category name
            return self.payment_category.name if self.payment_category else None
        elif self.subject == Payment.ACCOUNT_TRANSFER:
            return f"Trasferimento da {self.custom_accounts.name} a {self.custom_accounts.destination_account_name}"
        return None



class PaymentCategory(models.Model):

    INSTITUTIONAL = 1
    COMMERCIAL = 2

    TYPE = (
        (INSTITUTIONAL, 'Institutional'),
        (COMMERCIAL, 'Commercial')
    )

    payment_category_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=255, null=False)
    creation_date = models.DateTimeField(default=timezone.now)
    # if empty, it is a global category
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, null=True, blank=True)
    expense = models.BooleanField(default=False)
    tax_deductible = models.BooleanField(default=False)
    archived = models.BooleanField(null=False, default=False)
    type = models.PositiveSmallIntegerField(choices=TYPE, default=INSTITUTIONAL)
    vat_management = models.ForeignKey('VatManagement', on_delete=models.SET_NULL, null=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['payment_category_id', 'sport_association']),
        ]

    @classmethod
    def get_all_categories(cls, sport_association):
        return cls.objects.filter(Q(sport_association=sport_association) | Q(sport_association=None))


class VatManagement(models.Model):
    # IVA
    VARIE = 0
    IVA_ESENTE = 1
    IVA_ESCLUSA_EX_ART_15 = 2
    IVA_ESCLUSA_ART_4_DPR_633_72 = 3
    IVA_ESENTE_ART_10_C_1_N_20 = 4
    IVA_FUORI_CAMPO = 5
    IVA_NON_IMPONIBILE = 6
    IVA_NON_SOGGETTE = 7
    IVA_QUATTRO_PER_CENTO = 8
    IVA_CINQUE_PER_CENTO = 9
    IVA_SETTE_PER_CENTO = 10
    IVA_NOVE_PER_CENTO = 11
    IVA_DIECI_PER_CENTO = 12
    IVA_DICIANNOVE_PER_CENTO = 13
    IVA_VENTI_PER_CENTO = 14
    IVA_VENTUNO_PER_CENTO = 15
    IVA_VENTIDUE_PER_CENTO = 16

    # DETRAZIONE
    NO = 0
    DIECI_PER_CENTO = 1
    CINQUANTA_PER_CENTO = 2

    VAT = (
        (VARIE, 'Varie'),
        (IVA_ESENTE, 'IVA Esente'),
        (IVA_ESCLUSA_EX_ART_15, 'IVA Esclusa ex art. 15'),
        (IVA_ESCLUSA_ART_4_DPR_633_72, 'IVA Esclusa art. 4 DPR 633/72'),
        (IVA_ESENTE_ART_10_C_1_N_20, 'IVA Esente art. 10 c. 1 n. 20'),
        (IVA_FUORI_CAMPO, 'IVA Fuori campo'),
        (IVA_NON_IMPONIBILE, 'IVA Non imponibile'),
        (IVA_NON_SOGGETTE, 'IVA Non soggette'),
        (IVA_QUATTRO_PER_CENTO, 'IVA 4%'),
        (IVA_CINQUE_PER_CENTO, 'IVA 5%'),
        (IVA_SETTE_PER_CENTO, 'IVA 7%'),
        (IVA_NOVE_PER_CENTO, 'IVA 9%'),
        (IVA_DIECI_PER_CENTO, 'IVA 10%'),
        (IVA_DICIANNOVE_PER_CENTO, 'IVA 19%'),
        (IVA_VENTI_PER_CENTO, 'IVA 20%'),
        (IVA_VENTUNO_PER_CENTO, 'IVA 21%'),
        (IVA_VENTIDUE_PER_CENTO, 'IVA 22%')
    )

    DEDUCTION = (
        (NO, 'No'),
        (DIECI_PER_CENTO, '10%'),
        (CINQUANTA_PER_CENTO, '50%')
    )

    vat_management_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    vat = models.PositiveSmallIntegerField(choices=VAT, default=VARIE)
    deduction = models.PositiveSmallIntegerField(choices=DEDUCTION, default=NO)


auditlog.register(Payment)
auditlog.register(PaymentCategory)
auditlog.register(SupplierAndCustomers)
auditlog.register(VatManagement)
