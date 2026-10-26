# Saved message CREATE and DELETE in Go

This slice implements saved-message persistence only. The coordinator owns route composition, shared infrastructure, corrected Django reference, independent authored contracts, real PostgreSQL execution and publication. No route is marked verified by this worker. Requested base commit: `785ff8945b44dfbcfcacb197f831de1da2990741`.

## Composition interfaces

```go
store := repository.NewMessageMutations(db) // db: *sqlx.DB using database.OpenDB
mutations := service.NewMessageMutations(store, time.Now)
h := handler.NewMessageMutationsHandler(
    mutations, actors, identities, middleware.SessionAuditContext,
    paymentCategoryOperations,
)
// Parent applies the same admission-wrapper placement used by MessageReads.
e.Any("/communications/messages/add", h.Add)
e.Any("/communications/messages/:message_id/delete", h.Delete)
```

All constructor arguments are required. The lifecycle instance must participate in the existing server drain/expiry/wait protocol. The repository requires the shared owned pgx connector: stdlib-only pools are rejected by `beginOwnedTransaction`. The coordinator supplied the shared `appendModelAuditForApp(ctx, tx, appLabel, modelAuditEntry)` extension; this slice calls it with `"communications"`. The shared extension is outside worker ownership.

The handler's `MessageMutationsService` interface is:

```go
Admit(context.Context, domain.ResolvedIdentity) error
Add(context.Context, domain.ResolvedIdentity, dto.MessageMutationWrite, domain.LoginAuditContext) error
Delete(context.Context, domain.ResolvedIdentity, string, domain.LoginAuditContext) error
```

Service inputs require a trusted resolved identity, including actor, effective user and scope owner. The public DTO parser is `dto.ParseMessageMutationWrite(any) (dto.MessageMutationWrite, error)`. The owned HTTP JSON parser is `dto.ParseMessageMutationsJSON([]byte) (any,error)`. The DTO input is the shared ordered `domain.RequestObject`, with JSON numbers represented by `json.Number`; it is not an arbitrary Go map. The DTO has `Type string`, `Message string`, and `Subject *string`. Omitted and explicit-null subject both become nil, matching persisted CREATE behavior. Client IDs, association and timestamps are ignored by the parser and generated/resolved by the service.

`domain.MessageMutationStore.BeginMessageMutations(context.Context)` returns `domain.MessageMutationTransaction`. Its methods are `OwnerForUpdate(ctx, owner) (role int, active bool, deleted bool, err error)`, `AssociationForOwner(ctx, owner, associationScope *string) (string,error)`, `MessageForUpdate(ctx,id,association) (MessageMutationState,error)`, `InsertMessage(ctx,state) error`, `DeleteMessageDeliveries(ctx,id) error`, `DeleteMessage(ctx,id,association) error`, `AppendMessageAudit(ctx,event) error`, `Commit() error`, and `Rollback() error`.

Identity operation names are `communication_messages_add` and `communication_messages_delete`; the DELETE route parameter is `message_id`. No CLI is added.

## Reference behavior and deliberate corrections

Inspected sources include `BE/communications/serializers.py` (`MessageInputSerializer`), `models.py` (`Message`, `MessageTransaction`, registration list), `views.py` add/delete functions, all repository-wide Python references to Message/MessageTransaction and model FK declarations, `application/signals.py`, `application/audit_resolvers.py`, the installed production auditlog diff/receivers, and the existing Go payment-category and message-read layers.

CREATE accepts exactly the `EMAIL` and `INSIDE_APP` choice strings. Message is required and non-null, rejects the empty string, preserves whitespace, and has no model text length limit. Subject is optional, nullable, accepts blank text, preserves whitespace, and has a 255 Unicode-code-point limit. DRF CharField numeric conversion, null-character and surrogate validation reuse the existing DTO primitives. The owned JSON boundary first applies the shared 4,300-digit integer limit to every token, including ignored, overwritten and nested fields, then reuses the shared surrogate-preserving strict JSON parser. A 4,301-digit integer fails with parser400 and DRF's integer-limit detail; 4,300 digits pass. Signs do not count, and quoted digits/floating-point tokens do not inherit the integer limit. JSON raw invalid UTF-8 remains parser400; an escaped lone surrogate in a serializer field remains generic serializer400; literal replacement characters, surrogate pairs and ignored surrogate values remain distinct.

