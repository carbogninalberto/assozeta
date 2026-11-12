"""Author the document-views HTTP sequence (document-views.md).

The HTML views of BE/docmanager/views/printing_views.py that the renderer
service prints (@api_view(['GET'])):

- document/subscription/<uid>/view/ (IsAuthenticated): the scoped, group-aware
  subscription; payment_data is its payment's meta when truthy; each
  additional section of the association gets show_to ([1] members, [2] both,
  [3] athletes); show_regulation_to/show_demand_to from the association flags;
  header/footer through filter_mentions; the association's subscription
  template (subscription.html unless subscription_classic.html).
- document/subscription/preview/ (IsAuthenticated): ?print asks the renderer for
  '<path>?sport_association_id=<value>' with the request's Authorization header and answers
  {'file': base64} or 500 {'error': 'PDF not rendered'}; otherwise the live,
  scoped association named by ?sport_association_id with a placeholder
  subscription (no parameter: 500).
- document/compensation/<uid>/view/ (IsAuthenticated): the scoped, live,
  group-aware payment, served to the association's staff and to the account
  tied to its instructor; from/to are the min/max of its hours' dates (hourly)
  and periods ('dd/mm/YYYY al dd/mm/YYYY'); Italian is activated.
- document/template/<uid>/view/ (IsAuthenticated | HasBypassAuthorizationHeader):
  the module template by pk, served to the association's staff; its association's (or custom) header/footer when
  enabled and its body through filter_mentions; ?subscription_id adds the
  subscription's associate, main tutor and courses list when every step works.
- document/medical-appointment/<uid>/view/ (IsAuthenticated |
  HasBypassAuthorizationHeader): is_valid_uuid (400), the appointment by pk
  (missing: 500), served to the association's staff and to the member of its
  subscription, 303 for regions without an HTML
  template, then agonistica/non-agonistica.html in Italian.

Every HTML expectation is an authored template context (the values the view
must pass, the header/footer/body filter_mentions output written out by hand)
that the contract renders with Django's own template engine; '$today' is the
UTC date as dd/mm/YYYY.

Run with ``python3 -I``.
"""
import base64
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
# Signature objects stored in the family's private bucket: the view reads them
# as default_storage does and writes the bytes as a PNG data URI.
SIGNATURE_OBJECTS = [{'name': 'signatures/t6x2-stored.png', 'body_b64': base64.b64encode(b'signature fixture bytes').decode('ascii')}]
SIGNATURE_MISSING = 'signatures/t6x2-missing.png'


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
                '@Email</span>, iscritto @nome<span class="mention" data-type="mention" key="subscription.tessera_vecchia">@tessera'
                '</span><span class="mention" data-type="mention" key="sport_association.codice_coni">@coni</span></p>')
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
    # JSON column values: the subscription's additional_fields hold a big int, a
    # float, a list, a dict and a non-ASCII string; a section is stored with
    # show_to as a string (the view rewrites it from the flags).
    'jsonvalues': {'user': uid(13), 'id': uid(14), 'role': 'association', 'email': 'valori@example.test', 'deleted': False,
                   'denomination': 'Valori & <JSON>', 'tax_code': '55555555555', 'address': None, 'address_city': None,
                   'address_cap': None, 'logo': None, 'regulation': None, 'demand': None, 'federation': None, 'enroll_number': None,
                   'iban': None, 'website': None, 'sport': None, 'abbreviated': None, 'document_header': None, 'invoice_footer': None,
                   'subscription_template': 'subscription.html',
                   'additional_sections': [
                       {'name': 'Sezione ß', 'text': '<p>Testo &amp; <b>grassetto</b></p>', 'show_to': 'solo-soci',
                        'show_to_both': True},
                       {'name': 'Sezione Σ', 'text': None, 'show_to_members': True}],
                   'additional_fields': None, 'regulation_flags': (False, False, False), 'demand_flags': (False, False, False)},
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
    # Minors whose tutor relations have no primary one: get_main_tutor() promotes the first.
    'sara': {'id': uid(26), 'association': 'alpha', 'first': 'Sara', 'last': 'Neri', 'tax_code': None, 'sex': 'F',
             'born': '2014-04-04', 'born_city': 'Cuneo', 'address': None, 'city': None, 'cap': None, 'phone': None, 'email': None,
             'notes': None},
    'tino': {'id': uid(27), 'association': 'alpha', 'first': 'Tino', 'last': 'Gialli', 'tax_code': 'GLLTNI13A01L219B', 'sex': 'M',
             'born': '2013-01-01', 'born_city': None, 'address': None, 'city': None, 'cap': None, 'phone': None, 'email': None,
             'notes': None},
}
TUTORS = [('giulia', 'paolo', True), ('sara', 'paolo', False), ('sara', 'mario', False), ('tino', 'mario', False)]
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
    'sara': {'id': uid(44), 'association': 'alpha', 'associate': 'sara', 'type': 1, 'role': 1, 'created': '2026-04-04T04:04:04+00:00',
             'payment': None, 'additional_fields': None, 'signature_url': None, 'group': None},
    'tino': {'id': uid(45), 'association': 'alpha', 'associate': 'tino', 'type': 1, 'role': 1, 'created': '2026-04-05T04:04:04+00:00',
             'payment': None, 'additional_fields': None, 'signature_url': None, 'group': None},
    # The stored signature: one object under the bucket's location, one missing name.
    'signaturestored': {'id': uid(46), 'association': 'alpha', 'associate': 'mario', 'type': 2, 'role': 5,
                        'created': '2026-10-10T10:10:10+00:00', 'payment': None, 'additional_fields': None,
                        'signature_url': None, 'signature_key': SIGNATURE_OBJECTS[0]['name'], 'group': None},
    'signaturemissing': {'id': uid(47), 'association': 'alpha', 'associate': 'mario', 'type': 2, 'role': 5,
                         'created': '2026-10-11T11:11:11+00:00', 'payment': None, 'additional_fields': None,
                         'signature_url': None, 'signature_key': SIGNATURE_MISSING, 'group': None},
    'own': {'id': uid(50), 'association': 'own', 'associate': 'athlete', 'type': 2, 'role': 1, 'created': '2026-08-08T08:08:08+00:00',
            'payment': None, 'additional_fields': None, 'signature_url': None, 'group': None, 'user': 'athlete'},
    # A later subscription of the athlete's associate, entered by the association.
    'renewal': {'id': uid(51), 'association': 'own', 'associate': 'athlete', 'type': 1, 'role': 2, 'created': '2026-09-09T09:09:09+00:00',
                'payment': None, 'additional_fields': None, 'signature_url': None, 'group': None},
    # JSON values in additional_fields (big int, float, list, dict, non-ASCII
    # string) and a number in additional_fields (iterating it raises).
    'jsonvalues': {'id': uid(46), 'association': 'jsonvalues', 'associate': 'greek', 'type': 2, 'role': 1,
                   'created': '2026-10-10T10:10:10+00:00', 'payment': None,
                   'additional_fields': [
                       {'props': {'label': 'Numero grande', 'value': 12345678901234567890123}},
                       {'props': {'label': 'Peso', 'value': 12.5}},
                       {'props': {'label': 'Lista', 'value': [1, 'due', None]}},
                       {'props': {'label': 'Mappa', 'value': {'chiave': 'valore'}}},
                       {'props': {'label': 'Città', 'value': 'Ǻthos ŉ ǰ ß'}}],
                   'signature_url': None, 'group': None},
    'fieldsnumber': {'id': uid(47), 'association': 'alpha', 'associate': 'mario', 'type': 2, 'role': 1,
                     'created': '2026-10-11T10:10:10+00:00', 'payment': None,
                     'additional_fields': 12345678901234567890123, 'signature_url': None, 'group': None},
}
# Another member of the association (an athlete account without records).
EXTRA_USERS = {'member': {'id': uid(52), 'username': 'document-views-member', 'email': 'document-views-member@example.test'},
               # The account tied to the instructor anna.
               'coach': {'id': uid(53), 'username': 'document-views-coach', 'email': 'document-views-coach@example.test'}}
