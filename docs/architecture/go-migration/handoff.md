# Handoff: Go passes the requests it refuses to Django

The web UI's v2 switch sends every request to `/api/v2` (Go). Go refuses some inputs it has not ported
([v2-refusals.md](v2-refusals.md)): the stable 500 page, before any write. In production Django still runs next to Go
(`api:8000` and `api-go:8000` in `selfhost/compose.yml`), so with `DJANGO_HANDOFF_URL` set Go replays a refused
request to Django and streams Django's response back. The UI gets the Django answer for the rare cases and the Go
answer for everything else.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DJANGO_HANDOFF_URL` | empty in the binary; `http://api:8000` in `selfhost/compose.yml` (`api-go` only) | Django base URL. Empty: handoff off, refusals answer 500 as before. Must be an absolute `http(s)` URL without credentials, query or fragment; a base path is prefixed to the request path. An invalid value stops the server at start. |
| `DJANGO_HANDOFF_MAX_BODY_BYTES` | `10485760` (10 MiB) | Largest request body Go keeps for a replay. |

Compose uses `${DJANGO_HANDOFF_URL-http://api:8000}`: unset means the default, `DJANGO_HANDOFF_URL=` in `.env`
switches the handoff off. The harness leaves the variable unset for every family except `django-handoff`.

## Refusals: one check for every family

`BE_GO/internal/refusal` defines `refusal.New(reason)` and `refusal.ErrNotPorted`. Each family keeps its own sentinel
(`domain.ErrFolderWriteNotPorted`, `service.ErrCourseListUnported`, ...), created with `refusal.New`, so
`errors.Is(err, family.ErrX)` works as before and `errors.Is(err, refusal.ErrNotPorted)` matches all of them.
`refusal.Reason(err)` returns the sentinel's constant message for the log.

