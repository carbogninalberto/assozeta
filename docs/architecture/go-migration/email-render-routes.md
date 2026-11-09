# Collaborator invitation, payment request and invoice receipt e-mails — implementing with HTTP parity

`POST collaborators/add` (`collaborators_add`, `BE/application/views/collaborator_views.py`), `POST payment/<uid>/request`
(`payment_request`, `payment_views.py`) and `POST invoice/<uid>/send` (`invoice_send`, `invoice_views.py`) are implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/email_render_routes.go`). Registry: `other.users.collaborators.create`,
`bookeeping.payments.update`, `bookeeping.documents.invoices.update`. Each view renders an e-mail in the request with
`render_to_string` and queues it with `core.tasks.send_mail_async.apply_async(kwargs=...)`; the Go server renders the same bytes
with `BE_GO/internal/emailrender` and publishes the same message through the [Celery producer](celery-producer.md). Django's
workers send the mail.

## The renderer (`internal/emailrender`)

A small port of the Django 5.2 template language for exactly the templates these views use: `email/_base.html`,
`email/account/email_welcome_collaborator_message.html`, `email/payment/pending.html`, `email/payment/payment_receipt.html`
(embedded copies; a test requires them byte-identical to `BE/templates`).

- Supported: `{% extends %}` with `{% block %}` overrides (text outside the child's blocks is dropped), `{% load instance_branding %}`
  and `{% instance_email_brand as <var> %}`, `{% if/elif/else/endif %}` with `and`/`or`/`not` over variables, `{{ a.b|filter:arg }}`
  with dict lookups (a missing key, or any lookup on None, is `string_if_invalid` `''`), the `floatformat` (integer argument) and
  `linebreaksbr` filters.
- Values: None renders `None`, strings are autoescaped (`django.utils.html.escape`: `& < > " '` → `&amp; &lt; &gt; &quot; &#x27;`),
  `Safe` strings are not; booleans `True`/`False`; ints `str()`; `Decimal` through `number_format` (`{:f}`); floats through `repr`
  (via Decimal when it has an exponent). `USE_THOUSAND_SEPARATOR` is False, so localization is only the active language's
  `DECIMAL_SEPARATOR` (`LANGUAGE_CODE='it'`, `USE_I18N`, Django's default `LANGUAGES`; the table is generated from Django).
  `floatformat` follows Django's code: `Decimal(repr(value))`, the 200-digit guard, the integer shortcut for non-positive
  arguments, `ROUND_HALF_UP` quantize, no sign on a rounded zero, then `number_format` with the separator.
- The language is the one `LocaleMiddleware` activates (`django_language` cookie, then `Accept-Language`, then `it`): the existing
  Django language resolver in `handler/rest_route_locale.go`.
- `instance_email_brand`: `InstanceConfiguration.objects.first()`; the palette (`#rrggbb` check, lower case, WCAG luminance,
  white/black text, the 12% hover mix with Python's round-half-even), the logo (`APP_URL`-relative path, `?v=` + `updated_at`
  `%Y%m%d%H%M%S%f`; Bakney's logo unless `IS_WHITELABEL`), the name and `WHITELABEL_NAME`. The linearized channel values are a
  table generated from CPython so no result depends on `math.Pow` matching C's `pow`.
- Anything else is refused with `ErrUnsupported` when the templates are loaded or rendered: other tags, filters, filter argument
  suffixes (`2g`, `2u`), comparison operators, comments, literals, attribute lookups on non-dict values, context values of other
  types, non-finite floats, Decimal text outside plain notation, unknown languages, `updated_at` before year 1000.

Golden evidence: `TESTS/adapters/go/email_render_golden.py` sets Django up with `core.settings` (no database: the configuration is
an unsaved instance per case) and records in `BE_GO/internal/emailrender/testdata/django_renders.json` 25 `render_to_string`
outputs (all three mails; five configurations including none, a non-self-hosted one, logos by path and by URL, an invalid colour,
a dark colour; languages it, en, de, fr, es, nl, pt-br, en-gb; Decimal, int and float amounts, half-up and negative rounding,
`-0.0`, exponents, None values, escaping, `\r\n` line breaks, missing variables), the whitelabel logo branch, every language's
decimal separator, 3,259 palettes, the hover table, the linear channel table and a SHA-256 of the text colour over all 2^24
colours. `go test ./internal/emailrender` reproduces all of it and checks that unsupported features are refused.

## collaborators/add

- `@permission_classes([IsAuthenticated, IsAssociationUser])`: athletes get DRF's generic 403 before method dispatch;
  `Allow: POST, OPTIONS`; OPTIONS names `Collaborators Add` (no docstring).
- In Django's order: `request.data` (a malformed body is DRF's 400); `'email' in data` (a list or string body uses Python's
  `in`: a match raises on `data['email']`, 500; `None`/numbers raise, 500; otherwise 400 `["Email is required"]`); `check_email`
  (Django's `validate_email` on the raw value: falsy values and strings it rejects are 400 `["Email is not valid."]`; a truthy
  number or boolean, or a list/dict containing `'@'`, raises, 500); `int(data.get('collaborator_role', 3))` (Python `int()`,
  500 when it raises; a role other than 1, 2, 3 becomes 1); `collaborator_permissions = data.get(...)`, stored for every role
  (form bodies: the last value, a string).
- `CollaborationInvites.objects.create(user=request.user, email, expiration_date=now+90 days, token=secrets.token_hex(16),
  collaborator_role, collaborator_permissions)`: auditlog's create entry (every stored field against None,
  `CollaborationInvites object (<id>)`), indexed through the actor's live association (`actor.sport_association`; none for
  collaborators). A value PostgreSQL rejects (an email over 254 characters, `\u0000` in the permissions) is a 500 with nothing
  written. The following `save()` changes nothing and logs nothing.