COURSES = {'nuoto': {'id': uid(60), 'title': 'Nuoto & Acqua'}, 'yoga': {'id': uid(61), 'title': 'Yoga'},
           'untitled': {'id': uid(62), 'title': None}}
COURSE_SUBSCRIPTIONS = [(uid(63), 'nuoto', 'mario', False), (uid(64), 'yoga', 'mario', True), (uid(65), 'untitled', 'luca', False)]
INSTRUCTORS = {
    'anna': {'id': uid(70), 'volunteer': False, 'first': 'Anna', 'last': "D'Amico", 'tax_code': 'dmcnna85l48l219q', 'address': 'Via Nizza',
             'civic': '12/b', 'city': 'Torino', 'born': '1985-07-08', 'born_city': 'Cuneo', 'province': 'cn', 'phone': '333',
             'email': 'anna@example.test', 'study_title': 'Laurea', 'role': 'Allenatrice', 'contract': '2024-09-01', 'account': 'coach'},
    'volunteer': {'id': uid(71), 'volunteer': True, 'first': 'Vito', 'last': 'Volontario', 'tax_code': None, 'address': None,
                  'civic': None, 'city': None, 'born': None, 'born_city': None, 'province': None, 'phone': None, 'email': None,
                  'study_title': None, 'role': None, 'contract': None, 'account': None},
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
    # Periods written with the decimal digits of other scripts (strptime reads them).
    'digits': {'id': uid(87), 'association': 'alpha', 'instructor': 'anna', 'amount': '6.00', 'date': None, 'signature': None,
               'notes': None, 'deleted': False, 'group': None},
    'digitsmonth': {'id': uid(88), 'association': 'alpha', 'instructor': 'anna', 'amount': '6.00', 'date': None, 'signature': None,
                    'notes': None, 'deleted': False, 'group': None},
}
HOURS = [
    (uid(90), 'anna', 'hours', 'hourly', '2026-04-01', None),
    (uid(91), 'anna', 'hours', 'period', '2026-01-01', '01/03/2026 al 31/03/2026'),
    (uid(92), 'anna', 'hours', 'percentage', '2026-01-01', ' 5/2/2026 al 6/02/2026 al 9/9/2030'),
    (uid(93), 'anna', 'badperiod', 'period', '2026-01-01', '31/02/2026 al 01/03/2026'),
    (uid(94), 'anna', 'nullperiod', 'period', '2026-01-01', None),
    (uid(95), 'anna', 'digits', 'period', '2026-01-01', '1\u0665/03/\u0662\u0660\u0662\u0666 al 2\u06f9/03/202\uff16'),
    (uid(96), 'anna', 'digitsmonth', 'period', '2026-01-01', '15/0\u0663/2026 al 29/03/2026'),
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
    # Keys of earlier versions: names the models no longer have read as the empty text.
    'oldkeys': {'id': uid(104), 'association': 'alpha', 'name': 'Chiavi', 'header': False, 'footer': False, 'custom': False,
                'custom_header': None, 'custom_footer': None,
                'template': '<p>a' + ''.join(f'<span class="mention" data-type="mention" key="{key}">@x</span>|' for key in (
                    'sport_association.ragione_sociale', 'sport_association.presidente.nome', 'associate.soprannome',
                    'associate.tessera.numero', 'main_tutor.cellulare', 'other.stagione', 'other.stagione.anno',
                    'associate.first_name', 'other.today')) + 'z</p>'},
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
    'athlete': {'id': uid(116), 'subscription': 'own', 'region': 'agonistica', 'date': '2026-03-03', 'sport': 'Vela', 'meta': {}},
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


def add(name, actor, path, status, response, *, headers=None, html=None, method_='GET', renders=(), language='en', promoted=()):
    # promoted: [associate, actor] pairs whose first tutor relation the case saves as primary.
    case = {'name': f'document-views-{name}', 'actor': actor, 'method': method_, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'renders': list(renders), 'content_language': language,
            'promoted': [list(pair) for pair in promoted]}
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
        # The primary relation's tutor, else the first relation's (promoted by the view).
        tutor = next((t for a, t, primary in TUTORS if a == label and primary), None) or next(
            (t for a, t, primary in TUTORS if a == label), None)
        attrs['main_tutor'] = call(associate_object(tutor, with_tutor=False) if tutor else NONE)
    return obj(**attrs)


def signature_context_value(sub):
    """Subscription.get_signature_base64: a readable stored object's data URI, else None (the legacy URL stays refused)."""
    key, url = sub.get('signature_key'), sub['signature_url']
    if key:
        stored = next((row for row in SIGNATURE_OBJECTS if row['name'] == key), None)
        if stored:
            return s('data:image/png;base64,' + stored['body_b64'])
    assert not url, 'fixture.signature-url-refused'
    return NONE


def subscription_context(key, header, footer, *, uid_value=None):
    sub = SUBSCRIPTIONS[key]
    a = association_spec(sub['association'])
    payment = PAYMENTS.get(sub['payment']) if sub['payment'] else None
    payment_data = json_value(payment['meta']) if payment and payment['meta'] else NONE
    has_signature = bool(sub['signature_url']) or bool(sub.get('signature_key'))
    attrs = dict(sport_association=association_object(sub['association']), creation_date=dt(sub['created']), type=i(sub['type']),
                 role=i(sub['role']), associate=associate_object(sub['associate']) if sub['associate'] else NONE,
                 additional_fields=json_value(sub['additional_fields']), has_signature=b(has_signature))
    if has_signature:
        attrs['get_signature_base64'] = signature_context_value(sub)
    subscription = obj(**attrs)
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
        ('subscription-admin-impersonating-owner', 'admin', 'mario', ADMIN_OWNER),
        ('subscription-group-own', 'owner', 'group', {'X-Group-Id': IDS['own_group']}),
        ('subscription-group-malformed', 'owner', 'group', {'X-Group-Id': 'not-a-group'}),
        ('subscription-uppercase-uid', 'owner', 'mario', {})):
    path = SUB(key) if name != 'subscription-uppercase-uid' else SUB(key).replace(S(key), S(key).upper())
    add(name, actor, path, 200, HTML, headers=headers,
        html=subscription_context(key, *ALPHA_SUB, uid_value=S(key).upper() if 'uppercase' in name else None))
