# Login rate admission

`LoginRateLimit(enabled, gate)` wraps the login handler explicitly. It checks
only POST requests when enabled. The first key is the stripped first
`X-Forwarded-For` item, falling back to the transport peer address. No IP
normalization or validation is added. The IP counter runs before this wrapper
reads or parses the body; a reached threshold stops the account gate and handler.

The account key uses raw UTF-8 JSON regardless of Content-Type or charset.
Syntax/UTF-8 failures use `invalid-request`; an object with a missing, empty or
whitespace username uses `missing-username`. Other username values use Python
str/repr semantics, then strip and casefold. Dictionary order and duplicate
last-value/first-position behavior are retained. A non-object JSON body raises a
server error after consuming the IP attempt, without consuming an account attempt.

This parsing precedes DRF's stricter body parser. A dedicated DTO mode accepts
NaN/Infinity/-Infinity and enforces Python's integer digit limit on all fields.
The existing view/reference parser stays strict. The middleware restores original
body bytes for the handler and never interprets form or multipart fields itself.
Five account attempts and 20 IP attempts are allowed; denied account attempts
still consume IP attempts. Threshold responses are exactly HTTP 429,
`text/html; charset=utf-8`, and `Too many requests, blocked.` without a cookie,
Retry-After, authentication challenge or DRF Allow header.

`TESTS/adapters/django/login_rate.py` runs the shared portable contract module
`TESTS.contracts.test_login_rate` against actual Django WSGI and Go HTTP, using
private Redis. The adapter executes unchanged `_login_ip_key`,
`_login_account_key`, rate decorators and `handler403` AST from the application.
The terminal view is explicitly controlled and returns a SHA-256 of received
bytes; it does not stand in for verified credentials, audit/session middleware or
successful public login. A fixture-only reset endpoint clears only the adapter's
private Redis. It is absent from the production composition root.

The WSGI fixture buffers its declared small request frames before dispatch to
avoid wsgiref socket resets on an early denial with unread bytes. Request/key/view
body access still uses Django's real HttpRequest. Fifteen contracts cover shared
casefold keys across IPs, forwarded/peer fallback, threshold/error order, malformed
and mixed-media bodies, missing usernames, containers/order/duplicates, nonfinite
numbers/overflow, integer limits, disabled/method bypass and byte preservation.
Unit tests additionally prove the IP rejection does not read the wrapper's body
or call the next handler; parser tests/fuzzing preserve quoted text and strict
view-parser behavior. The Redis counter/race/resource checks remain separate.

The public-login root now composes this middleware, with five additional actual
application ordering contracts. Remaining work includes
composition with real login/authentication/audit/session/global middleware,
malformed Unicode escapes/lone surrogates, exact recursion boundaries, unusual
transport/length/body-limit responses, aggregate memory/admission budgets and
Django's exceptional Redis counter representations. In particular, global
ExceptionHandlerMiddleware can read a non-multipart body before rate admission;
these tests do not claim its size-error ordering. DEBUG/enable flags now load
through existing configuration but public composition remains to be wired.
