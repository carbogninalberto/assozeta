# Current Django security headers

`DjangoSecurityHeaders` supplies the current Django SecurityMiddleware defaults:
`X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` and
`Cross-Origin-Opener-Policy: same-origin`. An existing response field wins,
including an explicitly empty value. Headers are installed before the first
write/flush and on Echo's handling of returned errors.

The root composes this middleware after CORS. Preflight short-circuits it, as in
Django. A malformed CORS allowlist error replaces the inner response and its
security headers. HSTS and forced HTTPS redirects remain disabled by current
settings; forwarded HTTPS alone does not enable either. XFrameOptionsMiddleware
is commented out in the production middleware list, so this component does not
add X-Frame-Options. No environment setting or new policy is introduced.

`TESTS/adapters/django/security_headers.py` checks that production has not added
overrides for these defaults, then runs six portable methods against actual
Django ASGI/Uvicorn and Go HTTP servers with controlled terminals. Coverage
includes all admitted HTTP methods, successful/error statuses, explicit/empty
values, forwarded protocol, streaming, CORS preflight and 32 concurrent requests.
Five additional methods in the private refresh rehearsal run on the actual
applications: liveness, missing Bearer, CSRF/method denials, missing route and
preflight. Managed-model and session readback stay unchanged.

The contracts first passed on Django and failed on Go's missing component and
root composition. Evidence is under `quality-reports/go-migration/security-headers/`.
This is a transport-header increment, not complete trusted-host, redirect,
locale/compression, restore/maintenance, WebSocket or versioned-proxy parity.
Restore maintenance responses require their own middleware before security,
matching Django's current order; the Go restore lifecycle remains unimplemented.