# get_main_tutor() without a primary relation: the first relation is saved as primary while the page renders.
add('subscription-minor-promotes-tutor', 'owner', SUB('sara'), 200, HTML, html=subscription_context('sara', *ALPHA_SUB),
    promoted=[('sara', 'owner')])
add('subscription-minor-promoted-tutor-again', 'owner', SUB('sara'), 200, HTML, html=subscription_context('sara', *ALPHA_SUB))
# HEAD is not among the view's methods (the body is not sent).
add('subscription-head', 'owner', SUB('mario'), 405, method('HEAD'), method_='HEAD')
add('subscription-head-anonymous', 'anonymous', SUB('mario'), 401, ANONYMOUS, method_='HEAD')
add('subscription-classic-template', 'owner', SUB('classic'), 200, HTML, html=subscription_context('classic', '', '<p>Grazie</p>'))
# get_signature_base64: the stored object as a PNG data URI, or None when the object is missing and no legacy URL is set.
add('subscription-signature-stored', 'owner', SUB('signaturestored'), 200, HTML,
    html=subscription_context('signaturestored', *ALPHA_SUB))
add('subscription-signature-missing', 'owner', SUB('signaturemissing'), 200, HTML,
    html=subscription_context('signaturemissing', *ALPHA_SUB))
add('subscription-fallback-template', 'owner', SUB('fallback'), 200, HTML, html=subscription_context('fallback', '<p><br/></p>', FALLBACK_FOOTER_OUT))
# The JSON columns' values render as json.loads gives them (a big int, a
# float, a list, a dict and a non-ASCII string); additional_fields holding a
# number is iterated, which raises TypeError in Django (the 500 page).
add('subscription-json-values', 'owner', SUB('jsonvalues'), 200, HTML, html=subscription_context('jsonvalues', '', ''))
add('subscription-additional-fields-number', 'owner', SUB('fieldsnumber'), 500, SERVER)
add('subscription-scoped-athlete', 'owner', SUB('own'), 200, HTML, headers=SCOPED, html=subscription_context('own', '', ''))
# The page is rendered for the association's staff and for the member the subscription belongs to.
add('subscription-athlete-own', 'athlete', SUB('own'), 200, HTML, html=subscription_context('own', '', ''))
add('subscription-athlete-associate', 'athlete', SUB('renewal'), 200, HTML, html=subscription_context('renewal', '', ''))
add('subscription-athlete-direct', 'athlete', SUB('mario'), 403, SCOPED_HIDDEN)
add('subscription-athlete-without-associate', 'athlete', SUB('noassociate'), 403, SCOPED_HIDDEN)
add('subscription-athlete-sections-not-a-list', 'athlete', SUB('badsections'), 403, SCOPED_HIDDEN)
add('subscription-athlete-unknown', 'athlete', f'/document/subscription/{UNKNOWN}/view/', 500, SERVER)
add('subscription-member-of-athlete', 'member', SUB('own'), 403, SCOPED_HIDDEN)
add('subscription-member-of-associate', 'member', SUB('renewal'), 403, SCOPED_HIDDEN)
add('subscription-full-collaborator-of-athlete', 'full_collaborator', SUB('own'), 200, HTML, html=subscription_context('own', '', ''))
add('subscription-foreign-owner-of-athlete', 'foreign_owner', SUB('own'), 200, HTML, html=subscription_context('own', '', ''))
add('subscription-scoped-athlete-hidden', 'owner', SUB('mario'), 403, SCOPED_HIDDEN, headers=SCOPED)
add('subscription-group-other', 'owner', SUB('group'), 500, SERVER, headers={'X-Group-Id': IDS['foreign_group']})
add('subscription-unknown', 'owner', f'/document/subscription/{UNKNOWN}/view/', 500, SERVER)
add('subscription-invalid-uuid', 'owner', '/document/subscription/not-a-uuid/view/', 500, SERVER)
add('subscription-sections-not-a-list', 'owner', SUB('badsections'), 500, SERVER)
add('subscription-meta-list-format', 'owner', SUB('listmeta'), 500, SERVER)

