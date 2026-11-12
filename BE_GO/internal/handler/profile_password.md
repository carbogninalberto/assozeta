# Profile password change — scoped implementation, migration incomplete

`PATCH /profile/update/password` takes `password_data.current_password` and
`password_data.new_password`. The handler uses the existing Bearer, identity,
collaborator-permission and scoped-request admission. The service changes the
effective person's credentials; collaborator-owner mapping never changes the
owner's password. Direct administrators and association-bound impersonation are
rejected by their existing gates. Unrestricted administrator impersonation keeps
the effective person's identity.

The new-password rule is Django's ASCII uppercase/digit/special-character rule
with at least ten Unicode code points. The permitted special characters are
`!@#$&.-_*`; `+` does not qualify. One trailing LF is accepted by Python's `$`
anchor and remains part of the stored password. Nested missing fields or invalid
new-password types retain the reference server-error boundary. Wrong old
credentials return HTTP 200 with `EC000`; invalid new format returns HTTP 400.
Django model password verification deliberately has no login-view raw-bcrypt
fallback. Supported model hashes, null/non-text PBKDF2 inputs, malformed hashes
and unusable passwords retain separate outcomes. A correct obsolete hash is
upgraded even when the proposed new password has invalid format.

A parameterized row lock precedes verification. The transaction updates only
`bakney_user.password`. Passwords are excluded from Django audit registration;
there is no password-change notification, session rewrite or token revocation in
this reference workflow. The Go service bounds concurrent password-change work
to two operations and uses the existing verifier cost limits. The login workflow
retains its own admission pool. No new environment settings or schema are added.

The concurrency rehearsal exposed Django's previous full-row save overwriting a
profile changed while the password request waited. Django now uses the same
row-lock/credential-only invariant. A concurrent reset invalidates the submitted
old password; two duplicate changes accept it only once. This is an intentional
repair of a demonstrated race, not a preserved lost-update behavior.

Changes commit before response serialization. A later missing-association or
invalid-role response failure leaves the changed credential persisted, matching
Django. The response contains the ordinary profile and the base association only
for association owners. It omits login tokens and profile-info review/feature
augmentation. Avatar/logo processing uses the shared image pool.

Run `TESTS/adapters/django/accounts.py --only profile-password` with the production
Python dependencies and disposable PostgreSQL/Redis available. Portable HTTP and
login-flow assertions live in `TESTS/contracts/test_profile_password.py`; only
the adapter prepares ORM fixtures and checks durable state. The tagged
`profilepasswordintegration` suite exercises actual PostgreSQL transactions,
query counts, concurrent verification, cancellation, failed writes and recovery.
Generated evidence is under `quality-reports/go-migration/profile-password/`.

Current-model fixtures do not prove historical migrations, full frontend/browser
workflows, every imported credential cost, backend switching, release delivery
or whole-application parity. The compatibility ledger remains `implementing`.

A further real HTTP interleaving exposed Django `profile_update` overwriting a
Go-written password. Its personal-field save now writes only supplied fields.
Two additional races use the actual Django profile writer with both password
backends, asserting preservation of credentials/profile and one profile-field
audit. The complete Go profile-update workflow and other legacy writers remain
outside this focused endpoint proof.
