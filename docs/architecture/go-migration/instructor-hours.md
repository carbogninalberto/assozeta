# Instructor hours list, add, update, delete and calculate — implementing with HTTP parity

`GET instructor/<uid>/hours/list`, `POST instructor/<uid>/hours/add`, `PATCH instructor/<uid>/hours/<instructor_hours_id>/update`, `DELETE instructor/<uid>/hours/<instructor_hours_id>/delete` and `POST instructor/<uid>/hours/calculate` (function views in `BE/application/views/instructor_views.py`) are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/instructor_hours*.go`). Registry `association.instructor.hours.read` (list, calculate), `.create`, `.update` and `.delete`.

- Every view starts with `is_valid_uuid` on the path ids: 400 `["badly formed hexadecimal UUID string"]`. `Allow` is the decorated method plus OPTIONS; OPTIONS has no `actions`.
- `list` filters `InstructorHours` by `instructor__user=request.user` and the uid, ordered by `-date, -creation_date`.
  - `query[date_range]` `'A al B'` is parsed as YYYY/MM/DD, else DD/MM/YYYY, else the last 365 days to tomorrow (`datetime.now()`, UTC). A value without exactly one separator is a 500. An empty value is ignored.
  - `query[generalSearch]` is `icontains` on date, hours, amount and notes (`UPPER(col::text) LIKE`, pattern escaped; an empty value matches every row).
  - `KTDatatablePagination`: `pagination[page]` (`last`; out of range 404 `Invalid page.`) and `pagination[perpage]` (default 10). The response is `{'data': [...], 'meta': {total, page, pages, perpage, rowIds}}`.
  - For each row of the page, a paid payment marks the hour paid (`Model.save()`; auditlog records `paid` only when it changes). A document without a payment is deleted. Django's collector nulls the SET_NULL references, the Document delete is audited, and the response still renders the in-memory document id and token.
- `add` looks the instructor up by id and `user=request.user` (404 `{'error': 'instructor not found'}` for an unknown or another owner's instructor, and for athletes) after reading `request.data` (a parse error is a 400 first). `data['instructor'] = uid` makes a form body (an immutable `QueryDict`), a list or a `null` body a 500. It then saves `InstructorHoursSerializer` (fields `'__all__'`) and answers `{'msg': 'hours added.'}`. t5pk: `instructor_hours_id` is read-only, so an add stores a generated key whatever the body holds.
- `update` and `delete` look up the instructor by id and `user=request.user`, then the hour by instructor and id (404 `{'error': 'hours not found'}`), all before the body is read.
  - `update` is a partial serializer save (`{'success': True}`). A `null` body is `{'non_field_errors': ['No data provided']}` and a non-dict body DRF's dictionary error. t5pk: `instructor_hours_id` is read-only, so a key in the body (an unused one, another hour's, the path hour's in any case) is ignored and the hour the path names is saved in place (`update-new-key-ignored`, `update-existing-key-ignored`, `update-own-key-ignored`); a body holding only the key changes nothing and writes no audit entry.
  - `delete` deletes the hour's document first. The hour's delete diff keeps the in-memory document id. The answer is `{'msg': 'hours deleted.'}`.
- `InstructorHoursSerializer` validates these fields:
  - a read-only `instructor_hours_id`: a key in the body is ignored (the add stores a generated key, `add-existing-key-ignored` and `add-malformed-key-ignored`);
  - `creation_date` (ISO, rendered UTC) and `date` (DATE_INPUT_FORMATS `['%d/%m/%Y']`);
  - `compensation_type` (15, not blank), `period` (50, nullable, blank) and `notes` (nullable, blank);
  - the decimals `hours` (4, 2; required), `hourly_billing` and `amount` (9, 2) and `percentage_billing` (4, 2; a blank value is None);
  - `courses` and `calculation_data` (JSONField; HTML input must be valid JSON; text nested past 500 levels is ported, see the measured recursion bands below);
  - `paid`, and the relations `instructor`, `payment` (`Payment.objects`: live, group-aware) and `document`.
  A relation to another tenant's payment, or to another association's instructor (resolved through the instructor's owner, `instructor_resolver`; an owner without an association passes), is DRF's generic 403, which escapes before the field errors. Empty HTML values follow DRF's `get_value` rules.
- auditlog reads the in-memory instance on create, so model defaults the serializer did not provide are recorded as the float `0.0` for the decimals and as `str(timezone.now())` for `date`. The stored row holds `0.00` and the UTC date. An hour's create and update entries resolve the stored hour when the entry is written (auditlog writes the update entry before the save, so the previous instructor) through its instructor's owner's association (`instructor.user.sport_association`). Delete entries, document entries and hours whose instructor's owner has no association fall back to the actor's association (`actor.sport_association`); a collaborator or athlete actor then has no index row.
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

- `list` page rows with a payment and no document run `print_document_compensation.apply(args=[payment_id, Authorization])`
  (invoice-sync-print's eager runner: `GET CURRENT_HOST/document/compensation/<payment>`), in loop order. The response renders
  the in-memory rows, so those rows keep `document: null`. Go commits the loop's saves first and then runs the prints in the
  same order (Django interleaves each print after its own row's save; the printed document does not read the other rows).

## Not ported (refused before any write)

- A document delete (`list` or `delete`) that Django's collector would cascade beyond the SET_NULL references is ported in t4l2: the shared deletion collector deletes the dependants (instructor document links, cascaded rows) with their audit entries (cases `delete-document-with-link`, `list-orphan-document-with-link`).
- A `list` page where two document-only rows share a document is ported: the first row deletes the document (SET_NULL on both rows), the second loads it again and fails with `DoesNotExist` (500), the earlier rows' writes and prints are kept. Case `list-rows-sharing-a-document-server-error`: 136 cases, receipt `instructor-hours-http-sweep-restdocs-3` (GitHub run 42910943209), 4 transports. Mutation (the second row deleting again) CAUGHT.
- HTML JSON input with NaN or Infinity (the JSONField write fails in Django too): 500.
- `instructor/<uid>/hours/add/compensation` (payment creation plus synchronous PDF printing) and `instructor/lessons-hours`, `instructor/<uid>/lessons-hours` (calendar lesson computation) stay on Django.

Since long-tail PR 3 (agent B) the printer the server always wires is application state (`ErrServerComposition`, the
500 page, never handed off), and `ErrInstructorHoursNotPorted` is deleted. The document deletes and the shared
document page are ported (t4l2); the HTML JSON with NaN or Infinity answers the 500 page without a handoff and the
routes left on Django stay refusals.

## Measured form JSON recursion bands (owner decision X15)

The form path of `courses` and `calculation_data` reads text nested far past the old 500-level refusal. The bands were
measured on the parity harness's Django (CPython 3.13.16, default recursion limit) with an HTML form PATCH whose field is
`{"deep": <n nested objects>}` or `[<n nested objects>]`; D is `auditJSONDepth` of the text (n + 1 for those shapes).

- D <= 9938: 200, one audit update entry, the row updated (the UPDATE's `json.dumps` walks the parsed value).
- D 9939..9941: the 500 page. auditlog's `pre_save` entry is written before the UPDATE's `json.dumps` raises and the view
  is not atomic, so the entry (with the changes as Django writes them) is kept and the row is unchanged. Go keeps both
  writes in the request's transaction and skips `UpdateHour` in this band.
- D >= 9942: DRF's `json.loads` RecursionError during validation: the 500 page before any write, even with another field
  invalid (`hours=abc` answers the 400 up to D 9941).
- A duplicate key overwrites its deep value, so the text can be past a band while the parsed value (what `json.dumps`
  walks) is shallow: `{"a": <9900 levels>, "a": 1}` stores `{"a": 1}`. The band that binds there is the value's
  `JSONValueDepth`, not the text's.
- The add view assigns `data['instructor']` on `request.data`, so a form body is already the 500 page there (unchanged);
  only the update's form path reads form JSON text.
- Cases `update-form-courses-depth-9960-server-error`, `update-form-courses-depth-9960-other-field-invalid` and
  `update-form-courses-depth-9900-repeated-key`. A stored value in the 9939..9941 band cannot be read back by the state
  snapshot, so that band is pinned by the Go tests (`TestInstructorHoursUpdateJSONBand`).

`dto.ErrAuditNotPorted` is deleted: the two 500-level readers without a measured band (`AuditJSONLoads`,
`auditPythonJSONLoads`) have no production caller any more and answer `ErrAuditUnreachable` (application state, the 500
page) instead of a handoff.

## Evidence

`TESTS/fixtures/instructor_hours_http.json` (142 cases, generated by `TESTS/fixtures/generate_instructor_hours_http.py`).
The measured form JSON recursion bands (X15) add `update-form-courses-depth-9960-server-error`,
`update-form-courses-depth-9960-other-field-invalid` and `update-form-courses-depth-9900-repeated-key`, which the harness
sends through `raw_parts` repeat items.

- RED (`native-r5`): Django 127/127; Go 404 on the first case.
- GREEN (`versioned-r2`): 127/127 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact hour, document, audit and index effects.
- Mutation (`mutation-r1`): dropping `NOT deleted` from the related payment lookup fails Go on `instructor-hours-add-removed-payment` (CAUGHT).
- `instructor-info` and `instructor-list` re-run green natively (shared `main.go` route block).

Instructor lookups of `request.user` (add, calculate), calculate's courses of the association, the instructor relation's tenant scope and the index through the instructor's owner:

- RED (`red-sync1`): Django 132/132; Go fails `list-owner-syncs-paid` (index resolved through the actor).
- GREEN (`versioned-sync1`): 132/132 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact effects.
- Mutation (`mutation-sync2`): dropping the `user` filter from the instructor lookup fails `add-other-association-instructor` (403 instead of 404, CAUGHT).

Compensation prints of list rows (API v2 gap): 135 cases; the adapter runs invoice-sync-print's CURRENT_HOST stub for the whole run
and checks each case's outbound requests (`case['prints']`).

- RED (`v2doc-red-r1`): Django 135/135; Go 500 on `instructor-hours-list-owner-prints-compensations`.
- GREEN (`v2doc-versioned-r2`, after merging the current base): 135/135 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`v2doc-mutation-r1`): printing only paid rows fails `instructor-hours-list-owner-prints-compensations` (`print.request-count`, CAUGHT).
