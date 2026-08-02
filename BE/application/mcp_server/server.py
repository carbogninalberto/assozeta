"""
MCP Server for Bakney Sport data export.

Exposes tools for schema introspection, querying, and exporting data.
Supports stdio transport (for Claude Code / CLI) and SSE transport (for web clients).
"""
import base64
import json
import logging
import re
from datetime import date

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from application.mcp_server.attendance import build_attendance_matrix, export_attendance_matrix_xlsx, export_attendance_matrix_pdf
from application.mcp_server.schema import get_full_schema, get_model_schema
from application.mcp_server.query_builder import QueryBuilder
from application.mcp_server.exporters.csv_exporter import CSVExporter
from application.mcp_server.exporters.xlsx_exporter import XLSXExporter
from application.mcp_server.exporters.pdf_exporter import PDFExporter

logger = logging.getLogger(__name__)

_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_text(text: str, replacement: str = '-') -> str:
    """Remove characters that are unsafe in filenames."""
    if not text:
        return text
    sanitized = _UNSAFE_FILENAME_RE.sub(replacement, text)
    if replacement:
        sanitized = re.sub(re.escape(replacement) + '+', replacement, sanitized)
    return sanitized.strip().strip(replacement)


EXPORTERS = {
    'csv': CSVExporter(),
    'xlsx': XLSXExporter(),
    'pdf': PDFExporter(),
}

# ────────────────────────────────────────────────────────────────────
# Plain Python tool functions (used in-process by the agent)
# ────────────────────────────────────────────────────────────────────

def tool_get_schema(sport_association_id: str, model_name: str = None, **kwargs) -> dict:
    """Get the database schema for all whitelisted models or a specific model."""
    # Handle null/None model_name from LLM
    if model_name and model_name != 'null':
        result = get_model_schema(f'application.{model_name}')
        if not result:
            result = get_model_schema(model_name)
        return result or {'error': f'Model {model_name} not found'}
    return get_full_schema()


def tool_query_data(
    sport_association_id: str,
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
    **kwargs,
) -> dict:
    """Query data from a whitelisted model."""
    qb = QueryBuilder(sport_association_id)
    return qb.execute_query(
        model_name=model_name,
        fields=fields,
        filters=filters,
        exclude=exclude,
        or_filters=or_filters,
        joins=joins,
        order_by=order_by,
        limit=limit,
        offset=offset,
        include_archived=include_archived,
    )


def tool_count_data(
    sport_association_id: str,
    model_name: str,
    filters: dict = None,
    exclude: dict = None,
    or_filters: list = None,
    include_archived: bool = False,
    **kwargs,
) -> dict:
    """Count records matching filters."""
    qb = QueryBuilder(sport_association_id)
    return qb.count(
        model_name=model_name,
        filters=filters,
        exclude=exclude,
        or_filters=or_filters,
        include_archived=include_archived,
    )


def tool_get_field_values(
    sport_association_id: str,
    model_name: str,
    field_name: str,
    filters: dict = None,
    exclude: dict = None,
    or_filters: list = None,
    limit: int = 100,
    include_archived: bool = False,
    **kwargs,
) -> dict:
    """Get distinct values for a field."""
    qb = QueryBuilder(sport_association_id)
    return qb.get_field_values(
        model_name=model_name,
        field_name=field_name,
        filters=filters,
        exclude=exclude,
        or_filters=or_filters,
        limit=limit,
        include_archived=include_archived,
    )


