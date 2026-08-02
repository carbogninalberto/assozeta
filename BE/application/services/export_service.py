"""
Association Data Export Service

This service exports all data belonging to a sport association to a ZIP file
that can be imported into a fresh Bakney instance.
"""
import hashlib
import json
import logging
import os
import tempfile
import uuid
import zipfile
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from django.core.files.storage import default_storage
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Model
from django.utils import timezone

from application.models import (
    BillingPlan,
)
from application.models.attendee_models import (
    AttendanceDay,
    AttendanceRegistry,
    GlobalCalendarEvents,
    Reminders,
)
from application.models.balance_sheet_models import BalanceSheet, CustomAccounts
from application.models.carnet_models import Carnet, CarnetSubscription
from application.models.courses_models import (
    CampsAndRetreats,
    CampsAndRetreatsPeriod,
    CampsAndRetreatsPeriodsService,
    CampsAndRetreatsSubscription,
    CampsAndRetreatsSubscriptionPeriod,
    Course,
    CourseLocation,
    CourseSubscription,
    CourseSubscriptionInstallment,
    CourseTags,
)
from application.models.invoices_models import (
    CustomerInvoice,
    Invoice,
    InvoiceRows,
    InvoiceSuppliers,
)
from application.models.modules_models import Module, ModuleResponses
from application.models.payment_models import (
    Payment,
    PaymentCategory,
    SupplierAndCustomers,
    VatManagement,
)
from application.models.subscriptions_models import (
    CumulativeSubscriptionGymLinks,
    MedicalAppointments,
    MedicalCertificate,
    NotificationTemplates,
    ProductsAndServices,
    Signature,
    SoldProducts,
    Subscription,
    SubscriptionFile,
    SubscriptionMembership,
    SubscriptionToken,
    SubscriptionTransfer,
    Tags,
)
from application.models.user_models import (
    Associate,
    AssociateTutorRelation,
    CollaborationInvites,
    EmailLog,
    Family,
    Folder,
    Group,
    Instructor,
    InstructorHours,
    PreviewAndCustomFeatures,
    Reminder,
    SportAssociation,
    SportAssociationDocumentsArchive,
    SportAssociationHeadquarter,
    SportAssociationInvoices,
    SportAssociationMaterial,
    SportAssociationMembershipCardConfiguration,
    SportAssociationModuleTemplates,
    User,
    UsersOnboarding,
)
from communications.models import (
    AutomationWorkflow,
    CommunicationConfiguration,
    Message,
    MessageTransaction,
)
from docmanager.models import Document

logger = logging.getLogger(__name__)


class ExportJSONEncoder(DjangoJSONEncoder):
    """Custom JSON encoder that handles additional types."""

    def default(self, obj):
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        return super().default(obj)


