# Course subscriptions list, update, delete and bulk-delete — implementing with HTTP parity

`GET course-subscriptions/list`, `PATCH course-subscriptions/<pk>/update`, `DELETE course-subscriptions/<pk>/delete` and
`DELETE course-subscriptions/bulk-delete` (`CourseSubscriptionViewSet` list/update/delete/bulk_delete, with the
`CourseSubscription.delete()` soft delete and the course subscription, installment and payment `pre_delete` signals) are
implemented in Go (`BE_GO/internal/{domain,dto,repository,handler}/course_subscriptions.go`,
`BE_GO/internal/service/course_subscriptions.go` and `course_subscription_update.go`). Registry `association.courses.read`,
`.update` and `.delete`. Django remains the execution owner. `course-subscriptions/add` is another family
([course-enrolment](course-enrolment.md)); the list's `excel` and `pdf` exports are ported.

## Routing and lookups

- The viewset's own `delete()` answers DELETE on every mapped route: on `list` (no pk) it reads
  `request.user.sport_association` and then fails (500); on `update` it soft-deletes a live row. `Allow`: list
  `GET, POST, HEAD, DELETE, OPTIONS`, update `PATCH, DELETE, OPTIONS`, delete and bulk-delete `DELETE, OPTIONS`. OPTIONS on
  `list` describes the serializer under `actions.POST` (nested subscription, associate, payment, user, instructor, supplier,
  invoice and document serializers); the other routes have no actions. The scoped-request guard names the view
  `CourseSubscriptionViewSet`, which is not an athlete view.
- `User.sport_association` comes first: athletes get `403 User is not a sport association`, an owner without an
  association a 500. `get_queryset()` keeps the rows whose course (joined directly: deleted and grouped courses included)
  belongs to it, through the soft-delete manager (`all_objects()` for `include_deleted=true`, any case, and for the
  delete action). `course_id` and `subscription_id` filter when present; a value the UUID column rejects (including a
  blank one) is a 500.
- `get_object()` uses `django.shortcuts.get_object_or_404`, which does not catch the UUID `ValidationError`: a malformed pk is
  a 500; a missing, foreign or (outside the delete route) deleted row is `404 No CourseSubscription matches the given query.`.

## List

`{'data': [...]}`, unordered. Each row is `CourseSubscriptionOverviewSerializer`:

- `subscription` is `SubscriptionSerializerSimplify` (the associate through `AssociateSerializer`, `current_year` against the
  UTC date) and `medical_label` is `Subscription.get_medical_label()`: the age from `date.today()` (UTC), `Esente per N
  giorni` under six (0 without a birth date), otherwise the certificate against today in Europe/Rome (`Mancante da` days
  since the subscription's creation, `Scaduto da`, `N giorni rimanenti`), with the `AG` badge for competitive certificates.
  A row without subscription renders both as null.
- `installments` are the live, group-aware (`X-Group-Id`) installments; `paid` is `payment.paid` and `payment_date` the UTC
  date.
- `membership_payments` are the live, group-aware membership payments through `PaymentSerializer` (user, associate,
  supplier, attachments, custom account, category name and `get_subscription_id`'s installment / course subscription /
  carnet / associate lookups).
- `carnets` are the enabled holdings: lessons left from `meta.lessons_left` (default: the carnet's lessons), used and
  display only with lessons, the creation date as `%d/%m/%Y` in UTC.

`POST` runs the PDF/Excel export after building the same queryset: an unknown or missing `format` (or a body that is not a
dict) is a 500, as in Django.

## Excel export (`POST course-subscriptions/list`, `format: excel`)

After the queryset is built (so a filter error comes first) the view reads `request.data` (a parse error is DRF's 400) and calls
`current_view_course_subscription`: `data['format']` must be `pdf` or `excel` (anything else, a missing key or a body that is
not an object is the 500 page). `excel` answers `{"file": <base64 workbook>, "filename": "[DD/MM/YYYY] <title> - <denomination>.xlsx"}`:

- columns `Cognome`, `Nome`, `Nato il`, `Email`, `Telefono`, `Corso`, `Data creazione`, `Tags` (the subscription's tag names joined
  with `, `), a `Carnet` column when a listed row has an enabled carnet subscription (`<title> (DD/MM/YYYY) - <left>/<total>`
  per carnet, `-` for the rows without one), then one column per tag of the association (`assegnato` or empty);
- a row without subscription, associate or birth date is the 500 page;
- the carnet loop rebinds the function's `title`, so the filename carries the last carnet title it handled (else
  `Iscrizioni ai corsi`);
- the workbook is `get_excel_base64` (pandas with openpyxl, `xlsxexport.PandasOpenpyxl`).

`pdf` answers the same shape with a reportlab table document (`pdfexport.DataTable`, [pdf-exports](pdf-exports.md)): the same
columns as rows up to `Tags` and `Carnet` (the per-tag columns belong to the workbook), titled `<title> (giorno stampa
DD/MM/YYYY)` with the rebound title, in a file always named `[DD/MM/YYYY] Iscrizioni corsi filtrate - <denomination>.pdf`.
A member without phone or e-mail is an empty cell.

Row order. None of the export's statements has an ORDER BY: the rows, the tags and the carnets come in the order the database
returns them. Go sends the statements the ORM compiles, with the same tables, join types, predicates and selected columns
(`repository/course_subscriptions_export.go`): the nullable subscription is joined with `LEFT OUTER JOIN`, and with
`INNER JOIN` when the request filters on `subscription_id` (the ORM demotes the join then). Both backends therefore get the same
plan from the same database, and the plan decides the order.

An earlier attempt saw one listing in another order on Go. The cause was not the statement: both backends got the same plan
(`EXPLAIN` on both sides). The harness runs its workloads one after the other on one database, deleting and seeding the family's
rows in between. The second seeding reused the space of the deleted rows, so the same rows were stored in another physical
order, and an index or bitmap scan returns equal keys in stored order: Django itself returned the other order on that
workload's data. Planner statistics could also appear between two workloads (autovacuum), changing the plan. The adapter now
runs `VACUUM` on the export's tables before seeding and `ANALYZE` after it (`EXPORT_TABLES`), so every workload reads the same
stored layout with the same statistics. The export cases still filter one course: across courses the order follows the join
plan.

## Update (atomic, partial)

- After `get_object()`, `scoped_queryset(...).select_for_update().get(pk=...)`: an association-scoped session outside the
  row's subscription and course association is a 500 (DoesNotExist).
- Field validation: a writable `course_subscription_id` (UUIDField, UniqueValidator on the live rows), `subscription_id`
  (UUIDField; a boolean is an integer UUID), `creation_date`, `billed_from` and `billed_until` (DateTimeFields, the last two
  nullable), the BooleanFields, `billed_frequency` (0..32767) and `billed_from_day_of_month` (1..28), `membership_fee`
  (DecimalField(9, 2), a blank value is None), the JSONFields `multiple_quote` and `events`, and `course`, whose queryset
  `Course.objects.all()` is evaluated once at import time (live courses). An empty HTML
  value of an optional non-null field is skipped; of a nullable one it is None.
- `validate()` applies to a membership course or instance: the course must be the association's and unchanged, the
  subscription (group-aware lookup) must belong to the course's association and be unchanged, both period dates are
  required with the end not before the start, the duration within 1..12 months and the fee not negative.
- For a membership with a billing end, the unpaid live group-aware payment whose `meta.billed_until` is the current end is
  the current payment (two: 400); without one and without any payment for that end, a lone unpaid payment is recovered when
  both dates are sent; otherwise an unpaid payment means a mismatch (400). The payment's `meta.billed_from` must parse
  (`%Y-%m-%d`), a changed start (or a recovery) moves the period start, the end may not precede it and the period may not
  overlap another membership payment (jsonb comparisons). These errors render the view's `{'field': 'text'}`.
- `Model.save()` writes every column; auditlog records the changed fields, indexed through the course's association. The
  entry's `object_repr` is `CourseSubscription.__str__`, `str(self.__dict__)` of the in-memory instance: values the
  serializer set keep DRF's types (zoneinfo datetimes, the request JSON), values read back keep the database's; the
  `ModelState` address is the only part that is not reproducible.
- The current payment then takes the fee, the period dates (when the start changed), the description and the merged meta,
  and `Payment.save()` fills a null category with the first `entrate e proventi da attività tipiche` category; the entry is
  indexed directly. A subscription that does not exist fails the deferred foreign key at commit (500, nothing saved).

## Delete and bulk delete

- `delete()` soft-deletes: the live group-aware installments go through the collector (keys in order for `pre_delete`,
  reversed for the deletes). Each unpaid installment payment is deleted by `delete_related_payment`; Payment's own
  `pre_delete` deletes that payment's live installments first, so the trail is payment, installment, payment, then the
  installments again. SET_NULL references and through rows of deleted payments are cleared. The row is then saved with
  `deleted=True` (an update entry resolved through its course). A row that is already deleted (delete route) is hidden from
  auditlog's default manager: its save is an update entry with every field against None.
- `bulk_delete` reads `request.data.get('course_subscription_ids', [])` (a list or text body is a 500; null is not iterable,
  text is iterated by character, null members are dropped, integers and booleans are integer UUIDs) and hard-deletes the
  live rows of the association (and of the scoped session). The installments (base manager) get `pre_delete` first; then
  `delete_related_course_payment` runs per row: for a row with a payment it fails on `Payment.objects.get(id=...)` (logged,
  nothing else happens); otherwise a membership's unpaid live payments are deleted and then the row's live group-aware
  installments. Through rows go, then installments and rows are deleted with entries in reversed key order.
- Delete entries for payments are indexed through their stored association, installments and course subscriptions through
  the actor's own association (none for a collaborator).

## Installment edits (`events`)

The edit form always sends `events` (the selected course events). `CourseSubscriptionOverviewSerializer.update` runs before
the instance save, inside the atomic update:

- the row's live group-aware installments are loaded (database order); each one without an event of the same `id` (numbers
  compare by value, booleans as 0/1) goes: a paid payment is relabelled first (`Rata n.<id+1> del <dd/mm/yy> (<course>)`, its
  meta replaced, `Payment.save()` filling a missing category), then `installment.delete()`, whose `pre_delete` deletes an
  unpaid payment (the Payment `pre_delete` nests the installment delete: P, I, P, then the outer I);
- each event without `course_subscription_installment_id` is validated by `CourseEventsSerializer` (400 with its field errors)
  and becomes a payment of the course's association (its owner, the default bank account and category, the raw `X-Group-Id`)
  and an installment.
- Any error (400, a `KeyError`/`TypeError` of an event, an invalid group header) rolls everything back.

Membership payments render PaymentSerializer's nested `instructor` and `invoice`.

## Not ported (refused before any write)

- In the list exports a carnet `lessons_left` that is a list or an object: 500 (the list route itself fails on such a
  row in Django).
- Update: unmatched installments sharing one
  payment, a non-canonical `X-Group-Id` spelling with new events, a changed primary key (Model.save() would insert a copy)
  and HTML-form input for the JSON fields `multiple_quote` and `events`: 500.
- A soft delete whose row's own payment is also the unpaid payment of one of its installments (Django's save would fail
  after the autocommitted deletes): 500.