def tool_export_data(
    sport_association_id: str,
    model_name: str,
    format: str = 'csv',
    fields: list = None,
    filters: dict = None,
    exclude: dict = None,
    or_filters: list = None,
    joins: list = None,
    order_by: list = None,
    limit: int = None,
    title: str = '',
    column_labels: dict = None,
    include_archived: bool = False,
    header_text: str = '',
    footer_text: str = '',
    text_before: str = '',
    text_after: str = '',
    **kwargs,
) -> dict:
    """Query and export data to CSV/XLSX/PDF."""
    if format not in EXPORTERS:
        return {'error': f"Unsupported format: {format}. Use: csv, xlsx, pdf"}

    # First, query the data
    qb = QueryBuilder(sport_association_id)
    result = qb.execute_query(
        model_name=model_name,
        fields=fields,
        filters=filters,
        exclude=exclude,
        or_filters=or_filters,
        joins=joins,
        order_by=order_by,
        limit=limit,
        include_archived=include_archived,
    )

    data = result.get('data', [])
    if not data:
        return {'error': 'No data to export', 'count': 0}

    # Export
    exporter = EXPORTERS[format]
    filename = sanitize_text(title or model_name)
    export_kwargs = dict(
        data=data,
        filename=filename,
        title=title or model_name,
        column_labels=column_labels,
    )
    if format == 'pdf':
        export_kwargs.update(
            header_text=header_text,
            footer_text=footer_text,
            text_before=text_before,
            text_after=text_after,
        )
    file_bytes, content_type, extension = exporter.export(**export_kwargs)

    file_b64 = base64.b64encode(file_bytes).decode('utf-8')

    today = date.today().strftime('%d/%m/%Y')
    params = {
        'model_name': model_name,
        'format': format,
    }
    if title:
        params['title'] = title
    if fields:
        params['fields'] = fields
    if filters:
        params['filters'] = filters
    if exclude:
        params['exclude'] = exclude
    if or_filters:
        params['or_filters'] = or_filters
    if joins:
        params['joins'] = joins
    if order_by:
        params['order_by'] = order_by
    if limit:
        params['limit'] = limit
    if column_labels:
        params['column_labels'] = column_labels
    if include_archived:
        params['include_archived'] = include_archived
    if header_text:
        params['header_text'] = header_text
    if footer_text:
        params['footer_text'] = footer_text
    if text_before:
        params['text_before'] = text_before
    if text_after:
        params['text_after'] = text_after

    total_count = result.get('count', len(data))
    description_hint = f'{len(data)} righe'
    if filters:
        filter_count = len(filters)
        description_hint += f', {filter_count} filtro{"i" if filter_count > 1 else ""} applicato{"i" if filter_count > 1 else ""}'

    ui_fields = [
        {'key': 'format', 'type': 'select', 'label': 'Formato', 'options': ['csv', 'xlsx', 'pdf']},
    ]
    if filters:
        ui_fields.append({'key': 'filters', 'type': 'json', 'label': 'Filtri'})

    description = f'Export {title or model_name} ({format.upper()}) - {description_hint}'

    return {
        'type': 'export_ready',
        'filename': f'{filename}.{extension}',
        'content_type': content_type,
        'data_base64': file_b64,
        'row_count': len(data),
        'total_count': total_count,
        'save_info': {
            'tool_name': 'export_data',
            'params': params,
            'name': f'{title or model_name} - {today}',
            'description': description,
            'description_hint': description_hint,
            'ui_config': {'fields': ui_fields},
            'model_name': model_name,
        },
    }


def tool_aggregate_data(
    sport_association_id: str,
    model_name: str,
    filters: dict = None,
    exclude: dict = None,
    or_filters: list = None,
    aggregations: list = None,
    group_by: list = None,
    include_archived: bool = False,
    **kwargs,
) -> dict:
    """Execute aggregation queries, optionally grouped."""
    qb = QueryBuilder(sport_association_id)
    return qb.execute_aggregation(
        model_name=model_name,
        filters=filters,
        exclude=exclude,
        or_filters=or_filters,
        aggregations=aggregations,
        group_by=group_by,
        include_archived=include_archived,
    )