URL-encoded form percent escapes decode with the declared UTF-8 or Latin-1 charset: UTF-8 `%FF` becomes U+FFFD, Latin-1 `%E9` becomes `é`. The existing UTF-8 query primitive retains malformed escapes and replacement granularity (adjacent invalid bytes versus truncated sequences); Latin-1 percent decoding retains literal non-ASCII text. Django QueryDict's raw-body fallback is also retained: invalid raw UTF-8 falls back to Latin-1 before percent decoding, so raw FF becomes `ÿ`, separately from escaped `%FF`. The handler's owned body reader uses the existing authenticated body cache for both impersonation resolution and mutation parsing. No shared parser/API or constructor changes are required. Booleans, lists, dictionaries and uploads cannot supply text. Unknown fields are ignored. The parser uses the existing Python-code-unit-preserving JSON reader so a lone surrogate is rejected rather than silently replaced.

Successful CREATE returns HTTP 200 `{"msg":"message added."}`. Serializer errors return HTTP 400 `{"msg":"message not valid."}`; malformed bodies and unsupported media retain shared REST parser errors. No message/delivery DTO is returned. CREATE inserts no delivery rows and invokes no sending, workflow, external effects or subscription gates.

DELETE validates UUIDs with the existing Django-compatible parser. It scopes the message by resolved association and ID without a type filter; legacy channel types remain deletable. It deletes the actual `communications_messagetransaction` records before the message, without assuming PostgreSQL `ON DELETE CASCADE`. Success returns HTTP 200 `{"msg":"message deleted."}`. Missing or foreign message returns corrected HTTP 404 `{"msg":"message not found."}`; the Django baseline otherwise attempted `None.delete()`.

The coordinator reported the second bounded reference correction: Django autocommit could preserve a created Message after audit failure, and the audit-index signal could suppress failure outside strict context. Per coordinator instruction the Go mutation remains atomic across business rows, audit and audit index. The coordinator owns the Django atomic-audit correction and its independent oracle evidence. This worker did not modify Django.

Transport order follows MessageReads: format negotiation, Accept negotiation, actor authentication, scope resolution, missing-credential handling, business-role admission, OPTIONS/method dispatch, tracked operation, CREATE body validation, trusted audit extraction, lifecycle admission, service. Scoped requests share the cached body/error with the identity resolver. OPTIONS reports the existing metadata shape and `Allow: POST, OPTIONS` or `DELETE, OPTIONS`. Parent must register both handlers through its existing admission middleware; that route integration has not been exercised here.

## Audit and transaction behavior

Only Message is audit registered. MessageTransaction is the sole discovered concrete reverse FK and has no audit registration or application deletion hook. The default reverse name `messagetransaction` has no instance attribute (the accessor is `messagetransaction_set`), so the installed auditlog implementation contributes no reverse-manager diff.

Message audit uses `communications.message`, object representation `Message object (<uuid>)`, CREATE action 0 or DELETE action 2, and the same original actor/email, request metadata and impersonation attribution helpers as existing mutations. The index association is the message association. CREATE resolution is `direct`; DELETE resolution is `changes_json.sport_association` after row deletion. Audit values include UUID, association, type, raw message, non-null subject and datetime semantics from auditlog; literal text `"None"` compares equal to the absent side and is omitted. A null legacy datetime remains `["None","None"]`. A standalone offline production-Python auditlog diff probe confirmed those typed edge cases; the parent’s actual Message oracle remains authoritative.

