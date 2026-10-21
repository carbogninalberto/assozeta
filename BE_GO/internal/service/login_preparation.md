# Password admission and username audit

`PasswordAdmission` composes the password selector, login preparation and OTP gate
in that order. A wrong, ambiguous, disabled or missing credential never reaches
normalization. Preparation reloads the selected account under a row lock, checks
current active/deleted state, normalizes its username with Unicode uppercase and
commits its audit invariant. OTP runs **after that commit**: an OTP denial retains
the normalization and audit, while returning no admitted account. These components
still do not build a response, finalize last-login/deletion writes or issue cookies.

The preparation service owns the transaction; the repository owns parameterized
SQL over existing Django tables. Unchanged names require one locked account read
and generate no audit. Changed names use one bounded lookup for associations of
both the target and trusted actor, one update and one audit insert. An applicable
association index adds one insert: four queries without an index, five with one.
No model, schema or migration ownership changes are introduced.

Audit fields preserve the existing user update representation: action 1, user
content type, UUID object key, `name: <first> <last>` object representation and
`username: [before, after]` changes. Unused serialized/additional data and numeric
object ID remain null; changes text remains empty. Actor ID/email, remote address,
port and correlation ID come from explicit trusted request audit context. The
actor is independent of the logging-in account. Full session/Bearer-to-audit-context
composition in the Go HTTP middleware remains required.

Association indexing first uses the non-deleted association directly owned by the
changed user, then the actor's directly owned association. It does not follow a
collaborator's connected owner. The resolution paths remain
`user.sport_association` and `actor.sport_association`. Audit/index timestamps and
index UUIDs are generated; existing PostgreSQL sequences allocate event IDs.
Django reads and processes the resulting events and indexes.

Username, event and index form one invariant. Cancellation, unique-name collisions,
missing actor/index foreign keys and commit failures roll it back and return no
challenge. Deadlock/serialization conflicts retry the whole preparation at most
three times. Concurrent normalization locks the same row and produces one event;
already-normalized retries/readers do not insert duplicates.

This atomic failure boundary intentionally differs from Django's pre-save audit
hook: a Django username uniqueness failure leaves an audit for a change that did
not persist. The adapter characterizes that behavior explicitly. Go rolls it back
under the requested transaction invariant; this is not a claim of identical
failure-side-effect behavior. PostgreSQL sequence gaps on rolled-back attempts
retain normal database semantics. The ledger remains implementing.

The private account adapter invokes the actual Django login view through
`AuditlogMiddleware`, with production password hashes and only the OTP clock
controlled. Twenty explicit cases check admission outcomes, username mutations,
every stored audit field, association resolution and later Django last-login
behavior. Go runs the composed password/preparation/OTP services on the same
fixtures. Django validates the Go audit fields and relationships; after restoring
declared fixture changes, every managed table must match its baseline.

The adapter creates its schema after `django.setup()`, so it rebuilds the resolver
registry after schema creation to match a server started after migrations. Without
that step, the initial registry has no content types and incorrectly omits target
association indexing. This fixes the fixture lifecycle; it does not disable hooks.

Real PostgreSQL failure/concurrency checks cover audit/index/commit failure,
cancellation after writes, username collision, bounded query counts, concurrent
normalization and a forced cross-actor deadlock. The public login handler, social
providers, request coercion/rate limits, response-created configuration, final
login writes, all audit workflows, middleware, release and backend-switch evidence
remain unfinished. This service is not a production-ready login endpoint.
