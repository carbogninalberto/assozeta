# Templates and reports: template update and saved report run implementing

Two routes that complete the module-templates and saved-reports families:
`templates/<pk>/update` (`SportAssociationModuleTemplatesViewSet.update`,
`BE/application/views/archive_views.py`) and `saved-reports/<pk>/run`
(`SavedReportViewSet.run`, `BE/application/views/report_views.py`). Shared
transport, identity and listing behaviour is in `module-templates.md` and
`saved-reports.md`. Django remains the production execution owner.

## templates/<pk>/update (PATCH)

- Registry permission `association.templates.update`; `get_queryset` calls
  `SportAssociation.objects.get(user=request.user)` (athletes and missing or
  soft-deleted associations: HTTP 500). `get_object`: an unparseable pk is
  DRF's bare `404 Not found.`, an unknown or foreign pk
  `404 No SportAssociationModuleTemplates matches the given query.`.
- `ModelSerializer(fields='__all__', partial=True)`: the pk (UUID, nullable
  no; `UniqueValidator` excluding the instance), `name` (150 chars, trimmed,
  not blank, not null), nullable non-blank text fields, booleans (`null`
  rejected), and the related `sport_association` last. The relation's
  queryset is the default manager; a pk of another tenant is DRF's generic
  `403 You do not have permission to perform this action.` raised during
  field validation, so it wins over the other field errors. A non-dict body
  is `400 non_field_errors`. Form bodies work (partial: absent booleans stay;
  an empty nullable text is `None`).
- `save()` writes every field; auditlog logs only changed fields
  (`[str(old), str(new)]`, index `direct`), nothing when unchanged.
- A new, unused pk makes `Model.save()` insert a copy with the merged values
  (the original row stays). auditlog then logs two entries for the new pk:
  an update against `None` from `pre_save` (indexed through
  `changes_json.sport_association`, the row does not exist yet) and the create
  from `post_save` (index `direct`). The response is the copy.
- The viewset's `delete()` answers `DELETE` on this route with its pk
  (`204`, delete audit); `Allow: PATCH, DELETE, OPTIONS`.

## saved-reports/<pk>/run (POST)

- Unmapped in the registry (FULL collaborators pass, restricted ones get the
  unmapped 403). The report is resolved by id, association and
  `created_by=request.user` (malformed pk: HTML 500; otherwise
  `404 {"error": "Report not found"}`); a stored tool outside
  `TOOL_FUNCTIONS` is `400 {"error": "Unknown tool: ..."}`.
- `{**report.params, **request.data.get('overrides', {})}` runs outside the
  try: a non-dict body, non-mapping overrides (including form strings and
  `null`) or non-dict stored params are the HTML 500.
- `tool_fn(sport_association_id=..., **merged)`: any exception inside (binding
  errors such as a `sport_association_id` key or a missing `model_name`, a
  model outside `MODEL_WHITELIST`, `FieldError`, `ValueError`, database
  errors) is `500 {"error": "Errore durante l'esecuzione del report."}`; a
  result with `error` is a `400` with that dict; otherwise `{"data": result}`.
  `DELETE` on this route deletes the report (`204`).

### Query engine (`BE/application/mcp_server/query_builder.py`)

Ported in `BE_GO/internal/dto/report_tools.go` for the whitelisted models
`Tags`, `PaymentCategory` and `Carnet`:

- `QueryBuilder.__init__` reads `effective_integration('ai')`: a self-hosted
  instance's stored `max_results`/`query_timeout`, else
  `MCP_AGENT_MAX_RESULTS` (5000) and `MCP_AGENT_QUERY_TIMEOUT` (10s). Queries
  run read-only with that statement timeout.
- Base queryset: the tenant column, the soft-delete flags unless
  `include_archived` is truthy (`PaymentCategory.archived`), `.distinct()`.
- `filters` (AND), `exclude` (`NOT (...)`; on nullable columns Django adds
  `IS NOT NULL` inside the negation, so `NULL` rows stay) and `or_filters`
  (dict items ORed; other items and empty groups ignored). Blacklisted roots
  are dropped; unknown fields raise. Lookups: exact, iexact, contains,
  icontains, (i)startswith, (i)endswith, gt/gte/lt/lte, in (`None` dropped,
  empty: no rows), range, isnull. String values are coerced for date/datetime
  (`%Y-%m-%dT%H:%M:%S`, `%Y-%m-%dT%H:%M`, `%Y-%m-%d %H:%M:%S`, `%Y-%m-%d`, UTC),
  integer and decimal fields; `None` is `IS NULL` for exact/iexact.
- `query_data`: count, then `values()` with the validated fields (unknown or
  blacklisted names skipped; none valid: every concrete column by attname,
  without choice labels), ordering columns join the `DISTINCT`, limit
  `min(limit or MAX, MAX)` and offset, `has_more`.
- `count_data`, `get_field_values` (distinct values ordered, nulls last,
  `total_distinct` counts `NULL`, default limit 100, `None` unlimited) and
  `aggregate_data` (count/sum/avg/min/max with aliases where `.` and `-`
  become `_`; flat or grouped by direct fields and ordered by them; the 400s
  `No aggregations specified`, `No valid aggregations`, `No valid group_by
  fields`).
- Values: UUIDs as text, `isoformat()` datetimes, `Decimal` and averages as
  floats, `html.unescape` of text containing `&`, Italian choice labels
  (`PaymentCategory.type`).
- `get_schema`: the `{"error": "Model <name> not found"}` answer.

## Not ported (refused with the HTML 500 page before any query)

- File tools: `export_data`, `export_multi_sheet`, `get_attendance_matrix`
  (CSV/XLSX/PDF generation and base64 downloads).
- `get_schema` for a whitelisted model or the full schema (static model
  metadata).
- The other whitelisted models; relation traversal (`a__b`), reverse
  relations, `joins`, date-part transforms in filters and `group_by`, JSON
  field filters and values, ordering or grouping by a relation name, `?`
  ordering, lookups outside the list above, boolean/float limits and offsets,
  non-canonical UUID spellings, decimal values beyond the field's digits,
  integers outside the column range, aggregate aliases with `__`, quotes,
  whitespace or a field's name, and HTML references other than the common
  named ones (`&amp;`, `&lt;`, `&gt;`, `&quot;`, `&apos;`, `&nbsp;`).

MCP ledger entries: the code implements part of `query_data`, `count_data`,
`get_field_values`, `aggregate_data` and `get_schema` (above); none is
complete, so none is recorded as covered. `list_reports`, `save_report`,
`sanitize_text` and the export tools are not reached by this route.

## Evidence (TDD)

`TESTS/fixtures/templates_reports_http.json` (generator alongside) authors 84
cases: 22 for the template update and 62 for the report run (37 tool paths
over seeded tags, payment categories and carnets). The Django reference
corrected the copy expectation (two audit entries, the first indexed through
the changes). RED: Django 84/84 and Go 404 on the first case before any Go
code. GREEN: native Django, native Go, Caddy `/api/v1` and `/api/v2` pass
84/84 with exact template, audit, index and report effects. Dropping the
nullable `IS NOT NULL` from `exclude` fails the run.
