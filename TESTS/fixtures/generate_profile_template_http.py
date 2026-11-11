"""Author the profile/update/subscription/template HTTP sequence (profile-template.md).

profile_update_subscription_template (BE/application/views/profile_views.py;
@api_view(['PATCH']), IsAuthenticated; registry other.settings.update; not an
athlete view). Read from the view:

- request.data['sport_association'] becomes the serializer's initial_data
  without validation. A missing key, a non-dict body or a non-dict, non-null
  value is the 500 page; so is any missing key the view reads. Nothing is
  written before sport_association.save(), so every exception leaves state
  unchanged.
- With a dict, SportAssociation.objects (not deleted, impersonation-scoped)
  .get(user=request.user) (an athlete or an owner without association: 500),
  then: additional_sections (a list is filtered: sections without name/text or
  with an empty one are dropped, text goes through BeautifulSoup(html.parser)
  .prettify(), name through str.upper(), extra keys kept; an empty value is
  []), regulation and demand prettified, the six visibility flags and
  enable_quotes_management assigned raw when not None, the logo through
  compress_base64(max_size=(590, 130)) (None stores None), subscription_fee and
  membership_fee assigned raw when float() >= 0 (membership_fee '' stores
  None), fee plans kept when name and fee are not None and float(fee) >= 0
  (fee stored as float(fee); '' counts as 0; an empty plan list also forces
  the matching multiple_* flag False), then enabled_for, additional_fields,
  stripe_available_methods, invoice_template, subscription_template and
  extra_text_invoices assigned raw. One full save(); auditlog compares the
  stored row with the in-memory raw values (str(), json.dumps(sort_keys=True)
  for JSON fields).
- The response: role, UserAuthSerializer(request.user) with
  preview_and_custom_features for every role and the avatar through
  compress_base64 (200x200); for an association owner the re-read association
  with its logo through compress_base64 at 590x130.

Expected HTML and images are computed here with bs4 4.13.3 and Pillow (the
libraries the view calls), following the view's steps. Run with the production
Python (``python -I``).
"""
import ast
import base64
from decimal import Context, Decimal, ROUND_HALF_UP
import io
import json
from pathlib import Path

from bs4 import BeautifulSoup
from PIL import Image

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / 'profile_template_http.json'
IDS = json.loads((HERE / 'message_mutations.json').read_text())['ids']
ROOT = HERE.parents[1]


def default_sections():
    source = ast.parse((ROOT / 'BE/application/models/utils.py').read_text())
    for node in source.body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', '') == 'DEFAULT_ADDITIONAL_SECTIONS' for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError('DEFAULT_ADDITIONAL_SECTIONS')


SERVER = '<stable-server-error-page>'
PATH = '/profile/update/subscription/template'
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
SETTINGS_UPDATE = {'detail': 'Missing permission: other.settings.update'}
FEATURES = [{'name': 'Iscrizione per famiglia', 'description': ''}]
LIMIT = 1024 * 1024


# --- the libraries the view calls ---------------------------------------------------------------

def prettify(markup):
    return BeautifulSoup(markup, features='html.parser').prettify()


