# Payment list export (CSV and XLSX) — implementing with HTTP parity

`GET payment/list/export` (`payment_list_export` in `BE/application/views/payment_views.py`, with
`CustomAccountSerializer.get_current_balance_from` in `BE/application/serializers/balance_sheet.py` and `smart_search` in
`BE/application/utils/subscriptions_utils.py`) is implemented in Go for its CSV and XLSX outputs
(`BE_GO/internal/{domain,repository,service,handler}/payment_export.go`). Registry: `bookeeping.payments.read`. It reuses
payment-reads' filter query (`paymentReadsQuery`), range parsing (`paymentRange`), filter parsers, smart-search word
preparation and the reads dispatch (`PaymentReadsHandler.serve`). Django remains the execution owner.

`invoice/list/export` and `subscription/list/export` do not share an export helper with this view (each builds its own
rows; the subscription export goes through pandas) and are not part of this family.

## Behaviour

- `@api_view(['GET'])`: Allow `GET, OPTIONS`; OPTIONS names `Payment List Export` with an empty description. An
  association-scoped (owner to athlete) session answers the registry's `403 Operazione non disponibile nel contesto
  dell’associazione.`.
- Athletes (`request.user.role`) raise (500); then the scoped live association (`SportAssociation.objects.get`), 500
  without one.
- Window: `datetime.now()` minus 3650 days up to now, or `query[payment_range]` when present: `split(' al ')` must give two
  parts (otherwise, an empty value included, the unpacking ValueError is a 500); `%Y/%m/%d`, else `%d/%m/%Y`; a pair that
  parses with neither (a mixed pair included) falls back to the last 365 days up to tomorrow. The end then moves one day
  on and to 00:00 (UTC). Both ends are inclusive, on the payment date, or the creation date when the payment has none.
- Queryset: the scoped, live, `X-Group-Id`-aware payments of the association with `archived=` the raw `archived` value
  (BooleanField `to_python`: `t`, `True`, `1`, `f`, `False`, `0`, anything else a 500; default False), ordered by
  `-payment_date`.
- `export_type=petty_cash_book` forces `paid=True` and ignores `query[expense]` and the general search. Otherwise
  `query[paid]` and `query[expense]` apply when present (`== 'true'`). `query[payment_categories]` and `query[account]`
  apply when truthy (`[]`: no category / account; malformed UUIDs are 500s).
- `query[generalSearch]` (truthy): a `d/m/Y` date becomes `Y-m-d`; a `float()` (`,` as `.`) filters the amount
  (`amount::text` icontains `str(float)` or equality at 9 digits) inside a `try`, so a NaN falls back to `smart_search`;
  anything else is payment-reads' `smart_search` (distinct, no relevance ordering).
- Petty cash book: each of the association's account transfers dated in the window adds an outgoing and an incoming pseudo
  payment (`Trasferimento da <from> a <to>`, subject 3, type default, paid, the transfer's date); a transfer without one of
  its accounts is a 500.
- Rows sort stably by payment date; a row without one takes `timezone.now()`.
- Petty cash book progressive balance: each account's `get_current_balance_from(1970-01-01, window start)` — initial
  balance, plus transfers to and minus transfers from dated in that span, plus the live, group-aware paid payments of the
  account with a payment date in that span (expenses negative), exact Decimals, then `float()`. Each paid row with an
  account adds `float(amount)` (negated for expenses) and appends the running value (Python float repr); unpaid rows append
  `-`; paid rows without an account append nothing. Django computes the balances for `CustomAccounts.objects.all()`; Go
  computes them for the accounts the rows reference (the only ones read).
- Users: the associate's `'{} {}'.format(first_name, last_name)` (`None` for a missing name), else the instructor's, else
  the supplier's name, else the description. Activities: subject 0 the category name or `Altro`, 1 `Iscrizione`, 2
  `Corso`, 3 `Giroconto`; other subjects append nothing.
- CSV (`csv.writer`, excel dialect: `,`, `"` doubled, quoted only with `,`, `"`, CR or LF, CRLF line ends): the header,
  plus `Saldo progressivo` for the petty cash book; the rows zip the payments with the users, activities and (petty cash
  book) progressive balances, so the shortest list sets the row count and the shorter lists shift onto later payments.
  Row values: user, `Pagato`/`In Attesa`, type (`-` for default), creation and payment dates (`%Y-%m-%d`, UTC), invoice
  number, activity, the additional categories (`meta_payment_categories` when truthy: every item must be an object with
  `payment_category_id`, otherwise a 500; ids naming one of the association's or the global categories are joined by
  `,`), the category name or `-` (not in petty cash book rows, whose header still has `Causale`), the account name (`-`
  without one; the petty cash book reads it directly, so a rendered row without an account is a 500), `Uscita`/`Entrata`,
  the course, the amount (Decimal text), the notes, and the progressive balance.
- Course: the payment's course subscriptions through `all_objects()` (any state, `scoped_queryset`) first by key, its
  course's title; else its installments through `all_objects()` (any state, group-aware) first by key, the course
  subscription's course title; else the payment's `course` JSON when not None and not `''`: an object's `label` (str()
  of the value, `None` empty), an object without it or a string/list without `label` empty; a string containing `label`,
  a list holding `"label"`, a number or a bool raise (500).
- Response `{'data': {'file': base64 CSV, 'type': 'csv', 'filename': '[<today UTC>] PAGAMENTI a <denomination>.csv'}}`.
  `m` other than `xlsx` is CSV.
- `m=xlsx` ([xlsx-exports](xlsx-exports.md)): `get_excel_base64` over the column lists (zip truncates them to the shortest, so
  activities shift as in the CSV): the user (None kept), status, type, dates, the invoice number (an int, None without one, `''`
  without an invoice), the activities, the additional categories (`'payment_category_id' in mpc` first: a str meta yields
  nothing, a dict meta raises when a key contains it, a list item raises when it is a number, bool or None, or a str or list
  containing the key), the category name or `-`, the account name or `-` (also in the petty cash book, so its CSV 500 does not
  happen), the flow, the course (the label as its Python value), the amount (Decimal), the notes (None kept) and, for the petty
  cash book, the progressive balances (floats or `-`). Response `{'data': {'file': base64, 'type': 'xlsx', 'filename':
  '[<today>] PAGAMENTI a <denomination>.xlsx'}}`.

## Not ported (refused with the 500 page)

- `m=xlsx` inputs the writer refuses ([xlsx-exports](xlsx-exports.md)), and course labels that are bools, lists, objects or
  integers beyond int64.
- Infinite float search terms and smart-search terms with characters Python lower-cases differently (as payment-reads).
- A course JSON `label` that is a list or an object (its Python repr) or a non-finite number.

Not authored: association-scoped sessions of non-athlete users (the scoped course subscription and installment
filters are ported from `scoped_queryset`), additional categories that raise, equal payment dates (Django leaves their order
to the database), more than one payment without a payment date in one export.

## Evidence (TDD)

`TESTS/fixtures/payment_export_http.json` (72 cases, generated by `TESTS/fixtures/generate_payment_export_http.py`, whose
CSVs come from an independent model of the Django semantics; contract `TESTS/contracts/test_payment_export_http.py`, which
compares the base64 of the exact CSV bytes and accepts the server's UTC date in the filename; adapter
`TESTS/adapters/django/payment_export_http.py`). The fallback-window cases hold while no seeded payment or transfer (2024–
2025) is within a year of the run date.

- RED (`native-r4`): Django 72/72; Go 404 on the first case, before any Go code.
- GREEN (`versioned-r1`): 72/72 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with the model state unchanged.
- Mutation (`mutation-r1`): counting soft-deleted payments in the petty cash book's opening balances fails
  `payment-export-petty-spring` (CAUGHT).
- Shared code: none changed (new files and a route block in `main.go`).
- XLSX (`m=xlsx`, 15 more cases, 87 in all; workbooks compared with `TESTS/contracts/xlsx.py`): RED `xlsx-red-r1` (Django 87/87,
  Go 500 on `payment-export-xlsx`), GREEN `xlsx-versioned-r1` on all four transports, mutation `xlsx-mutation-r2` ('' for a
  payment without an account fails `payment-export-xlsx`, CAUGHT).