# --- document/subscription/preview/ (IsAuthenticated) -------------------------------------------------
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
add('preview-put-not-allowed', 'owner', PREVIEW, 405, method('PUT'), method_='PUT')
# Anonymous requests are asked for credentials, whatever the query; the renderer is not called.
add('preview-anonymous', 'anonymous', f"{PREVIEW}?sport_association_id={A('alpha')}", 401, ANONYMOUS)
add('preview-anonymous-without-association', 'anonymous', PREVIEW, 401, ANONYMOUS)
add('preview-anonymous-print', 'anonymous', f"{PREVIEW}?print&sport_association_id={A('alpha')}", 401, ANONYMOUS)
add('preview-anonymous-put', 'anonymous', PREVIEW, 401, ANONYMOUS, method_='PUT')
add('preview-athlete', 'athlete', f"{PREVIEW}?sport_association_id={A('classic')}", 200, HTML,
    html=preview_context('classic', '', '<p>Grazie</p>'))
add('preview-options', 'owner', PREVIEW, 200, {'name': 'Document Subscription Preview', 'description': '',
                                                   'renders': ['application/json'], 'parses': PARSES}, method_='OPTIONS')
add('preview-owner-alpha', 'owner', f"{PREVIEW}?sport_association_id={A('alpha')}", 200, HTML,
    html=preview_context('alpha', ALPHA_HEADER_OUT, alpha_footer_out('')))
