# Testimonials — add implementing with HTTP parity; update (webhook) characterized

`POST testimonials/add` (`testimonials_create`) and `POST testimonials/update`
(`testimonials_update`, the Senja webhook) are in `BE/application/views/profile_views.py`.
Add is implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/testimonials.go`);
update is not. Django remains the production execution owner.

## Add behavior

- **Permissions:** DRF `IsAuthenticated` only. The registry maps the path to
  `other.settings.update`: FULL collaborators pass, other collaborators without
  that grant get `403 {"detail": "Missing permission: other.settings.update"}`.
  Identity resolution denies administrators outside impersonation and sets
  `request.user` to the tenant owner for collaborators (the reference run
  disproved my first claim that collaborators fail on the reviewed lookup).
- **Role check:** `if not request.user.is_sport_association:` tests the bound
  method, which is always truthy, so it never rejects.
- **Body checks, in order:** Python membership `'text' in data` and
  `'score' in data` (dict keys, list members, or substrings of a string body;
  `null` and number bodies raise `TypeError`), then `data['score']` compared
  with `1` and `5`. Missing keys return `400 {"msg": "missing params."}`; a
  score below 1 or above 5 (including `false` and huge integers) returns
  `400 {"msg": "invalid score."}`. Non-numeric scores, list/string bodies that
  pass membership, and form bodies (string scores) raise and return HTTP 500.
  DRF's JSON parser rejects `NaN`/`Infinity` literals before the view.
- **Testimonial:** `Testimonial.objects.create` on `request.user.sport_association`
  (athletes: `403 User is not a sport association`; missing or soft-deleted
  association: 500). `TextField` stores `str(text)` (Python repr for
  containers, `'True'`, `'1e+16'`); a `null` text violates NOT NULL (500, no row).
  `FloatField` stores `float(score)`. Testimonial is not audited. Extra body
  fields are ignored.
- **Reviewed flag:** `scoped_queryset(request, SportAssociation.objects).filter(user=request.user).first()`
  (bounded by an impersonation association when present), then
  `reviewed = True` and a full-row `save()`. SportAssociation is audited, so a
  false-to-true change writes one update `LogEntry` (`{"reviewed": ["False", "True"]}`,
  `SportAssociation object (<id>)`, the initiating account as actor and the
  impersonation `additional_data`) and an `AuditLogIndex` row with path `self`.
  An already reviewed association logs nothing. If the lookup returns `None`
  the view raises after the testimonial was saved (no request transaction).
- **Response:** `200 {"msg": "Thank you for your review."}`.

## Go implementation

Go parses the body with the shared DRF-compatible reader, ports the membership,
comparison and `str()` rules (`dto.PythonStr`, unit-tested against CPython
output), resolves the association like `User.sport_association`, and runs the
insert, reviewed update and audit in one owned transaction. When the reviewed
lookup finds nothing, Go commits the testimonial before returning 500, matching
Django's autocommit outcome. The update is column-scoped (`reviewed` only)
where Django saves the full row; see the open staff-board decision.

## Update (webhook), not implemented

Default authentication, `AllowAny`. No `x-senja-signature` header: 401 with an
empty body. The signature is HMAC-SHA256 with `SENJA_WEBHOOK_SECRET` over
`json.dumps(request.data, separators=(',', ':'))` (ASCII escaping, parsed key
order, Python float repr; a form body serializes the QueryDict's value lists);
`hmac.compare_digest` raises on non-ASCII header text (500). Missing `type`:
400. Type `testimonial_created`: reads
`data.new.endorser.custom_data['sport-association-id']`, validates it as a UUID,
looks up the active association (404 when absent) and saves `reviewed=True`
(audited as above, anonymous actor); any exception inside returns 400. Other
types return 200. A Go port needs the secret in its configuration and the
launcher allowlist, and the compact `json.dumps` port.

## Evidence (TDD)

`TESTS/fixtures/testimonials_http.json` (generator alongside) authors 38 cases.
The Django reference run corrected two expectations, each confirmed in source:
a list body naming both keys passes membership and then fails indexing (500),
and collaborators act on the tenant owner (200). RED: Django 38/38 and Go 404 on
the first case before any Go code. GREEN: `--family testimonials --versioned`
passes 38/38 on native Django, native Go, Caddy `/api/v1` and `/api/v2` with
full-state, audit, index, attribution, cache and cleanup checks. Changing the Go
index path to `direct` fails the run.

The reference runs also showed that the shared driver started Django before the
schema existed, so `ready()` registered no audit resolvers and every index fell
back to `changes_json`/`actor` paths. The driver now registers them after
migrating, like a production process started on a migrated database.