def tool_get_attendance_matrix(
    sport_association_id: str,
    course_id: str = None,
    course_name: str = None,
    date_from: str = None,
    date_to: str = None,
    export: bool = False,
    format: str = 'xlsx',
    multi_sheet: bool = False,
    **kwargs,
) -> dict:
    """Get attendance matrix (names x dates -> P) with optional XLSX/PDF export.

    If no course_id/course_name is given, returns all courses with attendance data.
    With export=true, returns a file (XLSX or PDF based on format param).
    With multi_sheet=true (XLSX only), creates one sheet per course.
    """
    matrix = build_attendance_matrix(
        sport_association_id=sport_association_id,
        course_id=course_id,
        course_name=course_name,
        date_from=date_from,
        date_to=date_to,
    )

    if 'error' in matrix:
        return matrix

    if export:
        row_count = (
            sum(len(m['rows']) for m in matrix['courses'])
            if matrix.get('multi')
            else len(matrix['rows'])
        )

        params = {
            'export': export,
            'format': format,
        }
        if course_id:
            params['course_id'] = course_id
        if course_name:
            params['course_name'] = course_name
        if date_from:
            params['date_from'] = date_from
        if date_to:
            params['date_to'] = date_to
        if multi_sheet:
            params['multi_sheet'] = multi_sheet

        date_from_display = date_from or 'inizio'
        date_to_display = date_to or 'oggi'
        name = f'Presenze {date_from_display} - {date_to_display}'

        total_dates = matrix['summary']['total_dates']
        course_count = len(matrix['courses']) if matrix.get('multi') else 1
        description_hint = f'{row_count} iscritti, {total_dates} lezioni'
        if matrix.get('multi'):
            description_hint += f', {course_count} corsi'

        ui_fields = [
            {'key': 'date_from', 'type': 'date', 'label': 'Data inizio'},
            {'key': 'date_to', 'type': 'date', 'label': 'Data fine'},
            {'key': 'format', 'type': 'select', 'label': 'Formato', 'options': ['xlsx', 'pdf']},
        ]
        if matrix.get('multi'):
            ui_fields.append({'key': 'multi_sheet', 'type': 'boolean', 'label': 'Fogli separati per corso'})

        course_label = course_name or ('Tutti i corsi' if matrix.get('multi') else matrix.get('course'))
        desc_parts = [f'Registro presenze {course_label}']
        if date_from or date_to:
            desc_parts.append(f'({date_from_display} - {date_to_display})')
        desc_parts.append(f'- {description_hint}')

        save_info = {
            'tool_name': 'get_attendance_matrix',
            'params': params,
            'name': name,
            'description': ' '.join(desc_parts),
            'description_hint': description_hint,
            'ui_config': {'fields': ui_fields},
            'course_name': course_label,
            'date_from': date_from,
            'date_to': date_to,
        }

        if format == 'pdf':
            pdf_bytes, filename = export_attendance_matrix_pdf(matrix)
            file_b64 = base64.b64encode(pdf_bytes).decode('utf-8')
            return {
                'type': 'export_ready',
                'filename': filename,
                'content_type': 'application/pdf',
                'data_base64': file_b64,
                'row_count': row_count,
                'total_dates': total_dates,
                'save_info': save_info,
            }

        xlsx_bytes, filename = export_attendance_matrix_xlsx(matrix, multi_sheet=multi_sheet)
        file_b64 = base64.b64encode(xlsx_bytes).decode('utf-8')
        return {
            'type': 'export_ready',
            'filename': filename,
            'content_type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'data_base64': file_b64,
            'row_count': row_count,
            'total_dates': total_dates,
            'save_info': save_info,
        }

    # Preview mode
    if matrix.get('multi'):
        # Multi-course preview
        course_previews = []
        for cm in matrix['courses']:
            course_previews.append({
                'course': cm['course'],
                'total_enrolled': cm['summary']['total_enrolled'],
                'total_dates': cm['summary']['total_dates'],
            })
        return {
            'multi': True,
            'courses': course_previews,
            'summary': matrix['summary'],
            'note': 'Usa export=true per generare un unico file Excel con tutti i corsi (include colonna Corso).',
        }

    # Single-course preview
    preview_rows = []
    for row in matrix['rows'][:10]:
        preview_rows.append({
            'name': row['name'],
            'present_count': row['present_count'],
            'total_days': row['total_days'],
        })

    return {
        'course': matrix['course'],
        'dates': matrix['dates'],
        'total_enrolled': matrix['summary']['total_enrolled'],
        'total_dates': matrix['summary']['total_dates'],
        'preview': preview_rows,
        'note': 'Anteprima dei primi 10 iscritti. Usa export=true per generare il file Excel completo.',
    }