add('preview-owner-classic', 'owner', f"{PREVIEW}?sport_association_id={A('classic')}", 200, HTML,
    html=preview_context('classic', '', '<p>Grazie</p>'))
add('preview-fallback-italian', 'owner', f"{PREVIEW}?sport_association_id={A('fallback')}&x=1", 200, HTML,
    headers={'Accept-Language': 'it'}, language='it', html=preview_context('fallback', '<p><br/></p>', FALLBACK_FOOTER_OUT, 'it'))
add('preview-repeated-parameter', 'owner', f"{PREVIEW}?sport_association_id={A('classic')}&sport_association_id={A('alpha')}", 200,
    HTML, html=preview_context('alpha', ALPHA_HEADER_OUT, alpha_footer_out('')))
add('preview-without-association', 'owner', PREVIEW, 500, SERVER)
add('preview-empty-association', 'owner', f'{PREVIEW}?sport_association_id=', 500, SERVER)
add('preview-invalid-association', 'owner', f'{PREVIEW}?sport_association_id=xyz', 500, SERVER)
add('preview-unknown-association', 'owner', f'{PREVIEW}?sport_association_id={UNKNOWN}', 500, SERVER)
add('preview-deleted-association', 'owner', f"{PREVIEW}?sport_association_id={A('deleted')}", 500, SERVER)
add('preview-athlete-user', 'owner', f"{PREVIEW}?sport_association_id={A('athleteowned')}", 403,
    {'detail': 'User is not a sport association'})
