import logging
import re

from django.apps import apps

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

        # Check the root field name
        root_field = field_path.split('__')[0]
        if not is_field_allowed(root_field):
            logger.warning(f"Blocked filter on blacklisted field: {root_field}")
            continue

        if lookup not in ALLOWED_LOOKUPS:
            logger.warning(f"Blocked filter with disallowed lookup: {lookup}")
            continue

        validated[key] = value
    return validated
