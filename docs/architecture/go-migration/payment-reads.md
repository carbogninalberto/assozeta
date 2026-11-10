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
- Athletes: their own branch ("payment/list for athletes" below).
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

## payment/list for athletes

Go: `BE_GO/internal/{domain,dto,repository,service}/payment_reads_athlete.go`. The UI's athlete page
(`UI/src/routes/activities/payment/PaymentList.svelte`) calls the list without `mode`, optionally with
`course_subscription_id`. Owner filters, sorting and pagination parameters are ignored.

- With a `course_subscription_id` key (after `is_valid_uuid`; an empty value fails the UUID lookup, 500): the live course
  subscription with that id whose subscription has the athlete as user, or an associate of the athlete's group-aware
  subscriptions (the people of the list below; an association-scoped session keeps `scoped_queryset`'s members instead).
  Any other id is the 403 `Risorsa non disponibile per questo utente e associazione.` of the scoped-session reference
  check. When it has `multi_payments`, the payments of its live group-aware installments
  whose amount is above zero, or all of them when the course association's owner has `show_zero_payments` (an installment
  without payment: 500); otherwise its own payment under the same rule on the payment's amount. Then the payments of the
  carnet holdings linked to it, under the same rule with the carnet association's owner. The rows are the live group-aware
  payments with those ids, in database order (no ordering; the fixture compares them as a multiset).
- Without it: the athlete's group-aware subscriptions (any state) give the associates; the rows are the live group-aware
  payments of the athlete's user or of those associates with `amount >= 0`, `expense` false and created at most 60 days
  ahead, by `paid, -creation_date`, in any archived state. The exclusions walk the live course subscriptions of those
  subscriptions: when the course has `multi_payments`, each unpaid live installment due more than 31 days after today is
  excluded, and one due earlier is excluded when its payment is zero and the subscription association's owner does not show
  zero payments (such an installment without payment: 500); otherwise the course subscription's payment is excluded when
  it is zero and the owner does not show zero payments.
- Receipts: before the exclusions apply, every row that is paid and whose invoice has no PDF goes through `document_invoice`
  in-process with the list's own request (its query string, so `send_receipt_email=true` sends the receipt e-mail, its
  Authorization header for the renderer, its group and session). Go calls `DocumentRender.Render` with the `invoice` route
  (`document-render.md`): the live scoped invoice and its single payment with a payment date, a new Document, the renderer
  request, the `save_to_storage` task, the invoice link, the optional `send_mail_async`, each write audited. The view's
  answer is ignored (a deleted invoice, two payments on one invoice, a payment without date or a renderer failure leave the
  list unchanged), and the row still shows the invoice as loaded before: `document_pdf` is null in the request that
  generates the PDF and the Document id from the next one.
- Rows are PaymentCompressedSerializer: `payment_id`, `type`, `subject`, `amount`, `associate`
  (`{associate_id, first_name, last_name}` or null), `description`, `paid`, `creation_date`, `payment_date`,
  `payment_category`, `sport_association`, `notes`, `course` (the JSON) and `meta`. `get_meta` starts from
  `{type: '', is_installment: false, installment: {id, payment_date}, conjunction: '', description, notes, name,
  subscription: {subscription_id, start_date, end_date}}` and takes the first match, each lookup's first row by key: the
  live group-aware installment (`rata`, ` del corso `, the course title, the installment's `id` and `payment_date`), the
  group-aware subscription paid by it (`Iscrizione`, ` all'associazione `, the denomination), the carnet holding
  (`Assegnazione carnet`, ` con nome `, the carnet title), the live course subscription (`Iscrizione `, ` al corso `, the
  course title), the camp period (`Iscrizione `, ` al campo `, the period title). A match without its subscription (or a
  holding without its carnet) is a 500.
- The view adds `payment_method` (the type), `online_pay_available` (the payment association owner's `online_payments` and
  `stripe_direct_credentials_configured()`; a payment without association: 500), `sport_association_denomination`,
  `one_solution_available` and, for paid invoiced rows, `document_pdf`. `one_solution_available` needs online payments, a
  live scoped installment of the payment whose course has `one_fee_payment`, and an unpaid payment: it is true when one of
  the course's `events` has `int(id) == 0` and a `payment_date` equal to the installment's date as `d/m/Y` (events that are
  not a list of such objects, or any event when the installment date has microseconds, are a 500).
- The response is `{"data": {"0": row, "1": row, ...}, "meta": {}}` (an object keyed by index; `{}` without rows).
- In an association-scoped session `scoped_queryset` filters every queryset above to the session's association and to the
  athlete's family boundary (the payments of the athlete's user or of the members it resolves), and a
  `course_subscription_id` outside it is `403 Risorsa non disponibile per questo utente e associazione.` before the view.
  The course subscription itself is otherwise resolved by its id.

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

