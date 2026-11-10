# Payment list, payment stats and subscription payments — implementing with HTTP parity

`GET payment/list`, `GET payment/stats` (`payment_list`, `payment_stats` in `BE/application/views/payment_views.py`) and
`GET subscription/<uid>/payments` (`subscription_payments` in `BE/application/views/subscriptions_views.py`) are implemented
in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/payment_reads.go`). Registry: `bookeeping.payments.read` for
the first two, `association.members.read` for the third. Django remains the execution owner. The PaymentOptimizedSerializer
rendering reuses the PaymentSerializer port of course-subscriptions / payment-state (`dto.InvoicePartyPaymentItem`), the
InstructorSerializer and SupplierSerializer ports and personas-reads' BasicAssociateSerializer, tutor queries, smart-search
word preparation (`personaSearch`) and KTDatatable page resolution (`personaPage`).

## Shared behaviour

- `@api_view(['GET'])`: Allow `GET, OPTIONS`; OPTIONS names `Payment List`, `Payment Stats`, `Subscription Payments` with an
  empty description (no docstrings). `payment_list` and `subscription_payments` are athlete views for association-scoped
  sessions (their query and path references are checked); `payment_stats` is not.
- `Payment.objects` is live and `X-Group-Id`-aware (a header the UUID field rejects is ignored); the nested serializer lookups
  use the same thread-local group (Subscription, installments). An association-scoped session adds its association
  (`scoped_queryset`).
- KTDatatablePagination: `pagination[perpage]` a strictly positive `int()`, else 10, no maximum; `pagination[page]` absent or
  empty is 1, `last` the last page, else `int()` within the pages, otherwise `404 Invalid page.`; `meta` is
  `{total, page, pages, perpage, rowIds}` and `data` a list.
- PaymentOptimizedSerializer (`'__all__'`): `associate` is AssociateOptimizedSerializer (`available_tutors`: the tutor
  relations through the base manager, each `{tutor: BasicAssociateSerializer, is_primary}`, unordered), `instructor`,
  `supplier`, `invoice` (InvoiceSerializer with `document_token` and the attached document ids), `attachments`,
  `custom_account_type`/`_name`, `payment_category_name`, `user` and the other relations as keys, `amount` as a two-place
  string. `subscription_id`: subject 1 the live group-aware subscription of the payment (two: 500), else the associate's
  first live unarchived subscription by creation date; subject 2 the live group-aware installment's course subscription,
  else the live course subscription, else the carnet holding (two of any: 500; a related subscription that is None: 500);
  subject 0 the associate's first unarchived subscription by creation date; subject 3 None. `course` (subject 2): the live course subscription's course, else the live
  group-aware installment's, else the payment's `course` JSON when truthy, else None.
- The general search: a `d/m/Y` term becomes `Y-m-d`; a `float()` (`,` as `.`) searches the amount (`amount::text` icontains
  `str(float)` or equality with the float at 9 significant digits); otherwise text search (below).
- Integer filters (`query[subject]`) go through `int()` (else 500); outside `0..32767` the lookup is EmptyResultSet (no rows).
  UUID filters (`associate_id`, categories, account) that the field rejects are 500s.

## payment/list

- `course_subscription_id` (truthy) through `is_valid_uuid` (400 list) before anything else.
- `mode=compressed`: `request.user.sportassociation` (without one the scoped live lookup fails, 500), the `query[archived]` filter (BooleanField `to_python`: `t`,
  `True`, `1`, `f`, `False`, `0`; anything else, including `true` and empty, is a 500), `values(...)` by `-creation_date` with
  Decimals as floats. Other parameters are ignored.
- Athletes: own branch (not ported, see below).
- Otherwise the same association and archived filter, then one `filter()` of `associate_id`, `query[payment_range]`,
  `query[subject]`, `query[type]`, `query[paid]` / `query[expense]` (`== 'true'`), `query[payment_categories]` (`[]`: no
  category) and `query[account]` (`[]`: no account), each only when truthy. The range is `Y/m/d al Y/m/d` or
  `d/m/Y al d/m/Y` (one-digit months and days, `%d`'s leading space); the end is 23:59:59; the payment date is compared,
  or the creation date for payments without one. A value that does not split into two dates, or whose first date parses
  with the first format and second does not (Python's TypeError), is logged and ignored; two unparseable dates fall back to
  the last year up to today 23:59:59. The range is skipped when the search term is a valid UUID.
- Ordered by `-payment_date, -creation_date` (NULL payment dates first). The text search is `smart_search` over Payment:
  each `\w+` word of the stripped, lowercased term must icontains one of Payment's concrete fields (`field::text`, UTC
  timestamps, `true`/`false`), the associate's and its tutors' fifteen listed fields, the supplier's (`name`, `email`,
  `address`, `city`, `tax_code`, `vat_number`) or the instructor's (`first_name`, `last_name`, `email`, `phone`,
  `tax_code`, `address`); numeric words also match the term's date variations on the date fields; several words also match
  the reversed and original sequence on the name/address/city/notes fields. The conditions hold per joined row (two words
  matching two different tutors do not match), the result is distinct and Payment gets no relevance ordering (its first ten
  fields are its own).
- `sort[field]` orders ascending unless `sort[sort] == 'desc'`: `?` random, one leading `-` stripped (so `-x` with `desc`
  is a FieldError), unknown names a FieldError (500) before pagination.
- Rows: PaymentOptimizedSerializer plus, for instructor payments, `instructor_document` / `instructor_document_token` from
  the payment's InstructorHours document.

## payment/stats

The scoped live association (athletes, owners without one and soft-deleted associations: 500). The window is this month to
today 00:00, or `query[payment_range]` (a value that does not split into two dates is a 500; unparseable dates, or a first
`Y/m/d` date with a second that is not, fall back to the last year up to tomorrow 00:00), the end always at 00:00 (payments
later on the end date are excluded). `subject`, `type`, `paid` and `expense` apply whenever present (an empty subject is a
500, an empty type matches nothing, an empty paid means unpaid), categories and account when truthy, then the same general
search. The aggregates run over the distinct payments: `Sum(...) or 0` renders NULL and a zero Decimal as `0`, other values
as floats; `net_balance` is income − expenses + to pay − to cash in − Stripe fees. The account balances group the paid
payments by account type (and creation date, from the ordering) and add the net changes to `{cash, bank, other}`; the
search joins repeat a payment once per matching tutor row there (an associate with two tutors doubles a matching payment's
balance change but not the aggregates). Without Stripe credentials `stripe_charges` is `{total: 0, stripe_fee: 0}`.

With `stripe_direct_credentials_configured()` (the instance integration, else the environment) the view reads
`stripe_fees_direct_<sha256(secret)[:24]>_<int(from)>_<int(to)>` from the Django cache; on a miss it lists
`GET /v1/balance_transactions?created[gte]=..&created[lte]=..` through every page (`starting_after` the last id, on the
list's `url`), skips payouts, sums only `stripe_fee` amounts (the total is added inside that branch, so both values are
equal), stores `round(float(sum) / 100, 2)` for 300 seconds and answers it. Any exception inside the try (a Stripe error,
a missing key in a transaction) answers zero charges, uncached. `net_balance` then subtracts `Decimal(total or 0)` (the
float's exact value, default 28-digit context). Go (`service/payment_stats_stripe.go`) uses the stripe/* SDK port
(`stripeapi.Session.List`) and writes the entry as django-redis does (`repository/payment_stats_stripe.go`: pickle
protocol 4, `:1:` prefix); a cached value other than that dict, non-integer fee amounts and non-string ids are refused.

## subscription/<uid>/payments

Athletes get `Response(status=401)` (no body, no authentication challenge) before the UUID check; then `is_valid_uuid`
(400), the scoped live association (500), the group-aware subscription (missing or hidden by the group: 500; another
association: `403 User not allowed.`; without an associate: 500). The payments are the association's live group-aware
payments of the associate or of any associate whose tax code is `iexact` the subscription associate's stripped tax code, in
any archived state, by `-creation_date`. `subject`, `type`, `paid` and `expense` apply whenever present. The text search is
the whole term icontains over the payment id, notes, meta, description, category name, `first_name || ' ' || last_name`,
supplier name, instructor and associate names, tax code, payment and creation dates, type, subject, account name and the
course titles reached through the payment's course subscriptions and installments (any state); without `distinct()` a
payment joined to two installments is listed (and counted) twice. Rows add `is_carnet` and, for course payments, the first
course subscription by key (`all_objects()`), else the first installment's (`all_objects()`, group-aware) as
`course_subscriptions {course_id, title, deleted}`, else the first carnet holding as `carnet {course_id, title}`.

## Not ported (refused with the 500 page)

- payment/list for athletes outside the compressed mode (their branch, PaymentCompressedSerializer, online-payment flags and
  the synchronous invoice PDF generation), and the compressed mode in an association-scoped athlete session.
- Orderings Django resolves only when compiling the rows (relations other than `user`, relation paths and transforms, the
  `full_name` annotation of the subscription search), refused after the count and page checks; random ordering together with
  the payment/list text search (DISTINCT with `RANDOM()` in the select list); reverse relation names are refused as unknown.
- Infinite float search terms (`inf`) and smart-search terms with characters Python lower-cases differently (personas-reads).

Instructor payments in payment/list: the hours rows of the page's instructor payments are mapped by payment in database
order (a later row replaces an earlier one); a row with a document sets `instructor_document` and its token; for each
payment whose row has none, `print_document_compensation.apply(args=[payment_id, Authorization])` runs eagerly (invoice-sync-print's
runner: `GET CURRENT_HOST/document/compensation/<id>`, errors kept in the eager result), then the hours of those payments
are read again and each row with a document sets the fields. API v2 gaps: a second hours row of a compensation and one
without a document (every list page that shows it prints it, checked against a print stub); RED `v2gaps-red-r2` Django
242/242, Go 500; GREEN `v2gaps-versioned-r2`; mutation (never printing, `v2gaps-mutation-r3`) CAUGHT; payment-writes and
invoice-sync-print re-run green.

`sort[field]=user` (the list's "Informazioni" column) orders by `user_id`: User has no `Meta.ordering`, so Django orders
by the foreign key column (NULLs last ascending, first descending). API v2 gaps: 3 cases (242 in all); RED `v2gaps-red-r1`
Django 242/242, Go 500; GREEN `v2gaps-versioned-r1` on the four transports; mutation (ordering by `associate_id`) CAUGHT.

Not authored: payments in the athlete branch, HEAD, the Italian invalid-page message, Stripe configured.

## Evidence (TDD)

`TESTS/fixtures/payment_reads_http.json` (239 cases, generated by `TESTS/fixtures/generate_payment_reads_http.py`, whose
search, ordering and serializer expectations come from an independent model of the Django semantics; contract
`TESTS/contracts/test_payment_reads_http.py`, adapter `TESTS/adapters/django/payment_reads_http.py`). The fallback-window
cases hold while no seeded payment (2024) is within a year of the run date.

- RED (`red-r1`): Django 238/238; Go 404 on the first case, before any Go code. One ordering case relied on the database's
  order of NULL payment dates (it differed on a Caddy transport) and was replaced by two deterministic pages; `red-r2`
  re-ran RED on all 239 with the Go routes unregistered: Django 239/239, Go 404.
- GREEN (`versioned-r3`): 239/239 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with the model state unchanged.
- Mutation (`mutation-r1`): counting each matching payment once in the stats account balances (ignoring the repeated tutor
  join rows) fails `payment-reads-stats-search-rossi` (CAUGHT).
- Shared code: none changed (new files and a route block in `main.go`); payment-state 130/130 and personas-reads 201/201
  stay green natively.

API v2 Stripe fees (`v2pay2-stats-*`): 6 cases (248 in all): fees fetched over two pages and cached, read from a cache entry
written by Django, a Stripe 400 and a transaction without a fee type (zero, uncached), the integration disabled, and an empty
list (zero floats cached). The adapter runs a Stripe API stub (stripe-routes' handler and request checks), configures the
instance integration only during the case and reads the entry back with `cache.get` (with its expiry). RED
`v2pay2-stats-red-r1`: Django 248/248, Go 500. GREEN `v2pay2-stats-versioned-r1`: 248/248 on the four transports. Mutation
`v2pay2-stats-mutation-r1` (skipping refunds instead of payouts) CAUGHT.
