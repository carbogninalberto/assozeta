"""MCP server family: Django's business MCP tools and run_mcp_server versus Go.

Private PostgreSQL with the current Django schema and the rows of
mcp_server_seed.py (two associations; every whitelisted model with foreign,
deleted, archived and disabled controls). Two comparisons, both on the same
rows:

1. Tool functions: each authored call runs the actual Django tool function
   the way create_mcp_server's call_tool does (exceptions become
   {'error': str(e)}, json.dumps(result, default=str)) and the Go
   BusinessMCPTools (race-built driver); the JSON texts must be byte
   identical. Calls Go does not port (XLSX/PDF files) must be refused by Go.
   Authored expectations (from reading the Django code) are checked on
   Django first.
2. Protocol: the actual `manage.py run_mcp_server` process and the
   race-built Go cmd/mcp-server get the same JSON-lines sessions over pipes;
   stdout must be byte identical after replacing the uuid4 a save_report call
   generates and the times its row gets. Exit codes match; the Go command's stderr line is Django's.

Set ASSOZETA_MCP_SERVER_GO_ROOT to another BE_GO tree (the RED receipt uses
the base revision's) and ASSOZETA_MCP_SERVER_LABEL to name the receipt.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.mcp_server_seed import ASSOCIATION, IDS, OWNER, seed

DATABASE = 'assozeta_contract_mcp_server'
I = {key: str(value) for key, value in IDS.items()}

# (tool, arguments, authored expectation): None (Django/Go parity only), a
# dict whose items the result must contain, ('prefix', text) for an error
# text prefix, or 'refused' for a call Go must refuse.
CASES = [
    ('count_data', {'model_name': 'Associate'}, {'count': 3}),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name', 'last_name', 'sex'], 'order_by': ['last_name']}, {'count': 3, 'data': [{'first_name': 'Marco', 'last_name': 'Bianchi', 'sex': 'Maschio'}, {'first_name': 'Luca', 'last_name': 'D&Angelo', 'sex': 'Altro'}, {'first_name': 'Anna', 'last_name': 'Rossi', 'sex': 'Femmina'}]}),
    ('query_data', {'model_name': 'Associate'}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title', 'fee', 'status_flag', 'course_type', 'start_date'], 'order_by': ['-fee']}, None),
    ('query_data', {'model_name': 'CourseSubscription', 'fields': ['course__title', 'subscription__associate__first_name', 'paid', 'type'], 'filters': {'course__title__icontains': 'nuoto'}, 'order_by': ['course__title', 'subscription__associate__first_name']}, None),
    ('count_data', {'model_name': 'Course', 'filters': {'coursesubscription__paid': True}}, {'count': 2}),
    ('query_data', {'model_name': 'Course', 'fields': ['title', 'coursesubscription__paid'], 'or_filters': [{'coursesubscription__paid': True}, {'title': 'Yoga &amp; Pilates'}], 'order_by': ['title']}, None),
    ('aggregate_data', {'model_name': 'Payment', 'aggregations': [{'function': 'sum', 'field': 'amount'}, {'function': 'count', 'field': '*'}, {'function': 'avg', 'field': 'amount', 'alias': 'media'}]}, {'sum_amount': 87.85, 'count_*': 3}),
    ('aggregate_data', {'model_name': 'Payment', 'aggregations': [{'function': 'sum', 'field': 'amount'}], 'group_by': ['payment_date__year', 'payment_date__month', 'type']}, None),
    ('get_field_values', {'model_name': 'Payment', 'field_name': 'type'}, None),
    ('get_field_values', {'model_name': 'Payment', 'field_name': 'associate__last_name'}, None),
    ('query_data', {'model_name': 'Payment', 'fields': ['payment_date__year', 'payment_date__week_day', 'payment_date__date', 'payment_date', 'amount', 'meta', 'description', 'associate__first_name', 'type', 'payment_date__hour'], 'order_by': ['payment_date']}, None),
    ('query_data', {'model_name': 'Instructor', 'fields': ['first_name', 'last_name', 'born_date']}, None),
    ('query_data', {'model_name': 'MedicalCertificate', 'fields': ['expiration_date', 'notes']}, None),
    ('query_data', {'model_name': 'CarnetSubscription', 'fields': ['meta__lessons_left', 'carnet_id__title']}, None),
    ('count_data', {'model_name': 'Subscription', 'filters': {'tags__tag_name': 'Agonista'}}, {'count': 2}),
    ('query_data', {'model_name': 'Subscription', 'fields': ['associate__first_name', 'tags__tag_name'], 'filters': {'tags__tag_name__startswith': 'Under_'}}, None),
    ('query_data', {'model_name': 'Course', 'filters': {'titolo': 'x'}}, ('prefix', "Cannot resolve keyword 'titolo' into field. Choices are: attendanceregistry, ")),
    ('query_data', {'model_name': 'Course', 'fields': ['title__foo']}, {'error': "Cannot resolve keyword 'foo' into field. Join on 'title' not permitted."}),
    ('query_data', {'model_name': 'Course', 'fields': ['sport_association__foo']}, None),
    ('query_data', {'model_name': 'Course', 'filters': {'course_id': 'abc'}}, {'error': '[\'"abc" non è uno UUID valido.\']'}),
    ('query_data', {'model_name': 'Course', 'filters': {'start_date': '01/02/2025'}}, None),
    ('query_data', {'model_name': 'Course', 'filters': {'creation_date': '2025-02-30'}}, None),
    ('query_data', {'model_name': 'Course', 'filters': {'status_flag': 'abc'}}, {'error': "Field 'status_flag' expected a number but got 'abc'."}),
    ('query_data', {'model_name': 'Course', 'filters': {'fee': 'abc'}}, {'error': '[\'Il valore "abc" deve essere un numero decimale.\']'}),
    ('query_data', {'model_name': 'Course', 'filters': {'pinned': 'abc'}}, {'error': '[\'Il valore "abc" deve essere True oppure False.\']'}),
    ('query_data', {'model_name': 'User'}, {'error': "Model 'User' is not whitelisted"}),
    ('get_schema', {'model_name': 'Tags'}, None),
    ('get_schema', {}, None),
    ('get_schema', {'model_name': 'Nope'}, {'error': 'Model Nope not found'}),
    ('query_data', {'model_name': 'Payment', 'fields': ['amount'], 'exclude': {'associate__first_name': 'Anna'}, 'order_by': ['amount']}, {'count': 2, 'data': [{'amount': 12.35}, {'amount': 30.0}]}),
    ('query_data', {'model_name': 'Payment', 'fields': ['amount'], 'exclude': {'description': 'Piscina'}, 'order_by': ['amount']}, {'count': 2, 'data': [{'amount': 30.0}, {'amount': 45.5}]}),
    ('query_data', {'model_name': 'Payment', 'fields': ['amount'], 'filters': {'associate_id': I['anna']}, 'exclude': {'associate__last_name': 'X'}}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'associate__isnull': True}}, {'count': 1}),
    ('count_data', {'model_name': 'Payment', 'filters': {'associate': None}}, {'count': 1}),
    ('count_data', {'model_name': 'Payment', 'exclude': {'associate__first_name__isnull': True}}, None),
    ('count_data', {'model_name': 'Payment', 'or_filters': [{'associate__first_name': 'Anna'}, {'expense': True}]}, {'count': 2}),
    ('count_data', {'model_name': 'Payment', 'or_filters': [{'associate__first_name': 'Anna', 'type': 'cash'}, {'expense': True}, {}, 'x']}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'payment_date__year': 2025, 'payment_date__month__in': [3, 7]}}, {'count': 2}),
    ('count_data', {'model_name': 'Payment', 'filters': {'payment_date__year__gte': '2025'}}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'payment_date__year__lt': 2025}}, {'count': 1}),
    ('count_data', {'model_name': 'Payment', 'filters': {'payment_date__date': '2025-03-04'}}, {'count': 1}),
    ('count_data', {'model_name': 'Payment', 'filters': {'payment_date__gte': '2025-03-04', 'payment_date__lt': '2025-07-04T23:30:00'}}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'payment_date__gte': '2025-03-04T10:00:00Z'}}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'payment_date__range': ['2025-01-01', '2025-12-31']}}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'amount__gte': '30', 'amount__lt': 45.6}}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'amount__in': ['30.00', 45.5]}}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'type__in': []}}, {'count': 0}),
    ('count_data', {'model_name': 'Payment', 'exclude': {'type__in': []}}, {'count': 3}),
    ('count_data', {'model_name': 'Payment', 'include_archived': True}, {'count': 4}),
    ('count_data', {'model_name': 'Payment', 'filters': {'meta__has_key': 'b'}}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'meta__b': 1}}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'description__iexact': 'piscina', 'expense': 'True'}}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'password': 'x', 'stripe_x': 1}}, {'count': 3}),
    ('count_data', {'model_name': 'Payment', 'filters': {'type': None}}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'amount__gt': None}}, {'error': 'Cannot use None as a query value'}),
    ('count_data', {'model_name': 'Payment', 'filters': {'associate__first_name__isnull': 'yes'}}, {'error': 'The QuerySet value for an isnull lookup must be True or False.'}),
    ('count_data', {'model_name': 'Payment', 'filters': {'pk': I['p1']}}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'associate__subscription__status_flag': 1}}, None),
    ('count_data', {'model_name': 'Payment', 'filters': {'': 1}}, {'error': "Cannot parse keyword query ''"}),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name'], 'order_by': ['first_name'], 'limit': 2}, {'count': 3, 'has_more': True, 'data': [{'first_name': 'Anna'}, {'first_name': 'Luca'}]}),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name'], 'order_by': ['first_name'], 'limit': 2, 'offset': 2}, None),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name'], 'order_by': ['first_name'], 'offset': -1}, {'error': 'Negative indexing is not supported.'}),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name'], 'order_by': ['nope']}, None),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name'], 'order_by': ['-born_date__year', 'sport_association']}, None),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name', 'password', 'subscription__status_flag'], 'order_by': ['--x']}, None),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name', 'subscription__status_flag', 'subscription__tags'], 'order_by': ['first_name', 'subscription__status_flag']}, None),
    ('query_data', {'model_name': 'application.Associate', 'fields': ['nope', 'stripe_id']}, None),
    ('query_data', {'model_name': 'application.User'}, None),
    ('get_field_values', {'model_name': 'Associate', 'field_name': 'sex'}, {'values': ['Altro', 'Femmina', 'Maschio'], 'total_distinct': 3}),
    ('get_field_values', {'model_name': 'Associate', 'field_name': 'sex', 'limit': None}, None),
    ('get_field_values', {'model_name': 'Associate', 'field_name': 'subscription__tags__tag_name', 'limit': 1}, None),
    ('get_field_values', {'model_name': 'Payment', 'field_name': 'meta'}, {'error': "unhashable type: 'dict'"}),
    ('get_field_values', {'model_name': 'Payment', 'field_name': 'password'}, {'error': "Field 'password' is not allowed"}),
    ('get_field_values', {'model_name': 'Payment', 'field_name': 'associate'}, None),
    ('aggregate_data', {'model_name': 'Course', 'aggregations': [{'function': 'avg', 'field': 'fee'}, {'function': 'max', 'field': 'start_date'}, {'function': 'count', 'field': 'coursesubscription'}]}, None),
    ('aggregate_data', {'model_name': 'Course', 'aggregations': [{'function': 'sum', 'field': 'coursesubscription__type'}], 'group_by': ['title']}, None),
    ('aggregate_data', {'model_name': 'Course', 'aggregations': [{'function': 'count', 'field': '*', 'alias': 'title'}], 'group_by': ['title']}, {'error': "The annotation 'title' conflicts with a field on the model."}),
    ('aggregate_data', {'model_name': 'Course', 'aggregations': [{'function': 'median', 'field': 'fee'}]}, {'error': 'No valid aggregations'}),
    ('aggregate_data', {'model_name': 'Course', 'aggregations': []}, {'error': 'No aggregations specified'}),
    ('aggregate_data', {'model_name': 'Course', 'aggregations': [{'function': 'count', 'field': '*'}], 'group_by': ['nope']}, {'error': 'No valid group_by fields'}),
    ('aggregate_data', {'model_name': 'Course', 'aggregations': [{'function': 'count', 'field': '*', 'alias': 'a b'}]}, {'error': 'Column aliases cannot contain whitespace characters, hashes, control characters, quotation marks, semicolons, or SQL comments.'}),
    ('aggregate_data', {'model_name': 'Subscription', 'aggregations': [{'function': 'count', 'field': '*'}], 'group_by': ['status_flag', 'associate']}, None),
    ('aggregate_data', {'model_name': 'Subscription', 'aggregations': [{'function': 'sum', 'field': '*'}]}, None),
    ('export_data', {'model_name': 'Payment', 'format': 'csv', 'fields': ['payment_date', 'amount', 'type', 'description', 'expense', 'associate__first_name', 'payment_id'], 'order_by': ['payment_date'], 'column_labels': {'amount': 'Importo', 'payment_date': 'Data'}, 'title': 'Pagamenti: 2025/26'}, None),
    ('export_data', {'model_name': 'Associate', 'format': 'csv', 'filters': {'sex': 'F', 'first_name__icontains': 'a'}}, None),
    ('export_data', {'model_name': 'Associate', 'format': 'csv', 'filters': {'first_name': 'Nessuno'}}, {'error': 'No data to export', 'count': 0}),
    ('export_data', {'model_name': 'Associate', 'format': 'xlsx', 'filters': {'first_name': 'Nessuno'}}, {'error': 'No data to export', 'count': 0}),
    ('export_data', {'model_name': 'Associate', 'format': 'xlsx'}, {'type': 'export_ready', 'filename': 'Associate.xlsx'}),
    ('export_data', {'model_name': 'Payment', 'format': 'xlsx', 'title': 'Incassi & spese', 'fields': ['payment_date', 'amount', 'type', 'description', 'associate__first_name'], 'order_by': ['payment_date'], 'column_labels': {'amount': 'Importo', 'payment_date': 'Data'}}, None),
    ('export_data', {'model_name': 'Course', 'format': 'xlsx', 'fields': ['title', 'fee', 'status_flag', 'start_date'], 'order_by': ['title'], 'limit': 2}, {'type': 'export_ready'}),
    ('export_data', {'model_name': 'Associate', 'format': 'pdf', 'fields': ['first_name', 'last_name', 'sex'], 'order_by': ['last_name'], 'title': 'Soci'}, {'type': 'export_ready', 'filename': 'Soci.pdf'}),
    ('export_data', {'model_name': 'Payment', 'format': 'pdf', 'fields': ['payment_date', 'amount', 'description'], 'order_by': ['payment_date'], 'header_text': 'Pagamenti', 'footer_text': 'Pagina', 'text_before': 'Prima', 'text_after': 'Dopo'}, {'type': 'export_ready'}),
    ('export_data', {'model_name': 'Course', 'format': 'pdf', 'fields': ['title', 'fee', 'start_date'], 'order_by': ['title'], 'column_labels': {'title': 'Corso', 'fee': 'Quota'}, 'include_archived': True}, {'type': 'export_ready'}),
    ('export_data', {'model_name': 'Associate', 'format': 'xlsx', 'title': 5}, 'refused'),
    ('export_data', {'model_name': 'Associate', 'format': 'doc'}, {'error': 'Unsupported format: doc. Use: csv, xlsx, pdf'}),
    ('export_data', {'model_name': 'Nope', 'format': 'csv'}, {'error': "Model 'Nope' is not whitelisted"}),
    ('export_data', {'model_name': 'Course', 'format': 'csv', 'fields': ['title', 'description', 'fee', 'start_date', 'status_flag'], 'order_by': ['title'], 'limit': 2, 'include_archived': True, 'header_text': 'x'}, None),
    ('export_multi_sheet', {'sheets': []}, {'error': 'No sheets specified'}),
    ('export_multi_sheet', {'sheets': [{'model_name': 'Associate'}, {'title': 'x'}]}, {'error': 'Sheet 2: model_name is required'}),
    ('export_multi_sheet', {'sheets': [{'model_name': 'Associate', 'filters': {'first_name': 'Nessuno'}}]}, {'error': 'No data to export from any sheet', 'count': 0}),
    ('export_multi_sheet', {'sheets': [{'model_name': 'Associate', 'filters': {'nope': 1}}]}, None),
    ('export_multi_sheet', {'sheets': [{'model_name': 'Associate'}]}, {'type': 'export_ready', 'filename': 'Export.xlsx'}),
    ('export_multi_sheet', {'sheets': [{'model_name': 'Course', 'title': 'Corsi', 'fields': ['title', 'fee'], 'order_by': ['title']}, {'model_name': 'Payment', 'fields': ['payment_date', 'amount'], 'order_by': ['payment_date'], 'column_labels': {'amount': 'Importo'}}, {'model_name': 'Associate', 'filters': {'first_name': 'Nessuno'}}], 'filename': 'Riepilogo/anno'}, {'type': 'export_ready', 'filename': 'Riepilogo-anno.xlsx'}),
    ('get_attendance_matrix', {}, {'multi': True}),
    ('get_attendance_matrix', {'course_id': I['nuoto']}, {'course': 'Nuoto Adulti', 'dates': ['03/03/2025', '10/03/2025', '01/04/2025'], 'total_enrolled': 2, 'total_dates': 3}),
    ('get_attendance_matrix', {'course_id': I['nuoto'], 'date_from': '2025-03-05', 'date_to': '2025-12-31'}, None),
    ('get_attendance_matrix', {'course_id': I['nuoto'], 'date_from': '2026-01-01'}, None),
    ('get_attendance_matrix', {'course_id': I['bambini']}, {'error': 'Nessuna giornata di presenza trovata per questo corso nel periodo indicato.', 'course': 'Nuoto_Bambini'}),
    ('get_attendance_matrix', {'course_id': I['foreign_course']}, None),
    ('get_attendance_matrix', {'course_id': 'abc'}, None),
    ('get_attendance_matrix', {'course_name': 'nuoto'}, {'error': "Trovati piu' corsi con quel nome. Specifica meglio."}),
    ('get_attendance_matrix', {'course_name': 'yoga'}, {'course': 'Yoga &amp; Pilates', 'total_enrolled': 2}),
    ('get_attendance_matrix', {'course_name': 'zumba'}, {'error': 'Nessun corso trovato con nome: zumba'}),
    ('get_attendance_matrix', {'course_name': 'o', 'date_from': 'bad'}, None),
    ('get_attendance_matrix', {'date_from': '2025-05-01'}, None),
    ('get_attendance_matrix', {'date_from': '2027-05-01'}, {'error': 'Nessun dato di presenza trovato per nessun corso nel periodo indicato.'}),
    ('get_attendance_matrix', {'course_id': I['nuoto'], 'export': True}, {'type': 'export_ready'}),
    ('get_attendance_matrix', {'course_id': I['nuoto'], 'export': True, 'format': 'pdf'}, {'type': 'export_ready'}),
    ('get_attendance_matrix', {'export': True}, {'type': 'export_ready'}),
    ('get_attendance_matrix', {'export': True, 'multi_sheet': True}, {'type': 'export_ready'}),
    ('get_attendance_matrix', {'export': True, 'format': 'pdf'}, {'type': 'export_ready'}),
    ('get_attendance_matrix', {'course_name': 'yoga', 'export': True, 'date_from': '2025-01-01'}, {'type': 'export_ready'}),
    ('get_attendance_matrix', {'course_name': 'zumba', 'export': True}, None),
    ('sanitize_text', {'text': 'a/b'}, {'result': 'a-b'}),
    ('list_reports', {'user_id': str(I['anna'])}, None),
    ('query_data', {'model_name': 'SportAssociation', 'fields': ['denomination', 'tax_code', 'user__first_name']}, None),
    ('query_data', {'model_name': 'Group'}, None),
    ('query_data', {'model_name': 'Invoice'}, None),
    ('query_data', {'model_name': 'InvoiceRows', 'order_by': ['unit_price']}, None),
    ('query_data', {'model_name': 'BalanceSheet'}, None),
    ('query_data', {'model_name': 'CustomAccounts'}, None),
    ('query_data', {'model_name': 'Tags', 'order_by': ['tag_name']}, None),
    ('query_data', {'model_name': 'CourseTags', 'fields': ['tag_name', 'course__title']}, None),
    ('query_data', {'model_name': 'Carnet'}, None),
    ('query_data', {'model_name': 'CarnetSubscription'}, None),
    ('query_data', {'model_name': 'Module'}, None),
    ('query_data', {'model_name': 'AttendanceRegistry'}, None),
    ('query_data', {'model_name': 'AttendanceDay', 'order_by': ['date']}, None),
    ('query_data', {'model_name': 'Reminders'}, None),
    ('query_data', {'model_name': 'Subscription', 'order_by': ['subscription_number']}, None),
    ('query_data', {'model_name': 'Payment', 'order_by': ['payment_date']}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'CourseSubscription', 'order_by': ['creation_date', 'course_subscription_id']}, None),
    ('query_data', {'model_name': 'MedicalCertificate'}, None),
    ('query_data', {'model_name': 'Instructor'}, None),
    ('query_data', {'model_name': 'PaymentCategory', 'include_archived': True, 'order_by': ['name']}, None),

    # R1: regex and pattern lookups on non-text columns, unknown lookups,
    # date/time transforms and their values/ordering/grouping.
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'title__regex': '^Nuoto'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'title__iregex': '^nuoto.b'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'fee__regex': '^4'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'title__nope': 'x'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'title__exact__nope': 'x'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'title__lower': 'x'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'title__length': 5}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'title__search': 'x'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'fee__contains': '5'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'fee__icontains': 45}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'fee__startswith': 4}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__contains': 2}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__contains': '2025'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__startswith': '2025-09'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'pinned__contains': 'a'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'course_id__contains': 'a'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'course_id__gt': '00000000-0000-0000-0000-000000000000'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'course_id__icontains': 'A'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'course_id__startswith': '0'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'pinned__gt': False}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'pinned__lte': True}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__minute': 0}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__second': 0}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__time': '08:00:00'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__iso_year': 2025}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__iso_week_day': 1}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__year': 0}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__year': 10000}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__year__lt': 20000}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__year': None}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__month': None}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__date__year': 2025}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__time__hour': 8}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__month': 9.5}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__month': 99999999999999999999}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'sport_association__regex': 'x'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'sport_association__contains': 'x'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'sport_association__gt': '00000000-0000-0000-0000-000000000000'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'title__': 'x'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'title____exact': 'x'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name'], 'filters': {'born_date': '19900501'}, 'order_by': ['first_name']}, None),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name'], 'filters': {'born_date': '1990-05-01T10:00:00+02:00'}, 'order_by': ['first_name']}, None),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name'], 'filters': {'born_date': 5}, 'order_by': ['first_name']}, None),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name'], 'filters': {'born_date__gt': '1990-W18-1'}, 'order_by': ['first_name']}, None),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name'], 'filters': {'born_date__hour': 1}, 'order_by': ['first_name']}, None),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name'], 'filters': {'born_date__date': '1990-05-01'}, 'order_by': ['first_name']}, None),
    ('query_data', {'model_name': 'Associate', 'fields': ['first_name'], 'filters': {'born_date__iso_week_day': 2}, 'order_by': ['first_name']}, None),
    ('aggregate_data', {'model_name': 'Payment', 'aggregations': [{'function': 'count', 'field': '*'}], 'group_by': ['payment_date__minute']}, None),
    ('get_field_values', {'model_name': 'Course', 'field_name': 'start_date__time'}, None),
    ('get_field_values', {'model_name': 'Course', 'field_name': 'start_date__minute'}, None),
    ('get_field_values', {'model_name': 'Payment', 'field_name': 'payment_date__time'}, None),

    # Paths through relations that are not listed for the model: answered as an unknown or excluded name.
    ('get_schema', {'model_name': 'Instructor'}, None),
    ('get_schema', {'model_name': 'SportAssociation'}, None),
    ('get_schema', {'model_name': 'Payment'}, None),
    ('query_data', {'model_name': 'SportAssociation'}, None),
    ('query_data', {'model_name': 'Instructor', 'fields': ['first_name', 'user__email']}, None),
    ('get_field_values', {'model_name': 'Instructor', 'field_name': 'user__email'}, None),
    ('aggregate_data', {'model_name': 'Instructor', 'aggregations': [{'function': 'count', 'field': '*'}], 'group_by': ['user__email']}, None),
    ('query_data', {'model_name': 'Subscription', 'filters': {'associate__user__email__startswith': 'a'}, 'fields': ['subscription_id']}, None),
    ('count_data', {'model_name': 'Payment', 'exclude': {'user__is_staff': True}}, None),

    # R5: limit/offset as Python numbers, repeated fields/group_by names, field_name types.
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'], 'limit': True}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'], 'limit': 1.5}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'],
                    'limit': 1000000000000000000000000000000}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'], 'offset': True}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'], 'offset': 1.5}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'], 'offset': '1'}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title', 'title']}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title', 'fee', 'title']}, None),
    ('aggregate_data', {'model_name': 'Payment', 'aggregations': [{'function': 'sum', 'field': 'amount'}],
                        'group_by': ['type', 'type']}, None),
    ('get_field_values', {'model_name': 'Course', 'field_name': 5}, None),
    ('get_field_values', {'model_name': 'Course', 'field_name': None}, None),
    ('get_field_values', {'model_name': 'Course', 'field_name': ['title']}, None),
    ('get_field_values', {'model_name': 'Course', 'field_name': 'title', 'limit': True}, None),
    ('get_field_values', {'model_name': 'Course', 'field_name': 'title', 'limit': '1'}, None),

    # R5: joins are validated and then dropped by values(); HTML references.
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'], 'joins': [5]}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'], 'joins': [None]}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'], 'joins': [[1]]}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'], 'joins': [{'a': 1}]}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'], 'joins': [True]}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'], 'joins': 5}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'], 'joins': 'tags'}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'],
                    'joins': {'sport_association': 1}}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'], 'joins': True}, None),
    ('query_data', {'model_name': 'Course', 'order_by': ['title'], 'fields': ['title'],
                    'filters': {'title__contains': '&'}}, None),
    ('query_data', {'model_name': 'Associate', 'order_by': ['last_name'], 'fields': ['last_name'],
                    'filters': {'last_name__contains': 'Angelo'}}, None),
    ('query_data', {'model_name': 'Payment', 'order_by': ['amount'], 'fields': ['description'],
                    'filters': {'description__contains': 'Quota'}}, None),
    ('query_data', {'model_name': 'SportAssociation', 'fields': ['denomination']}, None),

    # R2: filter values of unusual types and spellings (value preparation).
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__in': None}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__range': None}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__range': [1]}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__range': [1, 2, 3]}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__range': [None, 2]}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__range': [1, None]}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__in': 'abc'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__in': '12'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__in': 2}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__in': {'1': 2}}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__range': '12'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'title__in': 'Nuoto Adulti'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag': 99999999999}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__gt': -99999999999}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__lt': 99999999999}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag__in': [2, 99999999999]}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag': 2.5}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag': '٢'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag': [2]}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'status_flag': {'a': 1}}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'fee': '٤٥.٥٠'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'fee': 'inf'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'fee': 'nan'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'fee': True}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'fee': [1]}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'pinned': []}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'course_id': 5}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'course_id': True}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'course_id': [1]}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'course_id': 1.5}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'course_id': '+0000000000000000000000000000000'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date': '20250901'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date': '2025-W36-1'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date': 5}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date': True}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date': 1.5}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date': [1]}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date__gt': '2025-09-01T08'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'start_date': ' 2025-09-01'}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'title': [1]}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'title': {'a': 1}}, 'order_by': ['title']}, None),
    ('query_data', {'model_name': 'Course', 'fields': ['title'], 'filters': {'title__contains': [1]}, 'order_by': ['title']}, None),
]

# JSON-lines sessions for the stdio comparison: (label, association, lines).
INIT = '{"jsonrpc":"2.0","id":"init","method":"initialize","params":{"protocolVersion":"%s","capabilities":{},"clientInfo":{"name":"contract","version":"1"}}}'
INITIALIZED = '{"jsonrpc":"2.0","method":"notifications/initialized"}'


def call(identifier, name, arguments):
    return json.dumps({'jsonrpc': '2.0', 'id': identifier, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}},
                      ensure_ascii=False)


SESSIONS = [
    ('main', str(ASSOCIATION), [
        INIT % '2025-06-18', INITIALIZED,
        '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}',
        call(3, 'count_data', {'model_name': 'Associate'}),
        call(4, 'count_data', {'model_name': 'Associate', 'sport_association_id': 'x'}),
        call(5, 'count_data', {'model_name': 'Associate', 'fn': 'x'}),
        call(6, 'count_data', {}),
        call(7, 'query_data', {'model_name': 5, 'fields': 'x'}),
        call(8, 'nope', {}),
        call(9, 'query_data', {'model_name': 'Payment', 'fields': ['amount', 'description', 'meta', 'associate__first_name'],
                               'order_by': ['payment_date']}),
        call(10, 'aggregate_data', {'model_name': 'Course', 'aggregations': [{'function': 'x', 'field': 'fee'}]}),
        call(11, 'aggregate_data', {'model_name': 'Payment', 'aggregations': [{'function': 'sum'}]}),
        call(12, 'aggregate_data', {'model_name': 'Payment', 'aggregations': [{'function': 'sum', 'field': 1}, {'field': 'x'}],
                                    'group_by': 'x'}),
        call(13, 'export_multi_sheet', {'sheets': [{'title': 'a'}, 5]}),
        call(14, 'query_data', {'model_name': 'Associate', 'limit': 2.0, 'offset': '1', 'include_archived': 'no'}),
        call(15, 'query_data', {'model_name': 'Associate', 'limit': 2.0, 'fields': ['first_name'], 'order_by': ['first_name']}),
        call(16, 'export_data', {'model_name': 'Associate', 'format': 'csv', 'fields': ['first_name', 'last_name', 'sex'],
                                 'order_by': ['last_name'], 'title': 'Soci ò "2025"'}),
        call(17, 'get_attendance_matrix', {'course_name': 'nuoto', 'date_to': '2025-03-31'}),
        call(18, 'get_attendance_matrix', {'course_id': I['nuoto'], 'date_to': '2025-03-31'}),
        call(19, 'get_schema', {'model_name': 'Carnet'}),
        call(20, 'sanitize_text', {'text': 'a/b', 'sport_association_id': 'x'}),
        call(21, 'list_reports', {'user_id': str(OWNER)}),
        call(22, 'save_report', {'name': 'Presenze', 'tool_name': 'get_attendance_matrix', 'params': {'b': 1, 'a': [2]},
                                 'user_id': str(OWNER)}),
        call(23, 'list_reports', {'user_id': str(OWNER)}),
        call(24, 'count_data', {'model_name': 'Associate', 'filters': {'first_name': 'Ànna ☃'}}),
        call(25, 'count_data', {'model_name': 'Nope é'}),
        call(26, 'get_field_values', {'model_name': 'Course', 'field_name': 'status_flag', 'filters': {'title__icontains': 'o'}}),
        '{bad json',
        '{"jsonrpc":"2.0","id":27,"method":"ping"}',
        call(28, 'query_data', {'model_name': 'Course', 'filters': {'start_date__year': 'x'}}),
        call(29, 'aggregate_data', {'model_name': 'Payment', 'aggregations': [{'function': 'sum', 'field': 'amount'}],
                                    'group_by': ['payment_date__year', 'type']}),
        call(30, 'export_data', {'model_name': 'Payment', 'format': 'pdf', 'filters': {'type': 'nope'}}),
        call(31, 'get_attendance_matrix', {'course_id': I['nuoto'], 'export': True, 'format': 'xlsx', 'date_from': '2030-01-01'}),
    ]),
    ('legacy-protocol', str(ASSOCIATION), [INIT % '2024-11-05', INITIALIZED, '{"jsonrpc":"2.0","id":1,"method":"ping"}',
                                           call(2, 'get_schema', {'model_name': 'null'})]),
    ('invalid-association', 'not-a-uuid', [INIT % '2025-03-26', INITIALIZED, call(1, 'count_data', {'model_name': 'Associate'}),
                                           call(2, 'get_attendance_matrix', {'course_name': 'nuoto'}),
                                           call(3, 'get_attendance_matrix', {'course_id': I['nuoto']}),
                                           call(4, 'sanitize_text', {'text': 'x:y'})]),
    ('foreign-association', str(IDS['foreign_associate']), [INIT % '2025-06-18', INITIALIZED,
                                                            call(1, 'count_data', {'model_name': 'Payment'}),
                                                            call(2, 'get_attendance_matrix', {})]),
]
UUID4 = re.compile(rb'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}')


def matches(result, expected):
    if expected is None:
        return True
    if isinstance(expected, tuple):
        return isinstance(result, dict) and str(result.get('error', '')).startswith(expected[1])
    return isinstance(result, dict) and all(result.get(key) == value for key, value in expected.items())


def file_digest(text):
    """The result text with a data_base64 file replaced by its normalized parts (the creation times set aside)."""
    result = json.loads(text)
    if not isinstance(result, dict) or 'data_base64' not in result or str(result.get('content_type')).startswith('text/'):
        return text
    from TESTS.contracts import pdf, xlsx
    data = base64.b64decode(result['data_base64'], validate=True)
    if result.get('content_type') == 'application/pdf':
        parts, _, _ = pdf.normalize(data)
        digest = hashlib.sha256(b'\0'.join(parts)).hexdigest()
    else:
        kept = [(name, kind, flags, xlsx.STAMP.sub(rb'\1-\4', body) if name == 'docProps/core.xml' else body)
                for name, kind, flags, body in xlsx.parts(data)]
        digest = hashlib.sha256(repr(kept).encode('latin-1', 'backslashreplace')).hexdigest()
    return json.dumps(dict(result, data_base64=digest), default=str)


def django_call(tool, arguments):
    """create_mcp_server.call_tool's dispatch for one business tool."""
    from application.mcp_server import server
    function = getattr(server, 'tool_' + tool)
    kwargs = dict(arguments) if tool == 'sanitize_text' else dict(arguments, sport_association_id=str(ASSOCIATION))
    try:
        result = function(**kwargs)
    except Exception as error:  # call_tool returns the exception text
        return json.dumps({'error': str(error)})
    return json.dumps(result, default=str)


