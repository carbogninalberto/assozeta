# printing/generate — report builders (excel)

`printing_generate` (`@api_view(['POST'])`, `application/views/printing_views.py`) and its eleven builders in
`application/utils/printing.py` run in Go for the `excel` format. Route: `POST /printing/generate` (registry key
`association.members.read`). Go: `domain/printing_generate.go`, `repository/printing_generate.go`,
`service/printing_generate.go`, `handler/printing_generate.go`.

## Behaviour

- The view logs `request.data.get(...)` first: a body without `.get` (JSON list, string, number, `null`) is the 500 page,
  athletes included. A malformed JSON body is DRF's 400 parse error.
- Athletes: `Response(status=403)` (empty body). Then `'type'` and `'format'` must be keys:
  400 `{'msg': 'Mancano alcuni parametri obbligatori:<type, format>'}`.
- Inside the try block every exception is 400 `{'msg': 'Errore nella generazione della stampa.'}`:
  `request.user.sport_association` (`SportAssociation.objects`, live rows only, exactly one) and any builder exception
  (the builders re-raise everything). An unknown `type` (any non-matching value) is 400 `{'msg': 'Tipo stampa non valido'}`.
- `filter_subscriptions`: the association's group-aware `Subscription.objects` rows with `archived=False`, member types
  `(3, 2)` and `medical_id IS NOT NULL` where the builder asks, `year` (`pre-iscrizioni`, `corrente`, `precedenti`
  against `timezone.now()`'s UTC date; other values add nothing) and `status` (`status_flag__in`). Request values keep
  Python semantics: `in` is a key test on a dict, an element test on a list, a substring test on a string (form bodies)
  and a `TypeError` otherwise; indexing a list or string by key raises. Status values go through
  `IntegerField.get_prep_value` (`int()`: strings with whitespace and underscores, floats truncated, booleans; anything
  else raises), dicts iterate their keys and strings their characters, `None` is dropped and an empty result matches
  nothing.
- Rows read `_get_phone_columns`: for minors (`int(days / 365.25) < 18`, 0 without a birth date)
  `Associate.main_tutor`, which promotes the first tutor relation (by primary key) when none is primary: an audited
  update (`is_primary` `False`→`True`, index through `associate.sport_association`). Promotions are autocommitted in
  Django; Go saves them in Python's order and keeps them when a later step raises (400).
- Builders: medical reports sort by days to expiry (stable), `expiring_memberships` filters
  `SubscriptionMembership` by `associate IN (<the subscriptions' associates>)` and `end_date <= today + 30`, the
  payment reports read `Payment.objects` (live, group-aware) with their filters and the optional
  `filters.period` (`strptime('%d/%m/%Y')`, bounds compared as UTC midnights: `<= to` keeps only rows at midnight of
  `to`), `expired_payments` sorts by delay (stable), `subscriptions_with_all_payments` reads the period inside its
  per-subscription loop (never without subscriptions) and falls back to one row per subscription without payments.
  `get_course_carnet_name` ports the course subscription / group-aware installment / carnet lookups, `meta.description`
  and `course.label`; `ACCOUNT_TRANSFER` raises (`CustomAccounts` has no `destination_account_name`).
- `excel`: `get_excel_base64(*columns, columns=...)` through `xlsxexport.PandasOpenpyxl`; `None` (for example an
  illegal control character) raises (400). Response `{'file': <base64>, 'filename': '[<dd/mm/YYYY>] <title> -
  <denomination>.xlsx'}`. Any other format but `pdf` raises after the rows (and their promotions).
- Querysets without ordering keep Django's joins and predicates in Go (same plans, same database order).

## Not ported

- The `pdf` format: `generate_pdf_from_data` renders in-process with reportlab (time-stamped, not byte-reproducible).
  Go answers the 500 page when a request would reach it, before any promotion is saved; requests that fail earlier
  (missing filters, bad status, …) keep their 400.
- Workbook inputs `xlsxexport` refuses (`ErrNotPorted`, see [xlsx-exports](xlsx-exports.md)) and integers beyond int64
  in a payment's `meta.description`: the 500 page before any write.

## Evidence

`TESTS/fixtures/generate_printing_generate_http.py` authors 91 cases (dates relative to the day of the run; the adapter
analyzes the seeded tables so every transport plans the unordered querysets alike). RED `native-r12` (Django 91/91,
Go 404), GREEN `versioned-r2` (91/91 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, exact effects),
mutation `mutation-r1`: sorting the medical reports by descending days fails
`printing-generate-expiring-medical`. Re-run after the `xlsxexport` change: payment-export, misc-exports,
modules-responses, instructor-writes (native, green).