Transactions concretely reuse existing owner/association locks, the owned connection, Read Committed isolation, shared restore advisory barrier and physical cleanup completion join. A locked owner is rechecked for association role, active state and non-deletion. Optional impersonation association ID is applied to the locked association lookup. DELETE then takes the scoped parent row lock, deletes deliveries and parent, writes audit/index, checks cancellation, and commits. Concurrent same-owner mutations serialize; a concurrent FK writer must wait on the message row and fail if DELETE commits first. No new orphan semantics or soft deletion are introduced.

Before lifecycle admission, client cancellation remains effective. After admission, the existing lifecycle detaches client cancellation while preserving context values; server expiry cancels the operation. Direct service cancellation aborts and joins rollback. Deferred rollback joins driver cleanup before the handler releases its tracked operation. The parent must retain its server restore/drain admission protocol; this slice adds no separate globals or server lifecycle.

## Tests and evidence

Tests were authored before the implementation. The first focused red run failed compilation because the new parser/domain/service/handler interfaces did not exist. Initial repository-root and default-cache setup failures are recorded separately in `quality-reports/worker-progress.md` and are not claimed as red tests. The first implemented focused run passed DTO, service and handler tests. The subsequent independently expected literal-None/null-datetime test failed behaviorally against the initial audit implementation, then passed after the fix. Exact outputs, final command results and file hashes are handed off in the progress report.

Unit tests cover serializer type/default/null/whitespace/Unicode behavior; handler success and generic serializer failure responses, auth/negotiation/body/method ordering; service ownership, foreign-record defense, valid trusted identities, impersonation attribution, write/audit/commit failures, cancellation and audit edge behavior.

The `messagemutationsintegration` suite is compiled but **not executed by this worker**. It requires `ASSOZETA_MESSAGE_MUTATIONS_DSN` for the parent's fully migrated historical fixture, using a Unix socket named `az-history-*` under `/tmp` or `/private/tmp`, database `assozeta_contract_historical`, user `contracts`, with no fallback connections. It creates bounded fixture owners/associations under `29000000-...` IDs and generated message/delivery IDs. The concrete INSERT-failure group temporarily creates a fixture-scoped `mm_mutations_reject_insert` trigger/function and removes both in subtest cleanup; it creates no application tables or services. The parent fixture must permit that fault-injection DDL. Run serially in the private fixture: complete-row snapshots intentionally compare all rows in the four affected tables. It fails rather than skips if configuration is missing. The tests introspect the actual reverse FK and cover real delivery cascades with a foreign sentinel, full audit/index records, four actual BEFORE INSERT failures (CREATE/DELETE × audit/index), separate rollback faults after successful audit/index writes, a real invalid audit-port SQL error, cancellation, concurrent DELETE, FK writer conflict and exclusive restore-barrier cancellation. All mutation rollback cases compare complete business, delivery, audit and index rows before and after, including preexisting owned/foreign sentinels; sequence gaps are outside the invariant. The writer race separately exercises the production DEFERRABLE INITIALLY DEFERRED default (successful INSERT, blocked COMMIT) and immediate constraints set before INSERT (blocked INSERT), requires SQLSTATE23503 after the winning DELETE, and checks one delete audit/index plus preservation of all other rows. The parent's prior real547schema pass of all five original groups applies to the earlier candidate only. This revision adds a sixth group and changes existing coverage; no new database pass is claimed. The parent's 12 corrected Django regressions (404 and strict atomic CREATE/DELETE), including documented red baselines, are parent-reported reference evidence. Shared-audit namespace results are also separate.

Parent commands, from `BE_GO` (set the DSN only for the real integration command):