def snapshot():
    from django.apps import apps
    return json.dumps({m._meta.label: list(m._base_manager.order_by('pk').values()) for m in apps.get_models()
                       if m._meta.managed and not m._meta.proxy}, default=str, sort_keys=True)


def reset_reports():
    from django.apps import apps
    apps.get_model('application', 'SavedReport')._base_manager.all().delete()


def stdio(command, lines, env, stderr_path):
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(stderr_path, 'wb'), env=env,
                               cwd=ROOT)
    output = b''
    try:
        for line in lines:
            process.stdin.write(line.encode() + b'\n')
            process.stdin.flush()
            if '"id"' not in line and not line.startswith('{bad'):
                time.sleep(0.2)
                continue
            while not output.endswith(b'\n') or output.count(b'\n') < sum('"id"' in item or item.startswith('{bad')
                                                                            for item in lines[:lines.index(line) + 1]):
                ready, _, _ = select.select([process.stdout], [], [], 30)
                if not ready:
                    raise TimeoutError('MCP response deadline exceeded')
                chunk = os.read(process.stdout.fileno(), 1 << 20)
                if not chunk:
                    raise RuntimeError('MCP process closed stdout')
                output += chunk
        process.stdin.close()
        output += process.stdout.read()
        code = process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    return code, output


