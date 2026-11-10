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
- Paths over forward FK/O2O, reverse FK/O2O and M2M (both directions), `pk`,
  attnames; transforms `year` (BETWEEN bounds), `month`, `day`, `week`,
  `week_day`, `quarter`, `hour`, `date`, JSON key transforms (one key);
  lookups exact, iexact, (i)contains, (i)startswith, (i)endswith, gt/gte/lt/
  lte, in (None dropped, deduplicated, empty: no rows), range, isnull,
  has_key; `_coerce_filter_values` (strptime formats, `int()`, `float()`)
  then each field's `get_prep_value`.
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
  the same order; text rendering happens in an outer projection.
- Serialisation (`_serialize_value`): UUID text, `isoformat()`, Decimal and
  averages as floats, integers from Extract/Sum, JSON values, `html.unescape`
  of strings with `&`, Italian choice labels (`ITALIAN_CHOICE_OVERRIDES`,
  otherwise Django's translated choices).

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

- Building XLSX or PDF files: `export_data` with xlsx/pdf when there is
  data, `export_multi_sheet` when any sheet has data, `get_attendance_matrix`
  with `export=true` once the matrix exists (a parallel xlsx-exports family
  was not merged when this was written).
- ORM inputs whose Django text includes difflib suggestions or SQL: unknown
  lookups/transforms on fields (`Unsupported lookup …`), regex/iregex,
  JSON containment and multi-key lookups, nested or integer JSON keys,
  iso_year/iso_week_day/minute/second/time transforms, `exclude()` across
  many-valued relations (split_exclude subquery), `?` ordering, ordering by
  a relation whose model has Meta.ordering, SUM/AVG/MIN/MAX of
  non-numeric/unsupported types (database errors; the saved-report route
  answers its generic 500 for them, as Django does), integers outside the
  column range, non-integral floats for integers/limits, non-canonical UUID
  spellings `int()` could accept, ISO date/datetime forms only
  `fromisoformat` accepts, non-ASCII digits, HTML references other than the
  common named ones, CSV cells holding JSON containers or ISO-looking text
  other than the engine's own datetimes, attendance names whose `str.lower()`
  differs from Go's.
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
