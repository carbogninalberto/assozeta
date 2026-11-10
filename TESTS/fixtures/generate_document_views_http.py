"""Author the document-views HTTP sequence (document-views.md).

The HTML views of BE/docmanager/views/printing_views.py that the renderer
service prints (@api_view(['GET'])):

- document/subscription/<uid>/view/ (IsAuthenticated): the scoped, group-aware
  subscription; payment_data is its payment's meta when truthy; each
  additional section of the association gets show_to ([1] members, [2] both,
  [3] athletes); show_regulation_to/show_demand_to from the association flags;
  header/footer through filter_mentions; the association's subscription
  template (subscription.html unless subscription_classic.html).
- document/subscription/preview/ (AllowAny): ?print asks the renderer for
  '<path>?sport_association_id=<value>' with no headers and answers
  {'file': base64} or 500 {'error': 'PDF not rendered'}; otherwise the live,
  scoped association named by ?sport_association_id with a placeholder
  subscription (no parameter: 500).
- document/compensation/<uid>/view/ (IsAuthenticated): the scoped, live,
  group-aware payment; from/to are the min/max of its hours' dates (hourly)
  and periods ('dd/mm/YYYY al dd/mm/YYYY'); Italian is activated.
- document/template/<uid>/view/ (IsAuthenticated | HasBypassAuthorizationHeader):
  the module template by pk, its association's (or custom) header/footer when
  enabled and its body through filter_mentions; ?subscription_id adds the
  subscription's associate, main tutor and courses list when every step works.
- document/medical-appointment/<uid>/view/ (AllowAny): is_valid_uuid (400),
  the appointment by pk (missing: 500), 303 for regions without an HTML
  template, then agonistica/non-agonistica.html in Italian.

Every HTML expectation is an authored template context (the values the view
must pass, the header/footer/body filter_mentions output written out by hand)
that the contract renders with Django's own template engine; '$today' is the
UTC date as dd/mm/YYYY.

Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('document_views_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
SERVER = '<stable-server-error-page>'
HTML = '<html>'


def uid(n):
    return f'a1000000-0000-4000-8000-{985000 + n:012d}'


UNKNOWN = uid(999)
ATHLETE_ASSOCIATE = 'a1000000-0000-4000-8000-000000700101'


# --- tagged template values (rendered by the contract with Django) ----------------------------
def s(v):
    return {'t': 'str', 'v': v}


def b(v):
    return {'t': 'bool', 'v': v}


def i(v):
    return {'t': 'int', 'v': v}


def flt(v):
    return {'t': 'float', 'v': v}


def dec(v):
    return {'t': 'decimal', 'v': v}


def d(v):
    return {'t': 'date', 'v': v} if v else NONE


def dt(v):
    return {'t': 'datetime', 'v': v} if v else NONE


def lst(*items):
    return {'t': 'list', 'v': list(items)}


def dct(*pairs):
    return {'t': 'dict', 'v': [[k, v] for k, v in pairs]}


def obj(**attrs):
    return {'t': 'obj', 'v': [[k, v] for k, v in attrs.items()]}


def call(v):
    return {'t': 'call', 'v': v}


def opt(v):
    return NONE if v is None else s(v)


NONE = {'t': 'none'}
DEFAULT_SECTIONS = {'t': 'default_sections'}  # DEFAULT_ADDITIONAL_SECTIONS with show_to [1, 2, 3]


def json_value(v):
    """A JSON column value as json.loads gives it."""
    if v is None:
        return NONE
    if isinstance(v, bool):
        return b(v)
    if isinstance(v, int):
        return i(v)
    if isinstance(v, float):
        return flt(v)
    if isinstance(v, str):
        return s(v)
    if isinstance(v, list):
        return lst(*[json_value(item) for item in v])
    return dct(*[(k, json_value(item)) for k, item in v.items()])


# --- fixture rows ------------------------------------------------------------------------------
DEFAULT_SECTION_FLAGS = (True, True, True)
ALPHA_HEADER = ('<p class="intestazione">Spett.le <span class="mention" data-type="mention" key="sport_association.denomination">'
                '@Denominazione</span> &amp; soci - CF <span data-type="mention" class="mention" key="sport_association.tax_code">@cf'
                '</span><br>Data: @dataodierna, contatto <span class="mention" data-type="mention" key="sport_association.user.email">'
                '@Email</span>, iscritto @nome</p>')
ALPHA_HEADER_OUT = ('<p class="intestazione">Spett.le ASD Alfa &amp; Figli &amp; soci - CF 01234567890<br/>Data: $today, contatto '
                    'alfa@example.test, iscritto </p>')
ALPHA_FOOTER = '<p>Grazie da @Denominazione — @listacorsi</p>\n<p>  </p>'


# TipTap's CodeBlock: whitespace-only strings inside <pre> are kept, the ones outside collapse.
FALLBACK_FOOTER = '<pre><code>Orari @nome:\n  lun   18-20\n\n  <b>  </b>\n</code></pre>\n<p>  </p>'
FALLBACK_FOOTER_OUT = '<pre><code>Orari :\n  lun   18-20\n\n  <b>  </b>\n</code></pre>\n<p> </p>'


def alpha_footer_out(courses):
    return f'<p>Grazie da @Denominazione — {courses}</p>\n<p> </p>'


ASSOCIATIONS = {
    'alpha': {'user': uid(1), 'id': uid(2), 'role': 'association', 'email': 'alfa@example.test', 'deleted': False,
              'denomination': 'ASD Alfa & Figli', 'tax_code': '01234567890', 'address': 'Via Roma 1', 'address_city': 'Torino',
              'address_cap': '10100', 'logo': 'data:image/png;base64,QUJD', 'regulation': '<p>Regolamento <b>interno</b></p>',
              'demand': None, 'federation': 'FIDAL', 'enroll_number': 'ab-12', 'iban': 'IT60X0542811101000000123456',
              'website': 'https://alfa.example.test', 'sport': 'Atletica', 'abbreviated': 'Alfa',
              'document_header': ALPHA_HEADER, 'invoice_footer': ALPHA_FOOTER, 'subscription_template': 'subscription.html',
              'additional_sections': [
                  {'name': 'Privacy & dati', 'text': '<p>Testo privacy</p>', 'show_to_members': True, 'show_to_both': False,
                   'show_to_athletes': True},
                  {'show_to': [9], 'name': 'Foto', 'text': 'Consenso <i>foto</i>', 'show_to_members': False},
                  {'name': 'Vuota', 'text': None, 'show_to_both': 1}],
              'additional_fields': [{'props': {'label': 'Taglia', 'value': 'm'}}, {'props': {'label': 'Allergie', 'value': None}}],
              'regulation_flags': (True, False, True), 'demand_flags': (False, True, False)},
    'classic': {'user': uid(3), 'id': uid(4), 'role': 'association', 'email': 'classico@example.test', 'deleted': False,
                'denomination': 'Club Classico', 'tax_code': '09876543210', 'address': None, 'address_city': None, 'address_cap': None,
                'logo': None, 'regulation': None, 'demand': '<p>Domanda</p>', 'federation': None, 'enroll_number': None, 'iban': None,
                'website': None, 'sport': None, 'abbreviated': None, 'document_header': None, 'invoice_footer': '<p>Grazie</p>',
                'subscription_template': 'subscription_classic.html', 'additional_sections': [], 'additional_fields': None,
                'regulation_flags': (False, False, False), 'demand_flags': (True, True, True)},
    'badsections': {'user': uid(5), 'id': uid(6), 'role': 'association', 'email': 'sezioni@example.test', 'deleted': False,
                    'denomination': 'Sezioni rotte', 'tax_code': '11111111111', 'address': None, 'address_city': None,
                    'address_cap': None, 'logo': None, 'regulation': None, 'demand': None, 'federation': None, 'enroll_number': None,
                    'iban': None, 'website': None, 'sport': None, 'abbreviated': None, 'document_header': None, 'invoice_footer': None,
                    'subscription_template': 'altro.html', 'additional_sections': {'name': 'non una lista'}, 'additional_fields': None,
                    'regulation_flags': (True, True, True), 'demand_flags': (True, True, True)},
    'fallback': {'user': uid(7), 'id': uid(8), 'role': 'association', 'email': 'ripiego@example.test', 'deleted': False,
                 'denomination': 'Ripiego <srl>', 'tax_code': '22222222222', 'address': 'Piazza "Duomo"', 'address_city': "L'Aquila",
                 'address_cap': '67100', 'logo': '', 'regulation': '', 'demand': None, 'federation': '', 'enroll_number': None,
                 'iban': None, 'website': None, 'sport': None, 'abbreviated': None, 'document_header': '<p><br></p>',
                 'invoice_footer': FALLBACK_FOOTER, 'subscription_template': 'altro.html', 'additional_sections': [], 'additional_fields': [],
                 'regulation_flags': (False, True, False), 'demand_flags': (False, False, False)},
    'athleteowned': {'user': uid(9), 'id': uid(10), 'role': 'athlete', 'email': 'atleta-assoc@example.test', 'deleted': False,
                     'denomination': 'Associazione di un atleta', 'tax_code': '33333333333', 'address': None, 'address_city': None,
                     'address_cap': None, 'logo': None, 'regulation': None, 'demand': None, 'federation': None, 'enroll_number': None,
                     'iban': None, 'website': None, 'sport': None, 'abbreviated': None, 'document_header': None, 'invoice_footer': None,
                     'subscription_template': None, 'additional_sections': [], 'additional_fields': None,
                     'regulation_flags': (True, True, True), 'demand_flags': (True, True, True)},
    'deleted': {'user': uid(11), 'id': uid(12), 'role': 'association', 'email': 'cancellata@example.test', 'deleted': True,
                'denomination': 'Cancellata', 'tax_code': '44444444444', 'address': None, 'address_city': None, 'address_cap': None,
                'logo': None, 'regulation': None, 'demand': None, 'federation': None, 'enroll_number': None, 'iban': None,
                'website': None, 'sport': None, 'abbreviated': None, 'document_header': None, 'invoice_footer': None,
                'subscription_template': None, 'additional_sections': [], 'additional_fields': None,
                'regulation_flags': (True, True, True), 'demand_flags': (True, True, True)},
}
# The base fixture's own association (model defaults).
OWN_ASSOCIATION = {'id': OWN, 'denomination': 'Message reads own', 'tax_code': '70000000001', 'address': None, 'address_city': None,
                   'address_cap': None, 'logo': None, 'regulation': None, 'demand': None, 'federation': None, 'enroll_number': None,
                   'document_header': None, 'invoice_footer': None, 'subscription_template': 'subscription.html',
                   'regulation_flags': (True, True, True), 'demand_flags': (True, True, True)}

ASSOCIATES = {
    'mario': {'id': uid(20), 'association': 'alpha', 'first': 'Mario', 'last': 'Rossi', 'tax_code': 'RSSMRA80A01L219X', 'sex': 'M',
              'born': '1980-01-01', 'born_city': 'Torino', 'address': 'Via Po 2', 'city': 'Torino', 'cap': '10123',
              'phone': '+39 333 0000000', 'email': 'mario@example.test', 'notes': 'Note & <x>'},
    'giulia': {'id': uid(21), 'association': 'alpha', 'first': 'Giulia', 'last': 'Bianchi', 'tax_code': 'bncgli15h55l219k', 'sex': 'F',
               'born': '2015-06-15', 'born_city': 'Asti', 'address': None, 'city': 'Asti', 'cap': None, 'phone': None,
               'email': 'giulia@example.test', 'notes': None},
    'paolo': {'id': uid(22), 'association': 'alpha', 'first': 'Paolo', 'last': 'Bianchi', 'tax_code': 'BNCPLA75C03L219Z', 'sex': 'M',
              'born': '1975-03-03', 'born_city': 'Straße', 'address': 'Corso Francia 9', 'city': 'Torino', 'cap': '10138',
              'phone': '011 123', 'email': None, 'notes': None},
    'luca': {'id': uid(23), 'association': 'alpha', 'first': 'Luca', 'last': 'Verdi', 'tax_code': None, 'sex': 'A',
             'born': '2016-02-02', 'born_city': None, 'address': None, 'city': None, 'cap': None, 'phone': None, 'email': None,
             'notes': None},
    'nulls': {'id': uid(24), 'association': 'alpha', 'first': None, 'last': None, 'tax_code': None, 'sex': '', 'born': None,
              'born_city': None, 'address': None, 'city': None, 'cap': None, 'phone': None, 'email': None, 'notes': None},
    'greek': {'id': uid(25), 'association': 'alpha', 'first': 'Δημήτρης', 'last': 'Παπαδόπουλος ', 'tax_code': 'xx', 'sex': 'M',
              'born': '1990-12-31', 'born_city': 'Αθήνα', 'address': 'Οδός 1', 'city': 'Αθήνα', 'cap': '10431', 'phone': None,
              'email': 'd@example.test', 'notes': None},
}
TUTORS = [('giulia', 'paolo', True)]
PAYMENTS = {
    'meta': {'id': uid(40), 'association': 'alpha', 'meta': {'subscription_data': {'name': 'Annuale <b>', 'subscription_fee': 30}}},
    'emptymeta': {'id': uid(41), 'association': 'alpha', 'meta': {}},
    'listmeta': {'id': uid(42), 'association': 'alpha', 'meta': [1]},
    'floatfee': {'id': uid(43), 'association': 'alpha', 'meta': {'subscription_data': {'name': 'Ridotta', 'subscription_fee': 12.345}},
                 'deleted': True},
}
SUBSCRIPTIONS = {
    'mario': {'id': uid(30), 'association': 'alpha', 'associate': 'mario', 'type': 2, 'role': 5, 'created': '2026-03-04T10:00:00+00:00',
              'payment': 'meta', 'additional_fields': [{'props': {'label': 'Note', 'value': 'ok <b>'}}], 'signature_url': None,
              'group': None},
    'giulia': {'id': uid(31), 'association': 'alpha', 'associate': 'giulia', 'type': 1, 'role': 7, 'created': '2025-12-31T23:30:00+00:00',
               'payment': 'floatfee', 'additional_fields': None, 'signature_url': None, 'group': None},
    'luca': {'id': uid(32), 'association': 'alpha', 'associate': 'luca', 'type': 3, 'role': 1, 'created': '2026-01-02T00:00:00+00:00',
             'payment': 'emptymeta', 'additional_fields': {}, 'signature_url': '', 'group': None},
    'nulls': {'id': uid(33), 'association': 'alpha', 'associate': 'nulls', 'type': 2, 'role': 8, 'created': '2026-02-03T04:05:06.123456+00:00',
              'payment': None, 'additional_fields': [], 'signature_url': None, 'group': None},
    'noassociate': {'id': uid(34), 'association': 'alpha', 'associate': None, 'type': 1, 'role': 2,
                    'created': '2026-02-03T04:05:06+00:00', 'payment': None, 'additional_fields': None, 'signature_url': None,
                    'group': None},
    'classic': {'id': uid(35), 'association': 'classic', 'associate': 'mario', 'type': 1, 'role': 1, 'created': '2026-05-05T05:05:05+00:00',
                'payment': None, 'additional_fields': None, 'signature_url': None, 'group': None},
    'badsections': {'id': uid(36), 'association': 'badsections', 'associate': 'mario', 'type': 1, 'role': 1,
                    'created': '2026-05-05T05:05:05+00:00', 'payment': None, 'additional_fields': None, 'signature_url': None,
                    'group': None},
    'listmeta': {'id': uid(37), 'association': 'alpha', 'associate': 'mario', 'type': 2, 'role': 3, 'created': '2026-05-05T05:05:05+00:00',
                 'payment': 'listmeta', 'additional_fields': None, 'signature_url': None, 'group': None},
    'group': {'id': uid(38), 'association': 'alpha', 'associate': 'greek', 'type': 2, 'role': 4, 'created': '2026-06-06T06:06:06+00:00',
              'payment': None, 'additional_fields': None, 'signature_url': None, 'group': 'own_group'},
    'fallback': {'id': uid(39), 'association': 'fallback', 'associate': 'greek', 'type': 3, 'role': 6, 'created': '2026-07-07T07:07:07+00:00',
                 'payment': None, 'additional_fields': [{'props': {'label': 'Città', 'value': 'roma'}}], 'signature_url': None,
                 'group': None},
    'own': {'id': uid(50), 'association': 'own', 'associate': 'athlete', 'type': 2, 'role': 1, 'created': '2026-08-08T08:08:08+00:00',
            'payment': None, 'additional_fields': None, 'signature_url': None, 'group': None, 'user': 'athlete'},
}
COURSES = {'nuoto': {'id': uid(60), 'title': 'Nuoto & Acqua'}, 'yoga': {'id': uid(61), 'title': 'Yoga'},
           'untitled': {'id': uid(62), 'title': None}}
COURSE_SUBSCRIPTIONS = [(uid(63), 'nuoto', 'mario', False), (uid(64), 'yoga', 'mario', True), (uid(65), 'untitled', 'luca', False)]
INSTRUCTORS = {
    'anna': {'id': uid(70), 'volunteer': False, 'first': 'Anna', 'last': "D'Amico", 'tax_code': 'dmcnna85l48l219q', 'address': 'Via Nizza',
             'civic': '12/b', 'city': 'Torino', 'born': '1985-07-08', 'born_city': 'Cuneo', 'province': 'cn', 'phone': '333',
             'email': 'anna@example.test', 'study_title': 'Laurea', 'role': 'Allenatrice', 'contract': '2024-09-01'},
    'volunteer': {'id': uid(71), 'volunteer': True, 'first': 'Vito', 'last': 'Volontario', 'tax_code': None, 'address': None,
                  'civic': None, 'city': None, 'born': None, 'born_city': None, 'province': None, 'phone': None, 'email': None,
                  'study_title': None, 'role': None, 'contract': None},
}
SIGNATURES = {'anna': {'id': uid(72), 'signature': 'data:image/png;base64,U0lH'}}
COMPENSATIONS = {
    'hours': {'id': uid(80), 'association': 'alpha', 'instructor': 'anna', 'amount': '80.00', 'date': '2026-04-05T09:00:00+00:00',
              'signature': 'anna', 'notes': 'Riga 1\nRiga <2> & 3', 'deleted': False, 'group': None},
    'volunteer': {'id': uid(81), 'association': 'alpha', 'instructor': 'volunteer', 'amount': '0.00', 'date': None, 'signature': None,
                  'notes': None, 'deleted': False, 'group': None},
    'badperiod': {'id': uid(82), 'association': 'alpha', 'instructor': 'anna', 'amount': '5.00', 'date': None, 'signature': None,
                  'notes': None, 'deleted': False, 'group': None},
    'bare': {'id': uid(83), 'association': None, 'instructor': None, 'amount': '1234.565', 'date': '2026-12-31T23:59:59+00:00',
             'signature': None, 'notes': '', 'deleted': False, 'group': None},
    'deleted': {'id': uid(84), 'association': 'alpha', 'instructor': 'anna', 'amount': '5.00', 'date': None, 'signature': None,
                'notes': None, 'deleted': True, 'group': None},
    'group': {'id': uid(85), 'association': 'alpha', 'instructor': 'volunteer', 'amount': '7.50', 'date': None, 'signature': None,
              'notes': 'g', 'deleted': False, 'group': 'own_group'},
    'nullperiod': {'id': uid(86), 'association': 'alpha', 'instructor': 'anna', 'amount': '5.00', 'date': None, 'signature': None,
                   'notes': None, 'deleted': False, 'group': None},
}
HOURS = [
    (uid(90), 'anna', 'hours', 'hourly', '2026-04-01', None),
    (uid(91), 'anna', 'hours', 'period', '2026-01-01', '01/03/2026 al 31/03/2026'),
    (uid(92), 'anna', 'hours', 'percentage', '2026-01-01', ' 5/2/2026 al 6/02/2026 al 9/9/2030'),
    (uid(93), 'anna', 'badperiod', 'period', '2026-01-01', '31/02/2026 al 01/03/2026'),
    (uid(94), 'anna', 'nullperiod', 'period', '2026-01-01', None),
]
TEMPLATES = {
    'plain': {'id': uid(100), 'association': 'alpha', 'name': 'Modulo', 'header': True, 'footer': True, 'custom': False,
              'custom_header': None, 'custom_footer': None,
              'template': '<h1>Modulo</h1><p>Io sottoscritto/a @nome @cognome (<span class="mention" data-type="mention" '
                          'key="associate.tax_code">@CF</span>), nato il <span class="mention" data-type="mention" '
                          'key="associate.born_date">@nascita</span>, tutore @nometutore, corsi: @listacorsi, oggi @dataodierna.</p>'},
    'custom': {'id': uid(101), 'association': 'alpha', 'name': 'Personalizzato', 'header': True, 'footer': False, 'custom': True,
               'custom_header': '<p><br></p>', 'custom_footer': '<p>Mai</p>', 'template': '<p>Corpo &nbsp;<b>@dataodierna</b></p>'},
    'bare': {'id': uid(102), 'association': 'classic', 'name': 'Vuoto', 'header': False, 'footer': False, 'custom': False,
             'custom_header': None, 'custom_footer': None, 'template': None},
    'foreign': {'id': uid(103), 'association': 'foreign', 'name': 'Estero', 'header': True, 'footer': True, 'custom': True,
                'custom_header': None, 'custom_footer': '<p>Piè @email</p>', 'template': '<p>Ciao @nome</p>'},
}
MEDICAL = {
    'agonistica': {'id': uid(110), 'subscription': 'mario', 'region': 'agonistica', 'date': '2026-05-06', 'sport': 'Calcio & corsa',
                   'meta': {'first_affiliation': True, 'renewal': False}},
    'minor': {'id': uid(111), 'subscription': 'giulia', 'region': 'non-agonistica', 'date': None, 'sport': None, 'meta': None},
    'noassociate': {'id': uid(112), 'subscription': 'noassociate', 'region': 'non-agonistica', 'date': '2026-01-31', 'sport': 'nuoto',
                    'meta': {}},
    'biella': {'id': uid(113), 'subscription': 'mario', 'region': 'agonistica-piemonte-biella', 'date': None, 'sport': None, 'meta': {}},
    'adult': {'id': uid(114), 'subscription': 'mario', 'region': 'non-agonistica', 'date': '2026-02-28', 'sport': 'Tennis',
              'meta': {'renewal': True}},
    'nometa': {'id': uid(115), 'subscription': 'nulls', 'region': 'agonistica', 'date': None, 'sport': '', 'meta': None},
}

cases = []

ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_HIDDEN = {'detail': 'Risorsa non disponibile per questo utente e associazione.'}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
INVALID_JWT = {'detail': 'Given token not valid for any token type', 'code': 'token_not_valid',
               'messages': [{'token_class': 'AccessToken', 'token_type': 'access', 'message': 'Token is invalid'}]}
PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']
method = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path.strip('/')}'. Contact administrator to grant access."}


def add(name, actor, path, status, response, *, headers=None, html=None, method_='GET', renders=(), language='en'):
    case = {'name': f'document-views-{name}', 'actor': actor, 'method': method_, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'renders': list(renders), 'content_language': language}
    if html is not None:
        case['html'] = html
    cases.append(case)


def common(name, path, title, *, anonymous=True):
    if anonymous:
        add(f'{name}-anonymous', 'anonymous', path, 401, ANONYMOUS)
    add(f'{name}-invalid-bearer', 'anonymous', path, 401, INVALID_JWT, headers={'Authorization': 'Bearer not-a-jwt'})
    add(f'{name}-admin-without-session', 'admin', path, 403, ADMIN)
    add(f'{name}-restricted-collaborator', 'restricted_collaborator', path, 403, unmapped(path.split('?')[0]))
    add(f'{name}-put-not-allowed', 'owner', path, 405, method('PUT'), method_='PUT')
    add(f'{name}-post-not-allowed', 'owner', path, 405, method('POST'), method_='POST')
    add(f'{name}-options', 'owner', path, 200, {'name': title, 'description': '', 'renders': ['application/json'], 'parses': PARSES},
        method_='OPTIONS')


# --- template objects --------------------------------------------------------------------------
def association_spec(label):
    return OWN_ASSOCIATION if label == 'own' else ASSOCIATIONS[label]


def sections_value(label):
    if label == 'own':
        return DEFAULT_SECTIONS
    items = []
    for section in ASSOCIATIONS[label]['additional_sections']:
        pairs = dict(section)
        show = [n for n, key in ((1, 'show_to_members'), (2, 'show_to_both'), (3, 'show_to_athletes')) if section.get(key)]
        pairs['show_to'] = show  # replaced in place, or appended
        items.append(json_value(pairs))
    return lst(*items)


def association_object(label, sections=True):
    a = association_spec(label)
    attrs = dict(logo=opt(a['logo']), denomination=s(a['denomination']), tax_code=s(a['tax_code']), address=opt(a['address']),
                 address_city=opt(a['address_city']), address_cap=opt(a['address_cap']), federation=opt(a['federation']),
                 enroll_number=opt(a['enroll_number']), regulation=opt(a['regulation']), demand=opt(a['demand']))
    if sections:
        attrs['additional_sections'] = sections_value(label)
    return obj(**attrs)


def full_name(first, last):
    return f'{first} {last}'.strip()


def age_minor(born):
    """is_minor_now for the authored dates (none near an 18th birthday)."""
    return born is None or born >= '2009-01-01'


def associate_object(label, *, with_tutor=True):
    if label == 'athlete':
        row = {'first': 'Athlete', 'last': 'Fixture', 'tax_code': None, 'sex': 'A', 'born': None, 'born_city': None, 'address': None,
               'city': None, 'cap': None, 'phone': None, 'email': None}
    else:
        row = ASSOCIATES[label]
    name = full_name(str(row['first']), str(row['last']))
    attrs = dict(first_name=opt(row['first']), last_name=opt(row['last']), tax_code=opt(row['tax_code']), sex=s(row['sex']),
                 born_date=d(row['born']), born_city=opt(row['born_city']), address=opt(row['address']),
                 address_city=opt(row['city']), address_cap=opt(row['cap']), phone=opt(row['phone']), email=opt(row['email']),
                 get_full_name=call(s(name)), full_name=s(name), is_minor_now=b(age_minor(row['born'])))
    if with_tutor:
        tutor = next((t for a, t, primary in TUTORS if a == label and primary), None)
        attrs['main_tutor'] = call(associate_object(tutor, with_tutor=False) if tutor else NONE)
    return obj(**attrs)


def subscription_context(key, header, footer, *, uid_value=None):
    sub = SUBSCRIPTIONS[key]
    a = association_spec(sub['association'])
    payment = PAYMENTS.get(sub['payment']) if sub['payment'] else None
    payment_data = json_value(payment['meta']) if payment and payment['meta'] else NONE
    subscription = obj(sport_association=association_object(sub['association']), creation_date=dt(sub['created']), type=i(sub['type']),
                       role=i(sub['role']), associate=associate_object(sub['associate']) if sub['associate'] else NONE,
                       additional_fields=json_value(sub['additional_fields']), has_signature=b(bool(sub['signature_url'])))
    show = lambda flags: lst(*[i(n) for n, flag in zip((1, 2, 3), flags) if flag])
    template = 'subscription_classic.html' if a['subscription_template'] == 'subscription_classic.html' else 'subscription.html'
    return {'template': 'document/application/' + template, 'language': 'en', 'context': {
        'uid': s(uid_value or sub['id']), 'subscription': subscription, 'payment_data': payment_data,
        'show_regulation_to': show(a['regulation_flags']), 'show_demand_to': show(a['demand_flags']), 'is_preview': b(False),
        'header': s(header), 'footer': s(footer)}}


S = lambda key: SUBSCRIPTIONS[key]['id']

# --- document/subscription/<uid>/view/ (IsAuthenticated) ---------------------------------------
SUB = lambda key: f'/document/subscription/{S(key)}/view/'
common('subscription', SUB('mario'), 'Document Subscription View')
add('subscription-document-token', 'anonymous', SUB('mario') + '?token=' + UNKNOWN, 401, ANONYMOUS)
add('subscription-bypass-header', 'anonymous', SUB('mario'), 401, ANONYMOUS, headers={'Authorization': '$bypass'})
ALPHA_SUB = (ALPHA_HEADER_OUT, alpha_footer_out(''))
for name, actor, key, headers in (
        ('subscription-owner', 'owner', 'mario', {}),
        ('subscription-minor-tutor', 'owner', 'giulia', {}),
        ('subscription-minor-without-tutor', 'owner', 'luca', {}),
        ('subscription-null-associate-fields', 'full_collaborator', 'nulls', {}),
        ('subscription-without-associate', 'owner', 'noassociate', {}),
        ('subscription-athlete-direct', 'athlete', 'mario', {}),
        ('subscription-admin-impersonating-owner', 'admin', 'mario', ADMIN_OWNER),
        ('subscription-group-own', 'owner', 'group', {'X-Group-Id': IDS['own_group']}),
        ('subscription-group-malformed', 'owner', 'group', {'X-Group-Id': 'not-a-group'}),
        ('subscription-uppercase-uid', 'owner', 'mario', {})):
    path = SUB(key) if name != 'subscription-uppercase-uid' else SUB(key).replace(S(key), S(key).upper())
    add(name, actor, path, 200, HTML, headers=headers,
        html=subscription_context(key, *ALPHA_SUB, uid_value=S(key).upper() if 'uppercase' in name else None))
add('subscription-classic-template', 'owner', SUB('classic'), 200, HTML, html=subscription_context('classic', '', '<p>Grazie</p>'))
add('subscription-fallback-template', 'owner', SUB('fallback'), 200, HTML, html=subscription_context('fallback', '<p><br/></p>', FALLBACK_FOOTER_OUT))
add('subscription-scoped-athlete', 'owner', SUB('own'), 200, HTML, headers=SCOPED, html=subscription_context('own', '', ''))
add('subscription-scoped-athlete-hidden', 'owner', SUB('mario'), 403, SCOPED_HIDDEN, headers=SCOPED)
add('subscription-group-other', 'owner', SUB('group'), 500, SERVER, headers={'X-Group-Id': IDS['foreign_group']})
add('subscription-unknown', 'owner', f'/document/subscription/{UNKNOWN}/view/', 500, SERVER)
add('subscription-invalid-uuid', 'owner', '/document/subscription/not-a-uuid/view/', 500, SERVER)
add('subscription-sections-not-a-list', 'owner', SUB('badsections'), 500, SERVER)
add('subscription-meta-list-format', 'owner', SUB('listmeta'), 500, SERVER)

# --- document/subscription/preview/ (AllowAny) -------------------------------------------------
PREVIEW = '/document/subscription/preview/'
BLANK = '____________________________'


def preview_person(minor):
    attrs = dict(first_name=s(BLANK), last_name=s(BLANK), tax_code=s(BLANK), born_city=s(BLANK), born_date=s(BLANK), address=s(BLANK),
                 address_cap=s('______________'), address_city=s(BLANK), sex=s('[ ] M [ ] F [ ] Altro'), phone=s(BLANK), email=s(BLANK),
                 get_full_name=call(s(BLANK + ' ' + BLANK)), is_minor_now=b(True))
    if minor:
        attrs['main_tutor'] = call(preview_person(False))
    return obj(**attrs)


def preview_context(label, header, footer, language='en'):
    a = ASSOCIATIONS[label]
    subscription = obj(sport_association=obj(**{**dict(association_object(label, sections=False)['v']),
                                                'additional_sections': json_value(a['additional_sections'])}),
                       creation_date=NONE, type=NONE, role=NONE, associate=preview_person(True),
                       additional_fields=json_value(a['additional_fields']), has_signature=b(False))
    template = 'subscription_classic.html' if a['subscription_template'] == 'subscription_classic.html' else 'subscription.html'
    return {'template': 'document/application/' + template, 'language': language, 'context': {
        'uid': NONE, 'subscription': subscription, 'payment_data': NONE, 'show_regulation_to': lst(i(1), i(2), i(3)),
        'show_demand_to': lst(i(1), i(2), i(3)), 'is_preview': b(True), 'header': s(header), 'footer': s(footer)}}


A = lambda label: ASSOCIATIONS[label]['id']
add('preview-restricted-collaborator', 'restricted_collaborator', PREVIEW, 403, unmapped(PREVIEW))
add('preview-admin-without-session', 'admin', PREVIEW, 403, ADMIN)
add('preview-invalid-bearer', 'anonymous', PREVIEW, 401, INVALID_JWT, headers={'Authorization': 'Bearer not-a-jwt'})
add('preview-put-not-allowed', 'anonymous', PREVIEW, 405, method('PUT'), method_='PUT')
add('preview-options', 'anonymous', PREVIEW, 200, {'name': 'Document Subscription Preview', 'description': '',
                                                   'renders': ['application/json'], 'parses': PARSES}, method_='OPTIONS')
add('preview-anonymous', 'anonymous', f"{PREVIEW}?sport_association_id={A('alpha')}", 200, HTML,
    html=preview_context('alpha', ALPHA_HEADER_OUT, alpha_footer_out('')))
add('preview-owner-classic', 'owner', f"{PREVIEW}?sport_association_id={A('classic')}", 200, HTML,
    html=preview_context('classic', '', '<p>Grazie</p>'))
add('preview-fallback-italian', 'anonymous', f"{PREVIEW}?sport_association_id={A('fallback')}&x=1", 200, HTML,
    headers={'Accept-Language': 'it'}, language='it', html=preview_context('fallback', '<p><br/></p>', FALLBACK_FOOTER_OUT, 'it'))
add('preview-repeated-parameter', 'anonymous', f"{PREVIEW}?sport_association_id={A('classic')}&sport_association_id={A('alpha')}", 200,
    HTML, html=preview_context('alpha', ALPHA_HEADER_OUT, alpha_footer_out('')))
add('preview-without-association', 'owner', PREVIEW, 500, SERVER)
add('preview-anonymous-without-association', 'anonymous', PREVIEW, 500, SERVER)
add('preview-empty-association', 'anonymous', f'{PREVIEW}?sport_association_id=', 500, SERVER)
add('preview-invalid-association', 'anonymous', f'{PREVIEW}?sport_association_id=xyz', 500, SERVER)
add('preview-unknown-association', 'anonymous', f'{PREVIEW}?sport_association_id={UNKNOWN}', 500, SERVER)
add('preview-deleted-association', 'anonymous', f"{PREVIEW}?sport_association_id={A('deleted')}", 500, SERVER)
add('preview-athlete-user', 'anonymous', f"{PREVIEW}?sport_association_id={A('athleteowned')}", 403,
    {'detail': 'User is not a sport association'})
add('preview-scoped-foreign', 'owner', f"{PREVIEW}?sport_association_id={FOREIGN}", 403, SCOPED_HIDDEN, headers=SCOPED)
PDF_OK = {'status': 200, 'content_type': 'application/pdf', 'body': '$pdf'}
add('preview-print', 'anonymous', f"{PREVIEW}?print&sport_association_id={A('alpha')}", 200, {'file': '$pdf-base64'},
    renders=[{'url': f"/document/subscription/preview/?sport_association_id={A('alpha')}", 'reply': PDF_OK}])
add('preview-print-without-association', 'owner', f'{PREVIEW}?print=0', 200, {'file': '$pdf-base64'},
    renders=[{'url': '/document/subscription/preview/?sport_association_id=None', 'reply': PDF_OK}])
add('preview-print-renderer-error', 'anonymous', f"{PREVIEW}?print=1&sport_association_id=x%20y", 500, {'error': 'PDF not rendered'},
    renders=[{'url': '/document/subscription/preview/?sport_association_id=x y',
              'reply': {'status': 502, 'content_type': 'text/plain', 'body': 'down'}}])
add('preview-print-not-pdf', 'anonymous', f'{PREVIEW}?print=', 500, {'error': 'PDF not rendered'},
    renders=[{'url': '/document/subscription/preview/?sport_association_id=None',
              'reply': {'status': 200, 'content_type': 'application/pdf', 'body': 'not a pdf'}}])

# --- document/compensation/<uid>/view/ (IsAuthenticated) ---------------------------------------
COMP = lambda key: f"/document/compensation/{COMPENSATIONS[key]['id']}/view/"


def compensation_context(key, from_date, to_date):
    row = COMPENSATIONS[key]
    instructor = NONE
    if row['instructor']:
        ins = INSTRUCTORS[row['instructor']]
        instructor = obj(is_volunteer=b(ins['volunteer']), first_name=s(ins['first']), last_name=s(ins['last']),
                         tax_code=opt(ins['tax_code']), address=opt(ins['address']), civic_number=opt(ins['civic']),
                         address_city=opt(ins['city']), born_date=d(ins['born']), born_city=opt(ins['born_city']),
                         born_province=opt(ins['province']), phone=opt(ins['phone']), email=opt(ins['email']),
                         study_title=opt(ins['study_title']), role=opt(ins['role']), stipulated_contract_in=d(ins['contract']))
    payment = obj(sport_association=association_object(row['association'], sections=False) if row['association'] else NONE,
                  instructor=instructor, amount=dec(row['amount']), payment_date=dt(row['date']),
                  signature=obj(signature=s(SIGNATURES[row['signature']]['signature'])) if row['signature'] else NONE,
                  notes=opt(row['notes']))
    return {'template': 'document/application/compensation.html', 'language': 'it',
            'context': {'payment': payment, 'from_date': d(from_date), 'to_date': d(to_date)}}


common('compensation', COMP('hours'), 'Document Compensation View')
add('compensation-owner', 'owner', COMP('hours'), 200, HTML, language='it', html=compensation_context('hours', '2026-02-05', '2026-04-01'))
add('compensation-volunteer-without-hours', 'owner', COMP('volunteer'), 200, HTML, language='it',
    html=compensation_context('volunteer', None, None))
add('compensation-bare', 'full_collaborator', COMP('bare'), 200, HTML, language='it', html=compensation_context('bare', None, None))
add('compensation-group-own', 'owner', COMP('group'), 200, HTML, language='it', headers={'X-Group-Id': IDS['own_group']},
    html=compensation_context('group', None, None))
add('compensation-group-other', 'owner', COMP('group'), 500, SERVER, headers={'X-Group-Id': IDS['foreign_group']})
add('compensation-athlete-direct', 'athlete', COMP('volunteer'), 200, HTML, language='it',
    html=compensation_context('volunteer', None, None))
add('compensation-admin-impersonating-owner', 'admin', COMP('volunteer'), 200, HTML, language='it', headers=ADMIN_OWNER,
    html=compensation_context('volunteer', None, None))
add('compensation-scoped-athlete-denied', 'owner', COMP('hours'), 403, SCOPED_DENIED, headers=SCOPED)
add('compensation-invalid-period', 'owner', COMP('badperiod'), 500, SERVER)
add('compensation-null-period', 'owner', COMP('nullperiod'), 500, SERVER)
add('compensation-deleted', 'owner', COMP('deleted'), 500, SERVER)
add('compensation-unknown', 'owner', f'/document/compensation/{UNKNOWN}/view/', 500, SERVER)
add('compensation-invalid-uuid', 'owner', '/document/compensation/12/view/', 500, SERVER)

# --- document/template/<uid>/view/ (IsAuthenticated | HasBypassAuthorizationHeader) ------------
TPL = lambda key: f"/document/template/{TEMPLATES[key]['id']}/view/"


def model_context(body, header, footer):
    return {'template': 'document/application/model.html', 'language': 'en',
            'context': {'template': s(body), 'header': s(header), 'footer': s(footer)}}


def plain_body(name='', tax='', born='', tutor='', courses=''):
    return (f'<h1>Modulo</h1><p>Io sottoscritto/a {name} ({tax}), nato il {born}, tutore {tutor}, corsi: {courses}, '
            'oggi $today.</p>')


common('template', TPL('plain'), 'Document Template View')
add('template-document-token', 'anonymous', TPL('plain') + '?token=' + UNKNOWN, 401, ANONYMOUS)
add('template-bypass-invalid', 'anonymous', TPL('plain'), 401, ANONYMOUS, headers={'Authorization': 'not-the-token'})
add('template-bypass-non-ascii', 'anonymous', TPL('plain'), 500, SERVER, headers={'Authorization': 'tokèn'})
PLAIN_EMPTY = model_context(plain_body(' '), ALPHA_HEADER_OUT, alpha_footer_out('[]'))
for name, actor, query, headers in (
        ('template-owner', 'owner', '', {}),
        ('template-bypass', 'anonymous', '', {'Authorization': '$bypass'}),
        ('template-foreign-owner', 'foreign_owner', '', {}),
        ('template-athlete', 'athlete', '', {}),
        ('template-unknown-subscription', 'owner', '?subscription_id=' + UNKNOWN, {}),
        ('template-invalid-subscription', 'owner', '?subscription_id=nope', {}),
        ('template-empty-subscription', 'owner', '?subscription_id=', {}),
        ('template-untitled-course', 'owner', f"?subscription_id={S('luca')}", {})):
    add(name, actor, TPL('plain') + query, 200, HTML, headers=headers, html=PLAIN_EMPTY)
add('template-subscription', 'owner', f"{TPL('plain')}?subscription_id={S('mario')}", 200, HTML,
    html=model_context(plain_body('Mario Rossi', 'RSSMRA80A01L219X', '01/01/1980', '', 'Nuoto &amp; Acqua'), ALPHA_HEADER_OUT,
                       alpha_footer_out('Nuoto &amp; Acqua')))
add('template-subscription-tutor', 'owner', f"{TPL('plain')}?subscription_id={S('mario')}&subscription_id={S('giulia')}", 200, HTML,
    html=model_context(plain_body('Giulia Bianchi', 'bncgli15h55l219k', '15/06/2015', 'Paolo', ''), ALPHA_HEADER_OUT,
                       alpha_footer_out('')))
add('template-subscription-without-associate', 'owner', f"{TPL('plain')}?subscription_id={S('noassociate')}", 200, HTML,
    html=model_context(plain_body(' '), ALPHA_HEADER_OUT, alpha_footer_out('')))
add('template-subscription-bypass', 'anonymous', f"{TPL('plain')}?subscription_id={S('mario')}", 200, HTML,
    headers={'Authorization': '$bypass'},
    html=model_context(plain_body('Mario Rossi', 'RSSMRA80A01L219X', '01/01/1980', '', 'Nuoto &amp; Acqua'), ALPHA_HEADER_OUT,
                       alpha_footer_out('Nuoto &amp; Acqua')))
add('template-subscription-group-hidden', 'owner', f"{TPL('plain')}?subscription_id={S('group')}", 200, HTML,
    headers={'X-Group-Id': IDS['foreign_group']}, html=PLAIN_EMPTY)
add('template-custom-header', 'owner', TPL('custom'), 200, HTML,
    html=model_context('<p>Corpo  <b>$today</b></p>', '<p><br/></p>', ''))
add('template-without-header-footer', 'owner', TPL('bare'), 200, HTML, html=model_context('', '', ''))
add('template-foreign-custom-footer', 'owner', TPL('foreign'), 200, HTML, html=model_context('<p>Ciao </p>', '', '<p>Piè </p>'))
add('template-unknown', 'owner', f'/document/template/{UNKNOWN}/view/', 500, SERVER)
add('template-invalid-uuid', 'owner', '/document/template/x/view/', 500, SERVER)
add('template-scoped-athlete-denied', 'owner', TPL('plain'), 403, SCOPED_DENIED, headers=SCOPED)

# --- document/medical-appointment/<uid>/view/ (AllowAny) ---------------------------------------
MED = lambda key: f"/document/medical-appointment/{MEDICAL[key]['id']}/view/"


def medical_context(key):
    row = MEDICAL[key]
    sub = SUBSCRIPTIONS[row['subscription']]
    associate = associate_object(sub['associate']) if sub['associate'] else NONE
    template = 'non-agonistica.html' if row['region'] == 'non-agonistica' else 'agonistica.html'
    return {'template': 'document/medical-appointments/' + template, 'language': 'it', 'context': {
        'medical_appointment': obj(date=d(row['date']), sport=opt(row['sport'])),
        'sport_association': association_object(sub['association'], sections=False), 'associate': associate,
        'meta': json_value(row['meta'])}}


add('medical-invalid-bearer', 'anonymous', MED('agonistica'), 401, INVALID_JWT, headers={'Authorization': 'Bearer not-a-jwt'})
add('medical-admin-without-session', 'admin', MED('agonistica'), 403, ADMIN)
add('medical-restricted-collaborator', 'restricted_collaborator', MED('agonistica'), 403, unmapped(MED('agonistica')))
add('medical-put-not-allowed', 'anonymous', MED('agonistica'), 405, method('PUT'), method_='PUT')
add('medical-options', 'anonymous', MED('agonistica'), 200, {'name': 'Document Medical Appointment View', 'description': '',
                                                             'renders': ['application/json'], 'parses': PARSES}, method_='OPTIONS')
for name, actor, key in (('medical-anonymous', 'anonymous', 'agonistica'), ('medical-owner', 'owner', 'agonistica'),
                         ('medical-minor', 'anonymous', 'minor'), ('medical-without-associate', 'foreign_owner', 'noassociate'),
                         ('medical-adult-non-agonistica', 'athlete', 'adult'), ('medical-null-meta', 'anonymous', 'nometa')):
    add(name, actor, MED(key), 200, HTML, language='it', html=medical_context(key))
add('medical-unsupported-region', 'anonymous', MED('biella'), 303,
    {'error': 'This kind of medical appointment is not supported in this endpoint'})
add('medical-unknown', 'anonymous', f'/document/medical-appointment/{UNKNOWN}/view/', 500, SERVER)
add('medical-invalid-uuid', 'anonymous', '/document/medical-appointment/nope/view/', 400, ['badly formed hexadecimal UUID string'])
add('medical-invalid-uuid-digits', 'anonymous', '/document/medical-appointment/' + 'g' * 32 + '/view/', 400,
    ["invalid literal for int() with base 16: '" + 'g' * 32 + "'"])
add('medical-scoped-athlete-denied', 'owner', MED('agonistica'), 403, SCOPED_DENIED, headers=SCOPED)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'document HTML views',
    'ids': IDS, 'associations': ASSOCIATIONS, 'associates': ASSOCIATES, 'tutors': TUTORS, 'payments': PAYMENTS,
    'subscriptions': SUBSCRIPTIONS, 'courses': COURSES, 'course_subscriptions': COURSE_SUBSCRIPTIONS, 'instructors': INSTRUCTORS,
    'signatures': SIGNATURES, 'compensations': COMPENSATIONS, 'hours': HOURS, 'templates': TEMPLATES, 'medical': MEDICAL,
    'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
