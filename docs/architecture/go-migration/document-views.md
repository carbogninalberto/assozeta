# Document views — the HTML pages the renderer prints, rendered in Go byte for byte

`document/{subscription,compensation,template,medical-appointment}/<uid>/view/` and `document/subscription/preview/`
(`BE/docmanager/views/printing_views.py`) are implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/document_views.go`)
on top of two new packages. Django remains the execution owner. `document/invoice/<uid>/view/` and `document/einvoice/<uid>/view/`
are not in this family (see the end).

## Shared pieces

- `BE_GO/internal/djtemplate` renders the embedded copies of `BE/templates/document/**` the way Django 5.2's `render()` does. It covers
  the language subset these templates use: `{% load format %}`, `{% if/elif/else %}` with Django's smartif operators and Python
  comparison semantics (an operator that raises is False), `{% for a[, b] in seq %}`, `{# #}`, variable lookups (dict key, then
  attribute, then index; callables are called; a failed lookup is `''` in `{{ }}` and `None` in `{% if %}`), autoescaping, and the
  filters `safe`, `default`, `upper`, `lower`, `date` (d, m, Y, l, F with Italian and English names), `yesno`, `linebreaksbr`, `add`,
  `length`, `floatformat`, plus the application's `format`. Anything else (other tags, filters, format letters, lookups that reach a
  Python method, `str()` of containers, `upper()` of the few runes whose Python full case mapping Go does not have) is refused with
  `ErrUnsupported`. Model instances are closed `Object`s: reading an attribute the port did not declare is refused. Attributes Django
  lacks (`health_insurance_card`, `tutor`) are declared `Missing`.
- `BE_GO/internal/mentions` is `filter_mentions`: BeautifulSoup 4.13 with `html.parser` on Python 3.13, the mention spans replaced by
  their context values, the legacy `@alias` words in the text nodes, and `str(soup)`. The parse covers well-formed markup only:
  lowercased tags and attributes, void elements, a missing end tag at the end, quoted or bare attributes, and the `&amp; &lt; &gt;
  &quot; &apos; &nbsp;` and numeric references (C1 and other windows-1252 code points excluded). The output follows bs4: attributes
  sorted by name, whitespace-split multi-valued attributes, quote choice, whitespace-only text runs collapsed to `\n` or ` ` (kept as
  they are inside an open `<pre>`, bs4's `preserve_whitespace_tags`), void elements as `<br/>`. Comments, declarations, processing
  instructions, raw-text elements and `<textarea>`, mismatched end
  tags, unknown references, a `<br/>` after a `<br>` (bs4's already-closed bookkeeping) and runes that Python's case-insensitive alias
  match folds to ASCII are refused.
- `emailrender/shared.go` exports the existing number formatting (`floatformat`, `localize`) for `djtemplate`.
- `pdfrender.Client.RenderWithoutHeaders` is `Puppeteer.pdf_from_url(url, headers={})`: the same request with `"headers": {}`.

Golden tests: `TESTS/adapters/go/document_template_golden.py` renders all eight document templates for 320 seeded contexts with
`render_to_string` (both languages; 6 raise in Django and must fail in Go) and runs `filter_mentions` on 516 inputs; the Go tests
(`go test ./internal/djtemplate ./internal/mentions`) require identical bytes (`testdata/*.json`) and check the refusals.

## Views

- **subscription/view** (`IsAuthenticated`): the scoped, group-aware subscription (`Subscription.objects`, no soft-delete filter);
  `payment_data` is its payment's `meta` when truthy (the payment read like the FK does: deleted rows included). Each item of the
  association's `additional_sections` gets `show_to` (1 members, 2 both, 3 athletes; an existing key is replaced in place); a value
  that is not a list of dicts is the 500 page. `show_regulation_to`/`show_demand_to` from the flags; header and footer through
  `filter_mentions` with `sport_association`, `subscription` and `other.today` (`datetime.now()` in UTC). The template is
  `subscription_classic.html` when configured, otherwise `subscription.html`. Rendered in the request language.
- **subscription/preview** (`IsAuthenticated`; a request without an account is the DRF 401 whatever its method and query, and
  the renderer is not called): `?print` (any value) posts `'<request.path>?sport_association_id=<value or None>'` to the renderer
  with the request's own `Authorization` header, so the renderer opens the page as the requester, and answers
  `{"file": base64(pdf)}` or `500 {"error": "PDF not rendered"}` (any renderer failure). The editor loads the page with the
  session and shows it from memory (`UI/src/utils/framedDocument.js`). Otherwise the
  live, scoped association named by the last `?sport_association_id` and a placeholder subscription (`is_preview`, show lists
  `[1, 2, 3]`, the association's `additional_fields`); no parameter, a value the UUID field rejects or a missing row is the 500 page; an
  association whose user is an athlete is `403 "User is not a sport association"`.
- **compensation/view** (`IsAuthenticated`): the scoped, live, group-aware payment, served to the staff and to the account tied
  to its instructor; `from_date`/`to_date` are the minimum and maximum of
  its hours' dates (`hourly`) and the first two `' al '` parts of the others' periods (`strptime('%d/%m/%Y')`; a bad or missing period is
  the 500 page). Italian is activated (`Content-Language: it`, `floatformat` with a comma, Italian day and month names).
- **template/view** (`IsAuthenticated | HasBypassAuthorizationHeader`): the module template by pk, served to the staff. `?subscription_id` (last
  value): the scoped subscription, its courses list, then `associate` and `main_tutor`; each step that raises stops the rest silently
  (a course without a title leaves the list `[]`, which a mention prints as `[]`; a subscription without an associate leaves
  `associate` as None). Header/footer (the association's, or the custom ones) only when enabled, through `filter_mentions` with
  `sport_association` and `other.{today, courses_list}`; the body with the full context.
- **medical-appointment/view** (`IsAuthenticated | HasBypassAuthorizationHeader`): `is_valid_uuid` (`400 ["<ValueError text>"]`),
  the appointment by pk (missing: 500), served to the staff and to the member of its subscription, `303
  {"error": ...}` for regions other than `agonistica`/`non-agonistica`, then the region's template in Italian.
- Every view: identity resolution first (admin without a session, collaborator registry denials, the athlete-view list and the scoped
  reference checks on the path `uid` and query parameters), then the view's permission, then method dispatch (`Allow: GET, OPTIONS`);
  unhandled exceptions are the stable 500 page; the HTML is `text/html; charset=utf-8`.

Mentions resolve these keys (the editor's list): `sport_association.{denomination, tax_code, iban, website, abbreviated, vat_number,
whatsapp, address, address_city, address_cap, logo, user.email, federation, sport, enroll_number}`,
`associate.*`/`main_tutor.*` (the editor's associate fields and `sex`), and `other.<key>`. A key for an object the view does not pass
is `''`, as in Django.

## Main tutor promotion (`v2doc4`)

`get_main_tutor()` for an associate whose tutor relations have no primary one saves the first relation (by id) with
`is_primary=True` while the page renders: one auditlog update entry (`is_primary` False -> True) indexed through
`associate.sport_association` (the actor's association when the associate has none). Go does the same for signed-in
requests, committing the relation as soon as the view asks for the tutor, so a page that fails later keeps it, as in
Django. The invoice views share the code.

`HEAD` answers the views' 405 without a body. The slash-less paths answer Django's `APPEND_SLASH` redirect (the
unknown-paths family covers them).

## Not ported (refused with the 500 page; the views write nothing)

- `get_main_tutor()` promotion on a request authorized only by the bypass header (no signed-in actor for the audit entry).
- `get_signature_base64()` (a subscription with a signature): it reads private storage or fetches the legacy URL.
- Mention keys outside the list above (and every `subscription.*` key), and markup outside the `filter_mentions` subset.
- Template inputs outside `djtemplate`'s subset (for example `format` of a non-number raises in Django as well), non-ASCII
  compensation periods, an association whose user is a collaborator (preview).
- The course list keeps Django's query without `ORDER BY`; several course subscriptions follow the database's order on both sides.

## Routes not in this family

- `document/invoice/<uid>/view/` needs the invoice context the view builds (`generate_invoice_description`, `get_subscription`,
  `get_course_carnet_name`, the meta payment categories, plan names, tutor selection). `djtemplate` already renders `invoice.html` and
  `invoice_classic.html` byte for byte (golden tests); the view logic is a follow-up family.
- `document/einvoice/<uid>/view/` returns `generate_invoice_html`, built from the a38 FatturaPA model, including its validation
  (which decides between the page and a 500) and VAT summary rounding. Not attempted here.

## Evidence (TDD)

`v2doc4`: 162 cases (tutor promotion from the subscription and template views, a second request that finds the
relation primary, `HEAD`). RED `v2doc4-red-r1` (the refusal restored in Go: Django 162/162, Go 500). GREEN `v2doc4-final-r2` on
Django, Go, `/api/v1` and `/api/v2`. Mutation `v2doc4-mutation-r1` (the last relation promoted instead of the first)
CAUGHT on `subscription-minor-promotes-tutor`. invoice-views has its own case and receipts.

`TESTS/fixtures/document_views_http.json` (generator alongside) authors 114 cases. HTML expectations are authored template contexts
(header, footer and body `filter_mentions` output written out by hand) that the contract renders with Django's own template engine.
The preview's renderer requests go to the document-render stub and are checked byte for byte. Every case must leave the complete model
state unchanged.

- RED (`native-r4`): Django 114/114; Go fails on the first case (routes unregistered).
- GREEN (`versioned-r1`): 114/114 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): dropping the payment soft-delete filter of the compensation lookup fails Go on
  `document-views-compensation-deleted` (200 instead of the 500 page).
- Families re-run for shared code: `document-render` (pdfrender), `email-render-routes` (emailrender).

`<pre>` blocks in headers and footers (TipTap's CodeBlock; API v2 gap): the golden generator adds eight `<pre>` inputs (code blocks,
whitespace-only runs inside and outside, nesting, an unclosed block, uppercase tags) and the fallback association's footer is a code
block, rendered by the subscription view and the preview.

- RED (`v2doc-red-r1`, with the Go change reverted): Django 114/114; Go 500 on `document-views-subscription-fallback-template`.
- GREEN (`v2doc-versioned-r1`): 114/114 on native Django, native Go, Caddy `/api/v1` and `/api/v2`; `go test ./internal/mentions` green
  on the regenerated golden file (the djtemplate renders file is unchanged).
- Mutation (`v2doc-mutation-r1`): preserving whitespace only for direct children of `<pre>` fails
  `document-views-subscription-fallback-template` (`html.body`, CAUGHT).
- Re-run for shared code: `invoice-views` (`v2doc-rerun-r1`, 76/76; it also uses `mentions`).

Member documents (`secdoc-*`): documents are served to the association's staff and to the member they belong to.
`docmanager.access` (Django, with `docmanager.test_document_access`) and `memberDocumentAccess` with
`repository.MemberDocuments` (Go) decide it the same way: accounts other than athletes, superusers and the instance owner
(`is_instance_owner`) reach every record; an athlete account reaches the records of its user, of the associates of its
subscriptions, of associates whose account it is and of associates sharing a non-empty tax code with those (base managers:
no group or soft-delete filter). Anything else answers `403 {"detail": "Risorsa non disponibile per questo utente e
associazione."}`. Association-scoped sessions keep their own boundary, and requests admitted without an account (a document
token, the renderer header) are decided by the view's permission classes as before.

`document/subscription/<uid>/view/` applies it to the subscription right after the lookup (a missing row is still the 500
page). The fixture has 123 cases (9 new): the athlete's own subscription and a later one of their associate render; another
member's subscription, one without an associate and another athlete account get the 403; collaborators and owners are
unchanged. RED `secdoc-red-r1`: Django 123/123, Go 200 on `document-views-subscription-athlete-direct` with the check switched
off. GREEN `secdoc-versioned-final` (after merging the base; `secdoc-versioned-r1` before): 123/123 on the four transports.
Mutation `secdoc-mutation-r1` (every subscription counted as the member's) CAUGHT. The other views of this family are
unchanged.

Staff pages (`secdoc2-*`). The rule of `docmanager.access` now covers the other pages of this family, each right after its
row lookup (a missing row is still the 500 page): `compensation/view` is served to the staff and to the account tied to the
payment's instructor (`MemberCompensation`); `template/view` to the staff (`memberDocumentStaffAccess`), with or without
`?subscription_id`; `medical-appointment/view` to the staff and to the member of the appointment's subscription, before the
region check. The appointment page takes an account or the renderer header (`IsAuthenticated |
HasBypassAuthorizationHeader`, as the template page): the PDF route hands the renderer the caller's `Authorization`, and
the PDF-form regions never fetch the page. A caller without either gets the 401, also for `?token` and a malformed uid.

The fixture has 152 cases (29 new or changed). RED `secdoc2-red-r1`: Django 152/152, Go 200 on
`document-views-compensation-instructor-account-other`. GREEN `secdoc2-versioned-final` (after merging the base; `secdoc2-versioned-r1` before): 152/152 on the four transports.
Mutation `secdoc2-mutation-r1` (the appointment page counting every subscription as the member's) CAUGHT.

### Preview for signed-in users (`imphard`)

`document/subscription/preview/` asks for an account and its PDF is rendered with the request's `Authorization` header. The
fixture has 156 cases (4 new): anonymous requests (page, no parameter, `?print`, `PUT`) are 401 with no renderer call; the
owner and a member account get the page; the four print cases check the renderer payload's `Authorization`.
`BE/docmanager/test_printing_views.py` (`SubscriptionPreviewTests`, 3 tests) covers the view. RED (`imphard-red-r1`): Django
156/156; Go 200 on `document-views-preview-anonymous`. GREEN (`imphard-versioned-r1`): 156/156 on native Django, native Go,
Caddy `/api/v1` and `/api/v2`. Mutation (`imphard-mutation-r1`): rendering the PDF without the header fails Go on
`document-views-preview-print` (`renderer.body:0`).
