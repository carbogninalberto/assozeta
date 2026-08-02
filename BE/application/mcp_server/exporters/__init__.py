"""
Export data formatting utilities.

All exporters use format_export_data() to ensure consistent output:
- Booleans -> "Vero" / "Falso"
- Dates -> DD/MM/YYYY
- Datetimes -> DD/MM/YYYY HH:MM
- Internal/system fields are stripped
- None -> "" (empty string)
"""
from datetime import datetime, date


# Fields that should never appear in user-facing exports
EXPORT_FIELD_BLACKLIST = {
    # Internal IDs (UUIDs used as PKs/FKs)
    'sport_association_id', 'sport_association',
    'associate_id', 'subscription_id', 'course_id', 'course_subscription_id',
    'payment_id', 'payment_category_id', 'invoice_id', 'invoice_row_id',
    'medical_id', 'instructor_id', 'balance_sheet_id', 'group_id',
    'carnet_id', 'carnet_subscription_id', 'module_id', 'module_response_id',
    'attendance_registry_id', 'attendance_day_id', 'reminder_id',
    'custom_account_id', 'tag_id', 'course_tag_id', 'document_id',
    'user_id', 'connected_user_id',
    # Soft delete / system flags
    'deleted', 'disabled', 'is_active', 'is_superuser', 'is_staff',
    # Timestamps that are rarely useful in exports
    'updated_at',
}


def format_value(value):
    """Format a single value for user-facing export."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'Vero' if value else 'Falso'
    if isinstance(value, datetime):
        return value.strftime('%d/%m/%Y %H:%M')
    if isinstance(value, date):
        return value.strftime('%d/%m/%Y')
    # Handle ISO date strings from serialized data
    if isinstance(value, str):
        # Try ISO datetime (2026-01-15T10:30:00)
        if len(value) >= 19 and value[4] == '-' and value[10] == 'T':
            try:
                dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return dt.strftime('%d/%m/%Y %H:%M')
            except (ValueError, IndexError):
                pass
        # Try ISO date (2026-01-15)
        if len(value) == 10 and value[4] == '-' and value[7] == '-':
            try:
                d = date.fromisoformat(value)
                return d.strftime('%d/%m/%Y')
            except (ValueError, IndexError):
                pass
    return value


def format_export_data(data: list[dict]) -> list[dict]:
    """Format all values in export data and strip internal fields.

    Args:
        data: List of row dicts from QueryBuilder.

    Returns:
        Cleaned and formatted list of row dicts.
    """
    if not data:
        return data

    # Determine which columns to keep (strip blacklisted)
    all_keys = list(data[0].keys())
    keep_keys = [k for k in all_keys if k not in EXPORT_FIELD_BLACKLIST]

    formatted = []
    for row in data:
        formatted_row = {}
        for key in keep_keys:
            formatted_row[key] = format_value(row.get(key))
        formatted.append(formatted_row)

    return formatted
