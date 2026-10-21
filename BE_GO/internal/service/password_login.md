# Password credential selection

`PasswordLogin.Select` implements the password credential stage of the existing
Django login view. Its result is **not an authenticated session**: username/audit
normalization, OTP, response construction, last-login/deletion changes and cookies
must still run. It is not registered as a public login route.

The lookup preserves the view's email-shaped identifier choice, Python whitespace
stripping, PostgreSQL case-insensitive equality, UUID ordering, duplicate legacy
identifiers and full Unicode casefold preference. It excludes deleted accounts,
checks every candidate and resolves ambiguity before checking active status.

`PasswordVerifier.CheckLogin` distinguishes preferred Django hashes, older Django
hashes and raw bcrypt imports. Correct older Django hashes are upgraded for every
matching candidate, even when selection is ambiguous or the account is inactive.
Raw bcrypt is upgraded only for the selected candidate, before active-status
rejection. Unknown/unusable hashes burn a preferred-hasher invocation; failed
low-cost preferred hashes retain Django's runtime hardening. New encodings use
Django-compatible PBKDF2-SHA256 with one million iterations and random salts.

The service owns one credential-stage transaction. The repository pages and locks
128 candidates at a time under repeatable read, then applies one parameterized
password update per nonempty page. Memory holds one page plus two possible selected
accounts. All matching hashes are checked; a late preferred match can win. Counts
saturate at two, because selection distinguishes zero, one and multiple matches.
Expected credential denials commit the documented rehash effects. Infrastructure,
encoding, cancellation and write failures roll back and return no selected account.
Serialization/deadlock conflicts retry the complete stage at most three times.
This atomic failure boundary is deliberate: Django's view can leave partial rehashes
on exceptions. Neither side has changed credentials on an unsuccessful verification.

A constructor-supplied admission bound limits concurrent credential stages before
a database connection or KDF is acquired. The per-hash cost limits still apply;
census and instance-wide budgets, cross-replica rate limiting and request admission
remain required before deployment. KDF cancellation remains cooperative around
upstream operations. Waiting callers can cancel without opening a transaction.

The dedicated adapter uses the production Django hasher list, overriding only the
ordinary test adapter's cheap MD5 fixture preference. It runs the actual Django
login view for explicit account-selection and password-write expectations, restores
its last-login/password effects, then runs the Go service on the same private
PostgreSQL schema. Django verifies the resulting Go hashes. Comparisons of every
managed model table reject writes beyond the declared password columns. Passwords
are excluded from the existing Django user auditlog registration, so this stage
must not insert audit entries. Credential fixtures are synthetic, temporary and
removed; reports retain counts only.

Real PostgreSQL checks cover 1, 16, 128, 129 and 257 candidates: reads are
1/1/2/2/3 queries and nonempty upgrade writes are 1/1/1/2/3 queries. The exact-page
case needs a final empty read to establish exhaustion. They also cover rollback
after a write, database constraint failure, 16 concurrent credential requests
(including rehash), two admission slots and a four-connection pool.
Query/admission tests substitute the KDF to isolate database behavior; compatibility
cases use real hash algorithms. This evidence does not certify full login HTTP,
malformed request-value coercion, social providers, OTP, username/audit changes,
response-created configuration, last-login/deletion behavior or release switching.
