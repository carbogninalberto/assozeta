import logging
import re

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist

logger = logging.getLogger(__name__)

# Model whitelist: app_label.ModelName -> config
# tenant_field: ORM path to filter by sport_association
#   '_self' means the model IS the SportAssociation
# soft_delete_field: field name for soft-delete filtering, or None
MODEL_WHITELIST = {
    'application.SportAssociation': {
        'tenant_field': '_self',
        'soft_delete_field': 'deleted',
    },
    'application.Associate': {
        'tenant_field': 'sport_association',
        'soft_delete_field': 'deleted',
    },
    'application.Group': {
        'tenant_field': 'sport_association',
        'soft_delete_field': None,
    },
    'application.Subscription': {
        'tenant_field': 'sport_association',
        'soft_delete_field': ['deleted', 'archived'],
    },
    'application.Course': {
        'tenant_field': 'sport_association',
        'soft_delete_field': 'deleted',
    },
    'application.CourseSubscription': {
        'tenant_field': 'course__sport_association',
        'soft_delete_field': ['deleted', 'archived'],
    },
    'application.Payment': {
        'tenant_field': 'sport_association',
        'soft_delete_field': ['deleted', 'archived'],
    },
    'application.PaymentCategory': {
        'tenant_field': 'sport_association',
        'soft_delete_field': 'archived',
    },
    'application.Invoice': {
        'tenant_field': 'sport_association',
        'soft_delete_field': ['deleted', 'archived'],
    },
    'application.InvoiceRows': {
        'tenant_field': 'invoice__sport_association',
        'soft_delete_field': None,
    },
    'application.MedicalCertificate': {
        'tenant_field': 'subscription__sport_association',
        'soft_delete_field': None,
    },
    'application.Instructor': {
        'tenant_field': 'user__sportassociation',
        'soft_delete_field': None,
    },
    'application.BalanceSheet': {
        'tenant_field': 'sport_association',
        'soft_delete_field': 'archived',
    },
    'application.CustomAccounts': {
        'tenant_field': 'sport_association',
        'soft_delete_field': None,
    },
    'application.Tags': {
        'tenant_field': 'sport_association',
        'soft_delete_field': None,
    },
    'application.CourseTags': {
        'tenant_field': 'sport_association',
        'soft_delete_field': None,
    },
    'application.Carnet': {
        'tenant_field': 'sport_association',
        'soft_delete_field': None,
    },
    'application.CarnetSubscription': {
        'tenant_field': 'carnet_id__sport_association',
        'soft_delete_field': 'disabled',
    },
    'application.Module': {
        'tenant_field': 'sport_association',
        'soft_delete_field': None,
    },
    'application.AttendanceRegistry': {
        'tenant_field': 'course__sport_association',
        'soft_delete_field': None,
    },
    'application.AttendanceDay': {
        'tenant_field': 'attendance_registry__course__sport_association',
        'soft_delete_field': None,
    },
    'application.Reminders': {
        'tenant_field': 'sport_association',
        'soft_delete_field': None,
    },
}

# Fields that must never be exposed
FIELD_BLACKLIST = {
    'password', 'token', 'secret', 'api_key',
    'stripe_account_id', 'stripe_customer_id', 'stripe_onboarded',
    'two_fa_secret', 'two_fa_enabled', 'two_fa_backup_codes',
    'signature_url', 'signature_storage_key', 'configuration', 'collaborator_permissions',
    'dashboard_layout', 'push_subscriptions',
    'social_auth', 'last_login', 'is_superuser', 'is_staff',
    'groups', 'user_permissions',
}

# Patterns to match against field names (prefix match)
FIELD_BLACKLIST_PREFIXES = ('stripe_', 'two_fa', 'social_auth_')