def pattern(size, mode='RGB'):
    w, h = size
    channels = len(mode)
    data = bytearray()
    for y in range(h):
        for x in range(w):
            data.extend([(x * 255 // max(w - 1, 1)) & 0xff, (y * 255 // max(h - 1, 1)) & 0xff, (x * 7 + y * 13) & 0xff,
                         (128 + x) & 0xff][:channels])
    return Image.frombytes(mode, size, bytes(data))


def png_uri(image):
    out = io.BytesIO()
    image.save(out, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode()


def compress(uri, bound):
    """compress_base64: unchanged on any failure or when already at the bound and <= 1 MiB."""
    try:
        _, encoded = uri.split(',', 1)
        data = base64.b64decode(encoded)
        image = Image.open(io.BytesIO(data))
        ratio = min(bound[0] / image.size[0], bound[1] / image.size[1])
        size = (int(image.size[0] * ratio), int(image.size[1] * ratio))
        if size == image.size and len(data) <= LIMIT:
            return uri
        if image.mode == 'RGBA':
            flat = Image.new('RGB', image.size, (255, 255, 255))
            flat.paste(image, mask=image.split()[3])
            image = flat
        image = image.resize(size, Image.Resampling.LANCZOS) if len(data) > LIMIT else image.resize(image.size)
        out = io.BytesIO()
        image.save(out, format='JPEG')
        return 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode()
    except Exception:
        return uri


AVATAR = png_uri(pattern((5, 4)))
LOGO_SMALL = png_uri(pattern((40, 20), 'RGBA'))
LOGO_WIDE = png_uri(pattern((700, 150)))
LOGO_AT_BOUND = png_uri(pattern((590, 130)))
LOGO_UNDECODABLE = 'data:image/png;base64,AAAA'


# --- the association's written fields ------------------------------------------------------------

TEXT = ('regulation', 'demand', 'logo', 'invoice_template', 'subscription_template', 'extra_text_invoices')
JSON_FIELDS = ('additional_sections', 'subscription_fee_plans', 'membership_fee_plans', 'enabled_for', 'additional_fields',
               'stripe_available_methods')
FLAGS = ('show_regulation_to_members', 'show_regulation_to_both', 'show_regulation_to_athletes', 'show_demand_to_members',
         'show_demand_to_both', 'show_demand_to_athletes')
BOOLEANS = FLAGS + ('multiple_subscription_fee', 'multiple_membership_fee', 'enable_quotes_management')
DECIMALS = ('subscription_fee', 'membership_fee')
WRITTEN = TEXT + JSON_FIELDS + BOOLEANS + DECIMALS

STATE = {
    'regulation': None, 'demand': None, 'logo': None, 'invoice_template': 'invoice.html',
    'subscription_template': 'subscription.html', 'extra_text_invoices': None, 'additional_sections': default_sections(),
    'subscription_fee_plans': [], 'membership_fee_plans': [], 'enabled_for': ['associate', 'associate-membership', 'membership'],
    'additional_fields': None, 'stripe_available_methods': ['card', 'sepa_debit'], **{flag: True for flag in FLAGS},
    'multiple_subscription_fee': False, 'multiple_membership_fee': False, 'enable_quotes_management': True,
    'subscription_fee': '0.00', 'membership_fee': None,
}
# The six visibility flags print as Python booleans in the audit entry.


def db_decimal(value):
    """DecimalField.to_python (floats through Context(prec=9)) then numeric(9, 2) rounding."""
    number = Context(prec=9).create_decimal_from_float(value) if isinstance(value, float) else Decimal(value)
    return str(number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def db_boolean(value):
    if value in ('t', 'True', '1'):
        return True
    if value in ('f', 'False', '0'):
        return False
    if value in (True, False):
        return bool(value)
    raise AssertionError(value)


def audit_text(value):
    return 'None' if value is None else str(value)


def audit_json(value):
    return json.dumps(value, sort_keys=True)


def jsonb(value):
    """What jsonb gives back: a float json.dumps wrote with an exponent is a numeric, read as an int when it has no fraction."""
    if isinstance(value, float) and 'e+' in repr(value):
        return int(Decimal(repr(value)))
    if isinstance(value, list):
        return [jsonb(item) for item in value]
    if isinstance(value, dict):
        return {key: jsonb(item) for key, item in value.items()}
    return value


def save(state, data, failing=False):
    """The view's assignments, the stored row and auditlog's change pairs."""
    raw, new = {}, dict(state)
    sections = data['additional_sections']
    if sections is not None:
        kept = []
        if len(sections) > 0:
            for section in sections:
                if 'name' in section.keys() and 'text' in section.keys():
                    if section['name'] == '' or section['text'] == '':
                        continue
                    kept.append({**section, 'text': prettify(section['text']), 'name': section['name'].upper()})
        raw['additional_sections'] = kept
    for name in ('regulation', 'demand'):
        if data[name] is not None:
            raw[name] = prettify(data[name])
    for name in FLAGS:
        if data[name] is not None:
            raw[name] = data[name]
    raw['logo'] = None if data['logo'] is None else compress(data['logo'], (590, 130))
    for kind in ('subscription', 'membership'):
        fee = data[f'{kind}_fee']
        if kind == 'membership' and fee == '':
            raw['membership_fee'] = None
        elif fee is not None and float(fee) >= 0:
            raw[f'{kind}_fee'] = fee
        if data[f'multiple_{kind}_fee'] is not None:
            raw[f'multiple_{kind}_fee'] = data[f'multiple_{kind}_fee']
        plans = data[f'{kind}_fee_plans']
        if plans is None:
            continue
        if len(plans) > 0:
            kept = []
            for plan in plans:
                value = 0 if plan[f'{kind}_fee'] == '' else plan[f'{kind}_fee']
                if plan['name'] is not None and value is not None and float(value) >= 0:
                    kept.append({**plan, f'{kind}_fee': float(value)})
            raw[f'{kind}_fee_plans'] = kept
        else:
            # An empty plan list also switches the multiple fee off.
            raw[f'multiple_{kind}_fee'] = False
            raw[f'{kind}_fee_plans'] = []
    if data['enable_quotes_management'] is not None:
        raw['enable_quotes_management'] = data['enable_quotes_management']
    for name in ('enabled_for', 'additional_fields', 'stripe_available_methods', 'invoice_template', 'subscription_template',
                 'extra_text_invoices'):
        raw[name] = data[name]
    # auditlog's pre_save receiver records the change pairs before the
    # UPDATE converts the values, so a conversion or database error still
    # leaves the entry (the request is not atomic).
    changes = {}
    for name, value in raw.items():
        if name in JSON_FIELDS:
            old, text = audit_json(state[name]), audit_json(value)
        elif name in BOOLEANS or name in DECIMALS:
            old, text = str(state[name]) if name in BOOLEANS else audit_text(state[name]), audit_text(value)
        else:
            old, text = audit_text(state[name]), audit_text(value)
        if old != text:
            changes[name] = [old, text]
    if failing:
        return state, changes
    for name, value in raw.items():
        if name in BOOLEANS:
            new[name] = db_boolean(value)
        elif name in DECIMALS:
            new[name] = None if value is None else db_decimal(value)
        elif name in JSON_FIELDS:
            new[name] = jsonb(value)
        else:
            new[name] = None if value is None else str(value)
    return new, changes


# --- response projections ----------------------------------------------------------------------

def profile(label, role):
    number = int(IDS[label][-6:])
    return {'user_id': IDS[label], 'first_name': 'Message', 'last_name': 'Fixture', 'username': f'message-reads-{number}',
            'email': f'message-reads-{number}@example.test', 'avatar_image': compress(AVATAR, (200, 200)) if label == 'owner' else None,
            'collaborator_role': 1, 'collaborator_permissions': None, 'google_sync_enabled': False, 'dashboard_layout': None,
            'full_installments_plan': False, 'show_zero_payments': False, 'dark_mode': False,
            'medical_certificate_notifications': True, 'hide_category_name': False, 'phone': None,
            'subscription_duration_equal_sport_year': True, 'balance_sheet_start_day': 1, 'balance_sheet_start_month': 1,
            'balance_sheet_year': 1, 'subscription_duration': 2, 'membership_duration': 2, 'membership_starting_number': 0,
            'default_membership_type': '', 'subscription_start_day': 1, 'subscription_start_month': 1, 'custom_end_date': False,
            'subscription_end_day': 31, 'subscription_end_month': 12, 'lead_sport_association_role': None,
            'lead_sport_association_size': None, 'lead_sport_market_channel': None, 'default_payment_category': None,
            'default_payment_category_courses': None, 'can_impersonate': role == 1, 'requires_welcome': role == 1,
            'onboarding': None, 'preview_and_custom_features': FEATURES}


def projection(state):
    return {'sport_association_id': IDS['own_association'], 'denomination': 'Message reads own', 'tax_code': '70000000001',
            'address': None, 'address_cap': None, 'address_city': None, 'regulation': state['regulation'], 'demand': state['demand'],
            'logo': state['logo'], 'subscription_fee': state['subscription_fee'],
            'multiple_subscription_fee': state['multiple_subscription_fee'], 'subscription_fee_plans': state['subscription_fee_plans'],
            'membership_fee': state['membership_fee'], 'multiple_membership_fee': state['multiple_membership_fee'],
            'membership_fee_plans': state['membership_fee_plans'], 'custom_subscription_form_data': None, 'document_header': None,
            'invoice_footer': None, 'enable_quotes_management': state['enable_quotes_management'],
            'configuration': {'mandatory_phone': False, 'mandatory_email': False, 'mandatory_signature': False},
            'reviewed': True, 'affiliate_code': None, 'additional_sections': state['additional_sections'], 'president_signature': None,
            'stamp': None, 'president_first_name': None, 'president_last_name': None, 'federation': None, 'enroll_number': None,
            'sport': None, 'enabled_for': state['enabled_for'], 'checkout_info': None, 'additional_fields': state['additional_fields'],
            'stripe_available_methods': state['stripe_available_methods'], 'iban': None, 'website': None, 'abbreviated': None,
            'vat_number': None, 'whatsapp': None, 'invoice_template': state['invoice_template'],
            'subscription_template': state['subscription_template'], 'extra_text_invoices': state['extra_text_invoices'],
            'imported_from_associami': False, 'groups': [{'label': 'Scope own group', 'value': IDS['own_group']}],
            **{flag: state[flag] for flag in FLAGS}}


def owner_response(state):
    data = profile('owner', 1)
    association = projection(state)
    if association['logo'] is not None:
        association['logo'] = compress(association['logo'], (590, 130))
    data['sport_association'] = association
    return {'role': 'association', 'user_data': data}


def frontend_payload(state, **changes):
    """What Template.svelte sends: the stored association it read, edited."""
    payload = projection(state)
    payload.update(empty_sections=True, review_url=None, review_url_enabled=False)
    payload.update(changes)
    return payload


cases = []


def add(name, actor, status, response, *, method='PATCH', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'profile-template-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def write(actor, data, *, headers=None, name, impersonation=None):
    """A successful save: the response and the complete written fields with their audit entry."""
    global STATE
    STATE, changes = save(STATE, data)
    audit = {'actor': actor, 'changes': changes}
    if impersonation:
        audit['impersonation'] = impersonation
    effect = {'association': {k: STATE[k] for k in WRITTEN}, 'audits': [audit] if changes else []}
    add(name, actor, 200, owner_response(STATE), body={'sport_association': data}, headers=headers, effect=effect)


# === authorization and transport ================================================================
add('anonymous', 'anonymous', 401, ANONYMOUS, body={})
add('admin-without-session', 'admin', 403, ADMIN, body={})
add('restricted-missing-permission', 'restricted_collaborator', 403, SETTINGS_UPDATE, body={'sport_association': None})
add('scoped-athlete-denied', 'owner', 403, SCOPED_DENIED, body={'sport_association': None}, headers=SCOPED)
add('wrong-method', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 200, {'name': 'Profile Update Subscription Template', 'description': '', 'renders': ['application/json'],
                              'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')

# === exceptions before the save: the 500 page, nothing written ==================================
add('missing-key', 'owner', 500, SERVER, body={})
add('list-body', 'owner', 500, SERVER, raw_body='[]', content_type='application/json')
add('string-body', 'owner', 500, SERVER, raw_body='"sport_association"', content_type='application/json')
add('form-body', 'owner', 500, SERVER, raw_body='sport_association=x', content_type='application/x-www-form-urlencoded')
add('association-string', 'owner', 500, SERVER, body={'sport_association': 'x'})
add('association-list', 'owner', 500, SERVER, body={'sport_association': []})
add('association-number', 'owner', 500, SERVER, body={'sport_association': 5})
add('association-empty-object', 'owner', 500, SERVER, body={'sport_association': {}})
partial = frontend_payload(STATE)
partial.pop('extra_text_invoices')
add('payload-missing-last-key', 'owner', 500, SERVER, body={'sport_association': partial})
add('athlete-object', 'athlete', 500, SERVER, body={'sport_association': frontend_payload(STATE)})
add('without-association-object', 'owner_without_association', 500, SERVER, body={'sport_association': frontend_payload(STATE)})
add('without-association-null', 'owner_without_association', 500, SERVER, body={'sport_association': None})

# === sport_association None: only the response =================================================
add('null-owner', 'owner', 200, owner_response(STATE), body={'sport_association': None})
add('null-athlete', 'athlete', 200, {'role': 'athlete', 'user_data': profile('athlete', 2)}, body={'sport_association': None})
add('null-full-collaborator', 'full_collaborator', 200, owner_response(STATE), body={'sport_association': None})
add('null-admin-session', 'admin', 200, owner_response(STATE), body={'sport_association': None}, headers=ADMIN_OWNER)
# The owner impersonating its collaborator inside its own association.
add('null-owner-collaborator-session', 'owner', 200, owner_response(STATE), body={'sport_association': None},
    headers={'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']})

# === saves ======================================================================================
REGULATION = ('<h2>Regolamento</h2><p>Il socio si impegna a rispettare lo <strong>statuto</strong> e il '
              '<a target="_blank" rel="noopener noreferrer nofollow" href="https://example.test/statuto?a=1&amp;b=2">regolamento</a>.</p>'
              '<ul><li><p>Quota annuale</p></li><li><p>Certificato&nbsp;medico</p></li></ul>')
DEMAND = '<p style="text-align: center">Il sottoscritto chiede di essere ammesso come <em>socio</em>.</p><p></p>'
sections = [dict(s) for s in STATE['additional_sections']] + [
    {'name': 'privacy è', 'text': '<p>Trattamento dei dati &amp; consenso</p>', 'show_to_members': True, 'show_to_both': False,
     'show_to_athletes': True, 'collapsed': False},
    # str.upper() maps ß, ligatures and ǰ to several characters.
    {'name': 'stra\u00dfe \ufb01ne \u01f0 \u0390', 'text': '<p>maiuscole</p>'},
    {'name': '', 'text': '<p>senza nome</p>'},
    {'name': 'Vuota', 'text': ''},
    {'name': 'Senza testo'},
]
write('owner', frontend_payload(
    STATE, regulation=REGULATION, demand=DEMAND, additional_sections=sections, logo=LOGO_SMALL,
    show_regulation_to_both=False, show_demand_to_athletes=False, subscription_fee=20, multiple_subscription_fee=True,
    subscription_fee_plans=[{'name': 'Base', 'subscription_fee': 15}, {'name': 'Ridotta', 'subscription_fee': ''},
                            {'name': None, 'subscription_fee': 3}, {'name': None, 'subscription_fee': 'non valutata'},
                            {'name': 'Negativa', 'subscription_fee': -1},
                            {'name': 'Nulla', 'subscription_fee': None}],
    membership_fee=12.345, membership_fee_plans=[{'name': 'Socio', 'membership_fee': '7.5', 'extra': {'peso': 1.0, 'n': 2}}],
    enable_quotes_management=False, enabled_for=['associate'],
    additional_fields=[{'label': 'Taglia', 'type': 'select', 'options': ['S', 'M'], 'required': True, 'weight': 1.5, 'note': 'è'}],
    stripe_available_methods=['card'], invoice_template='invoice2.html', subscription_template=None,
    extra_text_invoices='Grazie'), name='editor-save')
# The frontend reloads and saves the stored values again (prettified HTML, JPEG logo).
write('full_collaborator', frontend_payload(STATE, subscription_fee=float(STATE['subscription_fee']),
                                            membership_fee=float(STATE['membership_fee'])), name='collaborator-resave')
write('owner', frontend_payload(
    STATE, additional_sections={}, subscription_fee_plans='', membership_fee_plans=[], multiple_subscription_fee=True,
    multiple_membership_fee=True, regulation=None, demand=None, logo=None, subscription_fee=None, membership_fee='',
    enabled_for=None, additional_fields=None, stripe_available_methods=None, invoice_template=None, subscription_template=None,
    extra_text_invoices=None, enable_quotes_management=None, **{flag: None for flag in FLAGS}), name='empty-values')
write('owner', frontend_payload(
    STATE, subscription_fee='1234567.125', membership_fee=1234567.125,
    subscription_fee_plans=[{'name': 'Mille', 'subscription_fee': '1e3'}, {'name': 'Uno', 'subscription_fee': True},
                            {'name': 'Mezzo', 'subscription_fee': 0.5}],
    show_regulation_to_both='t', show_demand_to_both=0, show_demand_to_members='False', enable_quotes_management='1',
    multiple_subscription_fee=1, invoice_template=7, subscription_template=True, extra_text_invoices=2.5,
    additional_sections=[], logo=''), name='scalar-values')
write('owner', frontend_payload(
    STATE, subscription_fee='nan', membership_fee=-3, subscription_fee_plans=[{'name': 'NaN', 'subscription_fee': 'nan'}],
    multiple_subscription_fee=True, logo=LOGO_UNDECODABLE), name='skipped-fees')
write('owner', frontend_payload(STATE, logo=LOGO_AT_BOUND, subscription_fee=True, regulation='Testo semplice'),
      name='logo-at-bound')

# === conversion or database errors inside save(): the 500 page after auditlog's pre_save entry ====
for name, changes in (
        ('fee-overflow', {'subscription_fee': 12345678}),
        ('fee-infinite', {'subscription_fee': 'inf'}),
        ('plan-infinite', {'subscription_fee_plans': [{'name': 'Inf', 'subscription_fee': 'inf'}]}),
        ('flag-invalid', {'show_demand_to_members': 'maybe'}),
        ('flag-number', {'enable_quotes_management': 2}),
):
    data = frontend_payload(STATE, **changes)
    _, pairs = save(STATE, data, failing=True)
    add(name, 'owner', 500, SERVER, body={'sport_association': data},
        effect={'association': {k: STATE[k] for k in WRITTEN}, 'audits': [{'actor': 'owner', 'changes': pairs}]})

# === exceptions in the view before save(): the 500 page, nothing written ========================
for name, changes in (
        ('fee-comma', {'subscription_fee': '12,5'}),
        ('plan-missing-name', {'membership_fee_plans': [{'membership_fee': 5}]}),
        ('plan-string', {'subscription_fee_plans': ['x']}),
        ('sections-string', {'additional_sections': 'abc'}),
        ('section-name-number', {'additional_sections': [{'name': 5, 'text': '<p>x</p>'}]}),
        ('section-not-object', {'additional_sections': ['x']}),
        ('plans-number', {'membership_fee_plans': 5}),
        ('regulation-number', {'regulation': 5}),
        ('section-text-null', {'additional_sections': [{'name': 'A', 'text': None}]}),
        ('plans-object', {'subscription_fee_plans': {'name': 'A'}}),
):
    add(name, 'owner', 500, SERVER, body={'sport_association': frontend_payload(STATE, **changes)})

# === t4l2: float() spellings, containers and numbers in text fields, a logo that is not a string ====
write('owner', frontend_payload(
    STATE, subscription_fee='\u2003 1_0.5\n', membership_fee='\u0661\u0662.\u0665',
    subscription_fee_plans=[{'name': 'Grande', 'subscription_fee': 12345678901234567890}, {'name': 'Mille', 'subscription_fee': '1_000'},
                            {'name': 'Spazi', 'subscription_fee': ' 7 '}, {'name': 'Esponente', 'subscription_fee': '1e3'}],
    invoice_template=['a', 1, None, 2.5, {'k': "it's"}], extra_text_invoices={'k': 1.5, '\u00e8': [True, 'x\ny']},
    subscription_template=12345678901234567890123, logo=5), name='float-spellings-and-containers')
write('owner', frontend_payload(STATE, logo={'a': [1.5, None]}, subscription_fee='+.5', membership_fee='5.'), name='logo-object')
write('owner', frontend_payload(STATE, logo=True, invoice_template=None, extra_text_invoices=None), name='logo-boolean')
for name, changes in (
        ('fee-string-past-float-range', {'subscription_fee': '1e400'}),
        ('flag-container', {'show_demand_to_members': ['t']}),
        ('flag-object', {'enable_quotes_management': {'a': 1}}),
        # The audit entry holds the JSON text (escaped); the jsonb column then refuses the character.
        ('json-with-nul', {'enabled_for': ['a\x00']}),
):
    data = frontend_payload(STATE, **changes)
    _, pairs = save(STATE, data, failing=True)
    add(name, 'owner', 500, SERVER, body={'sport_association': data},
        effect={'association': {k: STATE[k] for k in WRITTEN}, 'audits': [{'actor': 'owner', 'changes': pairs}]})
# A JSON number past the float range is inf: the fee fails its conversion after the audit entry.
data = frontend_payload(STATE, subscription_fee='@fee@')
_, pairs = save(STATE, {**data, 'subscription_fee': float('inf')}, failing=True)
add('fee-number-past-float-range', 'owner', 500, SERVER,
    raw_body=json.dumps({'sport_association': data}).replace('"@fee@"', '1e400'), content_type='application/json',
    effect={'association': {k: STATE[k] for k in WRITTEN}, 'audits': [{'actor': 'owner', 'changes': pairs}]})
# float() raises, or the audit entry cannot hold the text: the 500 page, nothing written.
for name, changes in (
        ('fee-integer-past-float-range', {'subscription_fee': 10 ** 400}),
        ('fee-double-underscore', {'subscription_fee': '1__0'}),
        ('fee-inner-space', {'membership_fee': '1 0'}),
        ('text-with-nul', {'extra_text_invoices': 'a\x00b'}),
        ('flag-with-nul', {'show_demand_to_both': 't\x00'}),
        ('logo-without-comma-with-nul', {'logo': 'a\x00b'}),
):
    add(name, 'owner', 500, SERVER, body={'sport_association': frontend_payload(STATE, **changes)})
for name, field, value in (
        ('text-with-lone-surrogate', 'extra_text_invoices', '"a\\ud800b"'),
        ('logo-data-with-lone-surrogate', 'logo', '"data:image/png;base64,AA\\ud800"'),
        ('number-list-in-json-past-float-range', 'enabled_for', None),
):
    if value is None:
        continue
    add(name, 'owner', 500, SERVER, content_type='application/json',
        raw_body=json.dumps({'sport_association': frontend_payload(STATE, **{field: '@value@'})}).replace('"@value@"', value))
data = frontend_payload(STATE, additional_fields='@value@')
_, pairs = save(STATE, {**data, 'additional_fields': {'a\udc00': 1}}, failing=True)
add('json-key-with-lone-surrogate', 'owner', 500, SERVER, content_type='application/json',
    raw_body=json.dumps({'sport_association': data}).replace('"@value@"', '{"a\\udc00": 1}'),
    effect={'association': {k: STATE[k] for k in WRITTEN}, 'audits': [{'actor': 'owner', 'changes': pairs}]})
# A container in a text field is stored as its repr, which escapes the character.
write('owner', frontend_payload(STATE, extra_text_invoices=['a\x00b'], logo=['\x00']), name='text-container-with-nul')
add('multipart-body', 'owner', 500, SERVER, content_type='multipart/form-data; boundary=fixture',
    raw_body='--fixture\r\nContent-Disposition: form-data; name="sport_association"\r\n\r\n{}\r\n'
             '--fixture\r\nContent-Disposition: form-data; name="logo"; filename="logo.png"\r\nContent-Type: image/png\r\n\r\nx\r\n'
             '--fixture--\r\n')
add('multipart-file-as-association', 'owner', 500, SERVER, content_type='multipart/form-data; boundary=fixture',
    raw_body='--fixture\r\nContent-Disposition: form-data; name="sport_association"; filename="a.json"\r\n\r\n{}\r\n--fixture--\r\n')

# An administrator session saves for the owner (wide logo re-encoded at its size).
write('admin', frontend_payload(STATE, logo=LOGO_WIDE, demand='<pre><code>riga 1\n  riga 2</code></pre>'), name='admin-session-save',
      headers=ADMIN_OWNER, impersonation='admin_owner')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'profile/update/subscription/template authored sequence with audit effects', 'ids': IDS,
    'avatar': AVATAR, 'written_fields': list(WRITTEN), 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
