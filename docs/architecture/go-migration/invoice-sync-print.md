# Invoice sync print — invoice/<uid>/update and the valid invoice-customers/add, with their eager print tasks

`PATCH invoice/<uid>/update` (`invoice_update`) and `POST invoice-customers/add` (`invoice_customers_add`, both in
`BE/application/views/invoice_views.py`) are implemented in Go: `BE_GO/internal/{domain,repository,service,handler}/invoice_sync_print.go`,
`service/invoice_sync_print_customer.go`, `dto/invoice_sync_print.go`, on top of invoice-parties (the add's validation),
invoice-generate (`generate_invoice_description`, `Payment.save()` defaults), invoice-core (`InvoiceSerializer`) and invoice-views
(`internal/fatturapa`). Registry: `bookeeping.documents.invoices.update` and `bookeeping.documents.clientinvoices.create`. Django remains
the execution owner.

## The eager print tasks

Both views end with `<task>.apply(args=[str(id), request.headers.get('authorization')])`. The app's task class is `CoordinatedTask`
(`BE/instance/restore/task_base.py`), so the eager call runs its `__call__` (`InvoiceSyncPrint.apply`):

- with `DATA_RESTORE_LOCK_PATH` set, the shared operation lock is taken (`JobRestoreAdmission`, the same flock); a pending restore or a
  busy/unavailable lock is `Busy`, which defers the task (see "Not ported");
- an eager call has no `assozeta_published_at` header, so any completed restore (`completed_at` set) raises `StaleDataTask`; eager
  `apply()` keeps the exception in its result: no request, the view answers normally. Any other exception in the task is kept the same way;
- otherwise the task body: `requests.get(f"{CURRENT_HOST}/document/invoice/<id>?send_receipt_email=False")` (update) or
  `.../document/einvoice/<id>` (add) with `{'Authorization': <raw header>}`. requests' defaults are sent as they are
  (`User-Agent: python-requests/2.34.2`, `Accept-Encoding: gzip, deflate`, `Accept: */*`, `Connection: keep-alive`); a missing header is
  dropped, redirects are followed (at most 30, no `Referer`), the body is read, there is no timeout and every error is swallowed.

Go reads `CURRENT_HOST` from the environment (Django's required setting). The rendering itself happens in the requested route
([document-render](document-render.md)), not in the task.

## invoice/<uid>/update

`@api_view(['PATCH'])`: `Allow: PATCH, OPTIONS`, OPTIONS names `Invoice Update` (no docstring, no actions). Then, without atomicity:

1. `is_valid_uuid(uid)` (400 list), `request.data.keys()` (a list or string body is a 500), a missing `number` (500), `cancelled`
   (default False), `payment_date` through `strptime('%Y-%m-%d')` (any error, a non-string included, is None);
2. `scoped_queryset(SportAssociation.objects).filter(user=...).first()` (None, for athletes and owners without one: 500), then the
   scoped, live, `X-Group-Id`-aware invoice of that association, not archived (None: 500);
3. `int(number)` (Python `int()`: strings with spaces, floats truncated, booleans; text, None, lists and `1e999` are a 500) and
   `bool(cancelled)` (Python truthiness: `'false'` is True, `0`, `[]`, `None` and `''` are False);
4. `selected_tutor` present: `UUIDField.to_python` of the filter value (a malformed or blank string, a float or a dict is a 500; None
   matches nothing; an int is `UUID(int=)`), then the first scoped, live, `X-Group-Id`-aware associate by id: when found it is set,
   otherwise the invoice keeps its tutor. Without the key the tutor becomes None. Outside impersonation the associate resolves by id;
5. the first scoped, live, `X-Group-Id`-aware payment of the invoice by primary key: a parsed `payment_date` is assigned as a naive
   midnight (stored as UTC midnight, audited as `YYYY-MM-DD 00:00:00`), `Payment.save()` applies its defaults (default category,
   `get_course_carnet_name()`, `Quota Corso`) and writes its auditlog entry (only changed fields, indexed `direct` on the payment's
   own association); then `generate_invoice_description(payment,
   association)` with the saved values, every subject (OTHER, subscription with period and membership, course subscription, installment,
   carnet, reason fallback, supplier or instructor without an associate, no party). A subject without a template (ACCOUNT_TRANSFER with
   an associate) or a carnet subscription without a carnet is a 500 **after** the payment save;
6. `invoice.save()`: the auditlog entry (number, cancelled, selected_tutor, description) is written before the UPDATE, so a number
   outside int32 or a description over 255 characters is a 500 with the entry kept (and the payment saved);
7. the print task, then `{'message': 'Invoice updated', 'invoice': InvoiceSerializer(invoice)}` (document token, attached documents).

## invoice-customers/add (valid bodies)

The validation is invoice-parties' (see [invoice-parties](invoice-parties.md)). A valid body is now saved:

1. `serializer.save()`: the row with the model defaults and the request values as DRF converted them (missing or null
   `transmitting_date`/`payment_expiry_date` are today); the create entry renders the **in-memory** instance: an absent
   `payment_total_amount` is the int default `0` (`'0'`), JSON fields are `json.dumps(sort_keys=True)` of the parsed request values;
2. `invoice.save()`: auditlog diffs the stored row against the in-memory instance, so an absent amount is audited `['0.00', '0']`, and a
   JSON value jsonb renders differently is audited too (`1e+20` reads back as `100000000000000000000`);
3. `generate_invoice_xml` and `generate_invoice_json` of the in-memory instance (`internal/fatturapa`); an exception there (a38's
   validation, for example a missing PEC) is `200 {'error': 'Errore durante la generazione del file XML.'}` with the row kept and no
   task; otherwise the third save writes `xml` and `json` (the JSON text stored as a jsonb string) with its entry (the same in-memory
   differences plus `xml` and `json`);
4. the print task, then `200 {'message': 'Invoice created'}`.

ProgressivoInvio is a38's `(int(time.time()) << 16) + sequence` in base 43, the sequence kept per process and reset every second;
Go keeps the same process-wide counter (`invoiceSyncNextProgressivo`), one value per generated output (the XML's, then the JSON's).

## Not ported (refused with the 500 page before any write unless noted)

- A restore that becomes pending (or a lock that becomes busy) between the request's admission and the task: Django re-applies the
  eager task synchronously until admission passes. Go answers the 500 page after the writes (`ErrInvoiceSyncPrintDeferred`).
- Update bodies with uploaded files (multipart file parts).
- Add dates before year 1000 (`strftime('%Y')` is unpadded on glibc) and FatturaPA inputs outside `internal/fatturapa`'s subset
  (`ErrUnsupported`, checked with a dry run before the insert).
- Not compared: the outbound header order (Go writes `Host`, `User-Agent`, then the rest sorted); redirects to another host follow Go's
  `Authorization` forwarding rule (same host or subdomain) rather than requests' (same hostname).

## Evidence (TDD)

`TESTS/fixtures/invoice_sync_print_http.json` (generator alongside) authors 70 cases. The adapter's `service_context` runs a private
CURRENT_HOST stub for the whole run (Django's `printing_tasks.CURRENT_HOST` and Go's `CURRENT_HOST` point at it); each case checks
the response, the complete model state (payments, invoices, customer invoices, auditlog entries with index rows) and the exact outbound
requests (method, path and query, headers as a set, Host, Authorization, no body), including a redirect and a failing reply. Cases with a
`restore` row insert an `instance.DataRestore` only while their request runs (completed: no request; review or completed without a date:
the request). Customer XML and JSON must equal `generate_invoice_xml`/`generate_invoice_json` of the authored in-memory values with
ProgressivoInvio normalized, each value decoded and checked: request time `<< 16` plus a sequence, the JSON's following the XML's.

- RED (`native-r3`): Django 70/70; Go 404 on the first case.
- GREEN (`versioned-r1`): 70/70 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): ignoring the completed-restore cutoff in `InvoiceSyncPrint.apply` fails Go on
  `invoice-sync-print-update-plain-restore-completed-no-print` (an outbound request despite the completed restore).
- `go test ./internal/service -run InvoiceSync`: request headers, redirect without Referer, lease release, stale/deferred admission,
  the ProgressivoInvio counter.
- Re-run for shared code: invoice-parties (`native-r1`, 136/136; service and handler).
