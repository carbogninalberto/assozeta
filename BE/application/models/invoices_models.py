import uuid

from auditlog.registry import auditlog
from django.db import models
from django.utils import timezone

from application.mixin import GroupModelMixin
from application.models.user_models import SportAssociation
from docmanager.models import Document


class InvoiceRows(models.Model):
    """
    This model contains information about a invoice row
    """

    invoice_row_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    invoice = models.ForeignKey('Invoice', on_delete=models.CASCADE)
    description = models.CharField(max_length=255, null=True)
    quantity = models.IntegerField(default=1)
    unit_price = models.DecimalField(max_digits=9, decimal_places=2)
    vat = models.DecimalField(max_digits=9, decimal_places=2)
    unit_of_measure = models.CharField(max_length=255, null=True)
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['invoice_row_id', 'invoice']),
        ]


class Invoice(GroupModelMixin):
    """
    This model contains information about a invoice
    """

    invoice_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    document_pdf = models.ForeignKey(Document, on_delete=models.SET_NULL, null=True)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    creation_date = models.DateTimeField(default=timezone.now)
    membership_fee = models.DecimalField(max_digits=9, decimal_places=2)
    activity_fee = models.DecimalField(max_digits=9, decimal_places=2)
    meta_payment_categories = models.JSONField(null=True, blank=True)
    description = models.CharField(max_length=255, null=True)
    number = models.IntegerField(null=True)
    archived = models.BooleanField(null=False, default=False)
    cancelled = models.BooleanField(null=False, default=False)
    selected_tutor = models.ForeignKey('Associate', on_delete=models.SET_NULL, null=True)
    meta = models.JSONField(null=True, blank=True)
    imported_from_associami = models.BooleanField(null=True, default=False)
    # attached documents
    attached_documents = models.ManyToManyField(Document, related_name='attached_documents', blank=True)
    group = models.ForeignKey('Group', on_delete=models.SET_NULL, null=True)
    deleted = models.BooleanField(default=False)

    @property
    def total_amount(self):
        return self.membership_fee + self.activity_fee

    class Meta:
        indexes = [
            models.Index(fields=['invoice_id', 'sport_association']),
            models.Index(fields=['invoice_id', 'sport_association', 'creation_date'], name='invoice_creation_date_idx'),
            models.Index(fields=['invoice_id', 'sport_association', 'number'], name='invoice_number_idx'),
            models.Index(fields=['invoice_id', 'sport_association', 'archived'], name='invoice_archived_idx'),
            # New optimized indexes for invoice_list endpoint
            models.Index(fields=['sport_association', 'archived', '-number', '-creation_date'], name='invoice_list_main_idx'),
            models.Index(fields=['sport_association', 'archived', 'membership_fee'], name='invoice_membership_idx'),
            models.Index(fields=['sport_association', 'archived', 'activity_fee'], name='invoice_activity_idx'),
        ]

class InvoiceSuppliers(models.Model):
    """
    This model contains information about a invoice suppliers
    """

    invoice_supplier_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    invoice_identifier = models.CharField(max_length=255, null=False)
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    payment_date = models.DateTimeField(null=True)
    expire_date = models.DateTimeField(null=True)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    payment = models.ForeignKey('Payment', on_delete=models.SET_NULL, null=True)
    paid = models.BooleanField(null=False, default=False)
    notes = models.CharField(max_length=255, null=True)
    document_pdf = models.ForeignKey(Document, on_delete=models.SET_NULL, null=True)
    supplier = models.ForeignKey('SupplierAndCustomers', on_delete=models.SET_NULL, null=True)


