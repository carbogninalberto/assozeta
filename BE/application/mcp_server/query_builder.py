import html
import logging
import uuid
from datetime import datetime, date, time
from decimal import Decimal

from django.apps import apps
from django.conf import settings
from django.db import connection
from django.db.models import Count, Sum, Avg, Min, Max, Q

from application.mcp_server.security import (
    MODEL_WHITELIST,
    get_tenant_filter,
    get_soft_delete_filter,
    is_field_allowed,
    validate_filter_fields,
    ALLOWED_LOOKUPS,
)

logger = logging.getLogger(__name__)

AGGREGATION_FUNCTIONS = {
    'count': Count,
    'sum': Sum,
    'avg': Avg,
    'min': Min,
    'max': Max,
}

# Date-part suffixes that Django supports as transforms in values()/order_by()
DATE_PART_SUFFIXES = {'year', 'month', 'day', 'quarter', 'week', 'week_day', 'hour'}


def _serialize_value(value):
    """Convert Python values to JSON-serializable types."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return None  # Skip binary data
    if isinstance(value, str) and '&' in value:
        return html.unescape(value)
    return value


def _serialize_row(row: dict) -> dict:
    """Serialize all values in a row dict."""
    return {k: _serialize_value(v) for k, v in row.items()}


# Italian labels for choice fields whose Django labels are in English.
# Keyed by "ModelName.field_name", values are {raw_value: "Italian label"}.
ITALIAN_CHOICE_OVERRIDES = {
    'Associate.sex': {'A': 'Altro', 'M': 'Maschio', 'F': 'Femmina'},
    'Course.status_flag': {1: 'Archiviato', 2: 'Attivo', 3: 'Interno'},
    'Course.course_type': {1: 'Standard', 2: 'Quote multiple', 3: 'Tesseramento'},
    'CourseSubscription.type': {1: 'Standard', 2: 'Quote multiple', 3: 'Tesseramento'},
    'Payment.type': {
        'default': 'Predefinito', 'cash': 'Contanti', 'transfer': 'Bonifico',
        'online': 'Online', 'sepa-transfer': 'Bonifico SEPA', 'stripe': 'Stripe', 'pos': 'POS',
    },
    'PaymentCategory.type': {1: 'Istituzionale', 2: 'Commerciale'},
    'BalanceSheet.status_flag': {1: 'Bozza', 2: 'Approvato'},
    'AttendanceRegistry.status': {1: 'Bozza', 2: 'Pubblicato'},
}


def _build_choice_map(model, fields):
    """Build a {field_expression: {raw_value: label}} dict for choice fields.

    Walks FK chains (e.g. 'subscription__status_flag') to resolve the target
    field and its model. Uses ITALIAN_CHOICE_OVERRIDES when available, otherwise
    falls back to Django's own choice labels.
    """
    choice_map = {}
    for field_expr in fields:
        parts = field_expr.split('__')
        current_model = model
        target_field = None
        for part in parts:
            if part in DATE_PART_SUFFIXES:
                break
            try:
                field = current_model._meta.get_field(part)
                if field.is_relation and field.related_model:
                    current_model = field.related_model
                else:
                    target_field = field
            except Exception:
                break

        if target_field and hasattr(target_field, 'choices') and target_field.choices:
            model_name = current_model.__name__
            field_name = target_field.name
            override_key = f'{model_name}.{field_name}'
            if override_key in ITALIAN_CHOICE_OVERRIDES:
                choice_map[field_expr] = ITALIAN_CHOICE_OVERRIDES[override_key]
            else:
                choice_map[field_expr] = {k: v for k, v in target_field.choices}
    return choice_map


def _apply_choice_labels(row, choice_map):
    """Replace raw choice values with human-readable labels in a serialized row."""
    if not choice_map:
        return row
    for field_expr, mapping in choice_map.items():
        if field_expr in row:
            raw = row[field_expr]
            if raw in mapping:
                row[field_expr] = mapping[raw]
    return row


def _coerce_date_value(value):
    """Try to parse a string value into a date or datetime.

    Returns the parsed value, or the original if parsing fails.
    """
    if not isinstance(value, str):
        return value
    # Try datetime first (YYYY-MM-DDTHH:MM:SS)
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    # Try date (YYYY-MM-DD)
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        pass
    return value


def _coerce_filter_values(model, filters: dict) -> dict:
    """Coerce string filter values to proper Python types based on model field types.

    Handles: DateField, DateTimeField string values -> Python date/datetime.
    """
    coerced = {}
    for key, value in filters.items():
        # Extract root field name (handles FK traversal like subscription__start_date__gte)
        parts = key.split('__')
        # Walk the relation chain to find the actual field
        current_model = model
        target_field = None
        for i, part in enumerate(parts):
            if part in ALLOWED_LOOKUPS:
                break
            if part in DATE_PART_SUFFIXES:
                break
            try:
                field = current_model._meta.get_field(part)
                if field.is_relation and field.related_model:
                    current_model = field.related_model
                else:
                    target_field = field
            except Exception:
                break

        if target_field and isinstance(value, str):
            field_type = type(target_field).__name__
            if field_type in ('DateField', 'DateTimeField'):
                value = _coerce_date_value(value)
            elif field_type in ('IntegerField', 'SmallIntegerField', 'BigIntegerField',
                                'PositiveIntegerField', 'PositiveSmallIntegerField'):
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    pass
            elif field_type in ('FloatField', 'DecimalField'):
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    pass

        coerced[key] = value
    return coerced


def _build_or_q(or_filters: list, model) -> Q:
    """Build a Q object from a list of filter dicts ORed together.

    Each dict in the list is validated and ANDed internally.
    The dicts are then ORed together.
    """
    q = Q()
    for filter_dict in or_filters:
        if not isinstance(filter_dict, dict):
            continue
        validated = validate_filter_fields(model, filter_dict)
        coerced = _coerce_filter_values(model, validated)
        q |= Q(**coerced)
    return q


def _resolve_model(model_name: str):
    """Resolve a model name to a Django model class.

    Accepts either 'ModelName' or 'app_label.ModelName'.
    """
    # If it's already a full label
    if '.' in model_name:
        if model_name not in MODEL_WHITELIST:
            raise ValueError(f"Model '{model_name}' is not whitelisted")
        app_label, name = model_name.split('.')
        return apps.get_model(app_label, name), model_name

    # Try to find the model in the whitelist
    for label in MODEL_WHITELIST:
        if label.split('.')[1] == model_name:
            app_label, name = label.split('.')
            return apps.get_model(app_label, name), label

    raise ValueError(f"Model '{model_name}' is not whitelisted")


def _validate_group_by(model, group_by: list) -> list:
    """Validate group_by fields, allowing date-part suffixes."""
    valid = []
    for field_expr in group_by:
        parts = field_expr.split('__')
        root_field = parts[0]
        if not is_field_allowed(root_field):
            logger.warning(f"Blocked group_by on blacklisted field: {root_field}")
            continue
        # Check that root field exists (direct or FK)
        all_field_names = {f.name for f in model._meta.get_fields()}
        for f in model._meta.get_fields():
            if hasattr(f, 'attname') and f.attname != f.name:
                all_field_names.add(f.attname)
        if root_field not in all_field_names:
            logger.warning(f"Skipping unknown group_by field: {root_field}")
            continue
        valid.append(field_expr)
    return valid


class QueryBuilder:
    """Builds and executes Django ORM queries with tenant scoping."""

    def __init__(self, sport_association_id):
        self.sport_association_id = sport_association_id
        self.max_results = getattr(settings, 'MCP_AGENT_MAX_RESULTS', 5000)
        self.query_timeout = getattr(settings, 'MCP_AGENT_QUERY_TIMEOUT', 10)

    def _set_query_timeout(self):
        """Set PostgreSQL statement timeout for the current connection."""
        if connection.vendor == 'postgresql':
            timeout_ms = self.query_timeout * 1000
            with connection.cursor() as cursor:
                cursor.execute(f"SET statement_timeout = '{timeout_ms}'")

    def _reset_query_timeout(self):
        """Reset PostgreSQL statement timeout."""
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '0'")

    def _base_queryset(self, model, model_label: str, include_archived: bool = False):
        """Create a base queryset with tenant and soft-delete filters applied.

        Args:
            include_archived: If True, skip the soft-delete/archived filters.
                Use when the user explicitly asks for archived or deleted data.
        """
        tenant_filter = get_tenant_filter(model_label, self.sport_association_id)

        qs = model.objects.filter(**tenant_filter)
        if not include_archived:
            soft_delete_filter = get_soft_delete_filter(model_label)
            if soft_delete_filter:
                qs = qs.filter(**soft_delete_filter)
        return qs.distinct()

    def _apply_filters(self, qs, model, filters=None, exclude=None, or_filters=None):
        """Apply AND filters, exclude, and OR filters to a queryset."""
        if filters:
            validated = validate_filter_fields(model, filters)
            coerced = _coerce_filter_values(model, validated)
            qs = qs.filter(**coerced)

        if exclude:
            validated_ex = validate_filter_fields(model, exclude)
            coerced_ex = _coerce_filter_values(model, validated_ex)
            qs = qs.exclude(**coerced_ex)

        if or_filters and isinstance(or_filters, list):
            q = _build_or_q(or_filters, model)
            if q:
                qs = qs.filter(q)

        return qs

    def _validate_fields_for_model(self, model, fields: list) -> list:
        """Validate and filter fields, stripping blacklisted ones."""
        if not fields:
            # Return all allowed concrete field names
            return [
                f.name for f in model._meta.get_fields()
                if not f.is_relation and is_field_allowed(f.name)
            ]

        # Build set of valid field names including FK _id accessors
        all_field_names = set()
        for f in model._meta.get_fields():
            all_field_names.add(f.name)
            # For ForeignKey/OneToOne, also accept the _id column name
            if hasattr(f, 'attname') and f.attname != f.name:
                all_field_names.add(f.attname)

        valid = []
        for field in fields:
            root_field = field.split('__')[0]
            if root_field in all_field_names and is_field_allowed(root_field):
                valid.append(field)
            else:
                logger.warning(f"Skipping invalid/blacklisted field: {field}")
        return valid

    def execute_query(
        self,
        model_name: str,
        fields: list = None,
        filters: dict = None,
        exclude: dict = None,
        or_filters: list = None,
        joins: list = None,
        order_by: list = None,
        limit: int = None,
        offset: int = 0,
        include_archived: bool = False,
    ) -> dict:
        """Execute a query on a whitelisted model.

        Args:
            model_name: Model name (e.g. 'Associate' or 'application.Associate')
            fields: List of field names to select. None = all allowed fields.
            filters: Dict of filter kwargs (AND conditions)
            exclude: Dict of exclude kwargs (NOT matching)
            or_filters: List of filter dicts ORed together
            joins: List of related model names for select_related/prefetch_related
            order_by: List of field names for ordering (prefix with '-' for desc)
            limit: Max number of results (capped at MAX_RESULTS)
            offset: Number of results to skip

        Returns:
            Dict with count, has_more, and data list.
        """
        model, model_label = _resolve_model(model_name)
        qs = self._base_queryset(model, model_label, include_archived=include_archived)
        qs = self._apply_filters(qs, model, filters, exclude, or_filters)

        # Apply joins
        if joins:
            select_related = []
            prefetch_related = []
            for join in joins:
                # Determine if it's a FK (select_related) or M2M (prefetch_related)
                try:
                    field = model._meta.get_field(join)
                    if field.many_to_many or field.one_to_many:
                        prefetch_related.append(join)
                    else:
                        select_related.append(join)
                except Exception:
                    # May be a reverse relation
                    prefetch_related.append(join)

            if select_related:
                qs = qs.select_related(*select_related)
            if prefetch_related:
                qs = qs.prefetch_related(*prefetch_related)

        # Get total count before slicing
        total_count = qs.count()

        # Apply ordering
        if order_by:
            valid_order = []
            for field in order_by:
                clean = field.lstrip('-')
                root = clean.split('__')[0]
                if is_field_allowed(root):
                    valid_order.append(field)
            if valid_order:
                qs = qs.order_by(*valid_order)

        # Validate fields
        valid_fields = self._validate_fields_for_model(model, fields)
        choice_map = _build_choice_map(model, valid_fields)

        # Apply limit/offset
        effective_limit = min(limit or self.max_results, self.max_results)
        qs = qs[offset:offset + effective_limit]

        # Execute with timeout
        self._set_query_timeout()
        try:
            if valid_fields:
                final_qs = qs.values(*valid_fields)
            else:
                final_qs = qs.values()
            logger.info("MCP execute_query [%s] SQL: %s", model_name, final_qs.query)
            data = list(final_qs)
            data = [_apply_choice_labels(_serialize_row(row), choice_map) for row in data]
        finally:
            self._reset_query_timeout()

        return {
            'count': total_count,
            'has_more': (offset + effective_limit) < total_count,
            'data': data,
        }

    def count(
        self,
        model_name: str,
        filters: dict = None,
        exclude: dict = None,
        or_filters: list = None,
        include_archived: bool = False,
    ) -> dict:
        """Count records matching filters."""
        model, model_label = _resolve_model(model_name)
        qs = self._base_queryset(model, model_label, include_archived=include_archived)
        qs = self._apply_filters(qs, model, filters, exclude, or_filters)

        logger.info("MCP count [%s] SQL: %s", model_name, qs.query)
        self._set_query_timeout()
        try:
            result = qs.count()
        finally:
            self._reset_query_timeout()

        return {'count': result}

    def get_field_values(
        self,
        model_name: str,
        field_name: str,
        filters: dict = None,
        exclude: dict = None,
        or_filters: list = None,
        limit: int = 100,
        include_archived: bool = False,
    ) -> dict:
        """Get distinct values for a field, optionally filtered."""
        model, model_label = _resolve_model(model_name)

        root_field = field_name.split('__')[0]
        if not is_field_allowed(root_field):
            raise ValueError(f"Field '{field_name}' is not allowed")

        qs = self._base_queryset(model, model_label, include_archived=include_archived)
        qs = self._apply_filters(qs, model, filters, exclude, or_filters)
        field_choices = _build_choice_map(model, [field_name]).get(field_name, {})

        self._set_query_timeout()
        try:
            total_distinct = qs.values(field_name).distinct().count()
            values = list(
                qs.values_list(field_name, flat=True)
                .distinct()
                .order_by(field_name)[:limit]
            )
            values = [field_choices.get(v, _serialize_value(v)) for v in values]
        finally:
            self._reset_query_timeout()

        return {
            'values': values,
            'total_distinct': total_distinct,
        }

    def execute_aggregation(
        self,
        model_name: str,
        filters: dict = None,
        exclude: dict = None,
        or_filters: list = None,
        aggregations: list = None,
        group_by: list = None,
        include_archived: bool = False,
    ) -> dict:
        """Execute aggregation queries, optionally grouped.

        Args:
            model_name: Model name
            filters: Dict of filter kwargs (AND conditions)
            exclude: Dict of exclude kwargs (NOT matching)
            or_filters: List of filter dicts ORed together
            aggregations: List of dicts with 'function' and 'field' keys
            group_by: List of fields to group by. Supports date parts
                      (e.g. 'payment_date__year', 'payment_date__month').

        Returns:
            Without group_by: Dict with flat aggregation results.
            With group_by: Dict with 'data' list of grouped rows.
        """
        model, model_label = _resolve_model(model_name)
        qs = self._base_queryset(model, model_label, include_archived=include_archived)
        qs = self._apply_filters(qs, model, filters, exclude, or_filters)

        if not aggregations:
            return {'error': 'No aggregations specified'}

        agg_kwargs = {}
        for agg in aggregations:
            func_name = agg.get('function', '').lower()
            field = agg.get('field', '*')
            alias = agg.get('alias', f'{func_name}_{field}')
            # Sanitize alias for Django (no dots or dashes)
            alias = alias.replace('.', '_').replace('-', '_')

            func = AGGREGATION_FUNCTIONS.get(func_name)
            if not func:
                continue

            if field == '*' and func_name == 'count':
                agg_kwargs[alias] = Count('*')
            elif is_field_allowed(field.split('__')[0]):
                agg_kwargs[alias] = func(field)

        if not agg_kwargs:
            return {'error': 'No valid aggregations'}

        logger.info(
            "MCP aggregate [%s] group_by=%s aggs=%s base_SQL: %s",
            model_name, group_by, list(agg_kwargs.keys()), qs.query,
        )
        self._set_query_timeout()
        try:
            if group_by:
                valid_group_by = _validate_group_by(model, group_by)
                if not valid_group_by:
                    return {'error': 'No valid group_by fields'}

                group_choice_map = _build_choice_map(model, valid_group_by)
                agg_qs = (
                    qs.values(*valid_group_by)
                    .annotate(**agg_kwargs)
                    .order_by(*valid_group_by)
                )
                logger.info("MCP aggregate [%s] final SQL: %s", model_name, agg_qs.query)
                rows = list(agg_qs)
                data = [_apply_choice_labels(_serialize_row(row), group_choice_map) for row in rows]
                return {
                    'data': data,
                    'count': len(data),
                }
            else:
                result = qs.aggregate(**agg_kwargs)
                return {k: _serialize_value(v) for k, v in result.items()}
        finally:
            self._reset_query_timeout()
