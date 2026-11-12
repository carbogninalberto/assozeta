# Social provider clients

`Apple` implements the inspected `SocialAuthService.verify_apple_token` boundary
in `BE/application/services/social_auth_service.py`. It accepts an explicit HTTP
client, clock and positive concurrency limit. Current instance settings remain
outside this package; `service.SocialProviders` reads them for each operation,
stops disabled providers, dispatches the raw token/audience and returns the email
result to login admission. The Google client now has separate certificate/JWK/access-token verification;
see [its protocol and remaining scope](google.md). The public login root now explicitly composes both clients; full parity remains open.

Apple verification requests the fixed HTTPS keys URL before parsing the token,
selects the first matching `kid`, constructs an RSA public key and verifies only
RS256. It rereads keys on every operation, preserving rotation and first-match
behavior. Key responses are parsed even for a non-200 status, as in the current
Django implementation. Redirects are handled by the injected HTTP client; the
fixture checks a successful redirect and compressed response. No token or client
ID is sent to the keys endpoint.

Issuer and audience must match. Optional `iat`, `nbf` and `exp` follow PyJWT's
integer conversion and boundary checks; the existing Django client accepts a
signed token without `exp`, and the fixture explicitly records that behavior.
Present subject/JWT-ID claims must be strings. Email, email verification and
subject values are returned with Django's defaults and JSON types. Missing or
non-string email remains a later login-admission decision.

The pinned Go JWT library provides signature verification with an explicit
algorithm allowlist and JSON numbers; see its
[parsing options](https://golang-jwt.github.io/jwt/usage/parse/).
Additional checks preserve the inspected PyJWT 2.15.1 behavior: reject unknown or
malformed critical headers, detached payloads, trailing JSON and integers beyond
Python's 4,300-digit input bound. Known `b64` critical metadata with normal encoding
is accepted. TDD comparison exposed Go-library acceptance of malformed critical
metadata, trailing payload data and an oversized unused integer before these
guards were added. Date strings also preserve Python `int()` whitespace rules,
which differ from `str.strip()` for ASCII record/unit separators.

Each admitted verification has a ten-second context deadline. Queued calls obey
caller cancellation, and every exit releases the concurrency slot and closes any
response body. Caller cancellation propagates; provider/network/credential
failures return an unsuccessful verification without provider diagnostics or
credential values. The client reads at most 1 MiB plus one sentinel byte from the
decompressed keys response, accepts encoded tokens up to 1 MiB and RSA moduli up
to 16,384 bits. These are explicit Go resource limits, not limits inferred from
Django. Tests measure the actual read bound and verify token/modulus rejection;
deployment-wide budgets and supported-provider sizing remain release concerns.

Run `python TESTS/adapters/django/apple_tokens.py` in the repository's Django
dependency environment. It loads the actual Django service source and substitutes
only client-ID resolution, the keys URL and clock. Both implementations use real
HTTPS, JWK parsing and RSA verification against an ephemeral local server. The 89
declared cases compare complete identity results and exact HTTP request counts,
including disabled providers, key rotation, duplicate/malformed keys, signature
and algorithm rejection, claims, raw token types, gzip, redirects and truncated
bodies. Temporary signing material is synthetic and removed with the fixture;
no production credentials or developer `.env` are loaded. Dependency versions and
results are retained under `quality-reports/go-migration/apple-tokens/`.

Unit tests separately prove a two-slot bound, canceled waiters/running requests,
slot reuse, response closure and resource budgets. A service test uses real RSA
and HTTPS to show a changed client ID invalidates the old token and disabling the
provider suppresses HTTP. That test controls configuration input; the separate
33-case PostgreSQL configuration adapter proves persisted resolution. These
scoped tests do not prove a complete public login or a live Apple account login.

Remaining work includes the remaining Google input/protocol cases, complete
persisted-account/HTTP/browser coverage, provider changes
during admitted requests, transport policy at the composition root, broader
nonstandard JSON/base64 edge cases and live-provider/release evidence. Shared document/header/integer checks now also serve REST JWT verification. Its
separate 76-token credential adapter and 56 document scenarios over both HTTP
servers establish the tested REST behavior; the 89 Apple cases still pass after
extraction. Nonstandard JSON numbers and lone surrogate escapes remain open.

The public HTTP adapter now covers both actual root clients through a private
CONNECT proxy with a temporary CA and signed provider tokens. It asserts 45 provider
requests per backend across serial cases and sixteen concurrent requests spanning
two identities/providers. It checks existing aliases, invalid signatures, expiry,
audience/email mismatch, disabled/deleted users, OTP, Google access fallback and
wrong-client rejection, then validates session signatures and durable effects.
This fixture supplements the 89 Apple and 93 Google component protocol cases.

`RequestsTransport` owns a clone of the root's base transport. Explicit
`REQUESTS_CA_BUNDLE` (or nonempty `CURL_CA_BUNDLE` fallback) PEM files replace its
root pool; loading failures remain deferred HTTPS failures and never disable TLS.
The Google JWK transport remains separate. The public fixture first exposed 18
failures before this configuration reached the root. TLS unit tests independently
check hostname rejection, unrelated/malformed/empty/missing trust and no fallback
to the base transport's valid trust. Configured files load at construction;
OpenSSL hashed directories, live trust rotation, certifi/system-default equivalence,
full environment proxy policy and cross-platform JWK trust remain open.
