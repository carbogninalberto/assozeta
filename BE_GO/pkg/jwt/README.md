# Assozeta token protocol

This package implements the configured Ed25519/SimpleJWT credential protocol,
using `golang-jwt/v5` from the pinned Kuayle library family. It preserves four-hour
access and thirty-day refresh lifetimes, `token_type`, UUID-hex `jti`, identity
claims, issuer and audience. Parsing retains JSON number precision.

Current `JWTTokenService.refresh_access_token` does **not rotate** the refresh
credential. SimpleJWT 5.3.1 copies its original `iat` and custom claims to the new
access token while assigning a new access JTI and expiry. Go preserves those
semantics instead of following the contradictory rotation comment.

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
