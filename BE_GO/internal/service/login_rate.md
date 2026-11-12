# Shared login rate counters

`LoginRate` enforces the existing `login-ip` 20/minute and `login-account`
5/minute counter thresholds for an already resolved request key. The service
receives a clock and a `domain.LoginRateCounter`; the repository owns Redis.
The public-login root now composes both through request middleware.

The protocol follows the installed django-ratelimit implementation and the
unchanged `core.settings.CACHES` declaration:

- Redis cache database 1 and Django's `:1:` cache-version prefix.
- `rl:` plus SHA-256 of the concatenated group, normalized rate, value, window
  end and `POST`, with no separators beyond those in the rate itself.
- A 60-second window offset by CRC32(value) modulo 60. The end second belongs
  to the ending window; the next second advances it.
- `SET NX` of integer 1 with a 65-second TTL, followed by an atomic
  exists-and-increment script if the key already exists. Incrementing does not
  extend its TTL, and rejected attempts continue incrementing.
- If the key expires between those commands, deny without recreating it.
  Redis failures propagate separately from a reached threshold.

The constructor requires database 1, disabled automatic command retries and
context deadlines. The composition root already constructs its Redis client
with these settings. There is no local counter or cached admission decision.
One counter check uses one Redis command for creation or two for an existing
key. Transport must call the IP gate first, then the account gate, only for
enabled POST requests.

Run `python TESTS/adapters/django/login_rate.py` using the Django adapter
environment. It starts a private Redis, executes actual django-ratelimit against
14 declared keys/thresholds/windows, then checks Go on Django-written counters.
Django continues each Go-written counter. Existing TTLs are deliberately shortened
to distinguish preservation from renewal. Sixty-four concurrent Go requests use
at most four connections and 127 write commands: exactly five are admitted and
59 denied, with a final Django-readable count of 64.

Actual-key deletion between SET NX and increment proves the expiration race in
both runtimes. Redis `CLIENT PAUSE` verifies a bounded in-flight deadline, no
automatic retries and subsequent pool reuse. A command may execute after the
client times out; cancellation does not promise rollback of Redis effects.
A pre-cancelled operation writes nothing. A closed client remains an error.

The accompanying request middleware now has 15 portable HTTP contracts per
backend, using the actual Django key functions/decorators around a controlled
terminal view. It checks first-forwarded-IP selection, raw UTF-8 JSON keys,
Python string/casefold representations, IP-before-account denial, exact HTTP 429,
method/disable bypass and unchanged body bytes. The environment adapter now has
55 cases including required DEBUG and the derived RATELIMIT_ENABLE default.
See `../middleware/login_rate.md` for the boundary and remaining parser/body gaps.
Five public-root ordering contracts now exercise actual session/rate/REST
composition; the application's remaining global middleware stays open.
Django's fallback for corrupted/pickled or larger-than-int64 counters also remains
open; normal counters created by these login gates use Redis integer values.
These counter fixtures do not establish complete login parity or backend activation. Evidence is recorded under
`quality-reports/go-migration/login-rate/` without credential values.
