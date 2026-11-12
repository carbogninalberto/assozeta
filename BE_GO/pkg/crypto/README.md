# Password compatibility

`PasswordVerifier` verifies existing Django PBKDF2-SHA256, PBKDF2-SHA1,
bcrypt-SHA256, scrypt and Argon2 credentials, plus raw bcrypt imported by
Assozeta's existing login migration helper. It recognizes unusable `!` hashes.
It does not enable the test adapter's synthetic MD5 hasher in production.

Argon2 i/id/d v16 (including the version-omitted encoding) and v19 use the same
format as Django. Ordinary v19 i/id use `golang.org/x/crypto/argon2`; historical
variants use the narrowly adapted Go upstream kernel in
[`internal/argon2legacy`](internal/argon2legacy/README.md), with its license and
provenance retained. Raw bcrypt retains Python bcrypt 4.3's 72-byte truncation.

New hashes use Django 5.2.1's PBKDF2-SHA256, one million iterations and a random
salt with at least 128 bits of entropy. Verification itself never rewrites data.
`CheckLogin` classifies the existing Django upgrade versus raw bcrypt fallback
and retains unusable/unknown-hash and low-cost-hash runtime hardening. The password
selection service coordinates rehash writes in PostgreSQL; later login admission
and side effects remain required. See `internal/service/password_login.md` from
the module root. This package is not a login endpoint.

Verification has explicit resource limits. The default permits up to ten million
PBKDF2 iterations, bcrypt cost 20, 512 MiB scrypt/Argon2 memory, twenty Argon2 passes
and 255 lanes. A caller can supply another `PasswordLimits` policy. Exceeding it
returns `ErrPasswordCost`, separately from a wrong password. Before activation,
inspect supported fixture/imported hash costs and validate memory/concurrency
budgets; these defaults are not proof of every installation's stored parameters.
Cancellation is checked before and after a hash operation; upstream KDFs are not
interruptible mid-operation. Login admission/rate limiting remains required.

Run `python TESTS/adapters/django/credentials.py` with its documented dependencies
for both directions of real Django interoperability, in addition to Go unit/race
and bounded fuzz tests. Keys, tokens and generated hashes remain temporary; the
persistent report contains only counts, dependency versions and results.

`TOTPCode` and `VerifyTOTP` preserve the login view's PyOTP defaults and NFKC
comparison. Known-code tests cover exact 30-second boundaries; the account adapter
compares the second-factor service with the actual Django login view, including
invalid inputs and persisted-secret cases. See `internal/service/second_factor.md`
from the module root for the required workflow ordering and remaining work.
