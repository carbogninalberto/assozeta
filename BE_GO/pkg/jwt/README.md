# Assozeta token protocol

This package implements the configured Ed25519/SimpleJWT credential protocol,
using `golang-jwt/v5` from the pinned Kuayle library family. It preserves four-hour
access and thirty-day refresh lifetimes, `token_type`, UUID-hex `jti`, identity
claims, issuer and audience. Parsing retains integer precision and applies Python integer conversion to time claims.

Current `JWTTokenService.refresh_access_token` does **not rotate** the refresh
claims, expiry or JTI. It re-encodes and signs the refresh credential with the
default header; valid input padding, whitespace and custom headers need not survive. SimpleJWT 5.5.1 copies custom claims to the new access token while assigning a
new access `iat`, JTI and expiry. The refresh token retains its original `iat`. Go preserves those
semantics instead of following the contradictory rotation comment. Refresh
serialization preserves dictionary order, last duplicate values, ASCII escaping
and finite binary64 float formatting.

Token validation rejects bad signatures, other algorithms, expired/not-yet-valid
credentials, wrong types and required-claim errors. REST's configured SimpleJWT
does not enforce issuer/audience; WebSocket authentication must add its explicit
`https://bakney.com` / `bakney` checks before accessing account state.

A successfully verified token is **not an authorized user**. Disabled/deleted
accounts, actor/effective-user separation, collaborator ownership and grants,
family/instructor scope and impersonation revocation must be checked by services
against persisted state. The refresh HTTP route now composes these checks; login
and WebSocket admission remain incomplete. See `internal/handler/refresh.md` and
the migration ledger for HTTP evidence and remaining gates.

`IssueUnbound` preserves SimpleJWT's `RefreshToken(None)` constructor behavior:
tokens have no identity/custom claims. The refresh HTTP contract explicitly proves
the resulting access token cannot authenticate an account.

`TESTS/adapters/django/credentials.py` uses the real Assozeta token service and
SimpleJWT from repository dependencies to test both issuance directions and
refresh exchange. Synthetic keys are generated afresh, stored temporarily with
restricted permissions and deleted; report artifacts contain no token material.

The credential adapter verifies 76 Django-to-Go token cases, including 56 signed
access/refresh document cases. The same 56 declarations also exercise Bearer
admission and refresh over both actual HTTP servers. They cover critical headers,
key-ID types, padding, trailing/invalid UTF-8 JSON, duplicate claims, Python integer
bounds/conversion and exact normalized refresh bytes. Invalid Bearer credentials
precede body refresh; rejected credentials never issue a session cookie.

`document.go` shares document/header/integer checks with Apple verification. The
89-case Apple RSA/HTTPS oracle is rerun when these shared checks change. This is
scoped parser evidence: nonstandard JSON NaN/Infinity, overflowing custom floats,
lone surrogate escapes, broader base64 variants and deployment-wide parsing budgets
remain open. The current production baseline uses PyJWT 2.15.1 and SimpleJWT 5.5.1; they do not prove
behavior for every dependency version or close the full authentication ledger.

The current refresh HTTP oracle passes 37 portable contracts on each backend with
Python 3.13.16 / Django 5.2.18. Expired Bearer tokens report `Token is expired`;
malformed credentials report `Token is invalid`. Signature and future `iat`/`nbf`
failures take precedence over expiry. A refresh issued ten minutes earlier proves
that the new access token receives the current issue time while refresh claims
remain intact. Evidence: `quality-reports/go-migration/security-baseline/refresh-red.log`
and `refresh-current.log`. This focused run does not prove all authentication or
full accounts parity.