```sh
export GOCACHE=/private/tmp/message-mutations-go-cache
export GOMAXPROCS=2 GOFLAGS=-p=1 GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local GOWORK=off
GO=/Users/alberto/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.12.darwin-arm64/bin/go
"$GO" test -race ./internal/dto ./internal/service ./internal/handler ./internal/repository -run '^TestMessageMutations' -count=1
"$GO" test -race -tags messagemutationsintegration ./internal/service -run '^$' -count=1
"$GO" test -race -tags messagemutationsintegration ./internal/service -run '^TestMessageMutationsPostgres' -count=1 -timeout=120s
```

Unresolved acceptance: parent registration/admission integration, independently authored Django-vs-Go endpoint contracts (including collaborator/impersonation matrices, parser/media/locale and malformed-UUID cases), actual-Message audit edge confirmation, live PostgreSQL FK/rollback/race/restore checks, route-level drain/disconnect and real-service full acceptance. No service/database/route verification is claimed by this worker. Existing shared infrastructure and baseline tests are outside the owned edit scope.


## Review correction evidence

The independent review at `quality-reports/go-migration/parallel-scale-20261025/message-mutations-go-review-sol-r1/result.md` identified two concrete parser P2 defects. `TestMessageMutationsHTTPParserBoundaries` failed behaviorally against the existing candidate before either fix: percent-decoded invalid bytes/Latin-1 were retained as invalid bytes, and 4,301-digit integers reached the mutation service even in ignored fields. It passed after the owned fixes. Subsequent boundary regressions retain the raw-form Latin-1 fallback and JSON/surrogate distinctions. The exact final Go commands/results and SHA-256 manifest are in `quality-reports/worker-progress.md`.

Reference reproduction used only `/Users/alberto/Desktop/Bakney/Workdir/assozeta-go/quality-reports/go-migration/python31316/bin/python`, with asserted `sys.version_info[:3] == (3,13,16)` and `sys.get_int_max_str_digits() == 4300`, Django settings `DATABASES={}`, and installed DRF `FormParser`/`JSONParser` on in-memory streams. It confirmed UTF-8 `%FF` replacement, Latin-1 `%E9`, replacement granularity, malformed percent preservation, raw-body Latin-1 fallback, JSON invalid-byte/lone-surrogate/valid-pair distinctions, and 4,300/4,301 integer boundaries in message/ignored fields. These are offline library probes, not endpoint/database execution. The reviewer's ambient Python versions are not exact-production evidence.

## Parent integration evidence

The coordinator registered both routes with the existing identity, session-audit, restore-admission and operation-drain infrastructure. The outer locale writer now covers saved-message mutation responses, including restore denials; four focused admission regressions reproduced the missing header before the correction.

The integrated candidate passed `go test -race ./...` and `go vet ./...`, six real PostgreSQL service test groups against all 547 migrations, and 119 independently authored HTTP cases on each of native Django, native Go, shipped Caddy `/api/v1`, and `/api/v2`. The HTTP run compared all 128 managed models and the complete Redis DB1 state, historical audit preservation, exact new audit/index rows, actual failure-trigger rollback, separate tenant successes and reciprocal denials, scoped-session parsing and authenticated restore denial. Effect projections agreed across all four transports; actual process guards and an intentionally misrouted proxy rejected false Go attribution. Source hashes and cleanup were verified.

Exact HTTP evidence: `quality-reports/go-migration/message-mutations-versioned-http-r2/results.json`, SHA-256 `15c2af9c0ec14823bdcc7fcd0824ebdf32debd2fd4280bb93f35d7adc8ebf629`. It binds the tested source and native binary; later documentation and ledger edits are not relabeled as inputs to that run. PostgreSQL evidence remains `message-mutations-go-postgres-r2/results.json`; the reference audit regression passed 102 tests. Earlier worker-only compile statements above describe historical checkpoints.

The ledger remains **implementing**. Maintained acceptance CLI/CI publication, route-level disconnected-client and graceful-drain probes, mutation browser workflows, packaged mutation acceptance, broader performance, and installed activation/rollback remain unfinished. The saved-message slice does not implement email sending or the other communication workflows, and does not complete the migration.
