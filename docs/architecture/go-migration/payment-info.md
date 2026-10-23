# Public payment information — characterized, Go implementation missing

`GET /payment/<str:uid>/info` supplies the existing public Stripe checkout page.
The independent Django adapter passes **109 authored HTTP cases** with actual
signed JWTs, middleware, private PostgreSQL and Redis. Expected responses come
from explicit fixture inputs, not captured responses. Recursive JSON comparison
distinguishes booleans from numbers; full-body HTML checks avoid the shared
client's diagnostic truncation. Five pure regression checks cover these assertions. Complete managed-table
snapshots remain unchanged after every request and after fixture cleanup.
The Go composition root still returns 404 for the first shared portable case;
this is preserved failing parity evidence, not an implemented endpoint.

## Established behavior and unresolved concerns

Cases cover OTHER, SUBSCRIPTION and COURSE payments; direct course, installment
and carnet precedence; null and malformed relationships; exact money/date JSON;
method and content negotiation; credentials, collaborator permissions,
impersonation and group headers. Different course names distinguish the selected
relationship, and two fallback cases preserve legitimate carnet behavior.

Anonymous checkout is an existing frontend flow. Ordinary public and authenticated
UUID lookup can expose nested user, associate, association and course details.
Authentication still executes before AllowAny: disabled/deleted accounts fail,
restricted collaborators need `bookeeping.payments.read` (FULL collaborators pass), and administrators need an
impersonation context. Association-to-athlete impersonation is rejected by the
existing view allowlist before family filtering. These measured behaviors are
characterization, **not approval of the existing disclosure policy**.

Valid group headers narrow the query even for anonymous callers. Malformed group
UUIDs fail open in the current manager. Direct category and metadata-category
reads also lack the association scope applied to some payment relationships.
Ordinary course/carnet relationships can expose another association. These remain
explicit review gaps; do not infer that public checkout requires every nested
field, or introduce owner-only authentication without preserving supported flows.

Metadata category reads show an actual N+1: 0/1/8/32 metadata entries perform
1/2/9/33 category SELECTs and 6/7/14/38 total HTTP SQL statements. The future Go
implementation must batch these reads and assert bounded queries; this measured
Django cost is not the desired implementation contract. These cardinality cases
repeat one foreign category ID; distinct and mixed metadata IDs and larger input
sizes remain required Go batching tests. Generic HTML500 assertions establish the
error class and content type, not the internal exception path. All three scoped
foreign course/installment/carnet denials explicitly exclude their sensitive
fixture values from the complete response body. Managed-state snapshots prove
durable read-only behavior; the SQL counter is not a database-enforced read-only
transaction or coverage of every possible SQL syntax.

## Intentional nested-scope correction

The original baseline allowed an association-scoped installment to reference a
course owned by another association. Direct course and carnet paths already
apply the nested association restriction. Both Django's scope manager and Go's
scoped-resource repository now require the installment's course association.
This implements requirement 4's nested-resource boundary.

The original 107-case oracle retained the foreign-course HTTP200. Replaying that
expectation against the correction fails exactly that case; the final 109-case
oracle retains the view's existing no-relationship HTML500 while excluding the
foreign course payload. Ordinary public lookup and legitimate carnet fallback
retain their established behavior. Separate TDD runs fail first on Django scope,
then on Go scope, before the shared scope/identity/request-guard suite passes.
All 190 selected existing Django payment/course/carnet/impersonation regressions
and full Go race/vet checks pass.

`impersonation_scope.py` is an explicit dependency of five manual recipes:
attendance-automatic, attendance-carnet-variants, member-tags,
members-account-access and membership-links-cards. Their source bindings require
review; they have not been silently refreshed or published.

## Reproduction and evidence limits

```sh
LC_ALL=C LANG=C python TESTS/adapters/django/accounts.py --only payment-info
# Optional absence probe: succeeds only when the shared Go contract fails with 404.
LC_ALL=C LANG=C TESTS_PAYMENT_INFO_GO=1 \
  python TESTS/adapters/django/accounts.py --only payment-info
```

The portable contract is `TESTS/contracts/test_payment_info.py`; fixture preparation
is confined to `TESTS/adapters/django/payment_info.py`. CI requires the Django
baseline without the Go absence flag. Generated token-bearing fixtures are private
and excluded from uploaded evidence. Local receipts live under
`quality-reports/go-migration/payment-info/`; the parent run is recorded in
`payment-info-parent-final.log`. Assertion red/green evidence is in
`category-acceptance/payment-info-assertion-review.json`. Scope TDD receipts are in
`quality-reports/go-migration/installment-scope-{red,go-red,green}.log` and
`payment-category-parallel/baseline-installment-scope-results.json`.

These use current Django models, not historical migrations. Native HTTP evidence
does not prove versioned Caddy, packaged-image, browser, release, switching or
rollback acceptance for payment-info. The ledger entry remains `needs_review`
and complete payment-info parity remains false.