def tool_export_multi_sheet(
    sport_association_id: str,
    sheets: list,
    filename: str = 'Export',
    **kwargs,
) -> dict:
    """Query multiple datasets and export them as separate sheets in one XLSX file."""
    if not sheets:
        return {'error': 'No sheets specified'}

    qb = QueryBuilder(sport_association_id)
    prepared_sheets = []
    total_rows = 0

    for i, sheet_spec in enumerate(sheets):
        model_name = sheet_spec.get('model_name')
        if not model_name:
            return {'error': f'Sheet {i + 1}: model_name is required'}

        result = qb.execute_query(
            model_name=model_name,
            fields=sheet_spec.get('fields'),
            filters=sheet_spec.get('filters'),
            exclude=sheet_spec.get('exclude'),
            or_filters=sheet_spec.get('or_filters'),
            joins=sheet_spec.get('joins'),
            order_by=sheet_spec.get('order_by'),
            limit=sheet_spec.get('limit'),
            include_archived=sheet_spec.get('include_archived', False),
        )

        data = result.get('data', [])
        if not data:
            continue

        prepared_sheets.append({
            'data': data,
            'title': sheet_spec.get('title', model_name),
            'column_labels': sheet_spec.get('column_labels') or {},
        })
        total_rows += len(data)

    if not prepared_sheets:
        return {'error': 'No data to export from any sheet', 'count': 0}

    exporter = EXPORTERS['xlsx']
    safe_filename = sanitize_text(filename)
    file_bytes, content_type, extension = exporter.export_multi_sheet(
        sheets=prepared_sheets,
        filename=safe_filename,
    )

    file_b64 = base64.b64encode(file_bytes).decode('utf-8')

    today = date.today().strftime('%d/%m/%Y')
    sheet_count = len(prepared_sheets)
    model_names = [s.get('model_name', 'unknown') for s in sheets if s.get('model_name')]
    description_hint = f'{total_rows} righe, {sheet_count} fogli'

    return {
        'type': 'export_ready',
        'filename': f'{safe_filename}.{extension}',
        'content_type': content_type,
        'data_base64': file_b64,
        'row_count': total_rows,
        'sheet_count': sheet_count,
        'save_info': {
            'tool_name': 'export_multi_sheet',
            'params': {
                'filename': filename,
                'sheets': sheets,
            },
            'name': f'{filename} - {today}',
            'description': f'Export multi-foglio {filename} - {description_hint}',
            'description_hint': description_hint,
            'ui_config': {},
            'model_names': model_names,
        },
    }


def tool_sanitize_text(text: str, replacement: str = '-', **kwargs) -> dict:
    """Sanitize text for use as a filename."""
    return {'result': sanitize_text(text, replacement)}


def tool_save_report(
    sport_association_id: str,
    name: str,
    tool_name: str,
    params: dict,
    description: str = '',
    ui_config: dict = None,
    user_id: str = None,
    **kwargs,
) -> dict:
    """Save a report configuration for later replay from the frontend."""
    from django.apps import apps
    SavedReport = apps.get_model('application', 'SavedReport')
    User = apps.get_model('application', 'User')

    if not user_id:
        return {'error': 'user_id is required to save a report'}

    try:
        user = User.objects.get(user_id=user_id)
    except User.DoesNotExist:
        return {'error': 'User not found'}

    report = SavedReport.objects.create(
        sport_association_id=sport_association_id,
        created_by=user,
        name=name,
        description=description,
        tool_name=tool_name,
        params=params,
        ui_config=ui_config or {},
    )
    return {
        'saved_report_id': str(report.saved_report_id),
        'name': report.name,
        'message': f'Report "{name}" salvato.',
    }


