# Google provider verification

`Google` implements the inspected `SocialAuthService.verify_google_token` boundary
with an explicit HTTP client, dedicated JWK transport, clock and concurrency bound.
`SocialProviders` supplies the current configured audience. The public root now wires this client and has private CONNECT/TLS HTTP evidence;
live Google/browser compatibility remains unverified.

The certificate path fetches the fixed Google certificate URL before parsing a
token, checks RSA/SHA-256 or ECDSA/P-256 signatures using PEM public keys or X.509
certificates and requires `iat` and `exp`. A certificate supplies a verification
key; its validity dates are not an additional token-admission condition. Missing
or empty `kid` checks certificates in insertion order. An invalid earlier key can
abort before a later matching key. This path accepts expiry equal to the current
integer second, ignores `nbf` and retains raw subject/identity values.

A response containing `keys` instead delegates to the JWK path, matching the
installed google-auth 2.38.0 and PyJWT 2.15.1 behavior. It validates the unverified
document/header, fetches the same URL again and selects the first usable signing
key with a matching ID. The Go client creates a separate HTTP client around the
injected JWK transport and refuses redirects; certificate/access requests retain
the supplied client's redirect policy. Supported JWK implementations include
RSA/PSS, P-256/P-384/P-521 ECDSA, Ed25519 and HMAC. The oracle exercises RSA, PSS,
RS384, P-256, Ed25519 and HMAC. Algorithm choice is bound to the fetched key. Optional
time, subject/JTI and audience checks follow the shared PyJWT rules; expiry at the
current second is rejected here. Keys are reread per verification, without a
process-wide cache.

Only failures corresponding to the application's caught `ValueError` enter the
access-token path. It requests `tokeninfo` with the access token as a query value,
requires HTTP 200 and exact configured audience equality, then fetches `userinfo`
using Bearer authorization. Access-provider failures return no identity. ID-token
transport failures, invalid issuers and uncaught type/key errors propagate as the
generic `ErrGoogleProvider`, without credentials or upstream response details.
Both successful paths retain the complete email/verification/name/picture/subject
mapping, with original JSON types and absent-field defaults.

The client holds one concurrency slot across the full operation. Queued and active
calls observe cancellation and release slots. Certificate, JWK and access requests
have 120-, 30- and 10-second context deadlines respectively. Each response is closed
and read through a 1 MiB limit plus one sentinel byte. Encoded ID-token parsing is limited
to 1 MiB and RSA moduli to 16,384 bits. These are explicit Go resource choices;
Python's request timeouts are not proof of the same total-duration or byte limits.
Provider sizing, aggregate deployment budgets and root transport configuration
remain activation requirements.

`TESTS/adapters/django/google_tokens.py` declares 93 scenarios and checks the actual
Django service, installed Google/PyJWT verifiers and Go using real signatures and
an ephemeral trusted HTTPS server. Only configuration, destinations and clocks
are substituted. It compares complete identities, success/error boundaries,
request order, destinations, query values and authorization headers. Cases include
certificate and JWK redirect differences, malformed keys/documents, expiry/audience
rules, disabled providers, access fallback and unchanged raw identity values. A
fixture protocol failure fails the adapter even when authentication was expected
to fail. No production credentials, developer `.env` or live Google endpoint is used.

TDD exposed a missing client, missing JWK handling, premature unknown-algorithm
rejection, malformed key algorithm acceptance and incorrectly followed JWK redirects before the corresponding fixes.
Unit tests measure concurrency, cancellation/reuse, deadline phases, response read
bounds and closure. A separate real RSA/HTTPS service test verifies audience changes,
disable behavior and configuration failures; its configuration reader is controlled,
with persisted resolution covered by the existing PostgreSQL configuration adapter.

Remaining cases include raw container/upload access-token conversion, nonstandard
JSON/base64 and malformed key shapes, less common JWK curves/private-key forms,
numeric/boolean audience equality, access-token size budgets, transport/proxy/charset extremes and live-provider
evidence. Whole persisted-account/session/HTTP/browser composition and environment
wiring remain incomplete. The 93 passing cases do not certify all these boundaries.
