# MCP server: business tools and `run_mcp_server`

Django: `BE/application/mcp_server/` (`server.py`, `query_builder.py`,
`security.py`, `schema.py`, `attendance.py`, `exporters/`) and
`BE/application/management/commands/run_mcp_server.py`. Django stays the
execution owner; nothing here switches traffic.

## Go

- `BE_GO/cmd/mcp-server` (in the Go image as `/app/mcp-server`): the command. `--association-id` (required),
  `--transport stdio|sse` (default stdio), `--host`, `--port` (int), plus a
  Go-only `--env-file`. stdio writes `Starting MCP server (stdio) for
  association <id>` to stderr and serves JSON-RPC on stdin/stdout; `sse`
  prints Django's warning line on stdout and exits 0. Invalid invocations
  exit 2 like argparse (argparse's usage/error texts are not reproduced).
- `internal/handler/mcpstdio`: new opt-in `OrderedJSON` (pydantic field
  order, notifications with `jsonrpc` last), `Tool.RawInputSchema` and
  `ExecuteRaw` (raw arguments in, exact text content out). Default
  behaviour of the manual-tool transport is unchanged.
- `internal/handler/businessmcp`: `tools.json` (TOOL_DEFINITIONS exactly as
  the SDK lists them, generated), the SDK input validation (jsonschema
  draft 2020-12 type/properties/required/items/enum with `best_match`:
  shortest path, then the larger path, then type mismatches, first wins;
  manual tools keep `manualmcp.ValidateJSON`) and `call_tool`'s dispatch:
  unknown tools `{"error": "Unknown tool: …"}`, the `_call` binding
  `TypeError`s for a `sport_association_id` or `fn` argument (exact texts),
  manual tools get only `MANUAL_TOOL_PARAMETERS`.
- `internal/service/business_mcp*.go`: all eleven business tools; results
  are ordered values serialised like `json.dumps(result, default=str)`,
  tool exceptions are `{"error": str(e)}`.
- `internal/dto/mcp_query.go`, `mcp_prep.go`, `report_tools.go`: the
  QueryBuilder engine for every whitelisted model; `mcp_schema.go`:
  `get_schema`; `mcp_models.go` + `mcp_models.json`: model metadata generated
  from Django `_meta` by `TESTS/fixtures/generate_mcp_server_models.py` (the
  89 models the whitelist reaches, fields in `get_fields()` order, default
  manager filters, Italian validation templates). The saved-report run route
  uses the same engine (it now also answers `get_schema`).

## Query engine (QueryBuilder)

- `_resolve_model`, tenant filter (including `user__sportassociation` and the
  reverse `subscription__…` paths), default manager filters (`NOT deleted`),
  soft-delete filters unless `include_archived` is truthy, `distinct()`.
- Django's alias map: join reuse (single-valued joins always, many-valued
  joins only inside one `filter()` call; `values`/ordering/annotations reuse
  any), `trim_joins`, INNER/LEFT OUTER creation, `JoinPromoter` votes,
  promotion/demotion, `Q(**kwargs)` sorted keys, `Q.__or__` squashing for
  `or_filters`, `exclude()` with Django's `IS NOT NULL` for nullable or
  outer-joined columns.
- `exclude()` across a many-valued relation: `MultiJoin`/`split_exclude`
  build `NOT (EXISTS(SELECT 1 AS "a" FROM … WHERE (<condition> AND <join> =
  (<outer>.<col>)) LIMIT 1))` with `trim_start`'s trimmed prefix, the
  subquery's own `U0`/`U1` aliases and the `or_null_condition` of an outer
  trimmed prefix; each many-valued key of one `exclude` becomes its own
  subquery inside the same `NOT (…)`.
- Paths over forward FK/O2O, reverse FK/O2O and M2M (both directions), `pk`,
  attnames; transforms `year` (BETWEEN bounds), `month`, `day`, `week`,
  `week_day`, `quarter`, `hour`, `date`, JSON key transforms (one key);
  lookups exact, iexact, (i)contains, (i)startswith, (i)endswith, gt/gte/lt/
  lte, in (Python iteration, None dropped, deduplicated, empty: no rows),
  range, isnull, has_key; out-of-range integer comparisons map to
  EmptyResultSet/FullResultSet per `WhereNode.as_sql` (an empty OR branch is
  dropped, an empty AND empties, a negated empty is full);
  `_coerce_filter_values` (strptime formats, `int()`, `float()`) then each
  field's `get_prep_value` (integer/float truncation and rounding, decimal
  non-finite and container errors, UUID from int/bool and `int(hex, 16)`
  spellings, `parse_date`/`parse_datetime` with the full `fromisoformat`
  grammar, `str()` of containers for text).
- Exact exception texts: `Model 'X' is not whitelisted`, FieldError
  `Cannot resolve keyword … Choices are: …` and `… Join on 'f' not
  permitted.`, `Cannot parse keyword query ''`, UUID/date/datetime/boolean/
  decimal ValidationErrors (Italian, list repr), `Field 'f' expected a number
  but got …`, `Cannot use None as a query value`, the deferred isnull text,
  `Negative indexing is not supported.`, annotation conflicts and alias
  texts, `Field '…' is not allowed`, `unhashable type: …`, min() type errors.
- `values()`/`count()`/`aggregate()`/grouped annotate SQL keeps Django's
  shape (DISTINCT, select list, joins, ORDER BY columns added to the select,
  `__colN` subquery for flat aggregates), so unordered results come back in
  the same order; text rendering happens in an outer projection. A repeated
  name in `fields`/`group_by` keeps one column in first-appearance order, as
  `values()` does.