A new handler needs no handoff code: define the sentinel with `refusal.New`, return it before any write, and let the
error reach `restError` (directly or through the family's error mapper) as every refusal does today.
`refusal_test.go` fails when a sentinel named `Err*NotPorted`, `Err*Unported`, `Err*Unsupported`, `Err*Refused`,
`Err*CacheShape`, or one whose message says "not ported", is defined with `errors.New`.

Errors that are Django's own failures (a Python exception the view also raises: `Err*Server`, `Err*Python`) are not
refusals. Go answers them itself, as Django would.

## Request flow

1. `handoff.Middleware` is the first `Pre` middleware (`cmd/server/main.go`). It keeps the request as received
   (method, request-target, `Host`, headers), wraps the body in a recorder and the response writer in a writer that
   notes the first byte sent, and puts the request's state in the context. Disabled, it returns the next handler
   unchanged.
2. The handler runs. The body recorder copies what the handler reads, up to the limit.
3. The two places that answer an unmapped error call `handoff.Serve(c, err)` first: `restError`'s default branch
   (`handler/refresh.go`, the 500 page every family mapper ends in) and `DjangoHTTPErrorHandler`
   (`handler/django_not_found.go`, for errors returned to Echo).
4. `Serve` replays only when all of these hold; otherwise it logs `handoff skipped` with the cause and the caller
   answers the 500 page as before:
   - the error wraps a refusal sentinel;
   - nothing was written to the response;
   - the handler did not call `handoff.Forbid`;
   - no effect was recorded for the request and no database transaction of the request is open;
   - the body is complete within the limit (the unread rest is read first);
   - the request was not already handed off.
5. The replay sends the same method, the same request-target (escaped path and raw query, byte for byte), the same
   `Host`, the same headers (hop-by-hop headers and `Expect` removed) and the recorded body with its length. Caddy
   strips `/api/v2` before Go, as it strips `/api/v1` before Django, so the path Go received is the path Django
   expects. No proxy is used, redirects are not followed and compression is not negotiated by the client, so
   `Accept-Encoding` and the response bytes pass through untouched.
6. Django's status, headers and body are streamed to the client. The headers Go's middleware had prepared are
   dropped, so the response is Django's alone, plus `X-Assozeta-Handoff: django`.

Log lines (`api-go`, slog): `handoff to django` with `method`, `route` (the Echo route pattern), `status`, `reason`,
`duration_ms`; `handoff skipped: Go answers the refusal with 500` with `method`, `route`, `reason`, `cause`;
`handoff to django: response body interrupted` when Django's body ends early (the client gets the truncated response).
No header value, query or body is logged.

## Effects: when a refusal is not handed off

Handlers refuse before any write, and the tracker enforces it at run time. An effect recorded for the request context
blocks the handoff (`effects.go`):

| Effect | Where it is recorded |
|---|---|
| SQL that may write (anything but a known read statement; a data-modifying CTE, `nextval`, `setval`, `pg_notify`, `COPY`, batches) | `handoff.QueryTracer` on the pgx connection config |
| An open transaction of the request (`BEGIN` without its `COMMIT`/`ROLLBACK`) | same tracer; Django would wait on its row locks |
| A Redis command that is not a known read (cache, broker/Celery publish, reset tokens, notification store, channel layer) | `handoff.RedisHook` on every request-scoped client |
| Object storage put, delete, ACL change | `pkg/storage/documentobjects`, `instancelogo`, `s3objects` |
| SMTP send, Stripe, Google, Bakney authority and model requests, updater runner requests, eager print requests | `handoff.MarkEffect` at the call |
| Streamed backup upload | `handoff.Forbid` before the body is streamed (`instance_backup_actions.go`) |

The tracker is conservative: a write that is later rolled back still counts, and an unknown SQL or Redis command
counts as a write. Read-only outbound calls (the PDF renderer, release and health probes, social token checks) are
not effects.

New code that changes something outside the database and Redis must call `handoff.MarkEffect(ctx, "...")` before the
call, with the request context. `MarkEffect` and `Forbid` are no-ops without a handoff state (handoff off, workers,
background jobs).

## Cases that are not handed off

They answer the 500 page as before:

- A refusal after an effect, for example `ErrInstanceSetupRefused` for a logo name collision met after the old
  logos were deleted, or any sentinel returned inside a transaction that already wrote.
- `POST instance/admin/backups/upload` (streamed to storage) and any body over the limit (large import, restore and
  document uploads).
- A request whose target starts with `//`.
- Django unreachable or failing before its response headers arrive.
- Errors that are not refusals: unexpected Go failures and Django-parity 500s.
- A write issued with a context detached from the request is not seen by the tracker; handlers must keep the
  request context for request work.
- WebSockets (`/ws/*` is routed to Django by Caddy) and Celery tasks (`celeryworker.ErrHandoff` returns a task to the
  Django worker) are separate mechanisms.

## What Django sees

Django receives the request as Caddy would have sent it: same `Host`, `Authorization`, `X-Impersonation-Id`,
`X-Group-Id`, `X-Correlation-Id`, `X-Forwarded-*`, cookies and body. Its peer address is `api-go` instead of Caddy;
both are on the internal network. Reads done by Go before the refusal (authentication, lookups) are repeated by
Django. Keep the URL on the internal network: the replay carries the caller's token.

## Evidence

Go unit tests (`go test ./internal/handoff ./internal/refusal`):

| Test | Covers |
|---|---|
| `TestReplayFidelity` | method, escaped path, raw query, Host, repeated and odd-cased headers, body bytes; Django's status, headers and body returned unchanged with the marker |
| `TestUnreadAndPartlyReadBodiesAreReplayedWhole` | body buffering when the handler read nothing or a part |
| `TestBodyOverLimitIsNotHandedOff`, `TestStreamedUploadIsNotHandedOff` | limit and `Forbid` |
| `TestRefusalAfterEffectIsNotHandedOff`, `TestOpenTransactionIsNotHandedOff`, `TestSQLWrites` | effect tracking |
| `TestStartedResponseIsNotHandedOff`, `TestOnlyRefusalsAreHandedOff`, `TestDisabledKeepsThe500` | the other guards |
| `TestUpstreamErrors` | unreachable Django, Django's own 4xx/5xx passed through, interrupted body |
| `TestNewRejectsInvalidURLs`, `TestBasePathPrefixesTheRequestTarget` | configuration |
| `TestRefusalSentinels`, `TestNotPortedSentinelsAreRefusals` | every sentinel matches `refusal.ErrNotPorted` |

Harness family `django-handoff` (`TESTS/fixtures/django_handoff_http.json`, 10 cases): refused inputs of one
family (workflow-update) plus ported controls for it, for course-list and for personas-reads. The harness starts Go with `DJANGO_HANDOFF_URL` pointing at the run's
Django. Each case is checked by its source family's contract (exact status, headers, body; for the workflow writes the
saved row and the audit entry with the client's correlation id), then for the marker: `X-Assozeta-Handoff: django`
on the Go transports for a refused case and no marker otherwise. A handed-off case must be observed as one Django
request with the case's method, path, status and correlation id.

| Family | Refused input | Django's answer through Go |
|---|---|---|
| personas-reads | none: Cyrillic search terms are ported and served by Go (controls, no marker) | 200, empty page (control) |
| course-list | none: the orderings are the course's own columns, served by Go (control, no marker) | 200, the listed course (control) |
| workflow-update | `PATCH communications/workflows/<id>/update` with a form `automation_tree` nested past 500 levels (`ErrAutomationWorkflowFormTreeNotPorted`) | the stable 500 page |

Receipts (`quality-reports/go-migration/django-handoff-http-<label>`):

- RED `red-f3`, handoff off (`HANDOFF_FAMILY=off`): Django 10/10; Go answers the first refused case with 500.
- GREEN `versioned-f3`, handoff on: django-native, go-native, caddy-v1 and caddy-v2 10/10 each.
- Mutation `mutation-f3` (the replay forwards only `Authorization` and `X-Correlation-Id`): CAUGHT at
  `response.headers` of the first handed-off case.

Existing families, handoff off (the harness default), `run --versioned`, four transports each:

- Before the last merge of `codex/go-backend-parity` (label `handoff-f1`): 146 families green.
- After it (label `handoff-f2`): the 40 families whose fixtures, adapters or Go files the merge changed, green:
  balance-sheet, camps-periods, communications-send-email, communications-smtp, course-enrolment, course-list, docmanager-reads, document-render, document-views, documents-writes, impersonation-users, invoice-parties, invoice-sync-print, invoice-views, misc-routes, payment-misc, payment-reads, payment-state, payment-writes, personas-reads, personas-writes, profile-misc, statistic-reads, subscription-create, subscription-misc, subscription-reads, subscription-renew, subscription-sign, workflow-update, sso-pairing, documents-list, documents-delete, documents-move, documents-bulk-delete, router-documents, router-personas, misc-exports, payment-export, course-subscriptions, subscription-medical. The other 106 keep their `handoff-f1` result.
- `router-memberships` fails on the Django side before Go is asked (its adapter reuses the subscription-memberships
  effect check without that family's storage fixture); it is unrelated to the handoff.
- `login`, `manual-reader`, `refresh` and `session` have their own drivers and are not run by `family.py`; their Go
  packages pass `go test ./...`.

`make selfhost-test` passes; `test_go_lifecycle.py` checks the compose default and that an empty value stays empty.
