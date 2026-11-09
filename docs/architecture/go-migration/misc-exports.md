# Subscription and invoice list exports (CSV) — implementing with HTTP parity

`GET`/`POST subscription/list/export` (`subscription_list_export` with `_get_export_columns`, `_build_simplified_row`,
`_build_full_row` and `_export_to_csv` in `BE/application/views/subscriptions_views.py`) and `GET invoice/list/export`
(`invoice_list_export` in `BE/application/views/invoice_views.py`) are implemented in Go for their CSV outputs and the invoice
zip dispatch (`BE_GO/internal/{domain,repository,service,handler}/misc_exports.go`). Registry: `association.members.read` and
`bookeeping.documents.invoices.read`. Django remains the execution owner.

## Shared behaviour

- Function views without docstrings: OPTIONS names `Subscription List Export` / `Invoice List Export` with an empty
  description; Allow is `GET, POST, OPTIONS` / `GET, OPTIONS`. An association-scoped (owner to athlete) session gets the
  registry's `403 Operazione non disponibile nel contesto dell’associazione.`.
- Athletes (`request.user.role`) raise (500); then the scoped live association (`SportAssociation.objects.get`), 500 without one.
- The filename's date is `date.today()` (UTC); the denomination is the association's (`None` rendered as `None`).
- CSV: `csv.writer` (excel dialect, quoting only for `,`, `"`, CR or LF, CRLF line ends), returned base64 in the JSON body.

## subscription/list/export

- `filetype` (query string, also for POST; default `csv`): containing `simplified` selects the simplified row, otherwise the
  full row. The filename always ends in `.csv` for the ported modes.
- Rows: `scoped_queryset(Subscription.objects)` — `X-Group-Id`-aware (a header the UUID field rejects is ignored), the manager keeps
  deleted rows — of the association with `archived=False`, ordered by `-creation_date`; the associate and medical certificate
  are joined (`select_related`), the tags are the prefetch join's names in its order,
  joined by `, `.
- Values: `STATUS_DICT` of `status_flag` (1–5; another value is a 500), `si`/`no` for a certificate, its expiration date,
  `_get_medical_status` (`valido` when the expiration is today or later, else `esente` for minors and `scaduto`), `si`/`no` for
  a non-empty signature key or URL, dates as `%Y-%m-%d` (the creation date in UTC), `None` text as empty, subscription number
  and type empty when falsy.
- Full rows add the associate's 18 personal columns and the same columns of `Associate.main_tutor`: the primary tutor relation's
  tutor, else the first relation (by key), which `get_main_tutor` promotes (`is_primary=True`, `save()`): one auditlog update
  `{'is_primary': ['False', 'True']}` indexed through `associate.sport_association` (the actor's association when the associate
  has none). Each promotion is autocommitted, so a later failing row keeps it; the next row of the same associate finds it
  primary. Simplified exports never promote.
- A subscription without an associate raises (500) at its row: in a full export after the earlier rows' promotions.
- With zero rows, `pandas.DataFrame([]).columns = cols` raises a length mismatch (500).
- Response `{'data': {'filename': '[<today>] ISCRIZIONI a <denomination>.csv', 'type': 'csv', 'file': <base64>}}`.

## invoice/list/export

- `archived` (raw query value) goes through `BooleanField.to_python` (`t`, `True`, `1`, `f`, `False`, `0`; anything else, an
  empty value included, is a 500); default False.
- Invoices: `scoped_queryset(Invoice.objects)` (live, group-aware) of the association, ordered by `creation_date`. Each
  invoice's payment is `payment_set.first()` over the prefetch queryset (`scoped_queryset(Payment.objects)`: live,
  group-aware): Payment has no default ordering, so `first()` runs a query ordered by primary key.
- `m=csv` (default): header `data, nome, cognome, quota associativa, quota attivita, numero, modalità di pagamento`; per invoice
  the creation date (UTC), the payment associate's first and last names (empty without a payment or an associate), the fees
  (Decimal text), the number (empty when None), and `-` for a `default` payment type or no payment, otherwise the type.
  Response `{'data': {'file': <base64>, 'type': 'csv', 'filename': '[<today>] RICEVUTE a <denomination>.csv'}}`.
- `m=files`: `export_invoices_to_zip.delay([<document_pdf ids as str>, ...], <association UUID>)` for the listed invoices with a
  document, in creation-date order (an empty list included), published through the [Celery producer](celery-producer.md) after
  the reads (no write precedes it); a broker failure is a 500. Response
  `{'data': {'file': '', 'message': 'Export dei file in corso, controlla la sezione Documenti e Modelli tra qualche minuto.'}}`.
  Django's worker builds the zip.
- Any other `m` (except `xlsx`): `{'data': {'file': ''}}` after the same lookups.

## Not ported (refused with the 500 page before any lookup or write)

- `subscription/list/export` with `filetype` containing `xls` and `invoice/list/export` with `m=xlsx`: the pandas/xlsxwriter
  and `get_excel_base64` workbooks are not byte-reproducible (document properties and zip entries carry the generation time).

Not authored: the spreadsheet modes above, unknown subscription status flags, association-scoped sessions of non-athlete users,
equal subscription creation dates or invoice creation dates (Django leaves their order to the database).

## Evidence (TDD)

`TESTS/fixtures/misc_exports_http.json` (57 cases, generated by `TESTS/fixtures/generate_misc_exports_http.py`, whose CSVs come
from an independent model of the Django semantics; contract `TESTS/contracts/test_misc_exports_http.py`, which compares the
base64 of the exact CSV bytes and accepts the server's UTC date in the filename; adapter
`TESTS/adapters/django/misc_exports_http.py`, which checks the tutor promotions with their audit and index rows and every
Celery message on the run's private broker).

- RED (`native-r1`): Django 57/57; Go 404 on the first case, before any Go code.
- GREEN (`versioned-r2`, on the tree that also holds misc-routes): 57/57 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact effects.
- Mutation (`mutation-r1`): taking each invoice's payment with the highest key instead of the lowest fails
  `misc-exports-inv-owner` (CAUGHT).
- Shared code: none changed (new files and a route block in `main.go`).
