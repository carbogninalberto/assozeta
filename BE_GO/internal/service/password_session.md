# Password session composition

`LoginSessions` dispatches password/social admission, then shares JWT issuance, response construction
and final login writes with the existing `PasswordSessions` convenience API.
Both receive explicit interfaces and trusted audit context;
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

These service fixtures have no image that needs conversion and use controlled
social providers. The public root now has separate password/OTP, real-avatar,
session/audit and rate-ordering HTTP evidence in `../handler/login.md`. Missing
image formats, public social-provider HTTP, full middleware, mixed-runtime races
and backend switching remain open; these service cases do not close those gates.


The composed fixture now contains 26 declared cases: 18 password cases and eight
social cases with controlled provider results. Social scenarios include athlete,
association/card creation, collaborator and administrator sessions, missing/wrong
OTP, token rejection despite a supplied valid password, and a real final-write
failure after card creation. Provider alias/token/call counts are checked before
response comparison. These service cases execute actual JWT signing, profile/
configuration reads, audit writes and finalization; they do not establish remote
Google/Apple verification or successful public HTTP middleware behavior.


The full accounts rehearsal at `30f9c10` exposed a concurrent-session failure in
credential selection, before response delivery. The credential regression and
correction are described in [password_login.md](password_login.md#concurrent-login-finalization-and-credential-resets).
This does not relax the eight-concurrent-session check or its signed identity,
normalization/card uniqueness and connection-release requirements.