class CustomerInvoice(models.Model):
    """
    This model contains information about a customer invoice
    """

    PAYMENT_CONDITIONS = (
        ('TP01', 'Pagamento a rate'),
        ('TP02', 'Pagamento completo'),
        ('TP03', 'Anticipo'),
    )

    # PAYMENT MODALITIES
    PAYMENT_MODALITIES = (
        ('MP01', 'contanti'),
        ('MP02', 'assegno'),
        ('MP03', 'assegno circolare'),
        ('MP04', 'contanti presso Tesoreria'),
        ('MP05', 'bonifico'),
        ('MP06', 'vaglia cambiario'),
        ('MP07', 'bollettino bancario'),
        ('MP08', 'carta di pagamento'),
        ('MP09', 'RID'),
        ('MP10', 'RID utenze'),
        ('MP11', 'RID veloce'),
        ('MP12', 'RIBA'),
        ('MP13', 'MAV'),
        ('MP14', 'quietanza erario'),
        ('MP15', 'giroconto su conti di contabilità speciale'),
        ('MP16', 'domiciliazione bancaria'),
        ('MP17', 'domiciliazione postale'),
        ('MP18', 'bollettino di c/c postale'),
        ('MP19', 'SEPA Direct Debit'),
        ('MP20', 'SEPA Direct Debit CORE'),
        ('MP21', 'SEPA Direct Debit B2B'),
        ('MP22', 'Trattenuta su somme già riscosse'),
    )

    customer_invoice_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    prefix = models.CharField(max_length=10, default='FPR', null=True)
    number = models.IntegerField(null=True)
    fiscal_year = models.IntegerField(null=True)
    assignor_prefix_vat_number = models.CharField(max_length=3, default='IT')
    assignor_vat_number = models.CharField(max_length=20, null=True)
    assignor_tax_code = models.CharField(max_length=20, null=True)
    assignor_denomination = models.CharField(max_length=255, null=True)
    assignor_fiscal_regime = models.CharField(max_length=255, default='RF01')
    assignor_address = models.CharField(max_length=255, default='', null=False)
    assignor_postal_code = models.CharField(max_length=10, default='', null=False)
    assignor_city = models.CharField(max_length=255, default='', null=False)
    assignor_province = models.CharField(max_length=2, default='', null=False)
    assignor_country = models.CharField(max_length=2, default='IT')
    assignor_contact_email = models.CharField(max_length=255, blank=True, null=True)
    assignor_contact_phone = models.CharField(max_length=255, blank=True, null=True)
    assignor_fax = models.CharField(max_length=255, blank=True, null=True)

    transferor_prefix_vat_number = models.CharField(max_length=3, default='IT')
    transferor_vat_number = models.CharField(max_length=20, null=True)
    transferor_tax_code = models.CharField(max_length=20, null=True)
    transferor_denomination = models.CharField(max_length=255, null=True)
    transferor_fiscal_regime = models.CharField(max_length=255, default='RF01')
    transferor_address = models.CharField(max_length=255, default='', null=False)
    transferor_postal_code = models.CharField(max_length=10, default='', null=False)
    transferor_city = models.CharField(max_length=255, default='', null=False)
    transferor_province = models.CharField(max_length=2, default='', null=False)
    transferor_country = models.CharField(max_length=2, default='IT')
    transferor_contact_email = models.CharField(max_length=255, blank=True, null=True)
    transferor_contact_phone = models.CharField(max_length=255, blank=True, null=True)
    transferor_fax = models.CharField(max_length=255, blank=True, null=True)

    # e-invoice
    e_invoice = models.BooleanField(null=False, default=True)
    country_transmitter_prefix = models.CharField(max_length=3, default='IT')
    id_transmitter = models.CharField(max_length=20, null=True)
    transmitting_format = models.CharField(max_length=10, default='FPR12')
    document_type = models.CharField(max_length=10, default='TD01')
    currency = models.CharField(max_length=3, default='EUR')
    transmitting_date = models.DateField(null=True)
    causal = models.CharField(max_length=200, null=True)

    # lines format are {'description': 'desc', 'quantity': 1, 'unit_price': 1.0, 'vat': 22.0, unit_of_measure': 'unita'}
    lines = models.JSONField(null=True, blank=True)

    # payment condition "TP01", "TP02", "TP03" one of these choices
    payment_condition = models.CharField(max_length=10, null=True, choices=PAYMENT_CONDITIONS)
    # payment modality "MP01", "MP02", "MP03", "MP04", "MP05", "MP06", "MP07", "MP08", "MP09", "MP10", "MP11", "MP12",
    # "MP13", "MP14", "MP15", "MP16", "MP17", "MP18", "MP19", "MP20", "MP21", "MP22"
    payment_modality = models.CharField(max_length=10, null=True, choices=PAYMENT_MODALITIES)
    payment_total_amount = models.DecimalField(max_digits=9, decimal_places=2, default=0, null=False)
    payment_expiry_date = models.DateField(default=timezone.now, null=False)

    xml = models.TextField(null=True)
    pdf = models.ForeignKey(Document, on_delete=models.SET_NULL, null=True, related_name='customer_invoice_pdf')
    json = models.JSONField(null=True, blank=True)

    # other fields related to bakney
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    payment = models.ForeignKey('Payment', on_delete=models.SET_NULL, null=True)
    creation_date = models.DateTimeField(default=timezone.now)
    paid = models.BooleanField(null=False, default=False)
    transmitted = models.BooleanField(null=False, default=False)

    class Meta:
        unique_together = ('prefix', 'number', 'fiscal_year', 'sport_association')
        indexes = [
            models.Index(fields=['customer_invoice_id', 'sport_association'])
        ]

auditlog.register(Invoice)
auditlog.register(InvoiceSuppliers)
auditlog.register(InvoiceRows)
auditlog.register(CustomerInvoice)