# Fields and relations a report may read, per whitelisted model. Relations lead only to whitelisted models.
REPORT_ALLOWLIST = {
    'application.SportAssociation': {
        'fields': ('sport_association_id', 'denomination', 'address_city', 'address', 'address_cap', 'tax_code',
            'email', 'phone', 'regulation', 'demand', 'additional_sections', 'logo', 'subscription_fee',
            'multiple_subscription_fee', 'subscription_fee_plans', 'membership_fee', 'multiple_membership_fee',
            'membership_fee_plans', 'custom_subscription_form_data', 'document_header', 'invoice_footer',
            'enable_quotes_management', 'reviewed', 'affiliate_code', 'deleted', 'review_url',
            'review_url_enabled', 'president_signature', 'stamp', 'president_first_name', 'president_last_name',
            'enroll_number', 'federation', 'sport', 'enabled_for', 'checkout_info', 'additional_fields', 'iban',
            'website', 'abbreviated', 'vat_number', 'whatsapp', 'imported_from_associami', 'extra_text_invoices',
            'show_regulation_to_members', 'show_regulation_to_both', 'show_regulation_to_athletes',
            'show_demand_to_members', 'show_demand_to_both', 'show_demand_to_athletes', 'invoice_template',
            'subscription_template', 'notes',),
        'relations': ('group', 'associate', 'balancesheet', 'customaccounts', 'invoice', 'payment',
            'paymentcategory', 'subscription', 'tags', 'coursetags', 'course', 'reminders', 'carnet', 'module',),
    },
    'application.Associate': {
        'fields': ('associate_id', 'picture_path', 'first_name', 'last_name', 'sex', 'tax_code', 'born_date',
            'born_city', 'address_city', 'address', 'address_cap', 'nationality', 'nationality_residence', 'email',
            'phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label', 'phone_2_label', 'phone_3_label',
            'phone_4_label', 'is_minor', 'disabled', 'draft', 'deleted', 'family_role', 'id_number',
            'creation_date', 'migrated', 'notes',),
        'relations': ('tutored_associates', 'invoice', 'payment', 'subscription', 'sport_association', 'group',
            'tutors',),
    },
    'application.Group': {
        'fields': ('group_id', 'name', 'description',),
        'relations': ('associate', 'invoice', 'payment', 'subscription', 'course', 'sport_association',),
    },
    'application.Subscription': {
        'fields': ('subscription_id', 'custom_data', 'additional_fields', 'type', 'role', 'acceptance_date',
            'resignation_date', 'status_flag', 'creation_date', 'start_date', 'end_date', 'archived', 'draft',
            'trial', 'meta', 'subscription_number', 'subscription_type', 'deleted', 'competitive', 'notes',),
        'relations': ('coursesubscription', 'carnetsubscription', 'sport_association', 'associate', 'instructor',
            'medical', 'payment', 'group', 'tags',),
    },
    'application.Course': {
        'fields': ('course_id', 'status_flag', 'course_type', 'creation_date', 'title', 'description',
            'multi_payments', 'fee', 'events', 'one_fee_payment', 'one_fee', 'google_calendar_id',
            'google_background_color', 'multiple_quotes', 'start_date', 'end_date', 'pinned',
            'billed_duration_is_sport_season', 'billed_frequency', 'billed_from_subscription_date',
            'billed_from_day_of_month', 'auto_renewal', 'deleted',),
        'relations': ('coursesubscription', 'attendanceregistry', 'sport_association', 'group', 'tags',),
    },
    'application.CourseSubscription': {
        'fields': ('course_subscription_id', 'paid', 'creation_date', 'multi_payments', 'one_fee_payment',
            'archived', 'type', 'multiple_quote', 'billed_frequency', 'billed_from', 'billed_until',
            'billed_from_day_of_month', 'membership_active', 'membership_fee', 'auto_renewal', 'deleted',),
        'relations': ('carnetsubscription', 'course', 'subscription', 'payment', 'membership_payments',),
    },
    'application.Payment': {
        'fields': ('payment_id', 'type', 'subject', 'amount', 'creation_date', 'payment_date', 'paid', 'expense',
            'description', 'meta_payment_categories', 'archived', 'invoice_fiscal_code', 'meta', 'notes',
            'deleted', 'course',),
        'relations': ('subscription', 'coursesubscription', 'membership_course_subscriptions',
            'carnetsubscription', 'payment_category', 'invoice', 'associate', 'instructor', 'sport_association',
            'custom_accounts', 'group',),
    },
    'application.PaymentCategory': {
        'fields': ('payment_category_id', 'name', 'creation_date', 'expense', 'tax_deductible', 'archived', 'type',
            'deleted',),
        'relations': ('payment', 'sport_association',),
    },
    'application.Invoice': {
        'fields': ('invoice_id', 'creation_date', 'membership_fee', 'activity_fee', 'meta_payment_categories',
            'description', 'number', 'archived', 'cancelled', 'meta', 'imported_from_associami', 'deleted',),
        'relations': ('invoicerows', 'payment', 'sport_association', 'selected_tutor', 'group',),
    },
    'application.InvoiceRows': {
        'fields': ('invoice_row_id', 'description', 'quantity', 'unit_price', 'vat', 'unit_of_measure', 'meta',),
        'relations': ('invoice',),
    },
    'application.MedicalCertificate': {
        'fields': ('medical_id', 'creation_date', 'expiration_date', 'competitive_medical_certificate', 'notes',),
        'relations': ('subscription',),
    },
    'application.Instructor': {
        'fields': ('instructor_id', 'first_name', 'last_name', 'email', 'born_date', 'born_city', 'born_province',
            'tax_code', 'address_city', 'address', 'address_province', 'civic_number', 'study_title', 'role',
            'phone', 'phone_2', 'phone_3', 'phone_4', 'draft', 'is_volunteer', 'stipulated_contract_in',
            'default_hourly_billing', 'default_percentage_billing',),
        'relations': ('payment', 'subscription', 'reminders',),
    },
    'application.BalanceSheet': {
        'fields': ('balance_sheet_id', 'status_flag', 'creation_date', 'year', 'data', 'archived',),
        'relations': ('sport_association',),
    },
    'application.CustomAccounts': {
        'fields': ('custom_account_id', 'enabled', 'name', 'initial_balance', 'account_type', 'account_code',
            'editable',),
        'relations': ('payment', 'sport_association',),
    },
    'application.Tags': {
        'fields': ('tag_id', 'tag_name', 'creation_date',),
        'relations': ('subscription', 'sport_association',),
    },
    'application.CourseTags': {
        'fields': ('tag_id', 'tag_name', 'creation_date',),
        'relations': ('course', 'sport_association',),
    },
    'application.Carnet': {
        'fields': ('carnet_id', 'title', 'description', 'lessons_number', 'fee', 'creation_date', 'public', 'meta',),
        'relations': ('carnetsubscription', 'sport_association',),
    },
    'application.CarnetSubscription': {
        'fields': ('carnet_subscription_id', 'creation_date', 'disabled', 'meta',),
        'relations': ('subscription', 'carnet_id', 'payment', 'course_subscription',),
    },
    'application.Module': {
        'fields': ('module_id', 'custom_link', 'creation_date', 'title', 'require_approval', 'start_date',
            'end_date', 'always_active', 'max_responses', 'queue_mode', 'only_users', 'payment_required',
            'payment_data', 'response_message', 'allow_attachments', 'elements',),
        'relations': ('sport_association',),
    },
    'application.AttendanceRegistry': {
        'fields': ('attendance_registry_id', 'creation_date', 'events', 'status',),
        'relations': ('attendanceday', 'course',),
    },
    'application.AttendanceDay': {
        'fields': ('attendance_day_id', 'title', 'date', 'attendees', 'expected_absences', 'auto_marked',
            'associated_event',),
        'relations': ('attendance_registry',),
    },
    'application.Reminders': {
        'fields': ('reminders_id', 'event_id', 'event_title', 'event_description', 'event_course_title',
            'event_reminder_text', 'send_at', 'completed',),
        'relations': ('instructor', 'sport_association',),
    },
}