add('preview-scoped-foreign', 'owner', f"{PREVIEW}?sport_association_id={FOREIGN}", 403, SCOPED_HIDDEN, headers=SCOPED)
PDF_OK = {'status': 200, 'content_type': 'application/pdf', 'body': '$pdf'}
add('preview-print', 'owner', f"{PREVIEW}?print&sport_association_id={A('alpha')}", 200, {'file': '$pdf-base64'},
    renders=[{'url': f"/document/subscription/preview/?sport_association_id={A('alpha')}", 'authorization': '$bearer', 'reply': PDF_OK}])
add('preview-print-without-association', 'owner', f'{PREVIEW}?print=0', 200, {'file': '$pdf-base64'},
    renders=[{'url': '/document/subscription/preview/?sport_association_id=None', 'authorization': '$bearer', 'reply': PDF_OK}])
add('preview-print-renderer-error', 'owner', f"{PREVIEW}?print=1&sport_association_id=x%20y", 500, {'error': 'PDF not rendered'},
    renders=[{'url': '/document/subscription/preview/?sport_association_id=x y',
              'authorization': '$bearer', 'reply': {'status': 502, 'content_type': 'text/plain', 'body': 'down'}}])
add('preview-print-not-pdf', 'owner', f'{PREVIEW}?print=', 500, {'error': 'PDF not rendered'},
    renders=[{'url': '/document/subscription/preview/?sport_association_id=None',
              'authorization': '$bearer', 'reply': {'status': 200, 'content_type': 'application/pdf', 'body': 'not a pdf'}}])

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
# The page is served to the association's staff and to the account tied to the instructor.
add('compensation-instructor-account', 'coach', COMP('hours'), 200, HTML, language='it',
    html=compensation_context('hours', '2026-02-05', '2026-04-01'))
