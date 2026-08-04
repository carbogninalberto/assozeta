"""
Association Data Import Service

This service imports association data from a ZIP export file into a fresh
Bakney instance.
"""
import json
import logging
import os
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple, Type

from django.contrib.auth.hashers import make_password
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
import django.db.utils
from django.db import connection, models, transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from application.models import BillingPlan
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


@dataclass
class ValidationResult:
    """Result of a validation check."""
    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_error(self, error: str):
        self.is_valid = False
        self.errors.append(error)

    def add_warning(self, warning: str):
        self.warnings.append(warning)


@dataclass
class ImportOptions:
    """Options for the import process."""
    owner_email: str
    owner_password: str
    preserve_uuids: bool = False
    skip_files: bool = False
    dry_run: bool = False


class AssociationImportService:
    """
    Service for importing association data from a ZIP export file.

    The import process:
    1. Validates the export file structure and manifest
    2. Creates a new owner user with provided credentials
    3. Imports all database records in dependency order
    4. Uploads binary files to the new storage location
    5. Updates file references to point to new locations
    """

    # Import order (matches export order with adjustments for FK dependencies)
    # Note: The import service uses manifest to look up actual filenames,
    # so these filenames are defaults that can be overridden by the manifest
    IMPORT_ORDER: List[tuple] = [
        # Skip system data during import (BillingPlan should already exist)
        # ('00_billing_plans.json', BillingPlan, 'system'),

        # Tier 1: Root entity
        ('01_sport_association.json', SportAssociation),

        # Tier 2: Users
        ('02_users.json', User),
        ('03_groups.json', Group),
        ('04_users_onboarding.json', UsersOnboarding),
        ('05_collaboration_invites.json', CollaborationInvites),

        # Tier 3: Core entities
        ('06_families.json', Family),
        ('07_associates.json', Associate),
        ('08_associate_tutor_relations.json', AssociateTutorRelation),

        # Tier 4: Configuration
        ('09_vat_management.json', VatManagement),  # Must be before PaymentCategory (FK reference)
        ('10_payment_categories.json', PaymentCategory),
        ('11_custom_accounts.json', CustomAccounts),
        ('12_tags.json', Tags),
        ('13_course_tags.json', CourseTags),
        ('14_folders.json', Folder),
        ('99_documents.json', Document),  # Must be before SubscriptionFile, MedicalCertificate, etc.

        # Tier 5: Suppliers and products
        ('15_suppliers_and_customers.json', SupplierAndCustomers),
        ('16_products_and_services.json', ProductsAndServices),

        # Tier 6: Association configuration
        ('17_headquarters.json', SportAssociationHeadquarter),
        ('18_materials.json', SportAssociationMaterial),
        ('19_module_templates.json', SportAssociationModuleTemplates),
        ('20_membership_card_config.json', SportAssociationMembershipCardConfiguration),
        ('21_communication_config.json', CommunicationConfiguration),
        ('22_notification_templates.json', NotificationTemplates),

        # Tier 7: Instructors
        ('23_instructors.json', Instructor),
        ('24_instructor_hours.json', InstructorHours),

        # Tier 7.5: Signatures and Payments (must be before Subscriptions which reference Payment)
        ('42_signatures.json', Signature),
        ('43_payments.json', Payment),

        # Tier 8: Subscriptions
        ('25_subscriptions.json', Subscription),
        ('26_subscription_memberships.json', SubscriptionMembership),
        ('27_subscription_tokens.json', SubscriptionToken),
        ('28_subscription_files.json', SubscriptionFile),
        ('29_medical_certificates.json', MedicalCertificate),
        ('30_medical_appointments.json', MedicalAppointments),

        # Tier 9: Courses
        ('31_course_locations.json', CourseLocation),
        ('32_courses.json', Course),
        ('33_course_subscriptions.json', CourseSubscription),
        ('34_course_subscription_installments.json', CourseSubscriptionInstallment),

        # Tier 10: Carnets
        ('35_carnets.json', Carnet),
        ('36_carnet_subscriptions.json', CarnetSubscription),

        # Tier 11: Camps
        ('37_camps_and_retreats.json', CampsAndRetreats),
        ('38_camps_periods.json', CampsAndRetreatsPeriod),
        ('39_camps_period_services.json', CampsAndRetreatsPeriodsService),
        ('40_camps_subscriptions.json', CampsAndRetreatsSubscription),
        ('41_camps_subscription_periods.json', CampsAndRetreatsSubscriptionPeriod),

        # Tier 13: Invoices
        ('44_invoices.json', Invoice),
        ('45_invoice_rows.json', InvoiceRows),
        ('46_invoice_suppliers.json', InvoiceSuppliers),
        ('47_customer_invoices.json', CustomerInvoice),
        ('48_sport_association_invoices.json', SportAssociationInvoices),

        # Tier 14: Balance sheet
        ('49_balance_sheets.json', BalanceSheet),

        # Tier 15: Attendance
        ('50_attendance_registries.json', AttendanceRegistry),
        ('51_attendance_days.json', AttendanceDay),

        # Tier 16: Communications
        ('52_messages.json', Message),
        ('53_message_transactions.json', MessageTransaction),
        ('54_automation_workflows.json', AutomationWorkflow),
        ('55_reminders.json', Reminder),
        ('56_email_logs.json', EmailLog),

        # Tier 17: Calendar and events
        ('57_global_calendar_events.json', GlobalCalendarEvents),
        ('58_event_reminders.json', Reminders),

        # Tier 18: Modules
        ('59_modules.json', Module),
        ('60_module_responses.json', ModuleResponses),

        # Tier 19: Additional subscription-related models
        ('61_sold_products.json', SoldProducts),
        ('62_subscription_transfers.json', SubscriptionTransfer),
        ('63_cumulative_gym_links.json', CumulativeSubscriptionGymLinks),

        # Tier 20: System-like models
        ('64_preview_features.json', PreviewAndCustomFeatures),

        # Tier 21: Documents archive
        ('65_documents_archive.json', SportAssociationDocumentsArchive),
    ]

    # Primary key field names for each model (complete mapping)
    PK_FIELDS: Dict[str, str] = {
        # User Models
        'User': 'user_id',
        'Group': 'group_id',
        'Associate': 'associate_id',
        'AssociateTutorRelation': 'associate_tutor_relation_id',
        'Family': 'family_id',
        'Instructor': 'instructor_id',
        'InstructorHours': 'instructor_hours_id',
        'SportAssociation': 'sport_association_id',
        'UsersOnboarding': 'users_onboarding_id',
        'CollaborationInvites': 'collaboration_invite_id',
        'SportAssociationHeadquarter': 'sport_association_headquarter_id',
        'SportAssociationModuleTemplates': 'sport_association_module_templates_id',
        'SportAssociationMaterial': 'sport_association_material_id',
        'SportAssociationMembershipCardConfiguration': 'sport_association_membership_card_configuration_id',
        'SportAssociationInvoices': 'sport_association_invoice_id',
        'SportAssociationDocumentsArchive': 'sport_association_documents_archive_id',
        'Reminder': 'reminder_id',
        'EmailLog': 'email_log_id',

        # Payment Models
        'Payment': 'payment_id',
        'PaymentCategory': 'payment_category_id',
        'SupplierAndCustomers': 'supplier_id',
        'VatManagement': 'vat_management_id',
        'Signature': 'signature_id',
        'ProductsAndServices': 'product_id',

        # Subscription Models
        'Subscription': 'subscription_id',
        'SubscriptionMembership': 'subscription_membership_id',
        'SubscriptionToken': 'subscription_token_id',
        'SubscriptionFile': 'subscription_file_id',
        'MedicalCertificate': 'medical_id',
        'MedicalAppointments': 'medical_appointments_id',
        'Tags': 'tag_id',

        # Course Models
        'Course': 'course_id',
        'CourseTags': 'tag_id',
        'CourseLocation': 'course_location_id',
        'CourseSubscription': 'course_subscription_id',
        'CourseSubscriptionInstallment': 'course_subscription_installment_id',
        'CampsAndRetreats': 'camps_and_retreats_id',
        'CampsAndRetreatsPeriod': 'camps_and_retreats_period_id',
        'CampsAndRetreatsPeriodsService': 'camps_and_retreats_period_service_id',
        'CampsAndRetreatsSubscription': 'camps_and_retreats_subscription_id',
        'CampsAndRetreatsSubscriptionPeriod': 'camps_and_retreats_subscription_period_id',

        # Invoice Models
        'Invoice': 'invoice_id',
        'InvoiceRows': 'invoice_row_id',
        'InvoiceSuppliers': 'invoice_supplier_id',
        'CustomerInvoice': 'customer_invoice_id',

        # Balance Sheet Models
        'BalanceSheet': 'balance_sheet_id',
        'CustomAccounts': 'custom_account_id',
        'CustomAccountsTransfer': 'custom_account_transfer_id',

        # Carnet Models
        'Carnet': 'carnet_id',
        'CarnetSubscription': 'carnet_subscription_id',

        # Attendance Models
        'AttendanceRegistry': 'attendance_registry_id',
        'AttendanceDay': 'attendance_day_id',
        'GlobalCalendarEvents': 'global_calendar_id',
        'Reminders': 'reminders_id',

        # Module Models
        'Module': 'module_id',
        'ModuleResponses': 'module_response_id',

        # Communication Models
        'CommunicationConfiguration': 'communication_configuration_id',
        'NotificationTemplates': 'notification_template_id',
        'Message': 'message_id',
        'MessageTransaction': 'message_transaction_id',
        'AutomationWorkflow': 'automation_workflow_id',

        # Document Manager
        'Document': 'document_id',
        'Folder': 'id',

        # Additional subscription-related models
        'SoldProducts': 'sold_product_id',
        'SubscriptionTransfer': 'subscription_transfer_id',
        'CumulativeSubscriptionGymLinks': 'cumulative_subscription_gym_links_id',

        # System-like models
        'PreviewAndCustomFeatures': 'preview_and_custom_features_id',
    }

    # Foreign key mappings: model -> list of (fk_field, related_model) (complete mapping)
    FK_MAPPINGS: Dict[str, List[tuple]] = {
        # User Models
        'User': [
            ('connected_user_id', 'User'),
            ('default_payment_category_id', 'PaymentCategory'),
        ],
        'SportAssociation': [('user_id', 'User')],
        'Group': [('sport_association_id', 'SportAssociation')],
        'Family': [('sport_association_id', 'SportAssociation')],
        'Associate': [
            ('sport_association_id', 'SportAssociation'),
            ('user_id', 'User'),
            ('group_id', 'Group'),
            ('family_id', 'Family'),
        ],
        'AssociateTutorRelation': [
            ('associate_id', 'Associate'),
            ('tutor_id', 'Associate'),
        ],
        'UsersOnboarding': [('user_id', 'User')],
        'CollaborationInvites': [
            ('user_id', 'User'),
            ('sport_association_id', 'SportAssociation'),
        ],
        'SportAssociationHeadquarter': [('sport_association_id', 'SportAssociation')],
        'SportAssociationModuleTemplates': [('sport_association_id', 'SportAssociation')],
        'SportAssociationMaterial': [('sport_association_id', 'SportAssociation')],
        'SportAssociationMembershipCardConfiguration': [('sport_association_id', 'SportAssociation')],
        'SportAssociationDocumentsArchive': [
            ('sport_association_id', 'SportAssociation'),
            ('document_id', 'Document'),
            ('folder_id', 'Folder'),
        ],
        'Reminder': [('user_id', 'User')],
        'EmailLog': [('user_id', 'User')],

        # Instructor Models
        'Instructor': [('user_id', 'User')],
        'InstructorHours': [
            ('instructor_id', 'Instructor'),
            ('payment_id', 'Payment'),
            ('document_id', 'Document'),
        ],

        # Subscription Models
        'Subscription': [
            ('sport_association_id', 'SportAssociation'),
            ('associate_id', 'Associate'),
            ('medical_id', 'MedicalCertificate'),
            ('document_pdf_id', 'Document'),
            ('payment_id', 'Payment'),
            ('group_id', 'Group'),
            ('user_id', 'User'),
        ],
        'SubscriptionMembership': [
            ('subscription_id', 'Subscription'),
            ('payment_id', 'Payment'),
        ],
        'SubscriptionToken': [('subscription_id', 'Subscription')],
        'SubscriptionFile': [
            ('subscription_id', 'Subscription'),
            ('document_id', 'Document'),
        ],
        'MedicalCertificate': [
            ('user_id', 'User'),
            ('document_id', 'Document'),
        ],
        'MedicalAppointments': [('medical_certificate_id', 'MedicalCertificate')],
        'Tags': [('sport_association_id', 'SportAssociation')],

        # Payment Models
        'Payment': [
            ('sport_association_id', 'SportAssociation'),
            ('user_id', 'User'),
            ('associate_id', 'Associate'),
            ('invoice_id', 'Invoice'),
            ('payment_category_id', 'PaymentCategory'),
            ('custom_accounts_id', 'CustomAccounts'),
            ('signature_id', 'Signature'),
            ('group_id', 'Group'),
        ],
        'PaymentCategory': [
            ('sport_association_id', 'SportAssociation'),
            ('vat_management_id', 'VatManagement'),
        ],
        'Signature': [
            ('user_id', 'User'),
            ('associate_id', 'Associate'),
        ],
        'SupplierAndCustomers': [('sport_association_id', 'SportAssociation')],
        # VatManagement has no FKs - it's a shared lookup table
        'VatManagement': [],
        'ProductsAndServices': [('sport_association_id', 'SportAssociation')],

        # Course Models
        'Course': [
            ('sport_association_id', 'SportAssociation'),
            ('group_id', 'Group'),
        ],
        'CourseTags': [('sport_association_id', 'SportAssociation')],
        'CourseLocation': [('course_id', 'Course')],
        'CourseSubscription': [
            ('course_id', 'Course'),
            ('subscription_id', 'Subscription'),
            ('payment_id', 'Payment'),
        ],
        'CourseSubscriptionInstallment': [
            ('course_subscription_id', 'CourseSubscription'),
            ('payment_id', 'Payment'),
        ],

        # Camps and Retreats
        'CampsAndRetreats': [('sport_association_id', 'SportAssociation')],
        'CampsAndRetreatsPeriod': [('camps_and_retreats_id', 'CampsAndRetreats')],
        'CampsAndRetreatsPeriodsService': [('camps_and_retreats_period_id', 'CampsAndRetreatsPeriod')],
        'CampsAndRetreatsSubscription': [
            ('camps_and_retreats_id', 'CampsAndRetreats'),
            ('associate_id', 'Associate'),
        ],
        'CampsAndRetreatsSubscriptionPeriod': [
            ('camps_and_retreats_subscription_id', 'CampsAndRetreatsSubscription'),
            ('camps_and_retreats_period_id', 'CampsAndRetreatsPeriod'),
        ],

        # Invoice Models
        'Invoice': [
            ('sport_association_id', 'SportAssociation'),
            ('document_pdf_id', 'Document'),
            ('group_id', 'Group'),
        ],
        'InvoiceRows': [
            ('invoice_id', 'Invoice'),
            ('product_id', 'ProductsAndServices'),
        ],
        'CustomerInvoice': [
            ('sport_association_id', 'SportAssociation'),
            ('associate_id', 'Associate'),
        ],
        'InvoiceSuppliers': [
            ('sport_association_id', 'SportAssociation'),
            ('supplier_id', 'SupplierAndCustomers'),
        ],
        'SportAssociationInvoices': [
            ('sport_association_id', 'SportAssociation'),
            ('document_id', 'Document'),
        ],

        # Balance Sheet Models
        'BalanceSheet': [('sport_association_id', 'SportAssociation')],
        'CustomAccounts': [('sport_association_id', 'SportAssociation')],
        'CustomAccountsTransfer': [
            ('source_account_id', 'CustomAccounts'),
            ('destination_account_id', 'CustomAccounts'),
        ],

        # Carnet Models
        'Carnet': [
            ('sport_association_id', 'SportAssociation'),
            ('group_id', 'Group'),
        ],
        'CarnetSubscription': [
            ('carnet_id', 'Carnet'),
            ('subscription_id', 'Subscription'),
            ('course_subscription_id', 'CourseSubscription'),
            ('associate_id', 'Associate'),
            ('payment_id', 'Payment'),
        ],

        # Attendance Models
        'AttendanceRegistry': [
            ('sport_association_id', 'SportAssociation'),
            ('course_id', 'Course'),
        ],
        'AttendanceDay': [('attendance_registry_id', 'AttendanceRegistry')],
        'GlobalCalendarEvents': [('sport_association_id', 'SportAssociation')],
        'Reminders': [
            ('sport_association_id', 'SportAssociation'),
            ('user_id', 'User'),
            ('instructor_id', 'Instructor'),
        ],

        # Module Models
        'Module': [('sport_association_id', 'SportAssociation')],
        'ModuleResponses': [
            ('module_id', 'Module'),
            ('associate_id', 'Associate'),
        ],

        # Communication Models
        'CommunicationConfiguration': [('sport_association_id', 'SportAssociation')],
        'NotificationTemplates': [('sport_association_id', 'SportAssociation')],
        'Message': [('sport_association_id', 'SportAssociation')],
        'MessageTransaction': [
            ('message_id', 'Message'),
            ('associate_id', 'Associate'),
        ],
        'AutomationWorkflow': [('sport_association_id', 'SportAssociation')],

        # Document Manager
        'Document': [('folder_id', 'Folder')],
        'Folder': [
            ('sport_association_id', 'SportAssociation'),
            ('parent_id', 'Folder'),
        ],

        # Additional subscription-related models
        'SoldProducts': [
            ('subscription_id', 'Subscription'),
            ('supplier_id', 'SupplierAndCustomers'),
            ('product_id', 'ProductsAndServices'),
            ('payment_id', 'Payment'),
        ],
        'SubscriptionTransfer': [
            ('subscription_id', 'Subscription'),
            ('requester_id', 'User'),
            ('recipient_id', 'User'),
        ],
        'CumulativeSubscriptionGymLinks': [
            ('sport_association_id', 'SportAssociation'),
        ],

        # System-like models
        'PreviewAndCustomFeatures': [],  # No FKs, it's M2M to User
    }

    def __init__(self, zip_path: str, options: ImportOptions):
        """
        Initialize the import service.

        Args:
            zip_path: Path to the export ZIP file
            options: Import options
        """
        self.zip_path = zip_path
        self.options = options

        # UUID mapping: old_uuid -> new_uuid
        self.uuid_mapping: Dict[str, str] = {}

        # Track imported objects by model name
        self.imported_objects: Dict[str, Dict[str, Any]] = {}

        # Track which models have been imported (for FK resolution)
        self.imported_models: Set[str] = set()

        # The new association and owner
        self.owner_user: Optional[User] = None
        self.association: Optional[SportAssociation] = None

        # Document mapping: old_doc_id -> new_doc
        self.document_mapping: Dict[str, Document] = {}

        # Stats and errors
        self.stats: Dict[str, int] = {}
        self.errors: List[str] = []

        # Deferred FK resolution tracking
        # Format: {model_name: [(record_pk, fk_field, old_fk_value), ...]}
        self.deferred_fks: Dict[str, List[Tuple[str, str, str]]] = {}

        # Deferred M2M relationships to import after all records created
        # Format: {model_name: [(record_pk, m2m_field, [old_related_pks]), ...]}
        self.deferred_m2m: Dict[str, List[Tuple[str, str, List[str]]]] = {}

        # Model name to class mapping (built during import)
        self._model_class_cache: Dict[str, Type[models.Model]] = {}

    def validate(self) -> ValidationResult:
        """
        Validate the export file before importing.

        Returns:
            ValidationResult with any errors/warnings
        """
        result = ValidationResult()

        # Check file exists
        if not os.path.exists(self.zip_path):
            result.add_error(f"File not found: {self.zip_path}")
            return result

        # Check it's a valid ZIP
        if not zipfile.is_zipfile(self.zip_path):
            result.add_error(f"Not a valid ZIP file: {self.zip_path}")
            return result

        try:
            with zipfile.ZipFile(self.zip_path, 'r') as zf:
                # Check manifest exists
                if 'manifest.json' not in zf.namelist():
                    result.add_error("Missing manifest.json in export file")
                    return result

                # Read and validate manifest
                manifest = json.loads(zf.read('manifest.json'))

                # Check version
                if manifest.get('export_format') != 'bakney_sport_export_v1':
                    result.add_error(
                        f"Unsupported export format: {manifest.get('export_format')}"
                    )

                # Check required data files exist
                for filename, model_class in self.IMPORT_ORDER:
                    data_path = f"data/{filename}"
                    if data_path not in zf.namelist():
                        result.add_warning(f"Missing data file: {filename}")

                # Validate owner email doesn't exist (if not preserving UUIDs)
                if not self.options.preserve_uuids:
                    if User.objects.filter(email=self.options.owner_email).exists():
                        result.add_error(
                            f"User with email {self.options.owner_email} already exists"
                        )

        except json.JSONDecodeError as e:
            result.add_error(f"Invalid JSON in manifest: {e}")
        except Exception as e:
            result.add_error(f"Error validating export file: {e}")

        return result

    def _generate_uuid(self, old_uuid: str, model_name: str) -> str:
        """
        Generate or retrieve UUID for import.

        Args:
            old_uuid: Original UUID from export
            model_name: Name of the model

        Returns:
            UUID to use in import
        """
        if self.options.preserve_uuids:
            self.uuid_mapping[old_uuid] = old_uuid
            return old_uuid

        # Check if we already mapped this UUID
        if old_uuid in self.uuid_mapping:
            return self.uuid_mapping[old_uuid]

        # Generate new UUID
        new_uuid = str(uuid.uuid4())
        self.uuid_mapping[old_uuid] = new_uuid
        return new_uuid

    def _resolve_fk(self, old_uuid: Optional[str], related_model: str) -> Optional[Any]:
        """
        Resolve a foreign key reference to the new UUID.

        Args:
            old_uuid: Original FK UUID from export
            related_model: Name of the related model

        Returns:
            New UUID or None if not found
        """
        if old_uuid is None:
            return None

        imported_object = self.imported_objects.get(related_model, {}).get(str(old_uuid))
        if imported_object is not None and imported_object.pk is not None:
            return imported_object.pk

        if self.options.preserve_uuids:
            return self.uuid_mapping.get(old_uuid)

        return self.uuid_mapping.get(old_uuid)

    def _parse_value(self, value: Any, field: models.Field) -> Any:
        """
        Parse a value from JSON to the appropriate Python type.

        Args:
            value: Value from JSON
            field: Django model field

        Returns:
            Parsed value
        """
        if value is None:
            return None

        field_type = type(field).__name__

        if field_type == 'UUIDField':
            return uuid.UUID(value) if value else None
        elif field_type == 'DateTimeField':
            if isinstance(value, str):
                return parse_datetime(value)
            return value
        elif field_type == 'DateField':
            if isinstance(value, str):
                return parse_date(value)
            return value
        elif field_type == 'DecimalField':
            return Decimal(value) if value else None
        elif field_type == 'BooleanField':
            return bool(value)
        elif field_type in ('IntegerField', 'PositiveIntegerField', 'PositiveSmallIntegerField'):
            return int(value) if value is not None else None

        return value

    def _create_owner_user(self, zf: zipfile.ZipFile) -> User:
        """
        Create the new owner user for the association.

        Args:
            zf: Open ZIP file

        Returns:
            Created User instance
        """
        # Read original owner data
        users_data = json.loads(zf.read('data/02_users.json'))

        # Find the original owner (role = ASSOCIATION = 1)
        original_owner = None
        for user_data in users_data:
            if user_data.get('role') == 1:  # ASSOCIATION
                original_owner = user_data
                break

        if not original_owner:
            raise ValueError("No owner user found in export data")

        # Get old UUID
        old_user_id = original_owner.get('user_id')

        # Create new user
        new_user_id = self._generate_uuid(old_user_id, 'User')

        user = User(
            user_id=uuid.UUID(new_user_id),
            username=self.options.owner_email,
            email=self.options.owner_email,
            password=make_password(self.options.owner_password),
            role=User.ASSOCIATION,
            first_name=original_owner.get('first_name', ''),
            last_name=original_owner.get('last_name', ''),
            is_active=True,
            # Copy settings from original
            enumerate_invoices=original_owner.get('enumerate_invoices', False),
            online_payments=original_owner.get('online_payments', True),
            balance_sheet_year=original_owner.get('balance_sheet_year', 1),
            balance_sheet_start_day=original_owner.get('balance_sheet_start_day', 1),
            balance_sheet_start_month=original_owner.get('balance_sheet_start_month', 1),
            subscription_duration=original_owner.get('subscription_duration', 2),
            membership_duration=original_owner.get('membership_duration', 2),
            subscription_start_day=original_owner.get('subscription_start_day', 1),
            subscription_start_month=original_owner.get('subscription_start_month', 1),
            tables_settings=original_owner.get('tables_settings', {}),
        )

        if not self.options.dry_run:
            user.save()

        self.owner_user = user
        self.imported_objects.setdefault('User', {})[old_user_id] = user
        self.imported_models.add('User')
        self.stats['User'] = 1

        logger.info(f"Created owner user: {user.email}")
        return user

    def _import_sport_association(self, zf: zipfile.ZipFile) -> SportAssociation:
        """
        Import the sport association.

        Args:
            zf: Open ZIP file

        Returns:
            Created SportAssociation instance
        """
        assoc_data = json.loads(zf.read('data/01_sport_association.json'))

        if not assoc_data:
            raise ValueError("No association data found in export")

        # Get first (and only) association
        data = assoc_data[0]
        old_assoc_id = data.get('sport_association_id')
        new_assoc_id = self._generate_uuid(old_assoc_id, 'SportAssociation')

        association = SportAssociation(
            sport_association_id=uuid.UUID(new_assoc_id),
            user=self.owner_user,
            denomination=data.get('denomination', ''),
            address_city=data.get('address_city'),
            address=data.get('address'),
            address_cap=data.get('address_cap'),
            tax_code=data.get('tax_code', ''),
            email=data.get('email'),
            phone=data.get('phone'),
            regulation=data.get('regulation'),
            demand=data.get('demand'),
            additional_sections=data.get('additional_sections', {}),
            logo=data.get('logo'),
            subscription_fee=Decimal(data.get('subscription_fee', '0.00')),
            membership_fee=Decimal(data.get('membership_fee', '0.00')) if data.get('membership_fee') else None,
            configuration=data.get('configuration', {}),
            enabled_for=data.get('enabled_for'),
            invoice_template=data.get('invoice_template', 'invoice.html'),
            subscription_template=data.get('subscription_template', 'subscription.html'),
        )

        if not self.options.dry_run:
            association.save()

        self.association = association
        self.imported_objects.setdefault('SportAssociation', {})[old_assoc_id] = association
        self.imported_models.add('SportAssociation')
        self.stats['SportAssociation'] = 1

        logger.info(f"Created association: {association.denomination}")
        return association

    def _import_model_data(
        self,
        zf: zipfile.ZipFile,
        filename: str,
        model_class: Type[models.Model]
    ) -> int:
        """
        Import data for a single model.

        Args:
            zf: Open ZIP file
            filename: JSON filename in the ZIP
            model_class: Django model class

        Returns:
            Number of records imported
        """
        model_name = model_class.__name__
        data_path = f"data/{filename}"

        if data_path not in zf.namelist():
            logger.warning(f"Skipping {model_name}: file not found")
            return 0

        records = json.loads(zf.read(data_path))
        count = 0

        logger.info(f"Processing {model_name}: {len(records)} records to import")

        if model_class is Folder:
            return self._import_folders(records)

        for record in records:
            try:
                obj = self._create_model_instance(model_class, record)
                if obj and not self.options.dry_run:
                    obj.save()
                    count += 1
                    # Debug logging for User and Signature imports
                    if model_name == 'User':
                        logger.info(f"Saved User: {obj.pk} - {obj.email}")
                    elif model_name == 'Signature':
                        logger.info(f"Saved Signature: {obj.pk} - user_id={obj.user_id}")
                elif obj is None and model_name == 'User':
                    logger.info(f"Skipped User record (returned None): {record.get('user_id')}")
            except Exception as e:
                # Any error during import should abort - re-raise to trigger rollback
                logger.error(f"Error importing {model_name} record: {e}", exc_info=True)
                self.errors.append(f"Failed to import {model_name}: {e}")
                raise

        self.stats[model_name] = count
        # Mark this model as imported so FKs to it can be resolved
        self.imported_models.add(model_name)
        logger.info(f"Imported {count} {model_name} records")
        return count

    def _import_folders(self, records: List[Dict[str, Any]]) -> int:
        """Import folders parent-first and let MPTT generate its tree fields."""
        remaining = {}
        for record in records:
            old_pk = str(record.get('id'))
            if old_pk == 'None':
                raise ValueError("Folder record is missing its id")
            if old_pk in remaining:
                raise ValueError(f"Duplicate Folder id: {old_pk}")
            remaining[old_pk] = record

        imported = self.imported_objects.setdefault('Folder', {})
        count = 0

        while remaining:
            imported_in_pass = False

            for old_pk, record in list(remaining.items()):
                parent_old_pk = record.get('parent_id')
                parent = None
                if parent_old_pk is not None:
                    parent = imported.get(str(parent_old_pk))
                    if parent is None:
                        continue

                kwargs = {
                    'name': record.get('name', ''),
                    'parent': parent,
                    'sport_association': self.association,
                }
                if record.get('created_at'):
                    kwargs['created_at'] = parse_datetime(record['created_at'])

                folder = Folder(**kwargs)
                if not self.options.dry_run:
                    folder.save()
                    count += 1

                imported[old_pk] = folder
                del remaining[old_pk]
                imported_in_pass = True

            if not imported_in_pass:
                unresolved = ', '.join(sorted(remaining))
                raise ValueError(f"Folder hierarchy has missing or circular parents: {unresolved}")

        self.stats['Folder'] = count
        self.imported_models.add('Folder')
        logger.info(f"Imported {count} Folder records")
        return count

    def _create_model_instance(
        self,
        model_class: Type[models.Model],
        data: Dict[str, Any]
    ) -> Optional[models.Model]:
        """
        Create a model instance from exported data.

        Args:
            model_class: Django model class
            data: Record data from export

        Returns:
            Model instance (not saved)
        """
        model_name = model_class.__name__

        # Cache model class for later use
        self._model_class_cache[model_name] = model_class

        # Get primary key field and store old_pk before modification
        pk_field = self.PK_FIELDS.get(model_name)
        old_pk = None
        if pk_field and pk_field in data:
            old_pk = data[pk_field]
            new_pk = self._generate_uuid(old_pk, model_name)
            data[pk_field] = new_pk

        # Extract and defer M2M fields (they start with _m2m_)
        m2m_data = {}
        for key in list(data.keys()):
            if key.startswith('_m2m_'):
                m2m_field = key[5:]  # Remove '_m2m_' prefix
                m2m_data[m2m_field] = data.pop(key)

        # Store M2M for pass 3
        if m2m_data and old_pk:
            for m2m_field, related_pks in m2m_data.items():
                if related_pks:
                    self.deferred_m2m.setdefault(model_name, []).append(
                        (old_pk, m2m_field, related_pks)
                    )

        # Resolve foreign keys
        fk_mappings = dict(self.FK_MAPPINGS.get(model_name, []))
        for field in model_class._meta.get_fields():
            if field.is_relation and field.many_to_one:
                related_model = field.related_model.__name__
                if related_model in self.PK_FIELDS:
                    fk_mappings[field.attname] = related_model

        for fk_field, related_model in fk_mappings.items():
            if fk_field in data and data[fk_field]:
                # Skip FK if target model hasn't been imported yet
                # This handles circular dependencies (e.g., Payment.invoice_id -> Invoice)
                if related_model not in self.imported_models:
                    # DEFER instead of setting to NULL permanently
                    if old_pk:
                        self.deferred_fks.setdefault(model_name, []).append(
                            (old_pk, fk_field, data[fk_field])
                        )
                    logger.debug(f"Deferring {fk_field} -> {related_model} (not imported yet)")
                    data[fk_field] = None
                    continue
                old_fk = data[fk_field]
                new_fk = self._resolve_fk(old_fk, related_model)
                if new_fk is None:
                    # FK reference not found in mapping - the referenced record wasn't exported
                    logger.warning(
                        f"{model_name}.{fk_field} references {related_model} {old_fk} "
                        f"which was not in the export. Setting to NULL."
                    )
                    self.errors.append(
                        f"{model_name} references missing {related_model} {old_fk}"
                    )
                data[fk_field] = new_fk

        # Special handling for certain models
        if model_name == 'User':
            # Skip owner user (already created)
            if data.get('role') == 1:  # ASSOCIATION
                return None
            # Handle collaborators
            data['password'] = make_password(None)  # Set unusable password

        if model_name == 'Associate':
            # Resolve sport_association
            data['sport_association_id'] = str(self.association.sport_association_id)

        # Build kwargs for model creation
        kwargs = {}
        for field in model_class._meta.get_fields():
            if field.one_to_many or field.many_to_many:
                continue

            field_name = field.name
            if field.is_relation and field.many_to_one:
                # FK field - use the _id suffix
                fk_name = f'{field_name}_id'
                if fk_name in data:
                    value = data[fk_name]
                    if value:
                        kwargs[fk_name] = uuid.UUID(value) if isinstance(value, str) else value
            elif field_name in data:
                value = self._parse_value(data[field_name], field)
                kwargs[field_name] = value

        try:
            return model_class(**kwargs)
        except Exception as e:
            logger.error(f"Error creating {model_name}: {e}, data: {data}")
            raise

    def _import_files(self, zf: zipfile.ZipFile) -> int:
        """
        Import binary files from the export.

        Args:
            zf: Open ZIP file

        Returns:
            Number of files imported
        """
        if self.options.skip_files:
            logger.info("Skipping file import (--skip-files)")
            return 0

        from core.settings import STORAGE_DIR

        count = 0
        files_prefix = 'files/'

        for name in zf.namelist():
            if not name.startswith(files_prefix):
                continue

            # Parse path: files/<category>/<doc_id>/<filename>
            parts = name[len(files_prefix):].split('/')
            if len(parts) < 3:
                continue

            category, old_doc_id, filename = parts[0], parts[1], parts[2]

            try:
                # Get or create new document
                if old_doc_id in self.document_mapping:
                    doc = self.document_mapping[old_doc_id]
                else:
                    new_doc_id = self._generate_uuid(old_doc_id, 'Document')
                    # Check if Document was already imported from JSON
                    existing_doc = Document.objects.filter(document_id=new_doc_id).first()
                    if existing_doc:
                        doc = existing_doc
                    else:
                        doc = Document(
                            document_id=uuid.UUID(new_doc_id),
                            filename=filename,
                        )
                        if not self.options.dry_run:
                            doc.save()
                    self.document_mapping[old_doc_id] = doc

                # Build new storage path
                timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
                if STORAGE_DIR:
                    storage_path = f"{STORAGE_DIR}/{timestamp}/{doc.document_id}/{filename}"
                else:
                    storage_path = f"{timestamp}/{doc.document_id}/{filename}"

                # Upload to storage
                if not self.options.dry_run:
                    content = zf.read(name)
                    saved_path = default_storage.save(storage_path, ContentFile(content))
                    doc.filepath = saved_path
                    doc.save()

                count += 1

            except (
                django.db.utils.IntegrityError,
                django.db.utils.DatabaseError,
            ) as e:
                # Database errors abort the transaction - must re-raise
                logger.error(f"Database error importing file {name}: {e}")
                self.errors.append(f"Failed to import file {name}: {e}")
                raise
            except Exception as e:
                logger.error(f"Error importing file {name}: {e}")
                self.errors.append(f"Failed to import file {name}: {e}")

        self.stats['files_imported'] = count
        logger.info(f"Imported {count} files")
        return count

    def _resolve_deferred_fks(self):
        """
        Pass 2: Resolve all deferred foreign key relationships.

        This is called after all models have been imported in pass 1,
        so circular FK dependencies can now be resolved.
        """
        resolved_count = 0
        failed_count = 0

        for model_name, deferred_list in self.deferred_fks.items():
            model_class = self._model_class_cache.get(model_name)
            if not model_class:
                logger.warning(f"Model class not found for {model_name}, skipping deferred FKs")
                continue

            pk_field = self.PK_FIELDS.get(model_name)
            if not pk_field:
                logger.warning(f"PK field not found for {model_name}, skipping deferred FKs")
                continue

            for old_pk, fk_field, old_fk_value in deferred_list:
                # Get the new PK for this record
                new_pk = self.uuid_mapping.get(old_pk)
                if not new_pk:
                    logger.warning(f"Cannot resolve deferred FK: record {old_pk} not found in mapping")
                    failed_count += 1
                    continue

                # Get the new FK value
                new_fk_value = self.uuid_mapping.get(old_fk_value)
                if not new_fk_value:
                    logger.warning(f"Cannot resolve deferred FK {fk_field}: target {old_fk_value} not found")
                    failed_count += 1
                    continue

                # Update the record
                try:
                    if not self.options.dry_run:
                        model_class.objects.filter(**{pk_field: new_pk}).update(
                            **{fk_field: new_fk_value}
                        )
                    resolved_count += 1
                    logger.debug(f"Resolved {model_name}.{fk_field} = {new_fk_value}")
                except Exception as e:
                    logger.error(f"Failed to resolve deferred FK: {e}")
                    self.errors.append(f"Failed to resolve {model_name}.{fk_field}")
                    failed_count += 1

        self.stats['deferred_fks_resolved'] = resolved_count
        self.stats['deferred_fks_failed'] = failed_count
        logger.info(f"Resolved {resolved_count} deferred FKs, {failed_count} failed")

    def _import_m2m_relationships(self):
        """
        Pass 3: Import M2M relationships after all records exist.

        This is called after pass 1 (create records) and pass 2 (resolve FKs).
        """
        resolved_count = 0
        failed_count = 0

        for model_name, deferred_list in self.deferred_m2m.items():
            model_class = self._model_class_cache.get(model_name)
            if not model_class:
                logger.warning(f"Model class not found for {model_name}, skipping M2M")
                continue

            pk_field = self.PK_FIELDS.get(model_name)
            if not pk_field:
                logger.warning(f"PK field not found for {model_name}, skipping M2M")
                continue

            for old_pk, m2m_field, old_related_pks in deferred_list:
                # Get the new PK for this record
                new_pk = self.uuid_mapping.get(old_pk)
                if not new_pk:
                    logger.warning(f"Cannot import M2M: record {old_pk} not found in mapping")
                    failed_count += 1
                    continue

                try:
                    if self.options.dry_run:
                        resolved_count += 1
                        continue

                    instance = model_class.objects.get(**{pk_field: new_pk})
                    m2m_manager = getattr(instance, m2m_field, None)
                    if m2m_manager is None:
                        logger.warning(f"M2M field {m2m_field} not found on {model_name}")
                        failed_count += 1
                        continue

                    # Map old PKs to new PKs
                    new_related_pks = []
                    for old_related_pk in old_related_pks:
                        new_related_pk = self.uuid_mapping.get(old_related_pk)
                        if new_related_pk:
                            new_related_pks.append(new_related_pk)
                        else:
                            logger.debug(f"M2M related {old_related_pk} not found in mapping")

                    # Set the M2M relationship
                    if new_related_pks:
                        m2m_manager.set(new_related_pks)
                        logger.debug(f"Set {model_name}.{m2m_field} with {len(new_related_pks)} relations")
                    resolved_count += 1

                except model_class.DoesNotExist:
                    logger.warning(f"Record {new_pk} not found for M2M import")
                    failed_count += 1
                except Exception as e:
                    logger.error(f"Failed to import M2M {model_name}.{m2m_field}: {e}")
                    self.errors.append(f"M2M import failed: {model_name}.{m2m_field}")
                    failed_count += 1

        self.stats['m2m_resolved'] = resolved_count
        self.stats['m2m_failed'] = failed_count
        logger.info(f"Imported {resolved_count} M2M relationships, {failed_count} failed")

    @transaction.atomic
    def import_all(self) -> SportAssociation:
        """
        Perform the full import.

        Returns:
            The created SportAssociation instance
        """
        logger.info(f"Starting import from {self.zip_path}")

        with zipfile.ZipFile(self.zip_path, 'r') as zf:
            # Read manifest
            manifest = json.loads(zf.read('manifest.json'))
            logger.info(
                f"Importing association: {manifest['association']['denomination']}"
            )

            # Build model_name -> file mapping from manifest
            # This allows importing exports created with different file naming
            model_file_mapping = {}
            for model_info in manifest.get('models_exported', []):
                model_name = model_info.get('name')
                file_path = model_info.get('file')
                if model_name and file_path:
                    # Extract just the filename from the path (e.g., "data/09_vat_management.json" -> "09_vat_management.json")
                    if '/' in file_path:
                        filename = file_path.split('/')[-1]
                    else:
                        filename = file_path
                    model_file_mapping[model_name] = filename
                    logger.debug(f"Model {model_name} -> {filename}")

            # 1. Create owner user first
            self._create_owner_user(zf)

            # 2. Create sport association
            self._import_sport_association(zf)

            # 3. Import all other models in order
            # Use IMPORT_ORDER for the correct dependency order, but get filenames from manifest
            for _, model_class in self.IMPORT_ORDER:
                model_name = model_class.__name__

                # Skip SportAssociation (imported separately above)
                # Note: User is NOT skipped - only owner (role=1) is created above,
                # other users (athletes, collaborators) need to be imported here
                if model_name == 'SportAssociation':
                    continue

                # Get filename from manifest mapping, fall back to IMPORT_ORDER filename
                filename = model_file_mapping.get(model_name)
                if not filename:
                    # Fall back to finding in IMPORT_ORDER
                    for order_filename, order_model in self.IMPORT_ORDER:
                        if order_model.__name__ == model_name:
                            filename = order_filename
                            break

                if not filename:
                    logger.warning(f"No filename found for {model_name}, skipping")
                    continue

                try:
                    self._import_model_data(zf, filename, model_class)
                except Exception as e:
                    logger.error(f"Error importing {model_name}: {e}")
                    self.errors.append(f"Failed to import {model_name}: {e}")
                    raise  # Re-raise to trigger transaction rollback

        # Pass 2: Resolve deferred FKs
        logger.info("Pass 2: Resolving deferred foreign keys...")
        self._resolve_deferred_fks()

        # Pass 3: Import M2M relationships
        logger.info("Pass 3: Importing M2M relationships...")
        self._import_m2m_relationships()

        # Validate deferred database constraints before writing non-transactional files.
        connection.check_constraints()

        # Pass 4: Import files only after the database graph is valid.
        with zipfile.ZipFile(self.zip_path, 'r') as zf:
            self._import_files(zf)

        logger.info(f"Import completed: {self.association.denomination}")
        return self.association


def import_association(
    zip_path: str,
    owner_email: str,
    owner_password: str,
    preserve_uuids: bool = False,
    skip_files: bool = False,
    dry_run: bool = False
) -> Optional[SportAssociation]:
    """
    Convenience function to import an association.

    Args:
        zip_path: Path to the export ZIP file
        owner_email: Email for the new owner
        owner_password: Password for the new owner
        preserve_uuids: Whether to preserve original UUIDs
        skip_files: Whether to skip file import
        dry_run: Whether to validate without making changes

    Returns:
        Created SportAssociation or None if dry_run
    """
    options = ImportOptions(
        owner_email=owner_email,
        owner_password=owner_password,
        preserve_uuids=preserve_uuids,
        skip_files=skip_files,
        dry_run=dry_run,
    )

    service = AssociationImportService(zip_path, options)

    # Validate first
    validation = service.validate()
    if not validation.is_valid:
        for error in validation.errors:
            logger.error(f"Validation error: {error}")
        raise ValueError(f"Validation failed: {validation.errors}")

    for warning in validation.warnings:
        logger.warning(f"Validation warning: {warning}")

    if dry_run:
        logger.info("Dry run completed successfully")
        return None

    return service.import_all()