# Allowed ORM lookup suffixes
ALLOWED_LOOKUPS = {
    'exact', 'iexact', 'contains', 'icontains',
    'gt', 'gte', 'lt', 'lte',
    'in', 'isnull',
    'startswith', 'istartswith', 'endswith', 'iendswith',
    'range', 'year', 'month', 'day', 'date',
    # JSON field lookups
    'has_key',
}


def is_model_whitelisted(model_label: str) -> bool:
    return model_label in MODEL_WHITELIST


def get_model_config(model_label: str) -> dict | None:
    return MODEL_WHITELIST.get(model_label)


def get_tenant_filter(model_label: str, sport_association_id) -> dict:
    """Returns ORM filter kwargs to scope queries to a sport_association."""
    config = MODEL_WHITELIST.get(model_label)
    if not config:
        raise ValueError(f"Model {model_label} is not whitelisted")

    tenant_field = config['tenant_field']

    if tenant_field == '_self':
        # The model IS SportAssociation, filter by its own PK
        return {'sport_association_id': sport_association_id}

    # For models with direct or indirect FK to sport_association
    # e.g. 'sport_association' -> {'sport_association_id': uuid}
    # e.g. 'course__sport_association' -> {'course__sport_association_id': uuid}
    if tenant_field.endswith('sport_association'):
        return {f'{tenant_field}_id': sport_association_id}

    # Fallback
    return {f'{tenant_field}': sport_association_id}