- The compressed mode in an association-scoped athlete session (Django answers 500 there too: an athlete has no association).
- In the athlete list: a receipt whose filename is not a valid header value (the association's denomination with control
  characters; `document-render.md`) is refused before any write, where Django stores the Document and ignores the failed
  response.
- Orderings Django resolves only when compiling the rows (relations other than `user`, relation paths and transforms, the
  `full_name` annotation of the subscription search), refused after the count and page checks; random ordering together with
  the payment/list text search (DISTINCT with `RANDOM()` in the select list); reverse relation names are refused as unknown.
- Smart-search terms with characters Python lower-cases differently (personas-reads).

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

Not authored: HEAD, the Italian invalid-page message; in the athlete list, payments without association, matches without
their subscription, malformed course events and installment dates with microseconds on a one-fee course (Go follows the
source: 500).

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

API v2 athlete list (`v2athpay-*`): 22 cases (270 in all) at the end of the sequence, on rows of their own: two further
associations (one owner hides zero payments and takes online payments, the other the opposite), a group, three athletes,
their subscriptions, courses, course subscriptions, installments, a carnet holding, a camp period, invoices and payments,
plus an association-scoped session of the first owner for one athlete. They cover the empty list (also under an
administrator's impersonation), both variants of the query, the group header, the installment and zero-amount exclusions on
both owner settings, every `get_meta` branch, the online payment flags with Stripe configured, disabled and absent, the
one-fee flag, the 500 paths (an installment without payment in each variant, a deleted course subscription, an empty id),
and the receipts: a renderer failure (the Documents stay, nothing linked), two receipts generated with their e-mails
through the list's `send_receipt_email`, the ids shown from the next request, invoices that never generate (deleted, shared
by two payments, payment without date), and the scoped session (its rows, its receipt audited with the session, its
`course_subscription_id` check). The adapter now runs document-render's renderer stub and the Celery broker and checks
every case against the complete model state: unchanged, or exactly the authored Documents, invoice links, auditlog entries
with their index rows and Celery messages. RED `v2athpay-red-r3`: Django 270/270, Go 500 (the refusal). GREEN
`v2athpay-versioned-r1`: 270/270 on the four transports. Mutation `v2athpay-mutation-r1` (zero course subscription payments
excluded whatever the owner's setting) CAUGHT on `payment-reads-athlete-list-renderer-failure`. Shared code: `main.go`
(the `DocumentRender` service is now also handed to payment-reads) and the payment-reads handler; payment-writes,
payment-misc, payment-state and document-render re-run green (`v2athpay-rerun-r1`).

Athlete course subscriptions (`secath-*`): the athlete payment list only shows the athlete's own course subscriptions and
those of their associates. Django (`payment_views.payment_list`, with
`application.tests.test_payment_views.AthleteCourseSubscriptionPaymentTests`) and Go
(`paymentAthleteOwnCourseSubscription` in the repository, the 403 in the service) changed together; the fixture was
regenerated (276 cases): a course subscription of another user's subscription of a shared associate, the same under a group
header (no associates: 403) and the athlete's own under it (the group's payments: none), another athlete's course
subscriptions in both directions, an administrator's impersonation, and the unknown and soft-deleted ids (403). RED
`secath-red-r1`: Django 276/276, Go 500 on `payment-reads-list-athlete-unknown-course-subscription`. GREEN
`secath-versioned-r1`: 276/276 on the four transports. Mutation `secath-mutation-r1` (the course subscription lookup without
the athlete's condition) CAUGHT. payment-writes, payment-misc and payment-state re-run green natively (`secath-rerun-r1`).

v2-rest-b: an infinite search term (`inf`, `-Infinity`) follows NaN: the amount lookup refuses it when the filter is built, so
`payment/list` and `payment/stats` answer the 500 page and `subscription/<uid>/payments` falls back to the text search. Four
cases (280 in all); RED is the documented refusal (v2-refusals.md, payment-reads "search" row). GREEN `v2restb-green-r3`:
280/280 on the four transports. Mutation `v2restb-mutation-r1` (infinity searched as an amount) CAUGHT. Still refused: random
ordering with the text search (the rows repeat in random order, nothing to compare) and an athlete receipt whose filename
is not a valid header value.
