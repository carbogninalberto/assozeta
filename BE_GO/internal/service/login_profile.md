# Base login profile contract — implementing

`LoginProfiles.Read` maps the existing `UserAuthSerializer` fields for a trusted,
already admitted canonical account ID. It is an internal response-construction
component, not a public profile endpoint or a substitute for admission. The
composition root does not register a login handler yet.

The explicit DTO preserves all 37 base fields, including null versus empty text,
JSON scalars/containers and integer precision. Payment-category references remain
UUIDs without dereferencing or applying a new category visibility policy. Google
sync is enabled for every persisted JSON value except SQL NULL/JSON null; no
credential value is selected or passed to the service. Avatars remain untouched
at this base-serializer stage.

Only active, nondeleted association accounts or superusers can impersonate. This
output flag is descriptive; actual impersonation authorization remains in the
identity service. Inactive accounts can be serialized by this component, as by
Django's serializer, but password admission separately rejects them. Soft-deleted
accounts are excluded by the repository's normal account-manager predicate.

Association welcome decisions use the account's own nondeleted association:

- Superusers, athletes and collaborators always return false with null onboarding.
- The first matching self-hosted instance row by primary key controls fresh/import
  setup. Fresh requires welcome until completion; import suppresses it. Foreign
  primary associations and non-self-hosted rows do not apply.
- Legacy or unknown provenance checks logo, denomination, tax code and fiscal
  start day/month, then missing lead fields only when joined strictly less than
  30 elapsed days ago. Empty strings and null differ from whitespace.
- Exactly one onboarding row serializes its nine fields. Missing or duplicate
  rows return null. Missing/deleted owner associations fail profile construction.

The repository uses a read-only repeatable-read transaction: one parameterized
query joins account/association and the first matching instance configuration;
ordinary association profiles add one onboarding query capped at two rows.
Transaction commands are not counted as business SELECTs. There is no duplicate
onboarding count scan or per-row request. The schema remains Django-owned.

`TESTS/adapters/django/login_profile.py` writes explicit expected JSON, compares it
with the actual serializer through DRF's JSON renderer, then runs Go against the
same disposable PostgreSQL. Forty-six cases cover preference fields, category
references, Google JSON representations, setup precedence, the exact date
boundary, and onboarding counts 0/1/2/128/512. Real query counters assert one/two
SELECTs; 32 concurrent association reads with 512 duplicate onboarding rows use
64 SELECTs and release a four-connection pool. A persisted logo transition is
visible on the next read. All managed tables match after the declared transition
is restored. Unit tests reject partial/foreign data and cancellation and cover
UTC elapsed-time behavior across a local daylight-saving boundary.

This is backend-specific component evidence, not portable login HTTP parity.
Django catches lookup/serialization exceptions in `get_onboarding`. The Go
repository classifies this optional-read failure, clears partially scanned rows
and rolls back the read-only transaction. The service preserves Django’s fallback
policy and returns the otherwise complete profile with null onboarding. Context cancellation
still aborts the request. A real PostgreSQL role with denied onboarding SELECT
proves the same rendered profile in Django and Go and verifies connection reuse
after the aborted transaction. Required base-profile reads still propagate errors.
The consistent read transaction prevents mixing concurrent snapshots.

Association augmentation, Pillow-compatible image processing, instructor lookup,
registration features, membership-card configuration creation/audit, top-level
role/tables settings, token issuance composition, final last-login/deletion
writes, public transport, social providers and rate limiting remain required.