def get_soft_delete_filter(model_label: str) -> dict:
    """Returns ORM filter kwargs to exclude soft-deleted/archived records.

    Supports single field (str) or multiple fields (list).
    """
    config = MODEL_WHITELIST.get(model_label)
    if not config or not config.get('soft_delete_field'):
        return {}

    fields = config['soft_delete_field']
    if isinstance(fields, str):
        fields = [fields]

    result = {}
    for field in fields:
        result[field] = False
    return result


def is_field_allowed(field_name: str) -> bool:
    """Check if a field name is allowed (not blacklisted)."""
    if field_name in FIELD_BLACKLIST:
        return False
    for prefix in FIELD_BLACKLIST_PREFIXES:
        if field_name.startswith(prefix):
            return False
    return True


def is_path_allowed(model, path) -> bool:
    """Whether every field and relation named along ``path`` may be read.

    The first ``__`` segment is checked against the name blocklist (``is_field_allowed``) before the walk, whether
    or not the model has a field of that name: a blocked name is refused even when nothing on the model matches it,
    so the walk never treats it as an unknown name. The remaining segments are walked from ``model``: a relation
    must be listed for the model it is reached on and leads to the related model; a concrete field (or a relation's
    ``<name>_id`` column) must be listed and ends the walk (what follows is a transform or lookup). A later segment
    that names no field of the current model also ends the walk: it is a lookup, a JSON key or an unknown name,
    which the ORM answers as before.
    """
    segments = path.split('__')
    if not is_field_allowed(segments[0]):
        return False
    current = model
    for segment in segments:
        allowed = REPORT_ALLOWLIST.get(f'{current._meta.app_label}.{current.__name__}')
        if allowed is None:
            return False
        try:
            field = current._meta.get_field(segment)
        except FieldDoesNotExist:
            return True
        if field.is_relation and field.related_model is not None:
            if field.name not in allowed['relations']:
                return False
            if segment != field.name:
                return True
            current = field.related_model
            continue
        return field.name in allowed['fields']
    return True


def validate_fields(model, fields: list) -> list:
    """Filter out blacklisted fields from a field list."""
    if not fields:
        return fields
    model_field_names = {f.name for f in model._meta.get_fields()}
    return [f for f in fields if f in model_field_names and is_field_allowed(f)]


def validate_filter_lookups(filters: dict) -> dict:
    """Validate that all filter lookups use allowed suffixes."""
    validated = {}
    for key, value in filters.items():
        # Parse field__lookup format
        parts = key.split('__')
        if len(parts) >= 2:
            lookup = parts[-1]
            if lookup in ALLOWED_LOOKUPS:
                validated[key] = value
            elif lookup not in ALLOWED_LOOKUPS:
                # It might be a FK traversal, not a lookup (e.g. associate__first_name)
                # Check if it ends with an allowed lookup
                # Default to 'exact' if no lookup suffix
                field_path = key
                validated[field_path] = value
        else:
            # No lookup suffix, defaults to 'exact'
            validated[key] = value
    return validated


def parse_filter_key(key: str) -> tuple[str, str]:
    """Parse a filter key into (field_path, lookup).
    Returns the field path and lookup suffix."""
    parts = key.split('__')
    if len(parts) >= 2 and parts[-1] in ALLOWED_LOOKUPS:
        return '__'.join(parts[:-1]), parts[-1]
    return key, 'exact'


def validate_filter_fields(model, filters: dict) -> dict:
    """Validate filter keys: ensure field paths are valid and not blacklisted."""
    validated = {}
    for key, value in filters.items():
        field_path, lookup = parse_filter_key(key)

        # Check the field path
        if not is_path_allowed(model, field_path):
            logger.warning(f"Blocked filter on field path: {field_path}")
            continue

        if lookup not in ALLOWED_LOOKUPS:
            logger.warning(f"Blocked filter with disallowed lookup: {lookup}")
            continue

        validated[key] = value
    return validated
