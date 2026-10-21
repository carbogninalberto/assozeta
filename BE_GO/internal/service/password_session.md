# Password session composition

`PasswordSessions` connects password admission, JWT issuance, response construction
and final login writes. It receives explicit interfaces and trusted audit context;
there is no SQL or framework request context in the coordinator. The concrete
components retain their existing bounded queries, admission limits and transactions.
Identifier and password values remain uncoerced until credential selection; see
[password_login.md](password_login.md) for the JSON/multipart value semantics.

The sequence is intentional. Credential upgrades and username normalization commit
before the OTP decision. An OTP denial therefore retains a legitimate username
audit. Successful OTP permits token construction and the response's default-card
invariant. Only after the response is built does finalization update `last_login`
and clear a present/future `delete_on` date with its audit. Earlier committed
invariants survive a later failure, matching the supported workflow boundaries.
The finalization invariant itself remains atomic as documented separately.

Tokens remain local values until all stages succeed. Every stage error or context
cancellation returns a nil response; the transport must set cookies and send the
body only after successful return. Cancellation after a successful final commit
can retain that commit while withholding the response. The coordinator checks the
returned account/profile identity and token pair before finalization, preventing
an inconsistent dependency result from being delivered as a session. Audit actor
and authenticated account remain separate values throughout.

Unit tests specify stage order, failure and cancellation at every boundary,
canceled entry and inconsistent dependency results. The private PostgreSQL
adapter uses the actual Django login view, production password hashes, real
Ed25519 tokens and the concrete Go repositories/services. Its explicit cases cover
athletes, associations, collaborators, administrators, historical role zero,
OTP and credential denials, invalid stored roles, missing associations, default
card creation, deletion-date boundaries and a database-triggered final-write
failure. It verifies complete response fields, both signed token claims, all
managed tables after restoring declared effects, username/deletion audits and
association indexes. Eight concurrent repeated logins exercise one shared
coordinator and check that committed normalization/card effects do not duplicate.

The fixtures have no image that needs conversion. A complete image dispatcher,
social admission, complete transport parsing, rate limits, audit middleware, public HTTP
registration and backend switching remain open. The Django cookie assertions
characterize its transport only; they do not prove Go cookie parity. This service
is not a production-ready public login endpoint.
