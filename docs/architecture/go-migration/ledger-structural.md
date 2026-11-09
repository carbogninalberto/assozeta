# Structural ledger entries: mounts, commands, MCP definitions, audit index signal

Non-route declarations reviewed in one pass. Django stays the execution owner;
nothing here installs or activates a Go runtime.

## Ported

### `create_instance_superuser` → `BE_GO/cmd/create-instance-superuser`

Django: `BE/application/management/commands/create_instance_superuser.py`
(called by `selfhost/bin/assozeta`). Go reproduces `handle()` in order:

1. `--allow-weak-password` outside `ASSOZETA_DEPLOYMENT_MODE=development` fails first.
2. Missing options are prompted (`input()` on stdin/stdout) unless `--noinput`;
   values are `str.strip()`ed, the username `str.upper()`ed (full case mapping).
3. Username and email required; `validate_email`; `iexact` checks on
   `User.original_objects` (all users) for username, then email.
4. `Field.clean` for username (Unicode username validator, 150), email
   (validator, 254), first/last name (150), phone (20); every message of the
   first failing field, joined with `; `.
5. Password from `DJANGO_SUPERUSER_PASSWORD` (`--noinput`) or `getpass` twice
   (`/dev/tty` without echo, else the stdin fallback); empty refused;
   `validate_password` with the project validators (similarity, minimum
   length, Django 5.2.18's common password list, numeric), in the active
   Italian catalog.
6. `create_superuser` inside one transaction: NFKC username, lowercased email
   domain, PBKDF2-SHA256 1,000,000 iterations, athlete role, staff,
   superuser, active, model defaults; auditlog create entry (no actor, no
   index row). A unique violation is the "already exists" message.

Output: success line on stdout, `CommandError: <message>` on stderr with exit 1.
Shared code: `internal/djangoauth` (Python text semantics, validators),
`internal/service/instance_superuser.go`, `internal/repository/instance_superuser.go`.
The binary is added to the Go image (`/app/create-instance-superuser`).

Not ported: Django's global management options and argparse prefix
abbreviations (exit 2), the traceback Django prints for database errors other
than unique violations (Go prints a generic line, exit 1), GetPassWarning's
exact text, and Unicode 15.1 characters missing from Go's Unicode 15.0 tables.

Evidence: `TESTS/adapters/go/instance_superuser_command.py` runs the actual
`manage.py` process and the race-built Go binary on the same reset seed, 24
authored cases (profiles, case mapping, every validation message, existing,
soft-deleted and NFKC-colliding identities, weak-password bypass, interactive
stdin flows, cancellation). Exit status, stdout, the CommandError line and every
managed table must match; hashes must verify with Django's `check_password`.
RED: Django 24/24 against authored expectations, Go command absent.
GREEN: 24/24. Mutation (identity check excluding soft-deleted users) CAUGHT
on "soft-deleted account username".

### MCP tool functions `sanitize_text`, `list_reports`, `save_report`

`BE_GO/internal/service/business_mcp.go` (`BusinessMCPTools.Execute`) with
`internal/repository/business_mcp.go`, following `create_mcp_server.call_tool`
dispatch: the server association is fixed, `user_id` comes from the call
arguments, results are the JSON Django serialises, tool errors are
`{"error": ...}` results.

- `sanitize_text`: unsafe filename characters replaced, runs of the
  replacement's last character collapsed (`re.escape(replacement) + '+'`),
  Python whitespace strip, then strip of the replacement's characters.
- `list_reports`: `user_id` falsy → error; string (`UUID(hex=...)` forms) or
  integer/bool (`UUID(int=...)`) ids; an invalid string is the Italian
  ValidationError text; reports of the association and creator ordered by
  `-updated_at`, `isoformat()` timestamps.
- `save_report`: same `user_id` handling; `User.objects` (not deleted) lookup
  → `User not found`; insert with Python-`json.dumps`-equivalent params and
  `ui_config or {}`; `SavedReport` is not audited.

Refused before any query or write (`ErrBusinessMCPNotPorted`): replacements
containing a backslash (re template escapes), `user_id` lists/objects/floats,
invalid ids whose repr needs quoting, name over 200 or tool name over 100
characters, NUL characters, and a missing server association (Django returns
driver error text for these). Not ported: the other eight business tools, MCP
schema validation for business tools and a Go `run_mcp_server` command
(the `mcpstdio` transport is still the manual-tools component).

Evidence: `TESTS/adapters/go/business_mcp_tools.py` calls the actual Django
tool functions and the Go service (race-built test driver) on the same reset
rows: 25 authored calls, 22 identical results and states, 3 refused by Go
with state unchanged. RED: Django 25/25, Go driver absent. GREEN: 25/25.
Mutation (user lookup including deleted users) CAUGHT on the deleted-user save.

## Decisions without a port

- `mount silk/`: mounted only `if DEBUG` (`BE/core/urls.py`), `silk` is in
  `INSTALLED_APPS` only under DEBUG, and `selfhost/bin/assozeta` writes
  `DEBUG=False` for production (`selfhost/.env.example` too). Development
  profiler only: proposed deprecated.
- `command seed_selfhost`: a no-op whose help text declares it deprecated
  (prints one line, no reads or writes); no caller in `selfhost/bin` or
  entrypoints. Proposed deprecated.
- Manual fixture commands guarded to development in code: `seed_manuale`,
  `run_manuale_instance`, `run_manuale_attendance`, `run_manuale_dashboard_lists`
  (`assert_disposable`: development mode and a `manuale_<run>` database) and
  `install_manuale_development`, `install_manuale_preview`
  (`development_enabled`). Proposed deprecated.
- `build_manuale_index`, `compare_manuale_captures`, `evaluate_manuale`: manual
  build/CI tooling called only from `docs/scripts/manuale.py`, but without a
  code guard, so left open. `check-manual` validates the image bundle; it is
  not an equivalent of these commands.
- `sync_manuale_corpus`: production (`selfhost/bin/assozeta` start and
  `sync-manual`). `pkg/manual/refresh.go` implements the synchronizer with
  cross-runtime lock evidence (manual-reader.md), but no Go command exists;
  left open.
- `backfill_audit_log_index`: depends on the generic resolver below; left open.
- `migrate_subscription_signatures_to_s3`: migration 0415 removes the field in
  state only, so older databases keep the column and the command still applies;
  it writes to configured storage. Left open.
- Mounts other than `silk/` stay open: each include still has open child routes
  (application 35, docmanager 13, instance 8, sso 5, communications 2, chat 1).
  `include(router.urls)` also generates DRF router routes that are not ledger
  route entries and that Go does not serve: `<prefix>/` (GET list, POST create),
  `<prefix>/<pk>/` (GET, PUT, PATCH, DELETE), their `.<format>` suffixes for the
  eight registered viewsets, `documents/bulk_delete/`, and the API root.

## `create_audit_log_index` (post_save on LogEntry)

Django resolves the index at LogEntry creation: the content type's registered
resolver on `model_class.objects.get(pk)`, then `changes['sport_association']`
(old value first), then the actor's `SportAssociation.objects.filter(user=...).first()`.
Go writes each family's index row inline with the path that family proved.
A generic Go fallback is not provably equivalent yet: `model_class.objects` is
the default manager, which for group-aware models filters by the request's
thread-local `X-Group-Id`, so the outcome depends on request context a generic
function does not receive; ~60 registered content types each need their
manager and traversal characterised; and Django's handler swallows index
errors outside the scoped atomic mutations. Status stays needs_review.
