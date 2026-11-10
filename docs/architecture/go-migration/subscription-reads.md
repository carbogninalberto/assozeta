# Subscription list, list/all, list/archived, info and calendar — implementing with HTTP parity

`GET`/`POST subscription/list`, `GET subscription/list/all`, `GET subscription/list/archived`, `GET subscription/<uid>/info`
(`subscription_list`, `subscription_list_all`, `subscription_list_archived`, `subscription_info` in
`BE/application/views/subscriptions_views.py`) and `GET subscription/<uid>/calendar` (`subscription_calendar` in
`BE/application/views/attendee_views.py`) are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/subscription_reads.go`).
Registry: `association.members.read` (GET and POST `subscription/list`, `list/all`, `*/info`, `*/calendar`) and
`association.members.archive.read` (`list/archived`). Django remains the execution owner. The info view reuses the
CourseSubscriptionOverviewSerializer port of course-subscriptions, the CourseSerializer port of course-overview and the
personas-reads AssociateSerializer, tutor and family reads; smart-search preparation and KTDatatable pages come from
personas-reads and payment-reads.

## Shared behaviour

- Function views: Allow `GET, POST, OPTIONS` on `subscription/list`, `GET, OPTIONS` elsewhere; OPTIONS names
  `Subscription List`, `Subscription List All`, `Subscription List Archived`, `Subscription Info` (empty descriptions) and
  `Subscription Calendar` (its docstring). `subscription_calendar`'s `IsAssociationUser | IsAthleteUser` admits every
  authenticated role. All views except `subscription_list_archived` are athlete views for association-scoped sessions.
- `Subscription.objects` is group-aware (`X-Group-Id`; a header the UUID field rejects is ignored) and keeps soft-deleted
  rows: deleted subscriptions are listed. An association-scoped session adds its association (`scoped_queryset`).
- The association is `scoped_queryset(SportAssociation.objects).get(user=...)` (live): owners without one, or with a
  soft-deleted one, get the 500 page.
- KTDatatablePagination as in payment-reads (`pagination[perpage]` a strictly positive `int()`, else 10; `pagination[page]`
  empty or absent 1, `last`, else within the pages, otherwise `404 Invalid page.`); `data` is keyed by row index
  (`{"0": ...}`) and `meta` is `{total, page, pages, perpage, rowIds}`.
- `smart_search` over Subscription: each `\w+` word of the stripped, lowercased term must icontains one of the 22 concrete
  Subscription fields (`field::text`, the creation timestamp in UTC, JSON as jsonb text), the associate's or its tutors'
  fifteen listed fields; numeric words also match the date variations on the date fields; several words also match the
  reversed and original sequence on the name/address/city/notes fields. The conditions hold per joined tutor row, the rows
  are distinct and no relevance ordering applies (the first ten fields are Subscription's own).
- `sort[field]`: `associate` orders by first and last name, otherwise one leading `-` is stripped and the name must be a
  Subscription field or attname (`pk` included) — an unknown name is a FieldError (500) before pagination. Relations,
  relation paths, transforms, `?` and the `_has_future_renewal` annotation compile with the rows; Go refuses them after the
  count and the page check (an invalid page still answers 404).

## subscription/list

- `field` (truthy) comes first, for every role: the unarchived subscriptions of the user's association
  (`sport_association__user`), excluding those whose associate has no tutor with email and phone (four separate EXISTS)
  while its own email or phone is empty or null; `values_list('associate__<field>').distinct()` unordered (dates as
  `Y-m-d`, the creation timestamp in DRF format). Associate fields, `pk`, forward relation names and attnames are ported;
  anything else is the 500 page (a FieldError in Django, or a relation path / transform that is not ported).
- Without `m`: unarchived rows by `-creation_date`; `query[tags]` (comma UUIDs; `query[tags_and]=1` requires each, else
  any; a malformed id is a 500), `query[from_age]` / `query[to_age]` (`int()`; born on or before / after today minus the
  years, 29 February clamped; a year outside 1..9999 is a 500), `query[hide_associate_and_members]=1`,
  `query[hide_members]=1`; `query[status_flag]` (one value: `int()`, outside 0..32767 no rows; several comma values each
  `int()`); `query[current_year]` `2` future, `1` current, `0` past, `3` the `d/m/Y` period overlap (missing or unparseable
  dates: no filter; start after end: no rows); the general search (`query[prefix]` prepended, an absent term becomes
  `None`): `scade=<d/m/Y>` searches the end date, `@=` the username, else `d/m/Y` becomes `Y-m-d` and smart_search;
  `query[type_of_associate]` (custom data icontains), `query[certificate_missing_flag]=1`,
  `query[certificate_expired_flag]=1`, `query[subscription_not_paid_flag]=1`, `type=athletes|associates`; the last-name
  flags (any value but `0`; descending only for `desc=1`) and `sort[field]`, which replaces them.
- GET rows: SubscriptionFastOptimizedSerializer (user, AssociateInfo fields, the linked payment with a float amount, tags,
  `payments_info` / `all_payments_paid` over the associate's live group-aware unarchived payments, age and
  `plain_medical_label` when there is an associate, `renewal_available` from the `future_renewal_exists` annotation, whose
  candidates are group-aware, medical and document ids and tokens, `signature_present`).
- POST runs the export after the filters and the sorting, reading `request.data` then (a parse error is DRF's 400): a
  missing or unknown `format`, or a body that is not a dict, is a 500. See "Excel export".
- With `m` (any value, even empty): only the basic filters, then `filter` (absent: accepted; `all`: any; else the status
  with `int()`), SubscriptionFastBasicSerializer rows by `-creation_date`, `meta` `{}`; POST answers the same.

- Athletes (after the field mode, for GET and POST, every other parameter ignored): `get_optimized_subscriptions`. Their
  own subscriptions and those whose associate has a tax code of one of them (both group-aware, soft-deleted rows included),
  by `-creation_date`, as SubscriptionSerializerAthleteOptimizedList keyed by index with `meta` `{}`: the model fields
  (`signature_storage_key` excluded, forward relations and tags as keys), `is_current`, `carnets` (any CarnetSubscription),
  the certificate id, document, token and expiry, the live course subscriptions with their course (`one_fee` as a float),
  `renewal_available` (the annotation, or the IS NULL lookup without a tax code), `signature_present`, the
  user_serializers AssociateSerializer (`user`: the associate's, else its association owner's) and
  SportAssociationSearchSerializer (UserSerializerSearch of the owner: `registration_features()`, `online_payments` from
  the owner's flag and the Stripe credentials; the membership card configuration).

### Excel export (POST with `format` `excel`)

`current_view_subscriptions` over the whole filtered, sorted queryset (pagination parameters are ignored): 200 with
`{'file': <base64 workbook>, 'filename': '[<dd/mm/YYYY today>] <title> - <denomination>.xlsx'}`; the title is `Tesserati`
for `type=athletes`, `Libro Soci` for `type=associates`, else `Iscrizioni filtrate`. The workbook is `get_excel_base64`
(`xlsxexport.PandasOpenpyxl`) of Tesserato (`get_full_name()`), Email, Stato (`get_status_lang()`), Anno fiscale
(`current_year()`: `corrente`, `precedente`, `successivo` on the UTC date), Certificato, Data creazione, Utente
(username) and Tag (tag names joined with `, `). The view's rows carry the creation date (`%d/%m/%Y`) under Certificato
and `presente`/`-` for the certificate under Data creazione. A row without associate, or with a status outside 1–5, is
a 500.

Row order: the subscriptions follow the list's own `ORDER BY` (the clause the paginated list uses, the authored rows have
distinct keys). A subscription's tags come from `prefetch_related('tags')`, which Django sends without `ORDER BY`; Go
sends the same statement (`ExportTags` in `repository/subscription_reads.go`: same tables, join and predicate), so both
read the rows in the order the database returns them. The fixture seeds tags and their through rows in id order.

## subscription/list/all

- `preregistration=1`: athletes' own subscriptions of `sport_association` (absent: none) in status 1, 2, 4 or 5, or the
  association's unarchived ones; ordered by first name, last name, tax code and `-creation_date`; names and tax codes
  (`'%s %s'` lowercased, `None` for missing parts) with a next-year subscription are dropped, and only the first row of
  each name is kept, keyed by its index in the ordered rows; other parameters are ignored.
- Otherwise athletes need `sport_association` (`403 {'msg': 'not allowed'}`; a value the UUID field rejects, empty
  included, is a 500) and get their subscriptions there by `-creation_date`; owners get the association's unarchived rows
  (unordered). `current_year=1|true`, `type`, `course_id` (exclude the course's live course subscriptions; SQL `NOT IN`
  semantics: a live course subscription without subscription excludes every row). A row without associate is a 500.

## subscription/list/archived

Athletes get `Response(status=401)` (no body, no challenge); scoped athlete sessions the scoped-view 403. Archived rows of
the association by `-creation_date`, the `d/m/Y` conversion and smart search, `sort[field]` and then the last-name flags
(any non-empty value). SubscriptionSerializer (`exclude = signature_storage_key`; the attribute-less `renewal_available`
field is skipped), then `current_year` (the end of the owner's balance year containing the creation date, at 00:00 UTC,
not before now), `next_years` (created after that end), `renewal_available` (false in the current year, otherwise not
`has_future_renewal()`: the annotation for associates with a tax code; without one, the group-aware lookup of unarchived
subscriptions with a null tax code starting on or after the end date; a null end date there is a 500) and
`medical_document`. Without an associate, `payments_info` and `all_payments_paid` cover the live, group-aware unarchived
payments that have no associate.

## subscription/<uid>/info

`is_valid_uuid` (400 list). Athletes: their own subscriptions and those of associates sharing a tax code with one of
theirs (the raw path text is compared with the canonical ids: `403 Subscription not found.`), the subscription's live
association (`403 Sport association not found.`). Others: the scoped association. The group-aware subscription
(`404 {'msg': 'Subscription not found.'}`, another association `403 User not allowed.`). Courses: CourseDetailSerializer
of each live course subscription's course (deleted courses included; the tags and locations as keys) with its
CourseSubscriptionOverviewSerializer (`course_subs_dict`: the last row of a course wins) and, for multi-payment ones, the
scoped live group-aware installments (`paid` from the payment when there is one), sorted by the course creation date text,
descending. SubscriptionInfoSerializer: the membership card configuration (first by key, else the defaults), UserSerializer,
the personas AssociateSerializer (`is_tutor` from the relations), the certificate fields (`competitive_medical_certificate`
absent without a certificate), the scoped subscription files, `is_current`, `is_next_year`, `plain_medical_label` (a
subscription without associate is a 500), `signature_present` and the model fields with the tags as keys. Without `document_pdf` the view then publishes
`print_document_subscription.delay(str(id), request.headers.get('authorization'))` (internal/celery).

## subscription/<uid>/calendar

`is_valid_uuid`; the athlete's own or the association's group-aware subscription (none: `{'events': []}`); the scoped
registries of the courses of its live course subscriptions; each truthy events list item as
`{title: "<str(title)> (<course title>)", start, end, allDay}` (a missing key or a non-list value is a 500).

## Not ported (refused with the 500 page)

- Association-scoped athlete sessions on subscription/list, list/all, info and calendar (scoped_queryset's family boundary).
- The POST export of subscription/list with `format` `pdf` (a time-stamped PDF render). The Excel export is ported,
  except workbook inputs `xlsxexport` does not reproduce (`xlsx-exports.md`) and exports sorted by an ordering compiled
  with the rows.
- Orderings compiled with the rows (relations, paths, transforms, `?`, annotations), field-mode relation paths and
  transforms, and search terms with characters at or above U+0250 or U+0130 (personas-reads).

Not authored: the Italian invalid-page message, two course subscriptions of one course in info, a null end date in the
archived renewal lookup.

## Evidence (TDD)

`TESTS/fixtures/subscription_reads_http.json` (261 cases, generated by `TESTS/fixtures/generate_subscription_reads_http.py`
from a model of the Django semantics; contract `TESTS/contracts/test_subscription_reads_http.py`, adapter
`TESTS/adapters/django/subscription_reads_http.py`). Ages, `is_minor_now` and day counts are tokens resolved on the run date;
the other values hold through 2029. Django adds no tiebreak to these orderings (Subscription has no Meta.ordering), so the
authored rows have distinct keys for every ordering the cases use (creation dates, start dates, first and last names, birth
years).

- RED (`red-r1`, 245 cases): Django 245/245, Go 404 on the first case, before any Go code. Django runs before it corrected
  one authored expectation (`payments_info` of an archived subscription without associate). `red-r2` re-ran RED on the
  final 253 cases with the Go routes unregistered: Django 253/253, Go 404. Two archived subscriptions shared a last name, so
  the last-name orderings depended on the query plan (one concurrent run returned the tied rows swapped); the fixture now
  gives one of them its own associate, and `red-r3` re-ran RED on it: Django 253/253, Go 404 with the routes unregistered.
- GREEN (`versioned-r2`): 253/253 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with the model state unchanged.

Membership payments with an instructor and an invoice in subscription/info (API v2 gap): the course subscriptions' membership
payments render PaymentSerializer with the nested InstructorSerializer and InvoiceSerializer (course-subscriptions' loader and
renderer); the fixture links one such payment to a course subscription of an info case.

- RED (`v2doc-red-r1`): Django 261/261; Go 500 on `subscription-reads-info-hyphenless-uuid`.
- GREEN (`v2doc-versioned-r1`): 261/261 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`v2doc-mutation-r1`): rendering the nested invoice as null fails `subscription-reads-info-hyphenless-uuid` (CAUGHT).
- Stability: `stability-r1`..`r3` 253/253 natively, each run concurrently with personas-reads.
- Mutation (`mutation-r2`): dropping the group filter from the null-tax-code renewal lookup fails
  `subscription-reads-archived-group` (CAUGHT).
- Shared code: none changed (new files and a route block in `main.go`); payment-reads 239/239, course-subscriptions
  107/107, personas-reads 201/201 and course-overview 22/22 stay green natively.

v2-subscriptions (8 cases: `list-athlete*`, `info-print-dispatch-*`; a list athlete with foreign-association rows, a member
without user, one without tax code, a grouped and a soft-deleted row, course subscriptions and a carnet subscription). The
adapter now runs on the Celery broker and checks per case that the state is unchanged and the published tasks are exactly
the authored ones.

- RED (`v2sub-red-r2`): Django 261/261; Go 500 on `subscription-reads-list-athlete`.
- GREEN (`v2sub-versioned-r1`): 261/261 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`v2sub-mutation-r1`): ordering the athlete list by ascending creation date is CAUGHT.

API v2 (v2-misc): subscription/info renders the course subscriptions' membership payments with PaymentSerializer's nested
invoice and instructor (loaded by the shared course-subscriptions repository). The fixture seeds an instructor, an invoice and
three membership payments of one course subscription (invoiced, open, invoiced with an instructor); 261 cases. RED
`v2misc-red-r1`: Django 261/261, Go 500. GREEN `v2misc-green-r1`: 261/261 on the four transports. Mutation `v2misc-mutation-r1`
(the nested invoice not loaded) fails `info-hyphenless-uuid` on Go.
- Excel export: 33 more cases (294 in all). RED `v2xlsx-red-r1`: Django 294/294, Go answers 500 on the first export case.
  GREEN `v2xlsx-green-final`: 294/294 on native Django, native Go, Caddy `/api/v1` and `/api/v2`. Mutation
  `v2xlsx-mutation-final`: `precedente` decided by an end date after today fails Go on `list-excel-owner` (sheet1.xml
  differs).
