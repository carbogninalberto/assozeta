# Login HTTP transport

`LoginHandler` implements the transport boundary around a `LoginRequestService`.
It is deliberately not registered by `cmd/server` while request credential
coercion, password/social composition, shared rate limits, trusted audit middleware
and complete image processing remain unfinished.

The handler uses the existing DRF-compatible body reader and response helpers.
Negotiation and `format` selection precede supplied-Bearer authentication; trusted
identity/scoped-request resolution follows authentication. Scope checks retain a
body parse error until they need request data. OPTIONS and method rejection precede
ordinary body parsing. Anonymous requests cannot create an identity using scope
headers. The operation name comes from code, never a client header.

`ParseLoginRequest` validates field presence and selects the password/social branch
without coercing values. A present `token`, including null, prevents the password
branch; `username`, `token` and `backend` select social login even if a password is
also supplied. OTP absence and a present null remain distinguishable. Credential
services must handle those raw values in the same order as the reference view;
this DTO does not grant authentication.

Trusted audit metadata comes through an explicit request reader independently of
the newly authenticated account. The handler sends a response and secure,
HTTP-only, SameSite=Strict `BKN_AUTH` cookie only after the session service succeeds.
Credential/OTP failures preserve their existing 401 bodies. Infrastructure and
unexpected service errors return the generic Django-style 500 body without
provider details, tokens or cookies. Unit tests exercise both branches, identity
and body-error precedence, trusted audit propagation and success/failure cookies.
Those cookie tests use a test service and are not whole-login HTTP parity.

Fifteen portable contracts in `TESTS/contracts/test_login_transport.py` run over
real HTTP against Django and a Go test server using this handler. They cover
negotiation, method/OPTIONS metadata, bearer error precedence, malformed and
non-object JSON, missing fields, JSON/form/multipart parsing, `format` and anonymous
scope headers. They all stop before credential selection. The Go fixture counts
calls and rejects any attempt to reach accounts, identity resolution, audit
metadata or session construction; no successful credential work is simulated.
Django runs on the accounts adapter's temporary database, and a snapshot of every
managed table confirms no durable effects. Rate limits are disabled by that
fixture, so these contracts provide no rate-limit proof. Public endpoint wiring,
successful login HTTP and complete middleware remain separate acceptance gates.