def tool_list_reports(
    sport_association_id: str,
    user_id: str = None,
    **kwargs,
) -> dict:
    """List saved report configurations for the current user."""
    from django.apps import apps
    SavedReport = apps.get_model('application', 'SavedReport')

    if not user_id:
        return {'error': 'user_id is required to list reports'}

    report_list = list(
        SavedReport.objects.filter(
            sport_association_id=sport_association_id,
            created_by_id=user_id,
        ).values(
            'saved_report_id', 'name', 'description', 'tool_name', 'updated_at',
        )
    )
    return {
        'reports': [
            {
                'saved_report_id': str(r['saved_report_id']),
                'name': r['name'],
                'description': r['description'],
                'tool_name': r['tool_name'],
                'updated_at': r['updated_at'].isoformat(),
            }
            for r in report_list
        ],
        'count': len(report_list),
    }


# ────────────────────────────────────────────────────────────────────
# MCP Server definition (for stdio/SSE transport)
# ────────────────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        'name': 'get_schema',
        'description': (
            'Get the database schema. Returns all available models with their fields, '
            'types, relations, and constraints. Call with no arguments to get all models, '
            'or pass model_name to get a specific model. '
            'Call this first to understand what data is available.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'model_name': {
                    'type': 'string',
                    'description': 'Specific model name (e.g. "Associate", "Payment"). Omit to get all models.',
                },
            },
        },
    },
    {
        'name': 'query_data',
        'description': (
            'Query data from the database. Returns rows as JSON. '
            'All queries are automatically scoped to the current sport association. '
            'Use filters for WHERE clauses, exclude for NOT conditions, joins for related data, order_by for sorting.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'model_name': {
                    'type': 'string',
                    'description': 'Model to query (e.g. "Associate", "Payment", "Course")',
                },
                'fields': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Fields to select. Use related__field for joins. For FK fields use field_id (e.g. associate_id). Empty = all fields.',
                },
                'filters': {
                    'type': 'object',
                    'description': 'Filter conditions as {field__lookup: value}. Lookups: exact, icontains, gt, gte, lt, lte, in, isnull, range, year, month, day, date. Date values as strings (YYYY-MM-DD) are auto-converted.',
                },
                'exclude': {
                    'type': 'object',
                    'description': 'Exclude conditions (NOT matching). Same format as filters. E.g. {"associate_id__in": ["id1","id2"]} to exclude specific records.',
                },
                'or_filters': {
                    'type': 'array',
                    'items': {'type': 'object'},
                    'description': 'List of filter dicts ORed together. Each dict is ANDed internally. E.g. [{"status_flag": 1}, {"status_flag": 4}] matches records with status 1 OR 4.',
                },
                'joins': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Related models to join (e.g. ["associate", "course"])',
                },
                'order_by': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Fields to sort by. Prefix with "-" for descending.',
                },
                'limit': {
                    'type': 'integer',
                    'description': 'Max rows to return (default/max: 5000)',
                },
                'offset': {
                    'type': 'integer',
                    'description': 'Number of rows to skip (for pagination)',
                },
                'include_archived': {
                    'type': 'boolean',
                    'description': 'Set to true to include archived/deleted records. Default: false. Use ONLY when the user explicitly asks for archived data.',
                },
            },
            'required': ['model_name'],
        },
    },
    {
        'name': 'count_data',
        'description': 'Count records matching filters. Faster than query_data for just getting counts.',
        'parameters': {
            'type': 'object',
            'properties': {
                'model_name': {
                    'type': 'string',
                    'description': 'Model to count',
                },
                'filters': {
                    'type': 'object',
                    'description': 'Filter conditions as {field__lookup: value}',
                },
                'exclude': {
                    'type': 'object',
                    'description': 'Exclude conditions (NOT matching). Same format as filters.',
                },
                'or_filters': {
                    'type': 'array',
                    'items': {'type': 'object'},
                    'description': 'List of filter dicts ORed together.',
                },
                'include_archived': {
                    'type': 'boolean',
                    'description': 'Set to true to include archived/deleted records. Default: false.',
                },
            },
            'required': ['model_name'],
        },
    },
    {
        'name': 'get_field_values',
        'description': (
            'Get distinct values for a specific field with optional filters. '
            'Useful for getting IDs or values matching conditions (e.g. all associate_ids with subscriptions in 2025). '
            'Use this to build exclude lists for comparative queries.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'model_name': {
                    'type': 'string',
                    'description': 'Model name',
                },
                'field_name': {
                    'type': 'string',
                    'description': 'Field to get distinct values for (e.g. "associate_id")',
                },
                'filters': {
                    'type': 'object',
                    'description': 'Filter conditions to apply before getting values',
                },
                'exclude': {
                    'type': 'object',
                    'description': 'Exclude conditions (NOT matching)',
                },
                'or_filters': {
                    'type': 'array',
                    'items': {'type': 'object'},
                    'description': 'List of filter dicts ORed together.',
                },
                'limit': {
                    'type': 'integer',
                    'description': 'Max values to return (default: 100)',
                },
                'include_archived': {
                    'type': 'boolean',
                    'description': 'Set to true to include archived/deleted records. Default: false.',
                },
            },
            'required': ['model_name', 'field_name'],
        },
    },
    {
        'name': 'export_data',
        'description': (
            'Query and export data as CSV, XLSX, or PDF. '
            'Returns the file as base64-encoded data for download. '
            'Use column_labels to set Italian display names for columns.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'model_name': {
                    'type': 'string',
                    'description': 'Model to export',
                },
                'format': {
                    'type': 'string',
                    'enum': ['csv', 'xlsx', 'pdf'],
                    'description': 'Export format',
                },
                'fields': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Fields to include',
                },
                'filters': {
                    'type': 'object',
                    'description': 'Filter conditions',
                },
                'exclude': {
                    'type': 'object',
                    'description': 'Exclude conditions (NOT matching)',
                },
                'or_filters': {
                    'type': 'array',
                    'items': {'type': 'object'},
                    'description': 'List of filter dicts ORed together.',
                },
                'joins': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Related models to join',
                },
                'order_by': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Sort fields',
                },
                'limit': {
                    'type': 'integer',
                    'description': 'Max rows',
                },
                'title': {
                    'type': 'string',
                    'description': 'Title for the export document',
                },
                'column_labels': {
                    'type': 'object',
                    'description': 'Mapping of field_name -> Italian display label',
                },
                'include_archived': {
                    'type': 'boolean',
                    'description': 'Set to true to include archived/deleted records. Default: false.',
                },
                'header_text': {
                    'type': 'string',
                    'description': 'Custom header text for PDF export (replaces title in header). Only for PDF.',
                },
                'footer_text': {
                    'type': 'string',
                    'description': 'Custom footer text for PDF export (shown before page number). Only for PDF.',
                },
                'text_before': {
                    'type': 'string',
                    'description': 'Introductory text to display above the table in PDF export. Only for PDF.',
                },
                'text_after': {
                    'type': 'string',
                    'description': 'Notes or text to display below the table in PDF export. Only for PDF.',
                },
            },
            'required': ['model_name', 'format'],
        },
    },
    {
        'name': 'aggregate_data',
        'description': (
            'Run aggregation queries: count, sum, avg, min, max on fields. '
            'Supports GROUP BY with group_by parameter for breakdowns by field or date part '
            '(e.g. group_by=["payment_date__year", "payment_date__month"] for monthly totals). '
            'Useful for totals, averages, statistics, and time-series breakdowns.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'model_name': {
                    'type': 'string',
                    'description': 'Model to aggregate',
                },
                'filters': {
                    'type': 'object',
                    'description': 'Filter conditions',
                },
                'exclude': {
                    'type': 'object',
                    'description': 'Exclude conditions (NOT matching)',
                },
                'or_filters': {
                    'type': 'array',
                    'items': {'type': 'object'},
                    'description': 'List of filter dicts ORed together.',
                },
                'group_by': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': (
                        'Fields to group by. Supports date-part suffixes: '
                        'field__year, field__month, field__day, field__quarter, field__week. '
                        'E.g. ["payment_date__year", "payment_date__month"] for monthly breakdown.'
                    ),
                },
                'aggregations': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'function': {
                                'type': 'string',
                                'enum': ['count', 'sum', 'avg', 'min', 'max'],
                            },
                            'field': {
                                'type': 'string',
                                'description': 'Field to aggregate (use "*" for count)',
                            },
                            'alias': {
                                'type': 'string',
                                'description': 'Result key name',
                            },
                        },
                        'required': ['function', 'field'],
                    },
                    'description': 'List of aggregation operations',
                },
                'include_archived': {
                    'type': 'boolean',
                    'description': 'Set to true to include archived/deleted records. Default: false.',
                },
            },
            'required': ['model_name', 'aggregations'],
        },
    },
    {
        'name': 'get_attendance_matrix',
        'description': (
            'Get the attendance register as a matrix: enrolled people (rows) x lesson dates (columns), '
            'with "P" for present. Use this when the user asks for attendance/presence data. '
            'Omit course_id and course_name to get ALL courses with attendance data in a single result. '
            'With export=false (default) returns a preview. With export=true returns a file for download. '
            'Supports both XLSX and PDF export via the format parameter. '
            'For PDF with multiple courses, creates one page per course.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'course_id': {
                    'type': 'string',
                    'description': 'UUID of the course. Use this if you know the exact ID.',
                },
                'course_name': {
                    'type': 'string',
                    'description': 'Partial course name for search (case insensitive). Used if course_id is not given.',
                },
                'date_from': {
                    'type': 'string',
                    'description': 'Start date filter (YYYY-MM-DD). Optional.',
                },
                'date_to': {
                    'type': 'string',
                    'description': 'End date filter (YYYY-MM-DD). Optional.',
                },
                'export': {
                    'type': 'boolean',
                    'description': 'If true, returns the full matrix as a file for download. Default: false (preview only).',
                },
                'format': {
                    'type': 'string',
                    'enum': ['xlsx', 'pdf'],
                    'description': 'Export format. Default: xlsx.',
                },
                'multi_sheet': {
                    'type': 'boolean',
                    'description': 'If true and exporting multiple courses as XLSX, creates one sheet per course instead of a single flat sheet. Use with export=true and format=xlsx. Default: false.',
                },
            },
        },
    },
    {
        'name': 'export_multi_sheet',
        'description': (
            'Export multiple datasets as separate sheets in a single XLSX file. '
            'Each sheet can query a different model with its own fields, filters, and column labels. '
            'Use this when the user wants combined exports (e.g. members + payments in one file).'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'sheets': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'model_name': {
                                'type': 'string',
                                'description': 'Model to query for this sheet',
                            },
                            'title': {
                                'type': 'string',
                                'description': 'Sheet tab name (max 31 chars)',
                            },
                            'fields': {
                                'type': 'array',
                                'items': {'type': 'string'},
                                'description': 'Fields to include',
                            },
                            'filters': {
                                'type': 'object',
                                'description': 'Filter conditions',
                            },
                            'exclude': {
                                'type': 'object',
                                'description': 'Exclude conditions',
                            },
                            'or_filters': {
                                'type': 'array',
                                'items': {'type': 'object'},
                                'description': 'OR filter conditions',
                            },
                            'joins': {
                                'type': 'array',
                                'items': {'type': 'string'},
                                'description': 'Related models to join',
                            },
                            'order_by': {
                                'type': 'array',
                                'items': {'type': 'string'},
                                'description': 'Sort fields',
                            },
                            'limit': {
                                'type': 'integer',
                                'description': 'Max rows for this sheet',
                            },
                            'column_labels': {
                                'type': 'object',
                                'description': 'Mapping of field_name -> display label',
                            },
                            'include_archived': {
                                'type': 'boolean',
                                'description': 'Include archived/deleted records. Default: false.',
                            },
                        },
                        'required': ['model_name'],
                    },
                    'description': 'List of sheet specifications, each querying a different dataset',
                },
                'filename': {
                    'type': 'string',
                    'description': 'Filename for the XLSX file (without extension)',
                },
            },
            'required': ['sheets'],
        },
    },
    {
        'name': 'sanitize_text',
        'description': (
            'Remove characters that are unsafe for filenames from a string. '
            'Replaces characters like / \\ : * ? " < > | with the replacement character.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'text': {
                    'type': 'string',
                    'description': 'The text to sanitize',
                },
                'replacement': {
                    'type': 'string',
                    'description': 'Replacement character for unsafe chars. Default: "-"',
                },
            },
            'required': ['text'],
        },
    },
    {
        'name': 'save_report',
        'description': (
            'Save a report configuration so the user can replay it from the frontend without the AI. '
            'Call this after generating a report when the user asks to save it. '
            'Provide the tool_name and params you just used, plus a human-readable name. '
            'The ui_config should describe which fields are editable in the frontend (date pickers, format selects, etc.).'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'name': {
                    'type': 'string',
                    'description': 'Human-readable name for the saved report (e.g. "Presenze anno sportivo 2025/26")',
                },
                'tool_name': {
                    'type': 'string',
                    'description': 'The tool that generates this report (e.g. "get_attendance_matrix", "export_data")',
                },
                'params': {
                    'type': 'object',
                    'description': 'The full parameter dict used to generate the report (excluding sport_association_id)',
                },
                'description': {
                    'type': 'string',
                    'description': 'Optional description of what this report contains',
                },
                'ui_config': {
                    'type': 'object',
                    'description': (
                        'UI metadata for the frontend edit form. Should contain: '
                        '"fields" (list of editable params with key, label, type, options) '
                        'and optionally "available_columns" and "filter_fields" for export_data/query_data tools.'
                    ),
                },
            },
            'required': ['name', 'tool_name', 'params'],
        },
    },
    {
        'name': 'list_reports',
        'description': (
            'List the user\'s saved report configurations. '
            'Use this when the user asks to see their saved reports.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {},
        },
    },
]