## Evidence

`TESTS/fixtures/course_subscriptions_http.json` (108 cases, generated by `TESTS/fixtures/generate_course_subscriptions_http.py`).

- RED (`red-r1`): Django 107/107; Go 404 on the first case.
- GREEN (`versioned-r1`): 107/107 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact course subscription,
  installment, payment, through-row, audit and index effects.
- Mutation (`mutation-r1`): dropping the `X-Group-Id` filter from the soft delete's installment query fails
  `course-subscriptions-delete-group-installments` (CAUGHT).
- `subscription-memberships` stays green after the route block was added to `main.go`.

Installment edits and nested membership payment rows (114 cases):

- RED (`v2c-red`): Django 114/114; Go fails `list-owner` (membership payment with an invoice refused).
- GREEN (`v2c-green`): 114/114 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact effects (created payments
  and installments matched column by column).
- Mutation (`v2c-mutation-rata`): numbering the instalment label from 0 fails `update-events` (CAUGHT).
- Re-run green (`v2c-rerun-cs`): see the v2-refusals change list.

`bulk-row-without-payment-installments`: for a payment-less row the signal deletes its remaining live installments (payment-less
and grouped ones) after the installment with an unpaid payment went with that payment; the collector then deletes every collected
installment again (a second entry each).

- Django (`gaps-native-2`): 108/108 before any Go change (native Go also 108/108).
- GREEN (`gaps-versioned-1`): 108/108 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`gaps-mutation-1`): skipping the signal's installment delete in the bulk delete fails
  `bulk-row-without-payment-installments` (CAUGHT).