- `request.user.sport_association` (`SportAssociation.objects.get(user=...)`): an owner without exactly one live association is
  the 500 page with the invitation and its entry kept.
- The collaborator e-mail and `send_mail_async(subject='<WHITELABEL_NAME> | Invito a collaborare', message, from_email=
  DEFAULT_FROM_EMAIL, recipient_list=[email], html_message, fail_silently=False, sport_association_id=UUID)`; `{'msg': 'User invited'}`.
- Go writes the invitation, its entry and index in one transaction, commits, then renders and publishes (`context.WithoutCancel`,
  10 s); a publish failure is the 500 page with the invitation kept, as in Django.

## payment/<uid>/request

- `@api_view(['POST'])`, OPTIONS `Payment Request` / `API endpoint to add a new payment`; the view never reads `request.data`.
- `is_valid_uuid` (400 list), `scoped_queryset(SportAssociation.objects).get(user=request.user)` (not exactly one: 500), the
  scoped live `X-Group-Id`-aware payment `.first()` (none, or a payment without association: 500), another association's payment
  403 `User not allowed.`, a paid one 412 `{'msg': 'email not sent, payment already paid.'}`.
- Recipients: the associate's email when not None (an empty string included), then `request.user.email` when the payment's user
  is not `request.user` (or is None). Description: `'<description> '` (empty for None), replaced by `"per l'iscrizione "` (subject 1)
  or `'per il corso '` (subject 2). A payment without associate is a 500 (the name). The pending template (amount as Decimal,
  `online_payments_enabled` from the association owner's flag), then `send_mail_async(subject='<denomination> | Pagamento in attesa',
  ..., from_email=DEFAULT_TEAM_EMAIL, ..., reply_to=[association owner email])`; `{'msg': 'email sent.'}`.

## invoice/<uid>/send

- `@api_view(['POST'])`, OPTIONS `Invoice Send` / `API endpoint to add a new payment`; no body is read.
- `is_valid_uuid`, the scoped association `.get()` (500), the scoped live group-aware invoice `.first()` (none: 500), its first
  scoped live group-aware payment by key, another association's invoice 403 `User not allowed.`, no payment 500.
- Recipient: the associate, else the payment's user, else the supplier, each when its email is truthy (name `first last`, or the
  supplier's name); none: 412 `{'msg': 'Nessuna email associata.'}`. No PDF or no payment date: 500.
- The receipt template (`float(membership_fee) + float(activity_fee)|floatformat:2`, number, `dd/mm/YYYY`, description with
  `linebreaksbr`, download URL with the PDF token) and `send_mail_async(subject='<denomination> | Ricevuta di pagamento n. <number> -
  <dd/mm/YYYY>', ..., reply_to=[association owner email])`; `{'msg': 'email sent.'}`.

## Not ported (refused with the 500 page before any write)

- `collaborators/add` permissions containing a float whose Python repr uses an exponent (`1e+16`, `1e-05`) or `-0.0`: PostgreSQL
  returns them as other numbers, so Django's second `save()` logs an update entry; and e-mail strings with NUL characters or lone
  surrogates.
- Inputs the renderer refuses (see above); with the current templates and views this is only an invoice payment date before
  year 1000.
- `Accept-Language` values other than English are rendered correctly (golden evidence) but the HTTP families run in English only
  (the shared harness requires `Content-Language: en`).

## Evidence

`TESTS/fixtures/email_render_routes_http.json` (82 cases, generated by `TESTS/fixtures/generate_email_render_routes_http.py`; contract
`TESTS/contracts/test_email_render_routes_http.py`, adapter `TESTS/adapters/django/email_render_routes_http.py`). Every case checks the
complete model state, the invitation row, its audit entry and index, and the Celery messages on the run's broker: the message and
`html_message` are rendered by the adapter with Django's `render_to_string` from the authored template and context under the
request's language and the run's InstanceConfiguration, so the published HTML is compared byte for byte (envelope, `kwargsrepr`,
body).

- RED (`native-r3`): Django 82/82; Go 404 on the first case.
- GREEN (`versioned-r1`): 82/82 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): escaping `'` as `&#39;` (html/template's spelling) instead of Django's `&#x27;` in `emailrender.Escape`
  fails Go on the first message with an apostrophe — CAUGHT.
- `go test ./internal/emailrender` (golden renders, palette, separators, refusals) and `service/email_render_routes_test.go`
  (commit before publish, kept invitation without association, refusals write nothing).
