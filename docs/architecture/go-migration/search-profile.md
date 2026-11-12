# Search profile — implementing with HTTP parity

`GET search/profile/<username>` (`search_profile` in
`BE/application/views/search_views.py`) is implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/search_profile.go`, with
`integration_secrets.go` and `stripe_availability.go` for online payments).
Django remains the production execution owner.

## Behavior

- **Identity:** public (`AllowAny`). An authenticated caller still passes
  identity resolution: administrators need impersonation, and an
  association-scoped session may only search its association's username
  (`403 Associazione non disponibile.`).
- **User:** `User.objects` (active users) by `username__iexact`, first by key;
  none is `404 {"detail": "user not found."}`. `UserSerializerSearch`:
  registration features from the instance flags, account-creation flags and
  `online_payments` (association users: the owner's flag and
  `stripe_direct_credentials_configured()`, i.e. the instance override with its
  Fernet-encrypted secret, else the environment).
- **Athletes:** `{"data": {"user": {..., "is_athlete": true}}}`, avatar unchanged.
- **Others:** `SportAssociation.objects.get(user=...)` (missing is a 500, also
  for collaborators); avatar through `compress_base64`; `sport_association` is
  `SportAssociationSearchProfileSerializer`. With any `module_info` value the
  block adds the module fields and the preregistration window (open from 120
  days before the next season start, from the user's start month/day and
  today's UTC date; an impossible date closes it). Otherwise the body adds the
  live ACTIVE courses (unpinned and pinned, newest first, `CourseSerializer`
  with `athletes: null`, tags, locations and `total_subscriptions` from the
  soft-delete manager), carnets, and course tags with `active: true`.

## Evidence (TDD)

`TESTS/fixtures/search_profile_http.json` (generator alongside) seeds its own
users, association, courses, tags, location, course subscriptions and carnet,
and authors 16 cases. The association block is checked by its authored keys
and values, the preregistration window is recomputed by the contract, and the
runner requires the block to be identical across backends. The Django
reference corrected two expectations, confirmed in source: `compress_base64`
re-encodes any image not already at its 200×200 bound (the avatar is now
200×200), and the family registration flag defaults on. RED: Django 16/16 and
Go 404 before any Go code. GREEN: native Django, native Go, Caddy `/api/v1` and
`/api/v2` pass 16/16; counting deleted course subscriptions fails the public
case. Fernet decryption and the Stripe predicate are unit-tested against tokens
produced by Python's cryptography.

## Not yet characterized

`X-Group-Id` filtering of courses, and Stripe configured on the instance (the
harness has none; the predicate is unit-tested).