Excel export of the list (132 cases):

- RED (`famwiz-red-r1`, the Go code of the previous commit): Django 132/132; Go 500 on `course-subscriptions-list-excel-course`.
- GREEN natively (`famwiz-native-r1`): 132/132 on native Django and native Go, with the workbooks compared part by part.
- Mutation (`famwiz-mutation-r1`): a filename that keeps the list title instead of the last carnet title fails
  `course-subscriptions-list-excel-subscription-carnet-title` (CAUGHT).
- The router family's fixture was regenerated (the shared seed has one more carnet subscription).
- GREEN (`famwiz-versioned-r1`, after merging `codex/go-backend-parity`): 132/132 on native Django, native Go, Caddy `/api/v1`
  and `/api/v2`.
- Re-run green natively (`famwiz-rerun-r1`): `router-course-subscriptions` 45/45, `course-enrolment` 113/113,
  `course-attendance` 99/99.

PDF export of the list (13 more cases, 145 in all):

- RED (`v2pdf-red-r2`): Django 145/145; Go 500 on `course-subscriptions-list-pdf-course` (the first authored expectation
  carried the per-tag columns and failed on Django).
- GREEN (`v2pdf-versioned-r1` (and `v2pdf-versioned-r2` after merging `codex/go-backend-parity`)): 145/145 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, each document compared in
  decoded form.
- Mutation (`v2pdf-mutation-r1`): a document titled with the list title instead of the last carnet title fails
  `course-subscriptions-list-pdf-subscription-carnet-title` (CAUGHT).

## Events as an object or text, float carnet lessons (restcourses)

- Update with `events` sent as an object or as text: the loops walk its keys or its characters, each a string. With
  installments on the row the id comparison raises (500, nothing written). Without, each key or character is validated as
  an event (`400 {'non_field_errors': ['Invalid data. Expected a dictionary, but got str.']}`) unless it contains the text
  `course_subscription_installment_id`, which leaves it out (200, nothing changed). An empty object or text removes every
  installment, like an empty list.
- A carnet with a float `lessons_left` (`2.5`): the list shows `lessons_left` 2.5, `lessons_used` 7.5 and `2.5/10`; both
  exports write `2.5/10` in the Carnet column.

Go: `planEvents` uses `dto.PythonIterate`; `courseSubscriptionExportLessons` writes numbers with `dto.PythonStr`.

Evidence: 150 cases. RED: documented refusals (v2-refusals.md, course-subscriptions rows); `sweep-restcourses-red-r1` is the previous Go tree against the new cases. GREEN `sweep-restcourses-green-r2` on
native Django, native Go, Caddy `/api/v1` and `/api/v2`. Mutation `restcourses-mutation-r1` (an object or text read as an
empty selection) CAUGHT.
