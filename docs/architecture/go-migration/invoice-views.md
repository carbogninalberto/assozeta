# Invoice views — the receipt page and the FatturaPA page, rendered in Go byte for byte

`document/invoice/<uid>/view/` and `document/einvoice/<uid>/view/` (`document_invoice_view` and `document_einvoice_view` in
`BE/docmanager/views/printing_views.py`, `generate_invoice_html` in `BE/application/serializers/invoice_serializers.py`) are
implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/invoice_views.go`, `service/invoice_views_einvoice.go`)
on top of `internal/djtemplate`, `internal/mentions` (document-views) and the new `internal/fatturapa`. Django remains the
execution owner. These are the pages the renderer prints for `document/invoice/<uid>` and `document/einvoice/<uid>`.

## Shared

Both views are `@api_view(['GET'])` with `IsAuthenticated | HasBypassAuthorizationHeader`: identity resolution first (admin
without a session, collaborator registry denials — no registry key, so the "No permission mapping" 403 — and the scoped reference
check on the path `uid`), then the permission (a non-ASCII bypass header is the 500 page; `?token` is not a permission here), then
method dispatch (`Allow: GET, OPTIONS`). The page is `text/html; charset=utf-8` in the request language; unhandled exceptions are
the stable 500 page. Nothing is written.

## document/invoice/<uid>/view/

1. `scoped_queryset(Invoice.objects).get(invoice_id=uid)`: live, `X-Group-Id` aware (a header the UUID field rejects is ignored),
   the impersonation boundary; a uid the field rejects or a missing row is the 500 page. Then the invoice's one live, group-aware,
   scoped payment (`.get(invoice=...)`: none or two are the 500 page).
2. `total_amount` is the Decimal sum of the fees; the payment type in Italian (`get_type_in_lang`, an unknown type is a KeyError).
3. An OTHER payment (subject 0) gets `invoice.description = generate_invoice_description(...)` in memory: reason = description,
   else the category name, else `Pagamento`; without an associate the supplier name or the instructor's full name (`... di <b>X</b>
   con causale ...`), else the OTHER template; with an associate the OTHER template with the category name (or the reason).
   `format_html` escapes the arguments (shared with invoice-generate).
4. `stamp_free` for SUBSCRIPTION/COURSE payments or a tax-deductible category. A COURSE payment without a category gets an in-memory
   `PaymentCategory(name=get_course_carnet_name())`: the first course subscription (all rows), installment (group-aware, all rows)
   or carnet subscription paid by the payment — the payment meta's `description` when present, else the title (a carnet
   subscription without a carnet is the 500 page) — else `"<course.label>, <description or ''>"`, else the description.
5. Truthy `meta_payment_categories` (anything but a list is the 500 page): with a category on a non-SUBSCRIPTION payment, the
   category's lowercased name maps to `float(activity_fee) - float(sum(amounts))` (Python `sum` from int 0: ints stay exact, a
   float switches to float; a string or null amount is the 500 page); then each item's category by primary key (all rows; a value
   the UUID field rejects or a missing row is the 500 page) as its lowercased name, `name (1)`, `name (2)`... for repeats, with the
   raw amount; `stamp_free` also needs every listed category to be tax-deductible.
6. A cancelled invoice: the line-through body style and the `RICEVUTA ANNULLATA` footer suffix.
7. With an associate: `tutor = invoice.selected_tutor or associate.main_tutor`, and `is_minor = True` when there is one (otherwise
   the stored field).
8. `get_subscription`: the first live group-aware installment's course subscription's subscription, the first group-aware
   subscription by payment, the first carnet subscription's, the first live course subscription's, the first camp period's (the
   first match decides, even when its subscription is NULL). The first `SubscriptionMembership` of that subscription — without a
   subscription, `filter(subscription=None).first()`, the first membership row without one. `get_period()` of both.
9. The plan block runs inside `try/except Exception` and stops at the first exception, keeping what it assigned: `plan_id`,
   `membership_plan_id`, `plan_name`, `membership_plan_name` from the subscription meta; a missing `plan_id` ends the block (unbound
   name) after the names; the subscription association's `subscription_fee_plans` (when truthy and the name is still `-`) and then
   `membership_fee_plans` are searched for a dict whose `id` equals the meta id (Python equality); a non-dict plan, a plan without
   `id`, a non-list plan list or a missing `name` on the match ends the block.
10. `get_invoice_template()` (`invoice_classic.html` or `invoice.html`); the courses list; header, footer and `extra_text_invoices`
    through `filter_mentions` with `sport_association`, `associate`, `main_tutor` and `other.{today, courses_list}` (`invoice.*` and
    `payment.*` mention keys are refused); an extra text whose `get_text().strip()` is empty becomes None.
11. `hide_category_name` blanks the category name; `override_label`; `custom_data` gets `is_multiple` (appended False, or
    `bool()` in place; a truthy non-dict is the 500 page) and is passed only with an associate.

## document/einvoice/<uid>/view/

`CustomerInvoice.objects.get(pk=uid)` resolves the row by id (no manager scope, no group); a uid the field rejects or a missing row
is the 500 page. The page is `generate_invoice_html(invoice)`, ported in `BE_GO/internal/fatturapa` together with
`generate_invoice_xml` and `generate_invoice_json` (a38 0.1.8):

- the a38 model as `generate_einvoice` fills it, with a38's constructor cleaning (`DecimalField` quantize half-up to 2–8 places,
  `Decimal(float)` exact expansion, `str()` of values), `add_dettaglio_linee`/`autofill_prezzo_totale`, `build_dati_riepilogo`
  (grouped by rate, sorted, imponibile and imposta rounded half-up), `build_importo_totale_documento`;
- a38's full validation decides between the page and the exception (for example a missing transferor e-mail — `codice_destinatario`
  stays `0000000`, so the PEC is required —, an assignor without VAT number, an empty province, a zero rate without `natura`, a
  description over 1000 characters); a falsy `vat` becomes `22.0`;
- the HTML f-string with Python's `:.2f` (half-even) amounts, `ET.tostring` XML and the `json.dumps(indent=2)` JSON, with
  ProgressivoInvio passed in (it is time-based in a38).

Golden evidence: `TESTS/adapters/go/fatturapa_golden.py` builds 630 unsaved CustomerInvoice instances (135 authored, 495 seeded
random) and records the real outputs; `go test ./internal/fatturapa` requires identical bytes for the 276 invoices Python renders
(828 outputs) and `ErrInvalid` for every output Python raises on (1,041).

## Not ported (refused with the 500 page; the views write nothing)

- `get_main_tutor()` for an associate whose tutor relations have no primary one (Django promotes and saves one while rendering).
- Stored JSON outside the handled shapes: a non-dict truthy subscription meta other than numbers/bools, a non-dict truthy payment
  meta or `course` on a course payment, a non-string meta `description`, non-string `payment_category_id` values, integers beyond
  int64 or float overflow in amounts, `lower()` of the few runes with special Python mappings.
- Mention keys and markup outside the document-views subset; template inputs outside `djtemplate`.
- FatturaPA shapes outside the proven subset (`fatturapa.ErrUnsupported`): list/dict line text, decimal strings with whitespace,
  underscores or non-ASCII digits, dates before year 1000, arithmetic beyond Decimal's 28-digit context.
- `HEAD` and the slash-less paths.

## Evidence (TDD)

`TESTS/fixtures/invoice_views_http.json` (generator alongside) authors 76 cases. Invoice HTML expectations are authored template
contexts rendered by the contract with Django's template engine; einvoice expectations are the authored CustomerInvoice row,
which the contract passes unsaved to `generate_invoice_html`. Every case must leave the complete model state unchanged.

- RED (`native-r4`): Django 76/76; Go fails on the first case (routes unregistered).
- GREEN (`versioned-r2`): 76/76 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): dropping the payment soft-delete filter fails Go on `invoice-views-invoice-deleted-second-payment`
  (the 500 page instead of the receipt).
- Families re-run for shared code: `document-views` (`mentions.Filter` now shares its tree with `FilterBlank`).
