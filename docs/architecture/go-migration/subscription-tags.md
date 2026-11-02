# Subscription tags — implementing with HTTP parity

`GET subscription/tags/list`, `POST subscription/tags/add`,
`PATCH subscription/tags/<tag_id>/update`, `DELETE subscription/tags/<tag_id>/delete`
and `PATCH subscription/tags/<tag_id>/assign|unassign/<subscription_id>`
(`subscriptions_views.py`, `TagService` in `services/subscription_service.py`) are
implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/subscription_tags.go`).
Registry: `association.members.read` for the list, `association.members.update` for the writes.

## Behaviour

- Every view resolves `request.user.sport_association`: athletes get
  `403 User is not a sport association`, an owner without an association a 500.
  add and update read `request.data.get('tag_name')` first, so a non-dict body
  is a 500 even for athletes; the athlete denial comes before the name check.
- list returns the association's tags ordered by `tag_name` as
  `{"tags": [{tag_id, tag_name}]}`.
- `TagService` raises DRF's `ValidationError` and the views render `str(e)`,
  the repr of its detail list, e.g.
  `"[ErrorDetail(string='tag not found', code='invalid')]"`. add answers
  `400 {"error": ...}`; update `400 {"msg": ...}` for the name and
  `404 {"msg": ...}` for the tag; delete, assign and unassign `404 {"msg": ...}`.
- The name is required unless it is a value other than `None` and, when a
  string, non-empty after Python's `str.strip()` (a form `tag_name=` is
  empty). The CharField stores `str()` of any other JSON value (`False`,
  `0`) and keeps surrounding spaces; the response echoes the value as sent:
  add `{"tag": {tag_id, tag_name}}`, update `{tag_id, tag_name}`.
- Tags are the association's; a malformed tag or subscription id is the ORM's
  500. assign/unassign look the tag up first, then the subscription through the
  group-aware `SubscriptionManager` (`X-Group-Id`; deleted subscriptions are
  included). They answer `{"msg": "tag assigned"}` / `{"msg": "tag unassigned"}`.
- assign/unassign change the M2M link only when needed and then call
  `Subscription.save()`. save fills missing start/end dates from the owner's
  season settings (auditlog records `start_date`/`end_date` against `None`,
  indexed directly) and always computes the season range: a range error (for
  example a creation year of 1) is a 500 after the link was stored, because
  requests are not atomic. Go commits the link before answering that 500.
  An unchanged link calls no save, so missing dates stay empty.
- auditlog records the tag create, a changed rename (an unchanged name records
  nothing) and the delete; the delete removes the subscription links first and
  is indexed through `changes_json.sport_association`.

Nothing in these routes needs S3, Celery or PDF generation: nothing is refused.

## Evidence

`TESTS/fixtures/subscription_tags_http.json` (generator
`generate_subscription_tags_http.py`) authors 79 cases checked against the
complete model state, the audit entries and their index rows.

- RED (`native-r2`): Django 79/79, Go 404 on the first case. (`native-r1`
  corrected one authored expectation: an admin session acting as the owner
  reaches the view, so a foreign tag is the view's 404.)
- GREEN (`versioned-r1`): 79/79 on native Django, native Go, Caddy `/api/v1`
  and `/api/v2`.
- Mutation (`mutation-r1`): returning the season range error without
  committing the stored link fails Go on
  `subscription-tags-assign-season-error-keeps-link` (`state.links`): CAUGHT.