def create_mcp_server(sport_association_id: str) -> Server:
    """Create and configure an MCP server instance."""
    server = Server("bakney-sport-data")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=t['name'],
                description=t['description'],
                inputSchema=t['parameters'],
            )
            for t in TOOL_DEFINITIONS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            if name == 'get_schema':
                result = tool_get_schema(sport_association_id, **arguments)
            elif name == 'query_data':
                result = tool_query_data(sport_association_id, **arguments)
            elif name == 'count_data':
                result = tool_count_data(sport_association_id, **arguments)
            elif name == 'get_field_values':
                result = tool_get_field_values(sport_association_id, **arguments)
            elif name == 'export_data':
                result = tool_export_data(sport_association_id, **arguments)
            elif name == 'aggregate_data':
                result = tool_aggregate_data(sport_association_id, **arguments)
            elif name == 'get_attendance_matrix':
                result = tool_get_attendance_matrix(sport_association_id, **arguments)
            elif name == 'export_multi_sheet':
                result = tool_export_multi_sheet(sport_association_id, **arguments)
            elif name == 'sanitize_text':
                result = tool_sanitize_text(**arguments)
            elif name == 'save_report':
                result = tool_save_report(sport_association_id, **arguments)
            elif name == 'list_reports':
                result = tool_list_reports(sport_association_id, **arguments)
            else:
                result = {'error': f'Unknown tool: {name}'}

            return [TextContent(type="text", text=json.dumps(result, default=str))]
        except Exception as e:
            logger.exception(f"MCP tool error: {name}")
            return [TextContent(type="text", text=json.dumps({'error': str(e)}))]

    return server


async def run_stdio_server(sport_association_id: str):
    """Run the MCP server using stdio transport."""
    server = create_mcp_server(sport_association_id)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