class AssociationExportService:
    """
    Service for exporting all association data to a ZIP file.

    The export includes:
    - All database records belonging to the association
    - All binary files from S3 (medical certificates, signatures, invoices)
    - System defaults (billing plans) needed for fresh install
    """

    # Export order matters for FK relationships
    # Format: (Model class, filename prefix, [optional: 'system' for system data])
    EXPORT_ORDER: List[Tuple] = [
        # System defaults (needed for fresh install)
        (BillingPlan, '00_billing_plans', 'system'),

        # Tier 1: Root entity
        (SportAssociation, '01_sport_association'),

        # Tier 2: Users (association owner and collaborators)
        (User, '02_users'),
        (Group, '03_groups'),
        (UsersOnboarding, '04_users_onboarding'),
        (CollaborationInvites, '05_collaboration_invites'),

        # Tier 3: Core entities
        (Family, '06_families'),
        (Associate, '07_associates'),
        (AssociateTutorRelation, '08_associate_tutor_relations'),

        # Tier 4: Configuration
        (VatManagement, '09_vat_management'),  # Must be before PaymentCategory (FK reference)
        (PaymentCategory, '10_payment_categories'),
        (CustomAccounts, '11_custom_accounts'),
        (Tags, '12_tags'),
        (CourseTags, '13_course_tags'),
        (Folder, '14_folders'),

        # Tier 5: Suppliers and products
        (SupplierAndCustomers, '15_suppliers_and_customers'),
        (ProductsAndServices, '16_products_and_services'),

        # Tier 6: Association configuration
        (SportAssociationHeadquarter, '17_headquarters'),
        (SportAssociationMaterial, '18_materials'),
        (SportAssociationModuleTemplates, '19_module_templates'),
        (SportAssociationMembershipCardConfiguration, '20_membership_card_config'),
        (CommunicationConfiguration, '21_communication_config'),
        (NotificationTemplates, '22_notification_templates'),

        # Tier 7: Instructors
        (Instructor, '23_instructors'),
        (InstructorHours, '24_instructor_hours'),

        # Tier 8: Subscriptions
        (Subscription, '25_subscriptions'),
        (SubscriptionMembership, '26_subscription_memberships'),
        (SubscriptionToken, '27_subscription_tokens'),
        (SubscriptionFile, '28_subscription_files'),
        (MedicalCertificate, '29_medical_certificates'),
        (MedicalAppointments, '30_medical_appointments'),

        # Tier 9: Courses
        (CourseLocation, '31_course_locations'),
        (Course, '32_courses'),
        (CourseSubscription, '33_course_subscriptions'),
        (CourseSubscriptionInstallment, '34_course_subscription_installments'),

        # Tier 10: Carnets
        (Carnet, '35_carnets'),
        (CarnetSubscription, '36_carnet_subscriptions'),

        # Tier 11: Camps
        (CampsAndRetreats, '37_camps_and_retreats'),
        (CampsAndRetreatsPeriod, '38_camps_periods'),
        (CampsAndRetreatsPeriodsService, '39_camps_period_services'),
        (CampsAndRetreatsSubscription, '40_camps_subscriptions'),
        (CampsAndRetreatsSubscriptionPeriod, '41_camps_subscription_periods'),

        # Tier 12: Payments and signatures
        (Signature, '42_signatures'),
        (Payment, '43_payments'),

        # Tier 13: Invoices
        (Invoice, '44_invoices'),
        (InvoiceRows, '45_invoice_rows'),
        (InvoiceSuppliers, '46_invoice_suppliers'),
        (CustomerInvoice, '47_customer_invoices'),
        (SportAssociationInvoices, '48_sport_association_invoices'),

        # Tier 14: Balance sheet
        (BalanceSheet, '49_balance_sheets'),

        # Tier 15: Attendance
        (AttendanceRegistry, '50_attendance_registries'),
        (AttendanceDay, '51_attendance_days'),

        # Tier 16: Communications
        (Message, '52_messages'),
        (MessageTransaction, '53_message_transactions'),
        (AutomationWorkflow, '54_automation_workflows'),
        (Reminder, '55_reminders'),
        (EmailLog, '56_email_logs'),

        # Tier 17: Calendar and events
        (GlobalCalendarEvents, '57_global_calendar_events'),
        (Reminders, '58_event_reminders'),

        # Tier 18: Modules
        (Module, '59_modules'),
        (ModuleResponses, '60_module_responses'),

        # Tier 19: Additional subscription-related models
        (SoldProducts, '61_sold_products'),
        (SubscriptionTransfer, '62_subscription_transfers'),
        (CumulativeSubscriptionGymLinks, '63_cumulative_gym_links'),

        # Tier 20: System-like models
        (PreviewAndCustomFeatures, '64_preview_features', 'system'),

        # Tier 21: Documents archive
        (SportAssociationDocumentsArchive, '65_documents_archive'),

        # Tier 22: Documents (must be LAST - document_ids are collected during other model exports)
        (Document, '99_documents'),
    ]

    # Fields to exclude from export (sensitive data)
    EXCLUDED_FIELDS = {
        'User': ['password', 'two_fa_secret', 'stripe_account_id', 'integration_google_credentials'],
        'Payment': ['payment_intent_id'],
        'CommunicationConfiguration': ['smtp_password'],
    }

    # Models with soft delete - need to use original_objects or all_objects
    # Note: The get_queryset_for_model method now auto-detects deleted field,
    # but we keep this set for documentation and explicit handling
    SOFT_DELETE_MODELS = {
        'User', 'SportAssociation', 'Associate', 'Subscription',
        'Course', 'CourseSubscription', 'CourseSubscriptionInstallment',
        'Payment', 'PaymentCategory', 'SupplierAndCustomers', 'ProductsAndServices',
        'Invoice',  # Has deleted field
    }

    # ManyToMany field mappings for export
    # Format: {model_name: [m2m_field_name, ...]}
    M2M_FIELDS: Dict[str, List[str]] = {
        'Course': ['tags', 'locations'],
        'Subscription': ['tags'],
        'SubscriptionMembership': ['attached_membership_documents'],
        'Invoice': ['attached_documents'],
        'Payment': ['attachments'],
        'Instructor': ['documents'],
        'CourseLocation': ['documents'],
        'ModuleResponses': ['attachments'],
        'CarnetSubscription': ['course_subscription'],
        'CourseSubscription': ['membership_payments'],
        'CampsAndRetreatsPeriod': ['camps_and_Retreats_period_services'],
        'CampsAndRetreatsSubscriptionPeriod': ['camps_and_retreats_period_services'],
        'User': ['preview_and_custom_features'],
    }

    def __init__(self, sport_association_id: uuid.UUID):
        """
        Initialize the export service.

        Args:
            sport_association_id: UUID of the association to export
        """
        self.sport_association_id = sport_association_id
        self.sport_association = SportAssociation.original_objects.get(
            sport_association_id=sport_association_id
        )
        self.document_ids: Set[uuid.UUID] = set()
        self.stats: Dict[str, int] = {}
        self.errors: List[str] = []

    def get_queryset_for_model(self, model_class: type, is_system: bool = False) -> models.QuerySet:
        """
        Get the appropriate queryset for a model, including soft-deleted records.

        Args:
            model_class: The Django model class
            is_system: Whether this is system data (not filtered by association)

        Returns:
            QuerySet filtered appropriately
        """
        model_name = model_class.__name__

        # Use appropriate manager to include soft-deleted records
        # Check if model has a 'deleted' field (soft-delete capability)
        has_deleted_field = any(
            f.name == 'deleted' for f in model_class._meta.get_fields()
            if hasattr(f, 'name')
        )

        if has_deleted_field or model_name in self.SOFT_DELETE_MODELS:
            if hasattr(model_class, 'original_objects'):
                # Pattern 1: original_objects is unfiltered manager (User, SportAssociation)
                qs = model_class.original_objects.all()
            elif hasattr(model_class.objects, 'all_objects') and callable(getattr(model_class.objects, 'all_objects')):
                # Pattern 2: all_objects() is method on default manager (Course, CourseSubscription)
                qs = model_class.objects.all_objects()
            else:
                # Pattern 3: deleted field but no custom manager - use _base_manager to bypass filtering
                qs = model_class._base_manager.all()
        else:
            qs = model_class.objects.all()

        # System data is not filtered by association
        if is_system:
            return qs

        # Filter by association
        return self._filter_by_association(model_class, qs)

    def _filter_by_association(self, model_class: type, qs: models.QuerySet) -> models.QuerySet:
        """Filter queryset by association based on model relationships."""
        model_name = model_class.__name__

        # User model - get owner, collaborators, athlete users, and users referenced by signatures
        # Must check before generic sport_association check (User has reverse relation from SportAssociation)
        if model_name == 'User':
            owner = self.sport_association.user
            # Get athlete user IDs from subscriptions
            athlete_user_ids = Subscription.objects.filter(
                sport_association=self.sport_association
            ).values_list('user_id', flat=True).distinct()
            # Get user IDs from signatures (must match Signature export filter exactly)
            # Note: Signature model only has user FK (no associate FK)
            signature_user_ids = Signature.objects.filter(
                models.Q(user=owner) |
                models.Q(user__connected_user=owner) |
                models.Q(user_id__in=athlete_user_ids)
            ).values_list('user_id', flat=True).distinct()
            return qs.filter(
                models.Q(user_id=owner.user_id) |
                models.Q(connected_user=owner) |
                models.Q(user_id__in=athlete_user_ids) |
                models.Q(user_id__in=signature_user_ids)
            )

        # PaymentCategory - include association's categories AND shared categories referenced by Payments
        # Must be BEFORE generic sport_association filter
        if model_name == 'PaymentCategory':
            # Get category IDs referenced by this association's payments
            payment_cat_ids = Payment.objects.filter(
                sport_association=self.sport_association,
                payment_category__isnull=False
            ).values_list('payment_category_id', flat=True).distinct()
            return qs.filter(
                models.Q(sport_association=self.sport_association) |
                models.Q(payment_category_id__in=payment_cat_ids)
            )

        # Direct sport_association FK (check it's a real FK field, not a reverse relation)
        if hasattr(model_class, 'sport_association'):
            # Verify it's a forward FK field by checking _meta
            try:
                field = model_class._meta.get_field('sport_association')
                if field.is_relation and (field.many_to_one or field.one_to_one):
                    return qs.filter(sport_association=self.sport_association)
            except Exception:
                pass

        # MedicalCertificate - linked via subscriptions
        if model_name == 'MedicalCertificate':
            # Get medical certificate IDs linked to subscriptions of this association
            medical_ids = Subscription.objects.filter(
                sport_association=self.sport_association,
                medical__isnull=False
            ).values_list('medical_id', flat=True).distinct()
            return qs.filter(medical_id__in=medical_ids)

        # Signature - include signatures from owner, collaborators, and subscription users
        if model_name == 'Signature':
            owner = self.sport_association.user
            # Get athlete user IDs from subscriptions
            athlete_user_ids = Subscription.objects.filter(
                sport_association=self.sport_association
            ).values_list('user_id', flat=True).distinct()
            # Signature model only has user FK (no associate FK)
            return qs.filter(
                models.Q(user=owner) |
                models.Q(user__connected_user=owner) |
                models.Q(user_id__in=athlete_user_ids)
            )

        # Models with user FK (to association owner and collaborators)
        if hasattr(model_class, 'user'):
            owner = self.sport_association.user
            return qs.filter(
                models.Q(user=owner) |
                models.Q(user__connected_user=owner)
            )

        # Models related through subscription
        if model_name == 'SubscriptionMembership':
            return qs.filter(subscription__sport_association=self.sport_association)

        if model_name == 'SubscriptionToken':
            return qs.filter(subscription__sport_association=self.sport_association)

        if model_name == 'SubscriptionFile':
            return qs.filter(subscription__sport_association=self.sport_association)

        if model_name == 'MedicalAppointments':
            return qs.filter(subscription__sport_association=self.sport_association)

        # Models related through course
        if model_name == 'CourseSubscription':
            return qs.filter(course__sport_association=self.sport_association)

        if model_name == 'CourseSubscriptionInstallment':
            return qs.filter(course_subscription__course__sport_association=self.sport_association)

        if model_name == 'AttendanceRegistry':
            return qs.filter(course__sport_association=self.sport_association)

        if model_name == 'AttendanceDay':
            return qs.filter(attendance_registry__course__sport_association=self.sport_association)

        # Models related through carnet
        if model_name == 'CarnetSubscription':
            return qs.filter(carnet_id__sport_association=self.sport_association)

        # Models related through invoice
        if model_name == 'InvoiceRows':
            return qs.filter(invoice__sport_association=self.sport_association)

        # Models related through message
        if model_name == 'MessageTransaction':
            return qs.filter(message__sport_association=self.sport_association)

        # VatManagement - export records referenced by PaymentCategories used by this association
        if model_name == 'VatManagement':
            # Get category IDs referenced by this association's payments (including shared categories)
            payment_cat_ids = Payment.objects.filter(
                sport_association=self.sport_association,
                payment_category__isnull=False
            ).values_list('payment_category_id', flat=True).distinct()
            # Get VAT IDs from association's categories AND shared categories used by payments
            vat_ids = PaymentCategory.objects.filter(
                models.Q(sport_association=self.sport_association) |
                models.Q(payment_category_id__in=payment_cat_ids),
                vat_management__isnull=False
            ).values_list('vat_management_id', flat=True).distinct()
            return qs.filter(vat_management_id__in=vat_ids)

        # Models related through instructor
        if model_name == 'InstructorHours':
            return qs.filter(instructor__user=self.sport_association.user)

        # Family - through associates (include deleted associates)
        if model_name == 'Family':
            family_ids = Associate.objects.filter(
                sport_association=self.sport_association,
                family__isnull=False
            ).values_list('family_id', flat=True).distinct()
            return qs.filter(family_id__in=family_ids)

        # AssociateTutorRelation - both associate AND tutor must belong to this association
        if model_name == 'AssociateTutorRelation':
            associate_ids = Associate.objects.filter(
                sport_association=self.sport_association
            ).values_list('associate_id', flat=True)
            # Filter where BOTH associate and tutor are in our association
            return qs.filter(
                associate_id__in=associate_ids,
                tutor_id__in=associate_ids
            )

        # CampsAndRetreats - direct sport_association FK
        if model_name == 'CampsAndRetreats':
            return qs.filter(sport_association=self.sport_association)

        # CampsAndRetreatsPeriod - through camps_and_retreats
        if model_name == 'CampsAndRetreatsPeriod':
            camp_ids = CampsAndRetreats.objects.filter(
                sport_association=self.sport_association
            ).values_list('camps_and_retreats_id', flat=True)
            return qs.filter(camps_and_retreats_id__in=camp_ids)

        # CampsAndRetreatsPeriodsService - through period
        if model_name == 'CampsAndRetreatsPeriodsService':
            period_ids = CampsAndRetreatsPeriod.objects.filter(
                camps_and_retreats__sport_association=self.sport_association
            ).values_list('camps_and_retreats_period_id', flat=True)
            return qs.filter(camps_and_retreats_period_id__in=period_ids)

        # CampsAndRetreatsSubscription - through camps_and_retreats
        if model_name == 'CampsAndRetreatsSubscription':
            return qs.filter(camps_and_retreats__sport_association=self.sport_association)

        # CampsAndRetreatsSubscriptionPeriod - through subscription
        if model_name == 'CampsAndRetreatsSubscriptionPeriod':
            return qs.filter(
                camps_and_retreats_subscription__camps_and_retreats__sport_association=self.sport_association
            )

        # InvoiceSuppliers - direct sport_association FK
        if model_name == 'InvoiceSuppliers':
            return qs.filter(sport_association=self.sport_association)

        # SoldProducts - through subscription
        if model_name == 'SoldProducts':
            return qs.filter(subscription__sport_association=self.sport_association)

        # SubscriptionTransfer - through subscription
        if model_name == 'SubscriptionTransfer':
            return qs.filter(subscription__sport_association=self.sport_association)

        # CumulativeSubscriptionGymLinks - direct sport_association FK
        if model_name == 'CumulativeSubscriptionGymLinks':
            return qs.filter(sport_association=self.sport_association)

        # PreviewAndCustomFeatures - through User M2M (export features used by owner)
        if model_name == 'PreviewAndCustomFeatures':
            owner = self.sport_association.user
            return qs.filter(user=owner)

        # Document - collected separately
        if model_name == 'Document':
            return qs.filter(document_id__in=self.document_ids)

        logger.warning(f"No filter defined for model {model_name}, returning empty queryset")
        return qs.none()

    def serialize_record(self, obj: Model) -> Dict[str, Any]:
        """
        Serialize a model instance to a dictionary.

        Args:
            obj: Django model instance

        Returns:
            Dictionary representation of the model
        """
        model_name = obj.__class__.__name__
        excluded = self.EXCLUDED_FIELDS.get(model_name, [])

        data = {}
        for field in obj._meta.get_fields():
            # Skip reverse relations and excluded fields
            if field.one_to_many or field.many_to_many:
                continue
            if field.name in excluded:
                continue

            try:
                value = getattr(obj, field.name, None)

                # Handle FK and OneToOne fields - store the ID
                if field.is_relation and (field.many_to_one or field.one_to_one):
                    fk_id = getattr(obj, f'{field.name}_id', None)
                    data[f'{field.name}_id'] = str(fk_id) if fk_id else None
                else:
                    # Handle special types
                    if isinstance(value, uuid.UUID):
                        data[field.name] = str(value)
                    elif isinstance(value, Decimal):
                        data[field.name] = str(value)
                    elif isinstance(value, datetime):
                        data[field.name] = value.isoformat() if value else None
                    elif hasattr(value, 'date') and callable(getattr(value, 'isoformat', None)):
                        data[field.name] = value.isoformat() if value else None
                    elif isinstance(value, Model):
                        # Handle any remaining model instances (shouldn't normally happen)
                        pk = getattr(value, 'pk', None)
                        data[field.name] = str(pk) if pk else None
                    else:
                        data[field.name] = value
            except Exception as e:
                logger.warning(f"Error serializing field {field.name} on {model_name}: {e}")
                data[field.name] = None

        # Export M2M relationships
        if model_name in self.M2M_FIELDS:
            for m2m_field_name in self.M2M_FIELDS[model_name]:
                if hasattr(obj, m2m_field_name):
                    try:
                        m2m_manager = getattr(obj, m2m_field_name)
                        related_pks = list(m2m_manager.values_list('pk', flat=True))
                        data[f'_m2m_{m2m_field_name}'] = [str(pk) for pk in related_pks]
                    except Exception as e:
                        logger.warning(f"Error serializing M2M {m2m_field_name} on {model_name}: {e}")
                        data[f'_m2m_{m2m_field_name}'] = []

        # Collect document references for file export
        self._collect_document_references(obj, data)

        return data

    def _collect_document_references(self, obj: Model, data: Dict[str, Any]):
        """Collect all document IDs from the record for file export."""
        # Define all Document FK fields by their actual field names
        document_fk_fields = ['document_id', 'document_pdf_id', 'pdf_id']

        for field_name in document_fk_fields:
            doc_id = getattr(obj, field_name, None)
            if doc_id:
                self.document_ids.add(doc_id)

        # Define all Document M2M fields
        document_m2m_fields = [
            'documents',
            'attachments',
            'attached_documents',
            'attached_membership_documents',
        ]

        for field_name in document_m2m_fields:
            if hasattr(obj, field_name):
                try:
                    m2m_manager = getattr(obj, field_name)
                    for doc in m2m_manager.all():
                        self.document_ids.add(doc.document_id)
                except Exception:
                    pass

    def export_model_to_json(
        self,
        model_class: type,
        output_dir: str,
        filename_prefix: str,
        is_system: bool = False,
        chunk_size: int = 1000
    ) -> int:
        """
        Export a model's data to a JSON file.

        Args:
            model_class: Django model class to export
            output_dir: Directory to write the JSON file
            filename_prefix: Prefix for the output filename
            is_system: Whether this is system data
            chunk_size: Number of records to process at a time

        Returns:
            Number of records exported
        """
        model_name = model_class.__name__
        filename = f"{filename_prefix}.json"

        dest_dir = 'system' if is_system else 'data'
        filepath = os.path.join(output_dir, dest_dir, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        qs = self.get_queryset_for_model(model_class, is_system)
        count = 0

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('[')
            first = True

            for obj in qs.iterator(chunk_size=chunk_size):
                if not first:
                    f.write(',')

                record = self.serialize_record(obj)
                json.dump(record, f, cls=ExportJSONEncoder, ensure_ascii=False)
                first = False
                count += 1

            f.write(']')

        self.stats[model_name] = count
        logger.info(f"Exported {count} {model_name} records")
        return count

    def _get_document_filepath(self, doc: Document) -> Optional[str]:
        """
        Get the filepath for a document, reconstructing it if not set.

        Many documents have their filepath field not set but the file exists
        in storage following the pattern: {STORAGE_DIR}/{timestamp}/{document_id}/{filename}
        """
        from core.settings import STORAGE_DIR

        # If filepath is set, use it
        if doc.filepath:
            return doc.filepath

        # Try to reconstruct the filepath using the storage pattern
        if doc.creation_date and doc.filename:
            timestamp = str(doc.creation_date.timestamp())
            if STORAGE_DIR:
                reconstructed = f"{STORAGE_DIR}/{timestamp}/{doc.document_id}/{doc.filename}"
            else:
                reconstructed = f"{timestamp}/{doc.document_id}/{doc.filename}"

            if default_storage.exists(reconstructed):
                logger.info(f"Reconstructed filepath for document {doc.document_id}: {reconstructed}")
                return reconstructed

        return None

    def export_files(self, output_dir: str) -> int:
        """
        Export binary files from S3 to the output directory.

        Args:
            output_dir: Directory to write files to

        Returns:
            Number of files exported
        """
        files_dir = os.path.join(output_dir, 'files')
        os.makedirs(files_dir, exist_ok=True)

        count = 0
        failed = 0
        skipped = 0

        # Export documents
        documents = Document.objects.filter(document_id__in=self.document_ids)

        for doc in documents.iterator(chunk_size=100):
            filepath = self._get_document_filepath(doc)
            if not filepath:
                logger.debug(f"No filepath found for document {doc.document_id}")
                skipped += 1
                continue

            try:
                # Determine file category based on context
                category = self._categorize_document(doc)
                doc_dir = os.path.join(files_dir, category, str(doc.document_id))
                os.makedirs(doc_dir, exist_ok=True)

                # Download from S3
                if default_storage.exists(filepath):
                    with default_storage.open(filepath, 'rb') as src:
                        dest_path = os.path.join(doc_dir, doc.filename or 'file')
                        with open(dest_path, 'wb') as dest:
                            for chunk in iter(lambda: src.read(8192), b''):
                                dest.write(chunk)
                    count += 1
                else:
                    logger.warning(f"File not found in storage: {filepath}")
                    failed += 1
            except FileNotFoundError as e:
                # Don't report file not found errors (Errno 2) to user, just log them
                logger.warning(f"File not found for document {doc.document_id}: {e}")
                failed += 1
            except Exception as e:
                logger.error(f"Error exporting document {doc.document_id}: {e}")
                # Only report generic error without details
                self.errors.append(f"Failed to export document {doc.document_id}")
                failed += 1

        self.stats['files_exported'] = count
        self.stats['files_failed'] = failed
        self.stats['files_skipped'] = skipped
        logger.info(f"Exported {count} files, {failed} failed, {skipped} skipped (no filepath)")
        return count

    def _categorize_document(self, doc: Document) -> str:
        """Categorize a document based on its usage."""
        # Check if it's a medical certificate
        if MedicalCertificate.objects.filter(document=doc).exists():
            return 'medical_certificates'

        # Check if it's an invoice
        if Invoice.objects.filter(document_pdf=doc).exists():
            return 'invoices'

        # Check if it's a subscription file
        if SubscriptionFile.objects.filter(document=doc).exists():
            return 'subscription_documents'

        return 'general_documents'

    def create_manifest(self, output_dir: str) -> Dict[str, Any]:
        """
        Create the manifest file with export metadata.

        Args:
            output_dir: Directory where export files are located

        Returns:
            Manifest dictionary
        """
        manifest = {
            'version': '1.0.0',
            'export_format': 'bakney_sport_export_v1',
            'export_date': timezone.now().isoformat(),
            'source_environment': 'production',
            'association': {
                'sport_association_id': str(self.sport_association.sport_association_id),
                'denomination': self.sport_association.denomination,
                'tax_code': self.sport_association.tax_code,
            },
            'statistics': self.stats,
            'models_exported': [
                {
                    'name': item[0].__name__,
                    'count': self.stats.get(item[0].__name__, 0),
                    'file': f"{'system' if (len(item) > 2 and item[2] == 'system') else 'data'}/{item[1]}.json"
                }
                for item in self.EXPORT_ORDER
            ],
            'includes_deleted_records': True,
            'errors': self.errors if self.errors else None,
        }

        # Write manifest
        manifest_path = os.path.join(output_dir, 'manifest.json')
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        return manifest

    def create_zip(self, source_dir: str, zip_filename: str) -> str:
        """
        Create a ZIP file from the export directory.

        Args:
            source_dir: Directory containing export files
            zip_filename: Name for the ZIP file

        Returns:
            Path to the created ZIP file
        """
        zip_path = os.path.join(tempfile.gettempdir(), zip_filename)

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(source_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, source_dir)
                    zf.write(file_path, arcname)

        # Calculate checksum
        sha256 = hashlib.sha256()
        with open(zip_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)

        logger.info(f"Created ZIP file: {zip_path} (SHA256: {sha256.hexdigest()})")
        return zip_path

    def save_to_storage(self, zip_path: str, filename: str) -> Document:
        """
        Save the ZIP file to storage and create archive entry.

        Args:
            zip_path: Path to the local ZIP file
            filename: Filename for storage

        Returns:
            Created Document instance
        """
        from core.settings import STORAGE_DIR

        # Create document record
        document = Document.objects.create(
            filename=filename,
        )

        # Build storage path
        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        if STORAGE_DIR:
            storage_path = f"{STORAGE_DIR}/{timestamp}/{document.document_id}/{filename}"
        else:
            storage_path = f"exports/{timestamp}/{document.document_id}/{filename}"

        # Upload to storage
        with open(zip_path, 'rb') as f:
            saved_path = default_storage.save(storage_path, f)

        document.filepath = saved_path
        document.save()

        # Create archive entry
        SportAssociationDocumentsArchive.objects.create(
            sport_association=self.sport_association,
            document=document,
        )

        logger.info(f"Saved export to storage: {saved_path}")
        return document

    def export(self, include_files: bool = True) -> Document:
        """
        Perform the full export.

        Args:
            include_files: Whether to include binary files from S3

        Returns:
            Document instance for the created export file
        """
        logger.info(f"Starting export for association {self.sport_association_id}")

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create directory structure
            os.makedirs(os.path.join(temp_dir, 'data'), exist_ok=True)
            os.makedirs(os.path.join(temp_dir, 'system'), exist_ok=True)
            os.makedirs(os.path.join(temp_dir, 'files'), exist_ok=True)

            # Export all models in order
            for item in self.EXPORT_ORDER:
                model_class = item[0]
                filename_prefix = item[1]
                is_system = len(item) > 2 and item[2] == 'system'

                try:
                    self.export_model_to_json(
                        model_class,
                        temp_dir,
                        filename_prefix,
                        is_system=is_system
                    )
                except Exception as e:
                    logger.error(f"Error exporting {model_class.__name__}: {e}")
                    # Only report generic error without details
                    self.errors.append(f"Failed to export {model_class.__name__}")

            # Export binary files
            if include_files:
                self.export_files(temp_dir)

            # Create manifest
            self.create_manifest(temp_dir)

            # Create ZIP file
            timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
            zip_filename = f"export_{self.sport_association_id}_{timestamp}.zip"
            zip_path = self.create_zip(temp_dir, zip_filename)

            # Save to storage
            document = self.save_to_storage(zip_path, zip_filename)

            # Cleanup temp ZIP
            os.remove(zip_path)

            logger.info(f"Export completed: {document.document_id}")
            return document


def export_association(sport_association_id: uuid.UUID, include_files: bool = True) -> Document:
    """
    Convenience function to export an association.

    Args:
        sport_association_id: UUID of the association to export
        include_files: Whether to include binary files

    Returns:
        Document instance for the created export file
    """
    service = AssociationExportService(sport_association_id)
    return service.export(include_files=include_files)
