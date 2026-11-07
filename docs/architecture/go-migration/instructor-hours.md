# Instructor hours list, add, update, delete and calculate — implementing with HTTP parity

`GET instructor/<uid>/hours/list`, `POST instructor/<uid>/hours/add`, `PATCH instructor/<uid>/hours/<instructor_hours_id>/update`, `DELETE instructor/<uid>/hours/<instructor_hours_id>/delete` and `POST instructor/<uid>/hours/calculate` (function views in `BE/application/views/instructor_views.py`) are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/instructor_hours*.go`). Registry `association.instructor.hours.read` (list, calculate), `.create`, `.update` and `.delete`.

- Every view starts with `is_valid_uuid` on the path ids: 400 `["badly formed hexadecimal UUID string"]`. `Allow` is the decorated method plus OPTIONS; OPTIONS has no `actions`.
- `list` filters `InstructorHours` by `instructor__user=request.user` and the uid, ordered by `-date, -creation_date`.
  - `query[date_range]` `'A al B'` is parsed as YYYY/MM/DD, else DD/MM/YYYY, else the last 365 days to tomorrow (`datetime.now()`, UTC). A value without exactly one separator is a 500. An empty value is ignored.
  - `query[generalSearch]` is `icontains` on date, hours, amount and notes (`UPPER(col::text) LIKE`, pattern escaped; an empty value matches every row).
  - `KTDatatablePagination`: `pagination[page]` (`last`; out of range 404 `Invalid page.`) and `pagination[perpage]` (default 10). The response is `{'data': [...], 'meta': {total, page, pages, perpage, rowIds}}`.
  - For each row of the page, a paid payment marks the hour paid (`Model.save()`; auditlog records `paid` only when it changes). A document without a payment is deleted. Django's collector nulls the SET_NULL references, the Document delete is audited, and the response still renders the in-memory document id and token.
- `add` looks the instructor up by id and `user=request.user` (404 `{'error': 'instructor not found'}` for an unknown or another owner's instructor, and for athletes) after reading `request.data` (a parse error is a 400 first). `data['instructor'] = uid` makes a form body (an immutable `QueryDict`), a list or a `null` body a 500. It then saves `InstructorHoursSerializer` (fields `'__all__'`) and answers `{'msg': 'hours added.'}`.
- `update` and `delete` look up the instructor by id and `user=request.user`, then the hour by instructor and id (404 `{'error': 'hours not found'}`), all before the body is read.
  - `update` is a partial serializer save (`{'success': True}`). A `null` body is `{'non_field_errors': ['No data provided']}` and a non-dict body DRF's dictionary error. A new `instructor_hours_id` makes `save()` insert a copy under the new key. auditlog's `pre_save` finds no row and logs the full diff as an update, then the create.
  - `delete` deletes the hour's document first. The hour's delete diff keeps the in-memory document id. The answer is `{'msg': 'hours deleted.'}`.
- `InstructorHoursSerializer` validates these fields:
  - a writable `instructor_hours_id` (UniqueValidator `instructor hours with this instructor hours id already exists.`);
  - `creation_date` (ISO, rendered UTC) and `date` (DATE_INPUT_FORMATS `['%d/%m/%Y']`);
  - `compensation_type` (15, not blank), `period` (50, nullable, blank) and `notes` (nullable, blank);
  - the decimals `hours` (4, 2; required), `hourly_billing` and `amount` (9, 2) and `percentage_billing` (4, 2; a blank value is None);
  - `courses` and `calculation_data` (JSONField; HTML input must be valid JSON);
  - `paid`, and the relations `instructor`, `payment` (`Payment.objects`: live, group-aware) and `document`.
  A relation to another tenant's payment, or to another association's instructor (resolved through the instructor's owner, `instructor_resolver`; an owner without an association passes), is DRF's generic 403, which escapes before the field errors. Empty HTML values follow DRF's `get_value` rules.
- auditlog reads the in-memory instance on create, so model defaults the serializer did not provide are recorded as the float `0.0` for the decimals and as `str(timezone.now())` for `date`. The stored row holds `0.00` and the UTC date. An hour's create and update entries resolve the stored hour when the entry is written (auditlog writes the update entry before the save, so the previous instructor) through its instructor's owner's association (`instructor.user.sport_association`). Delete entries, the update entry of a copy under a new key (no row yet), document entries and hours whose instructor's owner has no association fall back to the actor's association (`actor.sport_association`); a collaborator or athlete actor then has no index row.
- `calculate` reads `period`, `courses` and `percentage` from `request.data` (a non-dict body is a 500). Its answers:
  - missing or falsy `courses`: 400 `{'error': 'courses is required'}`;
  - unknown or another owner's instructor (`user=request.user`): 404;
  - anything but a list of dicts with `course_id`: 400 `{'error': 'Invalid courses format'}`;
  - a `course_id` the UUID field rejects: 500 at `filter()`;
  - missing or empty period: 400 `{'msg': 'Periodo di pagamento non valido'}`;
  - a period without exactly one `' al '`: 400 `Invalid period format`;
  - unparsable dates: 400 `Invalid date format`. A start that parses as YYYY/MM/DD with an end that does not is a 500 (the second `strptime()` gets a datetime);
  - a non-string period: 500;
  - a non-float `percentage`: 400 `Invalid percentage`; outside 0–100 (including inf): 400. NaN passes the check and the strict JSON renderer then answers 500.

  The courses come from `Course.objects` (live, group-aware) of `request.user`'s association (other ids are ignored), their subscriptions from `CourseSubscription.objects` (live). The view then sums paid payments in the inclusive period:
  - membership courses: `membership_payments` (live, group-aware);
  - multi-payment courses: installments (live, group-aware) joined to paid payments of any state;
  - other courses: the subscription's payment through the forward relation (any state), when amount > 0.

  `amount` is an int 0 without entries, else the float of the Decimal sum, times `percentage / 100` when given. Entry order is not fixed by the view.
- Requests are not atomic in Django; Go commits each request's writes in one transaction.

## Not ported (refused before any write)

- A `list` page row with a payment and no document (the loop would print the compensation document with `print_document_compensation`): 500. Django would have saved the page's earlier rows first.
- A document delete (`list` or `delete`) that Django's collector would cascade beyond the SET_NULL references, and a `list` page where two document-only rows share a document: 500.
- HTML JSON input with NaN or Infinity (the JSONField write fails in Django too): 500.
- `instructor/<uid>/hours/add/compensation` (payment creation plus synchronous PDF printing) and `instructor/lessons-hours`, `instructor/<uid>/lessons-hours` (calendar lesson computation) stay on Django.

## Evidence

`TESTS/fixtures/instructor_hours_http.json` (132 cases, generated by `TESTS/fixtures/generate_instructor_hours_http.py`).

- RED (`native-r5`): Django 127/127; Go 404 on the first case.
- GREEN (`versioned-r2`): 127/127 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact hour, document, audit and index effects.
- Mutation (`mutation-r1`): dropping `NOT deleted` from the related payment lookup fails Go on `instructor-hours-add-removed-payment` (CAUGHT).
- `instructor-info` and `instructor-list` re-run green natively (shared `main.go` route block).

Instructor lookups of `request.user` (add, calculate), calculate's courses of the association, the instructor relation's tenant scope and the index through the instructor's owner:

- RED (`red-sync1`): Django 132/132; Go fails `list-owner-syncs-paid` (index resolved through the actor).
- GREEN (`versioned-sync1`): 132/132 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact effects.
- Mutation (`mutation-sync2`): dropping the `user` filter from the instructor lookup fails `add-other-association-instructor` (403 instead of 404, CAUGHT).
