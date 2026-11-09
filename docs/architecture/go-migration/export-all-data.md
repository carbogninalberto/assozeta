# export-all-data — the association data workbook

`export_all_data` (`@api_view(['POST'])`, `application/views/profile_views.py`) runs in Go: `POST /export-all-data`
(listed in the registry's `EXCLUDED_ENDPOINTS`). Go: `domain/export_all_data.go`, `repository/export_all_data.go`,
`service/export_all_data.go`, `handler/export_all_data.go`, `xlsxexport.OpenpyxlRowsResponse`.

## Behaviour

- The view never reads `request.data`. The first association access decides the identity outcome:
  `request.user.sport_association` raises `PermissionDenied` for athletes (403 `{'detail': 'User is not a sport
  association'}`) and `SportAssociation.objects` (live rows only) `.get(user=...)` raises without exactly one live
  association (500).
- Sheet `Iscrizioni`: `scoped_queryset(Subscription.objects)` (the group-aware manager) of the association with
  `select_related('associate', 'medical')`; headers are the concrete field names of `Subscription`,
  `MedicalCertificate`, `Associate` (`associate__`) and `Associate` again (`tutor__`) minus `EXCLUDE_HEADERS`.
  `status_flag`, `type` and `role` are the choice labels at index `value - 1` (Python indexing: 0 is the last label;
  out of range raises, 500). Without a certificate the medical columns are `-`; a subscription without an associate
  raises (500). Each tutor of `associate.tutors` (the M2M with Associate's default manager: live and group-aware)
  appends its columns.
- Sheet `Pagamenti`: `scoped_queryset(Payment.objects)` (live, group-aware) with six `select_related` relations:
  the payment's fields (`subject` as `SUBJECT[value]`, `type` as its `TYPE_CHOICES` label or `-`), `course_name`
  (course payments: the scoped live course subscription's course title, else the scoped live group-aware installment's,
  else `course['label']`; `.get` with several rows raises), then the `payment_category__`, `custom_accounts__`
  (`account_type` as its label), `associate__`, `supplier__`, `instructor__` and `invoice__` columns (`-` each without
  the relation).
- Every other value is `str(value)` or `-` for `None`: dates `YYYY-MM-DD`, aware UTC datetimes
  `YYYY-MM-DD HH:MM:SS[.ffffff]+00:00`, booleans, Decimals and ints as stored, UUIDs, JSON values as `str()` of the
  decoded jsonb value (keys in jsonb order), foreign keys `<Model> object (<pk>)`.
- `wb.save(HttpResponse)`: zipfile cannot seek the response, so every entry has general purpose flag `0x08` and a data
  descriptor. `xlsxexport.OpenpyxlRowsResponse` writes that layout and `TESTS/contracts/xlsx.py` builds it with the
  opt-in `'stream': True` spec key. Headers: `Content-Type` xlsx, `Content-Disposition: attachment;
  filename=export_<denomination>.xlsx` (Django's ISO-8859-1 or RFC 2047 header bytes).
- The querysets have no ordering: Go keeps Django's joins and predicates (same plans, same database order).

## Not ported

- `Content-Disposition` values Django's header handling rejects (line breaks and other control characters in the
  denomination) answer the 500 page.
- Workbook inputs `xlsxexport` refuses (`ErrNotPorted`): the 500 page.

## Evidence

`TESTS/fixtures/generate_export_all_data_http.py` authors 17 cases (the adapter analyzes the seeded tables so every
transport plans the unordered querysets alike). RED `native-r12` (Django 17/17, Go 404), GREEN `versioned-r2` (17/17 on
native Django, native Go, Caddy `/api/v1` and `/api/v2`), mutation `mutation-r2`: keeping tutors without a group under
an `X-Group-Id` header fails `export-all-data-group`. `go test ./internal/xlsxexport` covers the streamed writer
(`TestOpenpyxlRowsResponse`) and the golden workbooks.
