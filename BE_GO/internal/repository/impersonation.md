# Existing impersonation cache boundary

The supported Django configuration uses `django-redis` with Redis database 1,
default key prefix/version, and its default pickle serializer. This was inspected
in `BE/core/settings.py` and the installed `django-redis 5.4.0` implementation,
then exercised with Django 5.2.1, Redis Python client 5.2.1 and Python 3.12.
It is an Assozeta compatibility adapter, not a Kuayle queue/cache convention.

Only `:1:impersonation:v1:<canonical UUID>` records are handled. Their keys are
`actor_id`, `target_id` and optional `association_id`; values are canonical UUID
strings, with null/missing association representing an administrator session.
The Go reader recognizes the small dictionary/string/None subset of pickle
protocols 4/5, one optional frame and memoized strings. It rejects oversized data,
unknown fields, invalid IDs, truncation, trailing bytes and all object/call/global
opcodes. It never executes or instantiates Python objects. Unsupported cache
formats fail closed; there is no fallback to another interpretation.

Writes produce a protocol-4 record Django can read and set a one-hour TTL. Reads
use GET without extending expiry or caching authorization in the Go process.
Delete is idempotent and immediately visible across runtimes. The constructor
requires a client configured for database 1; normal environment/TLS credentials
remain owned by composition, with no second credentials file.

`TESTS/adapters/django/impersonation_cache.py` starts private Redis and uses the
actual Django cache implementation. Tests cover both issuance directions,
protocols 4/5, memo references, optional association, TTL/no renewal, expiry,
revocation and concurrent reads. Unit tests also retain a fixed synthetic Python
record and fuzz the bounded parser.

This is not a session authorization decision. Services must recheck the actor,
target eligibility, association binding and optional target header on every
request/event, preserve actor attribution, and perform begin/end audit writes.
Those workflows and their transaction/failure behavior are still unfinished.
No generic Django session, Celery or Channels wire compatibility is inferred.