add('compensation-instructor-account-other', 'coach', COMP('volunteer'), 403, SCOPED_HIDDEN)
add('compensation-instructor-account-without-instructor', 'coach', COMP('bare'), 403, SCOPED_HIDDEN)
add('compensation-instructor-account-unknown', 'coach', f'/document/compensation/{UNKNOWN}/view/', 500, SERVER)
add('compensation-athlete-direct', 'athlete', COMP('volunteer'), 403, SCOPED_HIDDEN)
add('compensation-athlete-invalid-period', 'athlete', COMP('badperiod'), 403, SCOPED_HIDDEN)
add('compensation-athlete-deleted', 'athlete', COMP('deleted'), 500, SERVER)
add('compensation-member', 'member', COMP('hours'), 403, SCOPED_HIDDEN)
add('compensation-admin-impersonating-owner', 'admin', COMP('volunteer'), 200, HTML, language='it', headers=ADMIN_OWNER,
    html=compensation_context('volunteer', None, None))
add('compensation-scoped-athlete-denied', 'owner', COMP('hours'), 403, SCOPED_DENIED, headers=SCOPED)
add('compensation-period-other-digits', 'owner', COMP('digits'), 200, HTML, language='it',
    html=compensation_context('digits', '2026-03-15', '2026-03-29'))
add('compensation-period-month-other-digits', 'owner', COMP('digitsmonth'), 500, SERVER)
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
add('template-subscription-promotes-tutor', 'full_collaborator', f"{TPL('plain')}?subscription_id={S('tino')}", 200, HTML,
    html=model_context(plain_body('Tino Gialli', 'GLLTNI13A01L219B', '01/01/2013', 'Mario', ''), ALPHA_HEADER_OUT,
                       alpha_footer_out('')), promoted=[('tino', 'full_collaborator')])
add('template-head', 'owner', TPL('plain'), 405, method('HEAD'), method_='HEAD')
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
add('template-old-mention-keys', 'owner', TPL('oldkeys'), 200, HTML, html=model_context('<p>a||||||||$today|z</p>', '', ''))
add('template-old-mention-keys-subscription', 'owner', f"{TPL('oldkeys')}?subscription_id={S('mario')}", 200, HTML,
    html=model_context('<p>a|||||||Mario|$today|z</p>', '', ''))
add('template-foreign-custom-footer', 'owner', TPL('foreign'), 200, HTML, html=model_context('<p>Ciao </p>', '', '<p>Piè </p>'))
# The page is served to the association's staff.
add('template-athlete', 'athlete', TPL('plain'), 403, SCOPED_HIDDEN)
add('template-athlete-own-subscription', 'athlete', f"{TPL('plain')}?subscription_id={S('own')}", 403, SCOPED_HIDDEN)
add('template-athlete-other-subscription', 'athlete', f"{TPL('foreign')}?subscription_id={S('mario')}", 403, SCOPED_HIDDEN)
add('template-athlete-unknown', 'athlete', f'/document/template/{UNKNOWN}/view/', 500, SERVER)
add('template-member', 'member', TPL('bare'), 403, SCOPED_HIDDEN)
add('template-instructor-account', 'coach', TPL('plain'), 403, SCOPED_HIDDEN)
add('template-unknown', 'owner', f'/document/template/{UNKNOWN}/view/', 500, SERVER)
add('template-invalid-uuid', 'owner', '/document/template/x/view/', 500, SERVER)
add('template-scoped-athlete-denied', 'owner', TPL('plain'), 403, SCOPED_DENIED, headers=SCOPED)

# --- document/medical-appointment/<uid>/view/ (IsAuthenticated | HasBypassAuthorizationHeader) ---
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