def main():
    require_production_python()
    label = os.environ.get('ASSOZETA_MCP_SERVER_LABEL', 'mcp-server-tools')
    go_root = Path(os.environ.get('ASSOZETA_MCP_SERVER_GO_ROOT', ROOT / 'BE_GO'))
    output = ROOT / 'quality-reports/go-migration' / label
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip(), 'go_root': 'BE_GO' if go_root == ROOT / 'BE_GO' else 'base revision tree'}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    binaries = Path(postgres).parent
    base = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    base.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-mcp-server-', dir='/tmp') as directory, (output / 'services.log').open('w') as log:
        work = Path(directory)
        attempted = False

        def run(args):
            return subprocess.run([str(a) for a in args], env=base, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        try:
            driver, command = work / 'business-mcp.test', work / 'mcp-server'
            built = subprocess.run(['go', 'test', '-race', '-c', '-tags', 'businessmcpintegration', '-o', str(driver), './internal/service'],
                                   cwd=go_root, env=base, stdout=log, stderr=subprocess.STDOUT, timeout=900)
            if built.returncode:
                driver = None
            built = subprocess.run(['go', 'build', '-race', '-o', str(command), './cmd/mcp-server'], cwd=go_root, env=base, stdout=log,
                                   stderr=subprocess.STDOUT, timeout=900)
            if built.returncode:
                command = None  # RED: Django still answers every session.
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            seed()
            seeded = snapshot()
            generated = [ROOT / 'BE_GO/internal/dto/mcp_models.json', ROOT / 'BE_GO/internal/handler/businessmcp/tools.json']
            before = [path.read_bytes() for path in generated]
            generator = subprocess.run([sys.executable, '-I', str(ROOT / 'TESTS/fixtures/generate_mcp_server_models.py')],
                                       capture_output=True, text=True, timeout=300)
            assert generator.returncode == 0, generator.stderr[-2000:]
            assert [path.read_bytes() for path in generated] == before, 'generated model metadata or tool declarations are stale'
            result['generated_metadata_current'] = True
            outcomes = []
            dsn = f'host={work} port=5432 user=contracts dbname={DATABASE} sslmode=disable'
            for index, (tool, arguments, expected) in enumerate(CASES):
                label_case = f'{index:03d} {tool} {json.dumps(arguments, ensure_ascii=False)[:90]}'
                django_text = django_call(tool, arguments)
                assert snapshot() == seeded, f'{label_case}: Django changed rows'
                django_result = json.loads(django_text)
                if expected != 'refused':
                    assert matches(django_result, expected), (label_case, django_text[:500])
                outcome = {'case': label_case, 'django': 'pass'}
                if driver is None:
                    outcomes.append(dict(outcome, go='missing driver'))
                    continue
                answer = work / 'answer.json'
                completed = subprocess.run([str(driver), '-test.run', '^TestBusinessMCPToolCall$', '-test.count=1'], env=dict(base,
                    ASSOZETA_BUSINESS_MCP_DSN=dsn, ASSOZETA_BUSINESS_MCP_ARGUMENTS=json.dumps(arguments), ASSOZETA_BUSINESS_MCP_TOOL=tool,
                    ASSOZETA_BUSINESS_MCP_ASSOCIATION=str(ASSOCIATION), ASSOZETA_BUSINESS_MCP_OUTPUT=str(answer)),
                    capture_output=True, text=True, timeout=120)
                if completed.returncode:
                    outcomes.append(dict(outcome, go='driver failed'))
                    log.write(completed.stdout[-2000:])
                    continue
                raw = answer.read_text()
                assert snapshot() == seeded, f'{label_case}: Go changed rows'
                if raw.startswith('{"refused"'):
                    go = 'refused'
                    assert expected == 'refused', f'{label_case}: Go refused a call it ports'
                else:
                    go_text = raw[len('{"result": '):-1]
                    if file_digest(go_text) != file_digest(django_text):
                        (output / 'difference.json').write_text(json.dumps({'case': label_case, 'django': django_text, 'go': go_text}) + '\n')
                    assert expected != 'refused', f'{label_case}: Go must refuse'
                    assert file_digest(go_text) == file_digest(django_text), f'{label_case}: Go result differs from Django'
                    go = 'pass'
                outcomes.append(dict(outcome, go=go))
                print(f'{go.upper()}: {label_case}', flush=True)
            result['cases'] = outcomes
            result['django_passed'] = len(outcomes)
            result['go_passed'] = sum(o['go'] == 'pass' for o in outcomes)
            result['go_refused'] = sum(o['go'] == 'refused' for o in outcomes)

            env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / 'BE'), PYTHONUNBUFFERED='1')
            sessions = []
            for name, association, lines in SESSIONS:
                reset_reports()
                django_code, django_out = stdio([sys.executable, str(ROOT / 'BE/manage.py'), 'run_mcp_server', '--association-id',
                                                 association], lines, env, work / f'django-{name}.err')
                django_rows = snapshot()
                django_stderr = (work / f'django-{name}.err').read_bytes()
                started = f'Starting MCP server (stdio) for association {association}\n'.encode()
                assert django_code == 0 and started in django_stderr, name
                session = {'session': name, 'requests': len(lines), 'django_exit': django_code,
                           'django_stdout_sha256': hashlib.sha256(django_out).hexdigest()}
                if command is None:
                    sessions.append(dict(session, go='missing command'))
                    continue
                reset_reports()
                go_code, go_out = stdio([str(command), '--association-id', association], lines, env, work / f'go-{name}.err')
                go_rows = snapshot()
                # save_report's generated values: the uuid4 id and auto_now times of today.
                today = datetime.now(timezone.utc).strftime('%Y-%m-%d').encode()
                normalize = lambda data: re.sub(re.escape(today) + rb'T\d{2}:\d{2}:\d{2}(\.\d{6})?\+00:00', b'<now>',
                                                UUID4.sub(b'<uuid4>', data))
                if normalize(go_out) != normalize(django_out):
                    (output / f'transcript-{name}.json').write_text(json.dumps({'django': django_out.decode(), 'go': go_out.decode()}) + '\n')
                assert normalize(go_out) == normalize(django_out), f'{name}: stdio transcript differs'
                assert go_code == django_code and (work / f'go-{name}.err').read_bytes() == started, name
                strip = lambda rows: re.sub(r'"(saved_report_id|created_at|updated_at)": "[^"]*"', '', rows)
                assert strip(go_rows) == strip(django_rows), f'{name}: rows differ'
                sessions.append(dict(session, go='identical', responses=django_out.count(b'\n')))
                print(f'PASS stdio session {name}: {django_out.count(b"\n")} lines', flush=True)
            reset_reports()
            # The command's other transport and argument handling.
            cli = []
            for arguments in (['--transport', 'sse', '--association-id', str(ASSOCIATION)], ['--transport', 'websocket',
                              '--association-id', 'x'], [], ['--port', 'eighty', '--association-id', 'x']):
                django = subprocess.run([sys.executable, str(ROOT / 'BE/manage.py'), 'run_mcp_server', *arguments], env=env, cwd=ROOT,
                                        capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
                item = {'arguments': arguments, 'django_exit': django.returncode}
                if command is not None:
                    go = subprocess.run([str(command), *arguments], env=env, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
                    assert go.returncode == django.returncode, arguments
                    if django.returncode == 0:
                        assert go.stdout == django.stdout, arguments
                    item['go_exit'] = go.returncode
                cli.append(item)
            result['sessions'], result['cli'] = sessions, cli
            print(json.dumps({'django': f"{len(outcomes)}/{len(CASES)}", 'go': f"{result['go_passed']}+{result['go_refused']} refused/{len(CASES)}",
                              'sessions': [s['go'] for s in sessions]}))
            assert driver is not None and command is not None, 'Go MCP server missing'
            assert result['go_passed'] + result['go_refused'] == len(CASES)
        finally:
            try:
                from django.db import connections
                connections.close_all()
            finally:
                if attempted:
                    run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    paths = ['TESTS/adapters/go/mcp_server_tools.py', 'TESTS/adapters/go/mcp_server_seed.py', 'TESTS/fixtures/generate_mcp_server_models.py',
             'BE/application/mcp_server/server.py', 'BE/application/mcp_server/query_builder.py', 'BE/application/mcp_server/security.py',
             'BE/application/mcp_server/schema.py', 'BE/application/mcp_server/attendance.py',
             'BE/application/mcp_server/exporters/csv_exporter.py', 'BE/application/mcp_server/exporters/__init__.py',
             'BE/application/mcp_server/exporters/xlsx_exporter.py', 'BE/application/mcp_server/exporters/pdf_exporter.py',
             'BE_GO/internal/service/report_files.go',
             'BE/application/management/commands/run_mcp_server.py',
             'BE_GO/internal/dto/mcp_query.go', 'BE_GO/internal/dto/mcp_prep.go', 'BE_GO/internal/dto/mcp_models.go',
             'BE_GO/internal/dto/mcp_schema.go', 'BE_GO/internal/dto/report_tools.go', 'BE_GO/internal/dto/mcp_models.json',
             'BE_GO/internal/service/business_mcp.go', 'BE_GO/internal/service/business_mcp_query.go',
             'BE_GO/internal/handler/businessmcp/server.go', 'BE_GO/internal/handler/businessmcp/validate.go',
             'BE_GO/internal/handler/businessmcp/tools.json', 'BE_GO/internal/handler/mcpstdio/server.go', 'BE_GO/cmd/mcp-server/main.go']
    result.update(success=True, case_count=len(CASES), session_count=len(SESSIONS), race_instrumented=True,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({'success': True, 'case_count': len(CASES)}))


if __name__ == '__main__':
    main()