- `limit`/`offset` follow Python's slicing: bools count as ints, float bounds
  are truncated by `int()`, a limit above the maximum is capped, an
  out-of-range offset stays in the SQL for PostgreSQL, and the type errors of
  `min()`/`+`/`<` keep Django's text. `joins` runs `_split_joins` and the
  `select_related`/`prefetch_related` checks; `values()` then drops both, so
  only a non-string item (`lookup.split`) or a non-iterable value raises.
- Serialisation (`_serialize_value`): UUID text, `isoformat()`, Decimal and
  averages as floats, integers from Extract/Sum, JSON values, `html.unescape`
  (`internal/pyhtml`) of strings with `&`, Italian choice labels
  (`ITALIAN_CHOICE_OVERRIDES`, otherwise Django's translated choices).

## Files

- `export_data`: CSV only (`format_export_data`, `csv.writer` excel dialect,
  UTF-8 BOM, base64, `save_info` with `date.today()`); every error path for
  any format (`Unsupported format`, `No data to export`, query errors).
- `export_multi_sheet`: every error path (`No sheets specified`, `Sheet N:
  model_name is required`, query errors, `No data to export from any
  sheet`).
- `get_attendance_matrix`: course resolution (id, name search with the
  `matches` list, all courses with registries), date filters, the matrix and
  both previews, every error dict.

## Not ported (refused explicitly, after reads only)

Refusal: the Go tool returns `ErrBusinessMCPNotPorted`; over stdio the call
answers `isError: true` "Tool execution failed". Nothing is written before.

- Building XLSX or PDF files is ported (v2-reports-3, below). Still refused: a file whose
  exporter raises with text Go does not reproduce (a title or `sheets` of another type, a
  sheet without columns and the other exceptions of the exporters), and a `format`,
  `export`, `multi_sheet`, `filename` or course/date argument of a type the save_info
  would write back differently.
- ORM inputs whose Django text includes SQL or parts Go does not reproduce:
  JSON `range` on a whole column, JSON multi-key lookups on a key value, `?`
  ordering, ordering by a relation whose model has Meta.ordering, integer list
  items outside the 64-bit parameter range, CSV cells holding JSON containers
  or ISO-looking text other than the engine's own datetimes, attendance names
  whose `str.lower()` differs from Go's.
- Manual tools in the Go command run `service.ManualTools` (parity is the
  manual family's, semantic); their text keeps Go key order.
- argparse usage/errors and Django's global command options.

## Evidence

`TESTS/adapters/go/mcp_server_tools.py` (seed `mcp_server_seed.py`) on a
private PostgreSQL with the current schema:

1. 135 authored tool calls: actual Django tool functions (call_tool's
   exception handling and `json.dumps`) versus the race-built Go driver;
   texts byte identical, rows unchanged; 56 calls also carry authored
   expectations checked on Django first (one was corrected by Django: the
   attendance list counts archived, non-deleted enrolments). Three calls are
   refusals (XLSX files).
2. stdio: four sessions (initialize with three protocol versions, tools/list
   byte equal to the SDK, validation errors, binding errors, unknown tool,
   malformed line notification, ping, every tool, save/list reports, an
   invalid and a foreign association id) through the actual `manage.py
   run_mcp_server` and the race-built `cmd/mcp-server`: stdout byte identical
   after replacing save_report's uuid4 and today's timestamps; equal rows,
   exit codes and start line; CLI exits for `sse`, an invalid transport, a
   missing association and a non-integer port.
3. The generated metadata and declarations are regenerated and must be
   unchanged.

RED: the same adapter on the base revision's BE_GO (no command; its driver rejects these calls):
Django 135/135 and 4 sessions, Go missing. GREEN: 135/135 (132 identical,
3 refused) and 4/4 sessions identical. Mutation: dropping the negated
`IS NOT NULL` of `exclude()` is caught. Re-run on the shared code: templates-reports
(native and versioned; the run first caught a non-string `model_name`
override and an impossible SUM, now Django's generic 500), the business MCP
tool adapter of ledger-structural (25/25) and the manual MCP Go transport.

`get_attendance_matrix` lists an enrolment without a subscription under its own id, as Django's outer join does (found by
the saved-report run cases, templates-reports.md); the XLSX and PDF results stay refused here.

v2-reports-2 (`mcp-server-tools-v2rep2-r2`, 135/135 with 3 refused, 4/4 sessions): the shared tool code now answers a
`format` that is not a string (`Unsupported format: <str(value)>`, or `unhashable type` for a list or dict) and writes CSV
labels and cells of other types with `str()`. Exceptions whose text is not reproduced (a title or `sheets` of another type)
and the XLSX and PDF results stay refused here. The server has no request, so no group filter applies.

v2-reports-3: the XLSX and PDF results of `export_data`, `export_multi_sheet` and
`get_attendance_matrix(export=true)` are built on the MCP transport (and the assistant channel)
by the code the saved-report run uses, with the whole result dict: `row_count`, `sheet_count` or
`total_dates` and `save_info`. The harness (`v2rep3-b-mcp-r3-mcp-server-tools`) compares a file by its normalized parts
(workbook parts with the two core.xml times set aside, PDF objects decoded with dates and
trailer ID set aside; the helpers of `TESTS/contracts`): 147 cases, 146 identical and 1
refused (`title` that is not a string), 4/4 sessions identical.