common('medical', MED('agonistica'), 'Document Medical Appointment View')
add('medical-document-token', 'anonymous', MED('agonistica') + '?token=' + UNKNOWN, 401, ANONYMOUS)
add('medical-bypass-invalid', 'anonymous', MED('agonistica'), 401, ANONYMOUS, headers={'Authorization': 'not-the-token'})
add('medical-bypass-non-ascii', 'anonymous', MED('agonistica'), 500, SERVER, headers={'Authorization': 'tokèn'})
add('medical-anonymous-invalid-uuid', 'anonymous', '/document/medical-appointment/nope/view/', 401, ANONYMOUS)
BYPASS = {'Authorization': '$bypass'}
# The page is served to the association's staff, to the renderer and to the member of the appointment's subscription.
for name, actor, key, headers in (('medical-owner', 'owner', 'agonistica', {}), ('medical-bypass', 'anonymous', 'agonistica', BYPASS),
                                  ('medical-minor', 'full_collaborator', 'minor', {}),
                                  ('medical-without-associate', 'foreign_owner', 'noassociate', {}),
                                  ('medical-adult-non-agonistica', 'anonymous', 'adult', BYPASS),
                                  ('medical-null-meta', 'owner', 'nometa', {}), ('medical-athlete-own', 'athlete', 'athlete', {}),
                                  ('medical-admin-impersonating-owner', 'admin', 'athlete', ADMIN_OWNER)):
    add(name, actor, MED(key), 200, HTML, language='it', headers=headers, html=medical_context(key))
add('medical-athlete-other', 'athlete', MED('adult'), 403, SCOPED_HIDDEN)
add('medical-athlete-other-without-associate', 'athlete', MED('noassociate'), 403, SCOPED_HIDDEN)
add('medical-athlete-other-unsupported-region', 'athlete', MED('biella'), 403, SCOPED_HIDDEN)
add('medical-athlete-unknown', 'athlete', f'/document/medical-appointment/{UNKNOWN}/view/', 500, SERVER)
add('medical-athlete-invalid-uuid', 'athlete', '/document/medical-appointment/nope/view/', 400, ['badly formed hexadecimal UUID string'])
add('medical-member-of-athlete', 'member', MED('athlete'), 403, SCOPED_HIDDEN)
add('medical-instructor-account', 'coach', MED('agonistica'), 403, SCOPED_HIDDEN)
add('medical-unsupported-region', 'owner', MED('biella'), 303,
    {'error': 'This kind of medical appointment is not supported in this endpoint'})
add('medical-unsupported-region-bypass', 'anonymous', MED('biella'), 303,
    {'error': 'This kind of medical appointment is not supported in this endpoint'}, headers=BYPASS)
add('medical-unknown', 'owner', f'/document/medical-appointment/{UNKNOWN}/view/', 500, SERVER)
add('medical-unknown-bypass', 'anonymous', f'/document/medical-appointment/{UNKNOWN}/view/', 500, SERVER, headers=BYPASS)
add('medical-invalid-uuid', 'owner', '/document/medical-appointment/nope/view/', 400, ['badly formed hexadecimal UUID string'])
add('medical-invalid-uuid-digits', 'anonymous', '/document/medical-appointment/' + 'g' * 32 + '/view/', 400,
    ["invalid literal for int() with base 16: '" + 'g' * 32 + "'"], headers=BYPASS)
add('medical-scoped-athlete-denied', 'owner', MED('agonistica'), 403, SCOPED_DENIED, headers=SCOPED)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'document HTML views',
    'ids': IDS, 'associations': ASSOCIATIONS, 'associates': ASSOCIATES, 'tutors': TUTORS, 'payments': PAYMENTS,
    'subscriptions': SUBSCRIPTIONS, 'courses': COURSES, 'course_subscriptions': COURSE_SUBSCRIPTIONS, 'instructors': INSTRUCTORS,
    'signatures': SIGNATURES, 'signature_objects': SIGNATURE_OBJECTS, 'compensations': COMPENSATIONS, 'hours': HOURS,
    'templates': TEMPLATES, 'medical': MEDICAL,
    'extra_users': EXTRA_USERS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
